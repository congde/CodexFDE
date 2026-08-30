from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Protocol
from urllib import error, request


class MCPProviderError(RuntimeError):
    """Raised when MCP seam is misconfigured or unreachable."""


class MCPProvider(Protocol):
    """Capability seam: Definition consumers call list_tools / call_tool."""

    def available(self) -> bool: ...

    def capabilities(self) -> dict: ...

    def list_tools(self) -> list[dict]: ...

    def call_tool(self, name: str, arguments: dict | None = None) -> dict: ...


class OffMCPProvider:
    """Explicitly disabled MCP provider (default for course follow-along)."""

    def available(self) -> bool:
        return False

    def capabilities(self) -> dict:
        return {
            "mcp_available": False,
            "provider": "off",
            "reason": "mcp provider=off；激活 mcp.http 或 mcp.manifest 后可用",
            "tools": [],
        }

    def list_tools(self) -> list[dict]:
        return []

    def call_tool(self, name: str, arguments: dict | None = None) -> dict:
        raise MCPProviderError("MCP seam 已关闭（provider=off）")


class ManifestMCPProvider:
    """Local manifest provider — course demos without a live MCP server."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path).resolve() if path else None
        self._tools: list[dict] = []
        self._responses: dict[str, object] = {}
        if self.path and self.path.is_file():
            data = json.loads(self.path.read_text(encoding="utf-8"))
            tools = data.get("tools") if isinstance(data, dict) else []
            if isinstance(tools, list):
                for item in tools:
                    if not isinstance(item, dict) or not item.get("name"):
                        continue
                    name = str(item["name"])
                    self._tools.append({
                        "name": name,
                        "description": str(item.get("description") or ""),
                    })
                    if "response" in item:
                        self._responses[name] = item["response"]

    def available(self) -> bool:
        return bool(self._tools)

    def capabilities(self) -> dict:
        if not self.path:
            return {
                "mcp_available": False,
                "provider": "manifest",
                "reason": "未配置 manifest 路径（插件 config.path 或 HARNESS_MCP_MANIFEST）",
                "tools": [],
            }
        if not self.path.is_file():
            return {
                "mcp_available": False,
                "provider": "manifest",
                "reason": f"manifest 不存在：{self.path}",
                "tools": [],
                "path": str(self.path),
            }
        return {
            "mcp_available": self.available(),
            "provider": "manifest",
            "reason": "ready" if self.available() else "manifest 无工具",
            "tools": self.list_tools(),
            "path": str(self.path),
        }

    def list_tools(self) -> list[dict]:
        return list(self._tools)

    def call_tool(self, name: str, arguments: dict | None = None) -> dict:
        if not self.available():
            raise MCPProviderError("MCP manifest 未加载或无工具")
        names = {item["name"] for item in self._tools}
        if name not in names:
            raise MCPProviderError(f"未知 MCP 工具：{name}")
        if name in self._responses:
            payload = self._responses[name]
            return payload if isinstance(payload, dict) else {"content": payload}
        return {
            "content": [{"type": "text", "text": json.dumps(arguments or {}, ensure_ascii=False)}],
            "isError": False,
            "echo": True,
        }


class HttpMCPProvider:
    """Minimal JSON-RPC client for an optional MCP HTTP bridge (stdlib only)."""

    def __init__(self, base_url: str | None = None, *, timeout: float = 10.0) -> None:
        self.base_url = (base_url or os.getenv("HARNESS_MCP_URL") or "").rstrip("/")
        self.timeout = timeout
        self._request_id = 0

    def available(self) -> bool:
        return bool(self.base_url)

    def capabilities(self) -> dict:
        if not self.available():
            return {
                "mcp_available": False,
                "provider": "http",
                "reason": "未配置 HARNESS_MCP_URL；MCP http provider 处于惰性关闭",
                "tools": [],
            }
        try:
            tools = self.list_tools()
            return {
                "mcp_available": True,
                "provider": "http",
                "reason": "ready",
                "tools": tools,
                "url": self.base_url,
            }
        except Exception as exc:  # noqa: BLE001 - surface provider health
            return {
                "mcp_available": False,
                "provider": "http",
                "reason": f"{type(exc).__name__}: {exc}",
                "tools": [],
                "url": self.base_url,
            }

    def list_tools(self) -> list[dict]:
        result = self._rpc("tools/list", {})
        tools = result.get("tools") if isinstance(result, dict) else None
        if not isinstance(tools, list):
            return []
        return [
            {
                "name": item.get("name"),
                "description": item.get("description") or "",
            }
            for item in tools
            if isinstance(item, dict) and item.get("name")
        ]

    def call_tool(self, name: str, arguments: dict | None = None) -> dict:
        if not self.available():
            raise MCPProviderError("MCP 未配置：设置 HARNESS_MCP_URL 后可用")
        result = self._rpc("tools/call", {"name": name, "arguments": arguments or {}})
        if not isinstance(result, dict):
            return {"content": result}
        return result

    def _rpc(self, method: str, params: dict) -> Any:
        if not self.base_url:
            raise MCPProviderError("MCP 未配置")
        self._request_id += 1
        payload = {
            "jsonrpc": "2.0",
            "id": self._request_id,
            "method": method,
            "params": params,
        }
        body = json.dumps(payload).encode("utf-8")
        req = request.Request(
            self.base_url,
            data=body,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8")
        except error.URLError as exc:
            raise MCPProviderError(f"MCP 请求失败：{exc}") from exc
        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise MCPProviderError("MCP 返回非 JSON") from exc
        if isinstance(decoded, dict) and decoded.get("error"):
            err = decoded["error"]
            message = err.get("message") if isinstance(err, dict) else str(err)
            raise MCPProviderError(f"MCP error: {message}")
        return decoded.get("result") if isinstance(decoded, dict) else decoded


# Backward-compatible alias used by older imports/tests.
MCPClient = HttpMCPProvider


def create_mcp_provider(provider: str, config: dict | None = None) -> MCPProvider:
    cfg = dict(config or {})
    if provider == "off":
        return OffMCPProvider()
    if provider == "http":
        return HttpMCPProvider(cfg.get("url") or os.getenv("HARNESS_MCP_URL"))
    if provider == "manifest":
        path = cfg.get("path") or os.getenv("HARNESS_MCP_MANIFEST") or ""
        return ManifestMCPProvider(path or None)
    raise ValueError(f"未知 mcp provider: {provider}")


def mcp_tool_handler(context: dict, args: dict) -> dict:
    """Tool Registry handler for mcp.call — routes through the mcp seam provider."""
    client: MCPProvider = context.get("mcp") or OffMCPProvider()
    name = str(args.get("name") or "").strip()
    if not name:
        raise ValueError("mcp.call 需要 name")
    caps = client.capabilities()
    return {
        "provider": caps.get("provider", "mcp"),
        "name": name,
        "result": client.call_tool(name, dict(args.get("arguments") or {})),
    }


def mcp_list_handler(context: dict, _args: dict) -> dict:
    client: MCPProvider = context.get("mcp") or OffMCPProvider()
    caps = client.capabilities()
    return {
        "provider": caps.get("provider"),
        "available": bool(caps.get("mcp_available")),
        "reason": caps.get("reason"),
        "tools": client.list_tools(),
    }


def load_mcp_manifest(path: str | Path | None = None) -> dict:
    """Optional local MCP manifest for course demos without a live server."""
    candidate = Path(path or os.getenv("HARNESS_MCP_MANIFEST") or "")
    if not candidate.is_file():
        return {"tools": [], "source": None}
    data = json.loads(candidate.read_text(encoding="utf-8"))
    tools = data.get("tools") if isinstance(data, dict) else []
    return {"tools": tools if isinstance(tools, list) else [], "source": str(candidate)}
