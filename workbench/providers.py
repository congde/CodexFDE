from __future__ import annotations

import os
from pathlib import Path
from typing import Callable

from eval.harness import run_suite

from .fs_provider import LocalFsProvider
from .mcp_provider import create_mcp_provider
from .plugin_runtime import PluginContract, PluginSupervisor
from .project_runner import ProjectEvalRunner, ProjectExecutionRunner
from .project_store import ProjectStore
from .runtime_store import HarnessRuntimeStore
from .session_persist import JsonlSessionPersist
from .shell_provider import DenyShellProvider, LocalShellProvider


ExecutionRunner = Callable[[dict], dict]


PLUGIN_REQUIRES: dict[str, frozenset[str]] = {
    "session.sqlite": frozenset(),
    "persist.jsonl": frozenset({"sessions"}),
    "workspace.local": frozenset(),
    "fs.local": frozenset({"workspace"}),
    "shell.local": frozenset({"workspace", "permission"}),
    "shell.deny": frozenset({"workspace", "permission"}),
    "tools.local": frozenset({"fs", "shell", "approval", "permission"}),
    "llm.template": frozenset({"sessions"}),
    "llm.codex": frozenset({"sessions"}),
    "eval.command": frozenset({"workspace"}),
    "eval.local": frozenset({"workspace"}),
    "execution.codex": frozenset({"workspace", "permission"}),
    "execution.verify": frozenset({"workspace", "permission"}),
    "approval.named": frozenset({"sessions"}),
    "permission.local": frozenset({"workspace"}),
    "mcp.off": frozenset({"permission"}),
    "mcp.http": frozenset({"permission"}),
    "mcp.manifest": frozenset({"permission"}),
}


