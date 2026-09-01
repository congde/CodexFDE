from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from threading import RLock
from typing import Callable, Iterable, Mapping


Disposer = Callable[[], None]
PluginFactory = Callable[["PluginContext", Mapping[str, object]], object]
LifecycleSink = Callable[[dict], None]


class PluginRuntimeError(RuntimeError):
    """Base error for the reversible plugin runtime."""


class PluginActivationError(PluginRuntimeError):
    """A plugin could not become active; the previous composition was restored."""


class PluginDisposalError(PluginRuntimeError):
    """One or more reversible side effects failed to clean up."""

    def __init__(self, plugin_id: str, errors: list[str]) -> None:
        super().__init__(f"插件 {plugin_id} 清理失败：{'；'.join(errors)}")
        self.plugin_id = plugin_id
        self.errors = tuple(errors)


class PluginState(str, Enum):
    PENDING = "pending"
    LOADING = "loading"
    ACTIVE = "active"
    FAILED = "failed"
    UNLOADING = "unloading"
    DISPOSED = "disposed"


class EffectScope:
    """Own reversible side effects and dispose them once in LIFO order."""

    def __init__(self, owner: str) -> None:
        self.owner = owner
        self._effects: list[tuple[str, Disposer]] = []
        self._disposing = False
        self._disposed = False
        self._lock = RLock()

    @property
    def disposed(self) -> bool:
        return self._disposed

    @property
    def size(self) -> int:
        return len(self._effects)

    def add(self, disposer: Disposer, label: str = "effect") -> Disposer:
        if not callable(disposer):
            raise TypeError("disposer 必须可调用")
        with self._lock:
            if self._disposed or self._disposing:
                raise PluginRuntimeError(f"EffectScope {self.owner} 已进入清理阶段")
            self._effects.append((label, disposer))
        return disposer

    def effect(self, installer: Callable[[], object], label: str = "effect") -> object:
        """Run an installer and own any disposer(s) it returns."""
        result = installer()
        if result is None:
            return result
        if callable(result):
            self.add(result, label)
            return result
        if isinstance(result, Iterable) and not isinstance(result, (str, bytes, dict)):
            for index, disposer in enumerate(result):
                self.add(disposer, f"{label}[{index}]")
            return result
        raise TypeError("effect installer 只能返回 disposer、disposer 序列或 None")

    def dispose(self) -> None:
        with self._lock:
            if self._disposed or self._disposing:
                return
            self._disposing = True
            effects = list(reversed(self._effects))
            self._effects.clear()
        errors: list[str] = []
        for label, disposer in effects:
            try:
                disposer()
            except Exception as exc:  # noqa: BLE001 - all cleanup must be attempted
                errors.append(f"{label}: {type(exc).__name__}: {exc}")
        with self._lock:
            self._disposed = True
            self._disposing = False
        if errors:
            raise PluginDisposalError(self.owner, errors)


class EventBus:
    """Small synchronous event bus whose listeners can belong to an EffectScope."""

    def __init__(self) -> None:
        self._listeners: dict[str, list[tuple[str, Callable[..., object]]]] = {}
        self._lock = RLock()

    def on(self, name: str, owner: str, listener: Callable[..., object]) -> Disposer:
        if not name.strip():
            raise ValueError("事件名不能为空")
        entry = (owner, listener)
        with self._lock:
            self._listeners.setdefault(name, []).append(entry)
        disposed = False

        def dispose() -> None:
            nonlocal disposed
            with self._lock:
                if disposed:
                    return
                disposed = True
                listeners = self._listeners.get(name, [])
                if entry in listeners:
                    listeners.remove(entry)
                if not listeners:
                    self._listeners.pop(name, None)

        return dispose

    def emit(self, name: str, *args: object, **kwargs: object) -> list[object]:
        with self._lock:
            listeners = tuple(self._listeners.get(name, ()))
        return [listener(*args, **kwargs) for _owner, listener in listeners]

    def listener_count(self, name: str | None = None) -> int:
        with self._lock:
            if name is not None:
                return len(self._listeners.get(name, ()))
            return sum(len(items) for items in self._listeners.values())


@dataclass(frozen=True)
class PluginContract:
    id: str
    provides: frozenset[str]
    requires: frozenset[str]
    factory: PluginFactory
    name: str = ""
    config: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("插件 id 不能为空")
        if not self.provides:
            raise ValueError(f"插件 {self.id} 必须声明 provides")
        if any(not item.strip() for item in self.provides | self.requires):
            raise ValueError(f"插件 {self.id} 的能力名不能为空")


