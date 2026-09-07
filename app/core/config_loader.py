"""Loading and validating the YAML configuration files.

Design goal: an operator edits YAML, restarts, and either the app runs with the
new business or it refuses to start with a message that names the exact field.
There is no partial or "best effort" configuration state.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, TypeVar

import yaml
from pydantic import BaseModel, ValidationError

from app.core.exceptions import ConfigurationError
from app.domain.config_schema import (
    BotConfig,
    BusinessConfig,
    TemplateRegistry,
    scan_for_secrets,
)

ModelT = TypeVar("ModelT", bound=BaseModel)

#: Refuse to parse absurdly large YAML -- a cheap denial-of-service guard.
MAX_CONFIG_BYTES = 2 * 1024 * 1024


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ConfigurationError(
            f"Configuration file not found: {path}. "
            f"Copy the matching .example.yaml file and edit it.",
            details={"path": str(path)},
        )
    if path.stat().st_size > MAX_CONFIG_BYTES:
        raise ConfigurationError(
            f"Configuration file {path} exceeds {MAX_CONFIG_BYTES} bytes.",
            details={"path": str(path)},
        )
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigurationError(
            f"Could not read configuration file {path}: {exc}", details={"path": str(path)}
        ) from exc

    try:
        # safe_load never constructs arbitrary Python objects.
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise ConfigurationError(
            f"Invalid YAML syntax in {path}: {exc}", details={"path": str(path)}
        ) from exc

    if data is None:
        raise ConfigurationError(
            f"Configuration file {path} is empty.", details={"path": str(path)}
        )
    if not isinstance(data, dict):
        raise ConfigurationError(
            f"Configuration file {path} must contain a YAML mapping at the top level.",
            details={"path": str(path)},
        )

    findings = scan_for_secrets(data)
    if findings:
        raise ConfigurationError(
            f"Secret-like values found in {path}. Move them to environment variables.",
            details={"path": str(path), "findings": findings},
        )
    return data


def _format_validation_error(path: Path, exc: ValidationError) -> str:
    lines = [f"Configuration in {path} is invalid:"]
    for error in exc.errors():
        location = ".".join(str(part) for part in error["loc"]) or "(root)"
        lines.append(f"  - {location}: {error['msg']}")
    return "\n".join(lines)


def load_model(path: Path, model: type[ModelT]) -> ModelT:
    """Load a YAML file into a validated Pydantic model or fail loudly."""
    data = _read_yaml(path)
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        raise ConfigurationError(
            _format_validation_error(path, exc),
            details={"path": str(path), "errors": exc.errors()},
        ) from exc


def load_business_config(path: Path) -> BusinessConfig:
    return load_model(path, BusinessConfig)


def load_bot_config(path: Path) -> BotConfig:
    return load_model(path, BotConfig)


def load_template_registry(path: Path) -> TemplateRegistry:
    """Load the template registry; an absent file yields an empty registry.

    An empty registry is valid (a service-only deployment sends no templates)
    but every template send then fails closed with a clear error.
    """
    if not path.exists():
        return TemplateRegistry(templates=[])
    return load_model(path, TemplateRegistry)


class AppConfig:
    """Container bundling the three configuration documents.

    Injected as a single dependency so services never read files themselves,
    which keeps them trivially testable with in-memory fixtures.
    """

    def __init__(
        self,
        business: BusinessConfig,
        bot: BotConfig,
        templates: TemplateRegistry,
    ) -> None:
        self.business = business
        self.bot = bot
        self.templates = templates
        self._cross_validate()

    def _cross_validate(self) -> None:
        """Checks that only make sense once all three documents are present."""
        problems: list[str] = []

        unsupported = set(self.bot.enabled_languages) - set(self.business.supported_languages)
        if unsupported:
            problems.append(
                f"bot.yaml enabled_languages {sorted(unsupported)} are not listed in "
                f"business.yaml supported_languages {self.business.supported_languages}."
            )
        if self.business.default_language not in self.bot.enabled_languages:
            problems.append(
                f"business.yaml default_language "
                f"{self.business.default_language!r} is not enabled in bot.yaml."
            )
        for template in self.templates.templates:
            if template.language not in self.business.supported_languages:
                problems.append(
                    f"templates.yaml template {template.name!r} uses language "
                    f"{template.language!r} which business.yaml does not support."
                )
        if problems:
            raise ConfigurationError(
                "Configuration files are individually valid but inconsistent:\n  - "
                + "\n  - ".join(problems),
                details={"problems": problems},
            )

    @classmethod
    def load(
        cls,
        business_path: Path,
        bot_path: Path,
        templates_path: Path,
    ) -> AppConfig:
        return cls(
            business=load_business_config(business_path),
            bot=load_bot_config(bot_path),
            templates=load_template_registry(templates_path),
        )
