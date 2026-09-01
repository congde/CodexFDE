from __future__ import annotations

import unittest
import subprocess
import tempfile
from pathlib import Path

from workbench.plugin_runtime import (
    EffectScope,
    PluginActivationError,
    PluginContract,
    PluginRuntime,
    PluginState,
)
from workbench.platform_api import HarnessPlatformAPI


class EffectScopeTests(unittest.TestCase):
    def test_disposes_in_reverse_order_and_is_idempotent(self) -> None:
        calls: list[str] = []
        scope = EffectScope("demo")
        scope.add(lambda: calls.append("first"), "first")
        scope.add(lambda: calls.append("second"), "second")

        scope.dispose()
        scope.dispose()

        self.assertEqual(["second", "first"], calls)
        self.assertTrue(scope.disposed)


class PluginRuntimeTests(unittest.TestCase):
    def test_provider_switch_unloads_dependents_then_restarts_them(self) -> None:
        trace: list[str] = []
        runtime = PluginRuntime("PROFILE-TEST")

        def provider(name: str):
            def start(ctx, _config):
                trace.append(f"start:{name}")
                ctx.effect(lambda: lambda: trace.append(f"stop:{name}"), f"stop:{name}")
                return name

            return start

        def consumer(ctx, _config):
            trace.append(f"start:consumer:{ctx.service('database')}")
            ctx.effect(lambda: lambda: trace.append("stop:consumer"), "stop:consumer")
            return {"database": ctx.service("database")}

        runtime.register(PluginContract(
            "db.a", frozenset({"database"}), frozenset(), provider("a")
        ))
        runtime.register(PluginContract(
            "db.b", frozenset({"database"}), frozenset(), provider("b")
        ))
        runtime.register(PluginContract(
            "consumer", frozenset({"app"}), frozenset({"database"}), consumer
        ))

        runtime.reconcile(["db.a", "consumer"])
        self.assertEqual({"database": "a"}, runtime.service("app"))
        runtime.reconcile(["db.b", "consumer"])

        self.assertEqual({"database": "b"}, runtime.service("app"))
        self.assertEqual(
            [
                "start:a", "start:consumer:a",
                "stop:consumer", "stop:a",
                "start:b", "start:consumer:b",
            ],
            trace,
        )
        consumer_status = next(item for item in runtime.status()["plugins"] if item["id"] == "consumer")
        self.assertEqual(PluginState.ACTIVE.value, consumer_status["state"])
        self.assertEqual(2, consumer_status["generation"])
        self.assertEqual("db.b:1", consumer_status["dependency_epoch"]["database"])

    def test_failed_replacement_cleans_effects_and_restores_previous_composition(self) -> None:
        resources: list[str] = []
        runtime = PluginRuntime("PROFILE-ROLLBACK")

        def stable(_ctx, _config):
            return "stable"

        def broken(ctx, _config):
            resources.append("opened")
            ctx.effect(lambda: lambda: resources.append("closed"), "broken-resource")
            raise RuntimeError("boom")

        runtime.register(PluginContract(
            "provider.stable", frozenset({"service"}), frozenset(), stable
        ))
        runtime.register(PluginContract(
            "provider.broken", frozenset({"service"}), frozenset(), broken
        ))
        runtime.reconcile(["provider.stable"])

        with self.assertRaises(PluginActivationError):
            runtime.reconcile(["provider.broken"])

        self.assertEqual("stable", runtime.service("service"))
        self.assertEqual(["opened", "closed"], resources)
        self.assertEqual(("provider.stable",), runtime.desired)
        stable_status = next(item for item in runtime.status()["plugins"] if item["id"] == "provider.stable")
        self.assertEqual(PluginState.ACTIVE.value, stable_status["state"])
        self.assertEqual(2, stable_status["generation"])

    def test_listener_is_owned_by_plugin_scope(self) -> None:
        received: list[str] = []
        runtime = PluginRuntime("PROFILE-EVENT")

        def observer(ctx, _config):
            ctx.on("task/changed", lambda value: received.append(str(value)))
            return "observer"

        runtime.register(PluginContract(
            "observer", frozenset({"observer"}), frozenset(), observer
        ))
        runtime.reconcile(["observer"])
        runtime.events.emit("task/changed", "first")
        self.assertEqual(1, runtime.events.listener_count())

        runtime.shutdown()
        runtime.events.emit("task/changed", "second")

        self.assertEqual(["first"], received)
        self.assertEqual(0, runtime.events.listener_count())

    def test_missing_dependency_stays_pending_until_provider_arrives(self) -> None:
        runtime = PluginRuntime("PROFILE-PENDING")
        runtime.register(PluginContract(
            "consumer", frozenset({"app"}), frozenset({"database"}),
            lambda ctx, _config: ctx.service("database"),
        ))
        runtime.register(PluginContract(
            "database", frozenset({"database"}), frozenset(),
            lambda _ctx, _config: "ready",
        ))

        pending = runtime.reconcile(["consumer"])
        self.assertEqual(["consumer"], pending["pending_plugins"])
        active = runtime.reconcile(["database", "consumer"])

        self.assertFalse(active["pending_plugins"])
        self.assertEqual("ready", runtime.service("app"))


class PluginRuntimeIntegrationTests(unittest.TestCase):
    def _api(self, root: Path) -> HarnessPlatformAPI:
        repo = root / "target"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        return HarnessPlatformAPI(root / "harness", repo)

    def test_profile_switch_exposes_live_state_and_append_only_events(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            api = self._api(Path(temporary))
            before = api.runtime.composition("PROFILE-DEFAULT")
            self.assertTrue(before["runtime_ready"])
            self.assertFalse(before["runtime"]["pending_plugins"])
            self.assertEqual(
                "eval.command",
                before["runtime"]["services"]["eval"]["plugin_id"],
            )

            updated = api.runtime.activate_plugin("PROFILE-DEFAULT", "eval.local")

            self.assertTrue(updated["ready"])
            self.assertEqual(
                "eval.local",
                updated["runtime"]["services"]["eval"]["plugin_id"],
            )
            events = api.runtime.plugin_events("PROFILE-DEFAULT", 20)
            eval_transitions = [
                (item["plugin_id"], item["to_state"])
                for item in reversed(events)
                if item["plugin_id"].startswith("eval.")
            ]
            self.assertIn(("eval.command", "unloading"), eval_transitions)
            self.assertIn(("eval.command", "disposed"), eval_transitions)
            self.assertIn(("eval.local", "active"), eval_transitions)
            route = api.dispatch(
                "GET", "/api/v1/profiles/PROFILE-DEFAULT/runtime", {}, {},
            )
            self.assertEqual(200, route.status)
            self.assertEqual("eval.local", route.body["services"]["eval"]["plugin_id"])

    def test_optional_plugin_disable_reverses_live_service(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            api = self._api(Path(temporary))
            self.assertIsNotNone(api.providers.persist_jsonl())

            api.runtime.set_plugin_enabled("persist.jsonl", False)

            self.assertIsNone(api.providers.persist_jsonl())
            composition = api.runtime.composition()
            self.assertTrue(composition["ready"])
            self.assertNotIn("persist", composition["runtime"]["services"])
            persist = next(
                item for item in composition["runtime"]["plugins"]
                if item["id"] == "persist.jsonl"
            )
            self.assertEqual(PluginState.DISPOSED.value, persist["state"])


if __name__ == "__main__":
    unittest.main()