@dataclass(frozen=True)
class ServiceBinding:
    name: str
    plugin_id: str
    generation: int
    value: object


@dataclass
class PluginRecord:
    contract: PluginContract
    state: PluginState = PluginState.DISPOSED
    generation: int = 0
    dependency_epoch: dict[str, str] = field(default_factory=dict)
    error: str | None = None
    scope: EffectScope | None = None


class PluginContext:
    def __init__(self, runtime: "PluginRuntime", record: PluginRecord, scope: EffectScope) -> None:
        self.runtime = runtime
        self.plugin_id = record.contract.id
        self._record = record
        self._scope = scope

    def service(self, name: str) -> object:
        return self.runtime.service(name)

    def effect(self, installer: Callable[[], object], label: str = "effect") -> object:
        return self._scope.effect(installer, label)

    def on(self, name: str, listener: Callable[..., object]) -> Disposer:
        disposer = self.runtime.events.on(name, self.plugin_id, listener)
        self._scope.add(disposer, f"event:{name}")
        return disposer

    def provide(self, name: str, value: object) -> ServiceBinding:
        if name not in self._record.contract.provides:
            raise PluginRuntimeError(f"插件 {self.plugin_id} 未声明 provides={name}")
        return self.runtime._provide(self._record, self._scope, name, value)


