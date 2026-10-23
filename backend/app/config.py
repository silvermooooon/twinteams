"""Explicit local identity and fail-closed production configuration."""

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]


def flag(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes"}


def local_database(url: str) -> bool:
    parsed = urlsplit(url)
    return parsed.scheme in {
        "postgres",
        "postgresql",
        "postgresql+psycopg",
    } and parsed.hostname in {"127.0.0.1", "localhost", "::1"}


@dataclass(frozen=True)
class Settings:
    tenant_id: str = os.getenv("TWINTEAMS_TENANT_ID", "local")
    environment: str = os.getenv("TWINTEAMS_ENV", "local")
    development_mode: bool = flag("TWINTEAMS_DEVELOPMENT_MODE", False)
    database_url: str = os.getenv("DATABASE_URL", "")
    jwt_key: str = os.getenv("TWINTEAMS_JWT_KEY", "")
    jwt_algorithm: str = os.getenv("TWINTEAMS_JWT_ALGORITHM", "RS256")
    jwt_issuer: str = os.getenv("TWINTEAMS_JWT_ISSUER", "")
    jwt_audience: str = os.getenv("TWINTEAMS_JWT_AUDIENCE", "")
    redis_prefix: str = os.getenv("TWINTEAMS_REDIS_PREFIX", "twinteams:" + os.getenv("TWINTEAMS_TENANT_ID", "local"))
    redis_url: str = os.getenv("TWINTEAMS_REDIS_URL", "redis://127.0.0.1:6379/0")
    compensation_seconds: int = int(os.getenv("TWINTEAMS_COMPENSATION_SECONDS", "60"))
    scheduled_task_min_interval_seconds: int = max(60, int(os.getenv("TWINTEAMS_SCHEDULED_TASK_MIN_INTERVAL_SECONDS", "60")))
    scheduled_task_max_per_owner: int = max(1, int(os.getenv("TWINTEAMS_SCHEDULED_TASK_MAX_PER_OWNER", "100")))
    scheduled_task_batch_size: int = max(1, min(500, int(os.getenv("TWINTEAMS_SCHEDULED_TASK_BATCH_SIZE", "100"))))
    scheduled_task_reconcile_seconds: int = max(30, int(os.getenv("TWINTEAMS_SCHEDULED_TASK_RECONCILE_SECONDS", "300")))
    onboarding_enabled: bool = flag("TWINTEAMS_ONBOARDING_ENABLED", True)
    max_running: int = int(os.getenv("TWINTEAMS_MAX_RUNNING", "8"))
    max_per_owner: int = int(os.getenv("TWINTEAMS_MAX_PER_OWNER", "2"))
    routing_concurrency: int = int(os.getenv("TWINTEAMS_ROUTING_CONCURRENCY", "2"))
    memory_dream_interval_seconds: int = max(60, int(os.getenv("TWINTEAMS_MEMORY_DREAM_INTERVAL_SECONDS", "14400")))
    presence_ttl_seconds: int = int(os.getenv("TWINTEAMS_PRESENCE_TTL_SECONDS", "35"))
    response_delay_seconds: int = 10
    cors_origins: tuple[str, ...] = tuple(
        os.getenv("TWINTEAMS_CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(
            ","
        )
    )
    workspace_root: Path = Path(
        os.getenv("TWINTEAMS_WORKSPACE_ROOT", str(ROOT / "data" / "workspaces"))
    ).resolve()

    @property
    def local_identity_enabled(self) -> bool:
        return (
            self.development_mode
            and self.environment == "local"
            and local_database(self.database_url)
        )

    @property
    def production_identity_configured(self) -> bool:
        return bool(
            self.jwt_key
            and self.jwt_issuer
            and self.jwt_audience
            and self.jwt_algorithm in {"RS256", "ES256", "HS256"}
        )


settings = Settings()
