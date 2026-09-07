"""Validate YAML business configuration, bot parameters, template registry, and environment."""

from __future__ import annotations

import sys
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.core.config_loader import AppConfig
from app.core.settings import Settings


def main() -> int:
    print("=" * 60)
    print("WHATSAPP BOT PLATFORM - CONFIGURATION VALIDATOR")
    print("=" * 60)

    errors: list[str] = []

    # 1. Validate environment settings
    print("[1/4] Validating .env settings...")
    try:
        settings = Settings()
        print(f"  OK: app_env = {settings.app_env}")
        print(f"  OK: llm_provider = {settings.llm_provider} (model: {settings.llm_model})")
        print(f"  OK: database_url = {settings.database_url}")
        print(f"  OK: Meta configured = {settings.meta_is_configured()}")

        # Every script that stores a contact derives its encryption key from
        # this value, so an unset key passes Settings() in development and then
        # fails at the first write. Surface it here instead.
        secret_len = len(settings.app_secret_key.get_secret_value())
        if secret_len < 32:
            print(
                f"  ERROR: APP_SECRET_KEY is {secret_len} characters; "
                "at least 32 are required to encrypt contact data."
            )
            errors.append("Settings: APP_SECRET_KEY must be at least 32 characters.")
        else:
            print("  OK: APP_SECRET_KEY length is sufficient")
    except Exception as exc:
        print(f"  ERROR in Settings: {exc}")
        errors.append(f"Settings: {exc}")

    # 2. Validate YAML configs
    print("\n[2/4] Validating YAML business and bot configuration...")
    try:
        config = AppConfig.load(
            PROJECT_ROOT / "config" / "business.yaml",
            PROJECT_ROOT / "config" / "bot.yaml",
            PROJECT_ROOT / "config" / "templates.yaml",
        )
        print(f"  OK: Business ID = {config.business.business_id}")
        print(f"  OK: Business Name = {config.business.name}")
        print(f"  OK: Timezone = {config.business.timezone}")
        print(f"  OK: Enabled Intents = {len(config.bot.enabled_intents)}")
        print(f"  OK: Registered Templates = {len(config.templates.templates)}")
    except Exception as exc:
        print(f"  ERROR in YAML Configuration: {exc}")
        errors.append(f"YAML Configuration: {exc}")

    # 3. Validate Knowledge Base files
    print("\n[3/4] Validating Knowledge Base directory...")
    knowledge_dir = PROJECT_ROOT / "data" / "knowledge"
    if knowledge_dir.exists():
        files = list(knowledge_dir.glob("*.md")) + list(knowledge_dir.glob("*.txt"))
        print(f"  OK: Found {len(files)} knowledge files in {knowledge_dir.name}/")
    else:
        print(f"  WARNING: {knowledge_dir} does not exist.")

    # 4. Summary
    print("\n" + "=" * 60)
    if errors:
        print(f"CONFIGURATION FAILED WITH {len(errors)} ERROR(S):")
        for err in errors:
            print(f"  * {err}")
        return 1

    print("CONFIGURATION VALIDATION PASSED! ALL CHECKS GREEN.")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