class PluginRuntime:
    """Dependency-aware, reversible runtime for one isolated Profile."""

    def __init__(self, profile_id: str, *, event_sink: LifecycleSink | None = None) -> None:
        self.profile_id = profile_id
        self.events = EventBus()
        self._records: dict[str, PluginRecord] = {}
        self._services: dict[str, ServiceBinding] = {}
        self._desired: list[str] = []
        self._event_sink = event_sink
        self._lock = RLock()

    @property
    def desired(self) -> tuple[str, ...]:
        return tuple(self._desired)

    def register(self, contract: PluginContract) -> None:
        with self._lock:
            if contract.id in self._records:
                raise ValueError(f"重复插件 id: {contract.id}")
            self._records[contract.id] = PluginRecord(contract=contract)

    def service(self, name: str) -> object:
        try:
            return self._services[name].value
        except KeyError as exc:
            raise PluginRuntimeError(f"Profile {self.profile_id} 缺少活动服务：{name}") from exc

    def binding(self, name: str) -> ServiceBinding:
        try:
            return self._services[name]
        except KeyError as exc:
            raise PluginRuntimeError(f"Profile {self.profile_id} 缺少活动服务：{name}") from exc

    def reconcile(self, desired_ids: Iterable[str]) -> dict:
        with self._lock:
            desired = list(dict.fromkeys(desired_ids))
            unknown = [item for item in desired if item not in self._records]
            if unknown:
                raise KeyError(unknown[0])
            self._provider_map(desired)  # duplicate-provider validation
            self._topological_order(desired)  # cycle validation
            previous = list(self._desired)
            try:
                self._apply(desired)
            except Exception as exc:
                rollback_error: Exception | None = None
                try:
                    self._apply(previous, rollback=True)
                except Exception as rollback_exc:  # noqa: BLE001 - surface both failures
                    rollback_error = rollback_exc
                detail = f"{type(exc).__name__}: {exc}"
                if rollback_error is not None:
                    detail += f"；回滚失败 {type(rollback_error).__name__}: {rollback_error}"
                raise PluginActivationError(detail) from exc
            return self.status()

    def shutdown(self) -> dict:
        return self.reconcile(())

    def status(self) -> dict:
        plugins = []
        desired = set(self._desired)
        for plugin_id, record in sorted(self._records.items()):
            plugins.append({
                "id": plugin_id,
                "name": record.contract.name or plugin_id,
                "desired": plugin_id in desired,
                "state": record.state.value,
                "generation": record.generation,
                "provides": sorted(record.contract.provides),
                "requires": sorted(record.contract.requires),
                "dependency_epoch": dict(record.dependency_epoch),
                "effects": record.scope.size if record.scope and not record.scope.disposed else 0,
                "error": record.error,
            })
        return {
            "profile_id": self.profile_id,
            "desired_plugins": list(self._desired),
            "active_plugins": [item["id"] for item in plugins if item["state"] == PluginState.ACTIVE.value],
            "pending_plugins": [item["id"] for item in plugins if item["state"] == PluginState.PENDING.value],
            "services": {
                name: {"plugin_id": item.plugin_id, "generation": item.generation}
                for name, item in sorted(self._services.items())
            },
            "listener_count": self.events.listener_count(),
            "plugins": plugins,
        }

    def _apply(self, desired: list[str], *, rollback: bool = False) -> None:
        old_provider = self._provider_map(self._desired)
        new_provider = self._provider_map(desired)
        changed_seams = {
            seam for seam in set(old_provider) | set(new_provider)
            if old_provider.get(seam) != new_provider.get(seam)
        }
        affected = {
            plugin_id
            for plugin_id, record in self._records.items()
            if record.state == PluginState.ACTIVE
            and (
                plugin_id not in desired
                or bool(record.contract.provides & changed_seams)
                or bool(record.contract.requires & changed_seams)
            )
        }
        changed = True
        while changed:
            changed = False
            affected_services = set().union(*(
                self._records[item].contract.provides for item in affected
            )) if affected else set()
            for plugin_id, record in self._records.items():
                if record.state == PluginState.ACTIVE and plugin_id not in affected:
                    if record.contract.requires & affected_services:
                        affected.add(plugin_id)
                        changed = True

        current_order = self._topological_order(self._desired)
        for plugin_id in reversed(current_order):
            if plugin_id in affected:
                self._stop(self._records[plugin_id], rollback=rollback)
        for plugin_id in sorted(affected - set(current_order)):
            self._stop(self._records[plugin_id], rollback=rollback)

        self._desired = list(desired)
        desired_set = set(desired)
        for plugin_id, record in self._records.items():
            if plugin_id not in desired_set and record.state not in {PluginState.ACTIVE, PluginState.FAILED}:
                record.state = PluginState.DISPOSED
                record.error = None

        for plugin_id in self._topological_order(desired):
            record = self._records[plugin_id]
            missing = sorted(name for name in record.contract.requires if name not in self._services)
            if missing:
                record.scope = None
                record.dependency_epoch = {}
                self._transition(record, PluginState.PENDING, error=f"missing dependencies: {', '.join(missing)}", rollback=rollback)
                continue
            if record.state == PluginState.ACTIVE:
                continue
            self._start(record, rollback=rollback)

    def _start(self, record: PluginRecord, *, rollback: bool) -> None:
        record.generation += 1
        record.dependency_epoch = {
            name: f"{self._services[name].plugin_id}:{self._services[name].generation}"
            for name in sorted(record.contract.requires)
        }
        record.error = None
        scope = EffectScope(record.contract.id)
        record.scope = scope
        self._transition(record, PluginState.LOADING, rollback=rollback)
        context = PluginContext(self, record, scope)
        try:
            value = record.contract.factory(context, record.contract.config)
            if len(record.contract.provides) == 1:
                context.provide(next(iter(record.contract.provides)), value)
            else:
                if not isinstance(value, Mapping):
                    raise PluginRuntimeError(f"插件 {record.contract.id} 提供多个服务时必须返回 mapping")
                for name in record.contract.provides:
                    if name not in value:
                        raise PluginRuntimeError(f"插件 {record.contract.id} 未返回服务 {name}")
                    context.provide(name, value[name])
            self._transition(record, PluginState.ACTIVE, rollback=rollback)
        except Exception as exc:
            cleanup_error: Exception | None = None
            try:
                scope.dispose()
            except Exception as dispose_exc:  # noqa: BLE001
                cleanup_error = dispose_exc
            message = f"{type(exc).__name__}: {exc}"
            if cleanup_error is not None:
                message += f"；清理失败 {type(cleanup_error).__name__}: {cleanup_error}"
            self._transition(record, PluginState.FAILED, error=message, rollback=rollback)
            raise PluginActivationError(f"插件 {record.contract.id} 激活失败：{message}") from exc

    def _stop(self, record: PluginRecord, *, rollback: bool) -> None:
        if record.state != PluginState.ACTIVE:
            return
        self._transition(record, PluginState.UNLOADING, rollback=rollback)
        try:
            if record.scope is not None:
                record.scope.dispose()
        except Exception as exc:
            self._transition(record, PluginState.FAILED, error=f"{type(exc).__name__}: {exc}", rollback=rollback)
            raise
        record.scope = None
        record.dependency_epoch = {}
        record.error = None
        self._transition(record, PluginState.DISPOSED, rollback=rollback)

    def _provide(self, record: PluginRecord, scope: EffectScope, name: str, value: object) -> ServiceBinding:
        existing = self._services.get(name)
        if existing is not None:
            raise PluginRuntimeError(
                f"服务 {name} 已由 {existing.plugin_id} 提供，不能再由 {record.contract.id} 注册"
            )
        binding = ServiceBinding(name, record.contract.id, record.generation, value)
        self._services[name] = binding

        def dispose() -> None:
            if self._services.get(name) is binding:
                self._services.pop(name, None)

        scope.add(dispose, f"service:{name}")
        return binding

    def _provider_map(self, desired: Iterable[str]) -> dict[str, str]:
        providers: dict[str, str] = {}
        for plugin_id in desired:
            for seam in self._records[plugin_id].contract.provides:
                existing = providers.get(seam)
                if existing is not None and existing != plugin_id:
                    raise PluginRuntimeError(f"能力 {seam} 同时由 {existing} 和 {plugin_id} 提供")
                providers[seam] = plugin_id
        return providers

    def _topological_order(self, desired: Iterable[str]) -> list[str]:
        desired_list = list(dict.fromkeys(desired))
        providers = self._provider_map(desired_list)
        edges = {
            plugin_id: {
                providers[name]
                for name in self._records[plugin_id].contract.requires
                if name in providers and providers[name] != plugin_id
            }
            for plugin_id in desired_list
        }
        order: list[str] = []
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(plugin_id: str) -> None:
            if plugin_id in visited:
                return
            if plugin_id in visiting:
                raise PluginRuntimeError(f"插件依赖成环：{plugin_id}")
            visiting.add(plugin_id)
            for dependency in sorted(edges[plugin_id]):
                visit(dependency)
            visiting.remove(plugin_id)
            visited.add(plugin_id)
            order.append(plugin_id)

        for plugin_id in desired_list:
            visit(plugin_id)
        return order

    def _transition(
        self,
        record: PluginRecord,
        state: PluginState,
        *,
        error: str | None = None,
        rollback: bool = False,
    ) -> None:
        previous = record.state
        record.state = state
        record.error = error
        if self._event_sink is not None:
            self._event_sink({
                "profile_id": self.profile_id,
                "plugin_id": record.contract.id,
                "from_state": previous.value,
                "to_state": state.value,
                "generation": record.generation,
                "dependency_epoch": dict(record.dependency_epoch),
                "error": error,
                "rollback": rollback,
            })


