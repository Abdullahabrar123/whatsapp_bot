"""Unit tests for configuration schemas and validation."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.config_loader import AppConfig
from app.core.settings import Settings

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_app_config_load_valid():
    config = AppConfig.load(
        PROJECT_ROOT / "config" / "business.yaml",
        PROJECT_ROOT / "config" / "bot.yaml",
        PROJECT_ROOT / "config" / "templates.yaml",
    )
    assert config.business.business_id == "brightsmile_dental"
    assert config.business.name == "BrightSmile Dental Care"
    assert config.bot.thresholds.reply > 0.0
    assert len(config.templates.by_name("appointment_reminder_v1")) > 0


def test_settings_validation_missing_in_production():
    with pytest.raises(ValidationError):
        Settings(
            app_env="production",
            app_secret_key="",
            meta_waba_id="",
        )


def test_settings_defaults_in_development():
    settings = Settings(app_env="development")
    assert settings.meta_graph_api_version == "v26.0"
    assert settings.llm_provider == "ollama"
    assert settings.llm_model == "qwen3:4b"
