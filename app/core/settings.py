"""Environment-driven settings.

Every value here is a *deployment* concern (secrets, endpoints, infrastructure).
Business behaviour lives in ``config/business.yaml`` and ``config/bot.yaml`` so a
new business can be onboarded by editing YAML and ``.env`` only -- never code.

Secrets are typed as ``SecretStr`` so an accidental ``repr()``/log call prints a
mask instead of the value.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.exceptions import ConfigurationError

AppEnv = Literal["development", "test", "staging", "production"]
LLMProvider = Literal["ollama", "deterministic"]

# Meta Graph API versions look like "v26.0".
_GRAPH_VERSION_RE = re.compile(r"^v\d+\.\d+$")
_PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """Validated application settings loaded from environment / .env."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        validate_default=True,
    )

    # ---------------------------------------------------------------- app
    app_env: AppEnv = "development"
    app_name: str = "wa-bot-platform"
    app_secret_key: SecretStr = Field(
        default=SecretStr(""),
        description="Used to derive encryption/HMAC keys for data at rest.",
    )
    log_level: str = "INFO"
    log_format: Literal["json", "console"] = "json"

    # Request hardening
    max_request_bytes: int = Field(default=1_048_576, ge=1024, le=16 * 1024 * 1024)
    cors_allow_origins: list[str] = Field(default_factory=list)

    # ---------------------------------------------------------------- storage
    database_url: str = "sqlite+aiosqlite:///./var/app.sqlite3"
    db_pool_size: int = Field(default=10, ge=1, le=100)
    db_max_overflow: int = Field(default=5, ge=0, le=100)
    db_echo: bool = False

    redis_url: str = "redis://localhost:6379/0"

    # ---------------------------------------------------------------- meta
    # These values are the entire per-business Meta identity. Swap them in .env
    # and restart to serve a different business -- no code changes.
    meta_graph_api_version: str = "v26.0"
    meta_graph_base_url: str = "https://graph.facebook.com"
    meta_waba_id: str = ""
    meta_phone_number_id: str = ""
    meta_access_token: SecretStr = SecretStr("")
    meta_app_secret: SecretStr = SecretStr("")
    meta_webhook_verify_token: SecretStr = SecretStr("")

    meta_request_timeout_seconds: float = Field(default=15.0, gt=0, le=120)
    meta_max_retries: int = Field(default=3, ge=0, le=10)
    meta_circuit_fail_threshold: int = Field(default=5, ge=1, le=100)
    meta_circuit_reset_seconds: float = Field(default=30.0, gt=0)

    # ---------------------------------------------------------------- llm
    llm_provider: LLMProvider = "ollama"
    ollama_base_url: str = "http://localhost:11434"
    llm_model: str = "qwen3:4b"
    llm_embedding_model: str = "nomic-embed-text"
    llm_api_key: SecretStr = SecretStr("")  # unused by Ollama; kept for parity
    llm_timeout_seconds: float = Field(default=30.0, gt=0, le=300)
    llm_max_retries: int = Field(default=2, ge=0, le=10)
    llm_temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    llm_num_ctx: int = Field(default=8192, ge=512, le=131072)
    # qwen3 emits <think> blocks by default; disabled for fast support replies.
    llm_enable_thinking: bool = False

    # ---------------------------------------------------------------- ops
    admin_api_key: SecretStr = SecretStr("")
    metrics_enabled: bool = True

    # ---------------------------------------------------------------- paths
    business_config_path: Path = _PROJECT_ROOT / "config" / "business.yaml"
    bot_config_path: Path = _PROJECT_ROOT / "config" / "bot.yaml"
    templates_config_path: Path = _PROJECT_ROOT / "config" / "templates.yaml"
    knowledge_dir: Path = _PROJECT_ROOT / "data" / "knowledge"
    index_dir: Path = _PROJECT_ROOT / "var" / "index"

    # ---------------------------------------------------------------- live tests
    live_test_enabled: bool = False
    live_test_allowlist: list[str] = Field(default_factory=list)

    # ================================================================ validators
    @field_validator("meta_graph_api_version")
    @classmethod
    def _check_graph_version(cls, value: str) -> str:
        if not _GRAPH_VERSION_RE.match(value):
            raise ValueError(f"META_GRAPH_API_VERSION must look like 'v26.0', got {value!r}")
        return value

    @field_validator("log_level")
    @classmethod
    def _check_log_level(cls, value: str) -> str:
        allowed = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}
        upper = value.upper()
        if upper not in allowed:
            raise ValueError(f"LOG_LEVEL must be one of {sorted(allowed)}, got {value!r}")
        return upper

    @field_validator("meta_graph_base_url", "ollama_base_url")
    @classmethod
    def _check_base_url(cls, value: str) -> str:
        if not value.startswith(("http://", "https://")):
            raise ValueError(f"Base URL must start with http:// or https://, got {value!r}")
        return value.rstrip("/")

    @field_validator("cors_allow_origins", "live_test_allowlist", mode="before")
    @classmethod
    def _split_csv(cls, value: object) -> object:
        """Accept both a JSON list and a comma-separated env string."""
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return []
            if stripped.startswith("["):
                return value
            return [item.strip() for item in stripped.split(",") if item.strip()]
        return value

    @model_validator(mode="after")
    def _check_deployment_requirements(self) -> Settings:
        """Refuse to start a non-development deployment with missing secrets.

        Development and test deliberately tolerate blanks so the suite and a
        first-run developer machine work without any Meta account.
        """
        if self.app_env in ("development", "test"):
            return self

        required: dict[str, str] = {
            "APP_SECRET_KEY": self.app_secret_key.get_secret_value(),
            "META_WABA_ID": self.meta_waba_id,
            "META_PHONE_NUMBER_ID": self.meta_phone_number_id,
            "META_ACCESS_TOKEN": self.meta_access_token.get_secret_value(),
            "META_APP_SECRET": self.meta_app_secret.get_secret_value(),
            "META_WEBHOOK_VERIFY_TOKEN": self.meta_webhook_verify_token.get_secret_value(),
            "ADMIN_API_KEY": self.admin_api_key.get_secret_value(),
        }
        missing = [name for name, value in required.items() if not value.strip()]
        if missing:
            raise ValueError(
                f"Missing required environment variables for app_env={self.app_env}: "
                f"{', '.join(sorted(missing))}. Copy .env.example to .env and fill these in."
            )

        if self.database_url.startswith("sqlite"):
            raise ValueError(
                "SQLite is not supported outside development/test. "
                "Set DATABASE_URL to a PostgreSQL DSN."
            )
        if len(self.app_secret_key.get_secret_value()) < 32:
            raise ValueError("APP_SECRET_KEY must be at least 32 characters.")
        if len(self.admin_api_key.get_secret_value()) < 24:
            raise ValueError("ADMIN_API_KEY must be at least 24 characters.")
        return self

    # ================================================================ helpers
    @property
    def graph_api_root(self) -> str:
        """Base URL for Graph API calls, e.g. https://graph.facebook.com/v26.0."""
        return f"{self.meta_graph_base_url}/{self.meta_graph_api_version}"

    @property
    def messages_endpoint(self) -> str:
        """Cloud API send endpoint bound to the configured phone number ID."""
        return f"{self.graph_api_root}/{self.meta_phone_number_id}/messages"

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    def meta_is_configured(self) -> bool:
        """True when enough Meta values exist to attempt a real API call."""
        return bool(
            self.meta_phone_number_id
            and self.meta_access_token.get_secret_value()
            and self.meta_app_secret.get_secret_value()
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings singleton.

    Raises:
        ConfigurationError: with an operator-readable message when validation fails.
    """
    try:
        return Settings()
    except Exception as exc:  # noqa: BLE001 - re-raised as a typed app error
        raise ConfigurationError(
            "Invalid environment configuration. Check your .env against .env.example.",
            details={"error": str(exc)},
        ) from exc


def reset_settings_cache() -> None:
    """Clear the settings cache (used by tests that swap environments)."""
    get_settings.cache_clear()