class HarnessProviders:
    """Resolve eval/execution/llm/fs/shell/persist providers from Profile composition."""

    def __init__(
        self,
        runtime: HarnessRuntimeStore,
        projects: ProjectStore,
        runtime_dir: str | Path,
        repository_root: str | Path,
    ) -> None:
        self.runtime = runtime
        self.projects = projects
        self.runtime_dir = Path(runtime_dir).resolve()
        self.repository_root = Path(repository_root).resolve()
        self._eval_runner = ProjectEvalRunner(projects, self.runtime_dir)
        self._execution_runner = ProjectExecutionRunner(projects, self.runtime_dir)
        self._fs_local = LocalFsProvider()
        self._shell_local = LocalShellProvider()
        self._shell_deny = DenyShellProvider()
        self._jsonl = JsonlSessionPersist(self.runtime_dir / "sessions_jsonl")
        self._plugin_supervisor: PluginSupervisor | None = None

    def attach_plugin_supervisor(self, supervisor: PluginSupervisor) -> None:
        self._plugin_supervisor = supervisor

    def plugin_contracts(self, tools: object) -> list[PluginContract]:
        """Build executable Definition+Provider contracts for the built-in catalog."""
        services: dict[str, object | Callable[[dict], object]] = {
            "session.sqlite": self.runtime,
            "persist.jsonl": self._jsonl,
            "workspace.local": self.repository_root,
            "fs.local": self._fs_local,
            "shell.local": self._shell_local,
            "shell.deny": self._shell_deny,
            "tools.local": tools,
            "llm.template": "template",
            "llm.codex": "codex",
            "eval.command": "command",
            "eval.local": "local",
            "execution.codex": "codex",
            "execution.verify": "verify",
            "approval.named": lambda config: {"provider": "named", **config},
            "permission.local": lambda config: {"provider": "local", **config},
            "mcp.off": lambda config: create_mcp_provider("off", config),
            "mcp.http": lambda config: create_mcp_provider("http", config),
            "mcp.manifest": lambda config: create_mcp_provider("manifest", config),
        }
        contracts: list[PluginContract] = []
        for plugin in self.runtime.plugins():
            plugin_id = str(plugin["id"])
            if plugin_id not in services:
                continue
            configured = services[plugin_id]

            def factory(_ctx, config, configured=configured):
                return configured(dict(config)) if callable(configured) else configured

            contracts.append(PluginContract(
                id=plugin_id,
                name=str(plugin["name"]),
                provides=frozenset({str(plugin["seam"])}),
                requires=PLUGIN_REQUIRES.get(plugin_id, frozenset()),
                factory=factory,
                config=dict(plugin.get("config") or {}),
            ))
        return contracts

    def _runtime_service(self, seam: str, profile_id: str) -> object | None:
        if self._plugin_supervisor is None:
            return None
        return self._plugin_supervisor.service(profile_id, seam)

    def _plugin_for_seam(self, seam: str, profile_id: str = "PROFILE-DEFAULT") -> dict:
        composition = self.runtime.composition(profile_id)
        for plugin in composition["plugins"]:
            if plugin["seam"] == seam and plugin["enabled"]:
                return plugin
        raise ValueError(f"Profile {profile_id} 缺少可用 {seam} provider")

    def _optional_plugin(self, seam: str, profile_id: str = "PROFILE-DEFAULT") -> dict | None:
        composition = self.runtime.composition(profile_id)
        for plugin in composition["plugins"]:
            if plugin["seam"] == seam and plugin["enabled"]:
                return plugin
        return None

    def eval_runner_for_task(self, task: dict, profile_id: str = "PROFILE-DEFAULT") -> Callable[..., dict]:
        provider = self._runtime_service("eval", profile_id)
        if provider is None:
            provider = self._plugin_for_seam("eval", profile_id)["provider"]
        if provider == "command":
            return self._eval_runner.for_task(task)
        if provider == "local":
            root = self._project_root_for_task(task)

            def run(_suite: str = "blocking", write_report: bool = True) -> dict:
                previous = Path.cwd()
                try:
                    os.chdir(root)
                    return run_suite("blocking", write_report)
                finally:
                    os.chdir(previous)

            return run
        raise ValueError(f"未知 eval provider: {provider}")

    def _project_root_for_task(self, task: dict) -> Path:
        reference = next(
            (item for item in task.get("business_refs", []) if str(item).startswith("PROJECT:")),
            "",
        )
        project_id = reference.partition(":")[2]
        if project_id:
            return Path(self.projects.get(project_id)["root_path"]).resolve()
        return self.repository_root

    def execution_runner_for_task(self, task: dict, profile_id: str = "PROFILE-DEFAULT") -> ExecutionRunner | None:
        provider = self._runtime_service("execution", profile_id)
        if provider is None:
            provider = self._plugin_for_seam("execution", profile_id)["provider"]
        if provider == "codex":
            return self._execution_runner
        if provider == "verify":
            return lambda _task: {
                "mode": "verification_only",
                "changed_files": [],
                "message": "verify provider：只验证，不写入代码",
            }
        raise ValueError(f"未知 execution provider: {provider}")

    def llm_provider(self, profile_id: str = "PROFILE-DEFAULT") -> str:
        provider = self._runtime_service("llm", profile_id)
        if provider is not None:
            return str(provider)
        return str(self._plugin_for_seam("llm", profile_id)["provider"])

    def fs_provider(self, profile_id: str = "PROFILE-DEFAULT"):
        service = self._runtime_service("fs", profile_id)
        if service is not None:
            return service
        provider = self._plugin_for_seam("fs", profile_id)["provider"]
        if provider == "local":
            return self._fs_local
        raise ValueError(f"未知 fs provider: {provider}")

    def shell_provider(self, profile_id: str = "PROFILE-DEFAULT"):
        service = self._runtime_service("shell", profile_id)
        if service is not None:
            return service
        provider = self._plugin_for_seam("shell", profile_id)["provider"]
        if provider == "local":
            return self._shell_local
        if provider == "deny":
            return self._shell_deny
        raise ValueError(f"未知 shell provider: {provider}")

    def persist_jsonl(self, profile_id: str = "PROFILE-DEFAULT") -> JsonlSessionPersist | None:
        if self._plugin_supervisor is not None:
            try:
                service = self._plugin_supervisor.service(profile_id, "persist")
            except Exception:  # optional seam can be absent or pending
                return None
            if not isinstance(service, JsonlSessionPersist):
                raise ValueError("persist seam 未提供 JsonlSessionPersist")
            return service
        plugin = self._optional_plugin("persist", profile_id)
        if not plugin:
            return None
        if plugin["provider"] != "jsonl":
            raise ValueError(f"未知 persist provider: {plugin['provider']}")
        return self._jsonl

    def mcp_provider(self, profile_id: str = "PROFILE-DEFAULT"):
        if self._plugin_supervisor is not None:
            try:
                return self._plugin_supervisor.service(profile_id, "mcp")
            except Exception:  # optional seam can be absent or pending
                return create_mcp_provider("off")
        plugin = self._optional_plugin("mcp", profile_id)
        if not plugin:
            return create_mcp_provider("off")
        return create_mcp_provider(plugin["provider"], plugin.get("config") or {})

    def capabilities(self) -> dict:
        execution = self._execution_runner.capabilities()
        mcp = self.mcp_provider().capabilities()
        persist = self._optional_plugin("persist")
        mcp_plugin = self._optional_plugin("mcp")
        return {
            **execution,
            "mcp": mcp,
            "providers": {
                "eval": self._plugin_for_seam("eval")["provider"],
                "execution": self._plugin_for_seam("execution")["provider"],
                "llm": self._plugin_for_seam("llm")["provider"],
                "fs": self._plugin_for_seam("fs")["provider"],
                "shell": self._plugin_for_seam("shell")["provider"],
                "persist": persist["provider"] if persist else None,
                "mcp": mcp_plugin["provider"] if mcp_plugin else "off",
            },
        }
