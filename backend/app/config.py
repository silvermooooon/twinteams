"""Explicit local identity and fail-closed production configuration."""
from dataclasses import dataclass
import os
from urllib.parse import urlsplit
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def flag(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes"}


@dataclass(frozen=True)
class Settings:
    tenant_id: str = os.getenv("TWINTEAMS_TENANT_ID", "local")
    environment: str = os.getenv("TWINTEAMS_ENV", "local")
    development_mode: bool = flag("TWINTEAMS_DEVELOPMENT_MODE", False)
    database_url: str = os.getenv("DATABASE_URL", f"sqlite:///{ROOT / 'data' / 'twinteams.db'}")
    jwt_key: str = os.getenv("TWINTEAMS_JWT_KEY", "")
    jwt_algorithm: str = os.getenv("TWINTEAMS_JWT_ALGORITHM", "RS256")
    jwt_issuer: str = os.getenv("TWINTEAMS_JWT_ISSUER", "")
    jwt_audience: str = os.getenv("TWINTEAMS_JWT_AUDIENCE", "")
    presence_ttl_seconds: int = int(os.getenv("TWINTEAMS_PRESENCE_TTL_SECONDS", "35"))
    response_delay_seconds: int = 10
    cors_origins: tuple[str, ...] = tuple(os.getenv("TWINTEAMS_CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(","))
    workspace_root: Path = Path(os.getenv("TWINTEAMS_WORKSPACE_ROOT", str(ROOT / "data" / "workspaces"))).resolve()

    @property
    def local_identity_enabled(self) -> bool:
        return self.development_mode and self.environment == "local" and urlsplit(self.database_url).scheme in {"sqlite", "sqlite+pysqlite"}

    @property
    def production_identity_configured(self) -> bool:
        return bool(self.jwt_key and self.jwt_issuer and self.jwt_audience and self.jwt_algorithm in {"RS256", "ES256", "HS256"})


settings = Settings()