class PluginSupervisor:
    """Own one isolated PluginRuntime per Profile and provide rollback callbacks."""

    def __init__(self, contracts: Iterable[PluginContract], *, event_sink: LifecycleSink | None = None) -> None:
        self._contracts = tuple(contracts)
        self._event_sink = event_sink
        self._runtimes: dict[str, PluginRuntime] = {}
        self._lock = RLock()

    def ensure(self, profile_id: str, desired_ids: Iterable[str]) -> PluginRuntime:
        with self._lock:
            runtime = self._runtimes.get(profile_id)
            if runtime is None:
                sink = None
                if self._event_sink is not None:
                    sink = lambda event: self._event_sink(event)
                runtime = PluginRuntime(profile_id, event_sink=sink)
                for contract in self._contracts:
                    runtime.register(contract)
                self._runtimes[profile_id] = runtime
            runtime.reconcile(desired_ids)
            return runtime

    def reconcile(self, profile_id: str, desired_ids: Iterable[str]) -> Disposer:
        with self._lock:
            runtime = self._runtimes.get(profile_id)
            if runtime is None:
                runtime = self.ensure(profile_id, desired_ids)
                previous: tuple[str, ...] = ()
            else:
                previous = runtime.desired
                runtime.reconcile(desired_ids)
        disposed = False

        def rollback() -> None:
            nonlocal disposed
            if disposed:
                return
            disposed = True
            runtime.reconcile(previous)

        return rollback

    def service(self, profile_id: str, name: str) -> object:
        try:
            runtime = self._runtimes[profile_id]
        except KeyError as exc:
            raise PluginRuntimeError(f"Profile {profile_id} 尚未启动") from exc
        return runtime.service(name)

    def status(self, profile_id: str) -> dict:
        try:
            return self._runtimes[profile_id].status()
        except KeyError as exc:
            raise KeyError(profile_id) from exc

    def shutdown(self) -> None:
        for runtime in reversed(tuple(self._runtimes.values())):
            runtime.shutdown()
