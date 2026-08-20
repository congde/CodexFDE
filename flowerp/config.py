from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from pathlib import Path


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.getenv(name)
    value = default if raw is None else int(raw)
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} 必须在 {minimum}..{maximum} 范围内")
    return value


@dataclass(frozen=True)
class Settings:
    runtime_dir: Path
    host: str
    port: int
    environment: str
    debug: bool
    auth_required: bool
    bootstrap_admin: str | None
    bootstrap_password: str | None
    session_hours: int
    max_body_bytes: int
    allowed_origins: tuple[str, ...]
    cookie_secure: bool
    database_busy_timeout_ms: int
    legacy_api_enabled: bool
    max_concurrent_requests: int
    request_rate_per_minute: int
    request_timeout_seconds: int
    minimum_free_disk_mb: int
    backup_max_age_hours: int
    require_recent_backup: bool

    @property
    def production(self) -> bool:
        return self.environment == "production"

    def validate(self) -> None:
        if self.production and not self.auth_required:
            raise ValueError("生产环境必须启用 FLOWERP_AUTH_REQUIRED")
        if self.production and self.bootstrap_password and len(self.bootstrap_password) < 12:
            raise ValueError("生产环境初始管理员密码至少 12 位")
        if self.production and self.host in {"0.0.0.0", "::"} and not self.allowed_origins:
            raise ValueError("公网监听时必须配置 FLOWERP_ALLOWED_ORIGINS")


def load_settings(runtime_dir: str | Path | None = None) -> Settings:
    runtime = Path(runtime_dir or os.getenv("FLOWERP_RUNTIME_DIR", ".runtime")).resolve()
    env = os.getenv("FLOWERP_ENV", "development").strip().lower()
    origins = tuple(
        item.strip().rstrip("/")
        for item in os.getenv("FLOWERP_ALLOWED_ORIGINS", "").split(",")
        if item.strip()
    )
    settings = Settings(
        runtime_dir=runtime,
        host=os.getenv("FLOWERP_HOST", "127.0.0.1"),
        port=_int("FLOWERP_PORT", 8000, 1, 65535),
        environment=env,
        debug=_bool("FLOWERP_DEBUG", env != "production"),
        auth_required=_bool("FLOWERP_AUTH_REQUIRED", env == "production"),
        bootstrap_admin=os.getenv("FLOWERP_BOOTSTRAP_ADMIN") or None,
        bootstrap_password=os.getenv("FLOWERP_BOOTSTRAP_PASSWORD") or None,
        session_hours=_int("FLOWERP_SESSION_HOURS", 12, 1, 168),
        max_body_bytes=_int("FLOWERP_MAX_BODY_BYTES", 1_000_000, 1024, 20_000_000),
        allowed_origins=origins,
        cookie_secure=_bool("FLOWERP_COOKIE_SECURE", env == "production"),
        database_busy_timeout_ms=_int("FLOWERP_DB_BUSY_TIMEOUT_MS", 5000, 100, 60000),
        legacy_api_enabled=_bool("FLOWERP_LEGACY_API_ENABLED", env != "production"),
        max_concurrent_requests=_int("FLOWERP_MAX_CONCURRENT_REQUESTS", 64, 1, 1000),
        request_rate_per_minute=_int("FLOWERP_REQUEST_RATE_PER_MINUTE", 600, 10, 100000),
        request_timeout_seconds=_int("FLOWERP_REQUEST_TIMEOUT_SECONDS", 30, 1, 300),
        minimum_free_disk_mb=_int("FLOWERP_MIN_FREE_DISK_MB", 512, 32, 1_000_000),
        backup_max_age_hours=_int("FLOWERP_BACKUP_MAX_AGE_HOURS", 36, 1, 720),
        require_recent_backup=_bool("FLOWERP_REQUIRE_RECENT_BACKUP", False),
    )
    settings.validate()
    return settings


def generate_bootstrap_password() -> str:
    """Generate a display-once password for local initialization."""
    return secrets.token_urlsafe(18)
