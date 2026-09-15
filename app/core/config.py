"""Application settings.

Everything is read from the environment (12-factor). There are no hardcoded
secrets or hosts in the codebase; `.env.example` documents every knob.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import computed_field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# `test` is used by the test-server compose configuration.  Keep it distinct
# from local development while preserving the production-only auth safeguard.
Environment = Literal["local", "dev", "test", "staging", "production"]
AuthMode = Literal["dev", "keycloak"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- General -----------------------------------------------------------
    env: Environment = "local"
    debug: bool = False
    app_name: str = "CRM IT School"
    api_prefix: str = "/api/v1"
    log_level: str = "INFO"

    # --- Database ----------------------------------------------------------
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_user: str = "crm"
    postgres_password: str = "crm"
    postgres_db: str = "crm"
    db_echo: bool = False
    db_pool_size: int = 10
    db_max_overflow: int = 20

    # --- Auth --------------------------------------------------------------
    auth_mode: AuthMode = "dev"
    keycloak_issuer: str | None = None
    keycloak_audience: str | None = None
    keycloak_jwks_url: str | None = None

    # --- Imports -----------------------------------------------------------
    import_max_file_size: int = 20 * 1024 * 1024  # 20 MiB, per SPEC §6
    import_max_rows: int = 100_000
    # Minimal similarity (0..100) for suggesting an existing university as a
    # match for a slightly different spelling coming from Excel.
    import_fuzzy_threshold: int = 88

    # --- Attachments -------------------------------------------------------
    # Stage attachments (FR-04). Stored in the database, so the cap also bounds
    # how large a single row can get.
    attachment_max_file_size: int = 25 * 1024 * 1024  # 25 MiB

    # --- Pagination --------------------------------------------------------
    page_size_default: int = 50
    page_size_max: int = 200

    @computed_field  # type: ignore[prop-decorator]
    @property
    def database_url(self) -> str:
        """Async (asyncpg) DSN used by the application."""
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def sync_database_url(self) -> str:
        """Sync DSN — Alembic's `run_migrations_offline` and tooling use it."""
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @model_validator(mode="after")
    def _forbid_dev_auth_in_production(self) -> Settings:
        """SPEC §8: the debug-header auth stub must be impossible in production.

        Failing here means the process refuses to start, which is exactly what
        we want: a misconfigured deployment must not silently accept
        `X-Debug-User`.
        """
        if self.env == "production" and self.auth_mode != "keycloak":
            raise ValueError(
                "AUTH_MODE=dev is forbidden when ENV=production; set AUTH_MODE=keycloak"
            )
        if self.auth_mode == "keycloak" and not self.keycloak_issuer:
            raise ValueError("AUTH_MODE=keycloak requires KEYCLOAK_ISSUER to be set")
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings: Settings = get_settings()
