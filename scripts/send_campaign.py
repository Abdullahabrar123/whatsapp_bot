"""CLI tool for creating, dry-running, and dispatching outbound template campaigns."""

from __future__ import annotations

import argparse
import asyncio
import csv
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.core.config_loader import AppConfig
from app.core.db import create_engine, create_session_factory, session_scope
from app.core.security import FieldCipher
from app.core.settings import get_settings
from app.integrations.whatsapp_client import WhatsAppClient
from app.repositories.campaigns import CampaignRecipientRepository, CampaignRepository
from app.repositories.consent import ConsentRepository
from app.repositories.contacts import ContactRepository, SuppressionRepository
from app.repositories.conversations import ConversationRepository, MessageRepository
from app.services.campaign_service import CampaignService
from app.services.consent_service import ConsentService


async def run_campaign_cli(
    name: str,
    template_name: str,
    csv_file: Path,
    *,
    dry_run: bool,
    confirm: bool,
) -> int:
    print("=" * 60)
    print(f"OUTBOUND CAMPAIGN RUNNER: {name}")
    print(f"Template: {template_name} | Dry Run: {dry_run} | Confirm: {confirm}")
    print("=" * 60)

    if not dry_run and not confirm:
        print("ERROR: Live sending requires --confirm flag. Use --dry-run for simulation.")
        return 1

    settings = get_settings()
    config = AppConfig.load(
        settings.business_config_path,
        settings.bot_config_path,
        settings.templates_config_path,
    )

    if not csv_file.exists():
        print(f"Error: Recipients file {csv_file} does not exist.")
        return 1

    with csv_file.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        recipient_rows = list(reader)

    print(f"Loaded {len(recipient_rows)} recipients from {csv_file}")

    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    cipher = FieldCipher(settings.app_secret_key.get_secret_value())
    whatsapp_client = WhatsAppClient(settings)

    async with session_scope(session_factory) as session:
        campaigns = CampaignRepository(session)
        recipients = CampaignRecipientRepository(session)
        contacts = ContactRepository(session)
        conversations = ConversationRepository(session)
        messages = MessageRepository(session)
        suppressions = SuppressionRepository(session)
        consents = ConsentRepository(session)

        consent_svc = ConsentService(
            business=config.business,
            bot=config.bot,
            contacts=contacts,
            consents=consents,
            suppressions=suppressions,
        )

        campaign_svc = CampaignService(
            business=config.business,
            bot=config.bot,
            templates=config.templates,
            cipher=cipher,
            secret_key=settings.app_secret_key.get_secret_value(),
            campaigns=campaigns,
            campaign_recipients=recipients,
            contacts=contacts,
            conversations=conversations,
            messages=messages,
            consent_service=consent_svc,
            whatsapp_client=whatsapp_client,
        )

        # 1. Create or load campaign
        existing = await campaigns.get_by_name(config.business.business_id, name)
        if existing is None:
            campaign = await campaign_svc.create_campaign(
                name=name, template_name=template_name
            )
            print(f"Created new campaign: ID={campaign.id}")
            # 2. Stage recipients
            print("Staging recipients and running pre-send compliance evaluation...")
            report = await campaign_svc.stage_recipients(campaign, recipient_rows)
            print(f"Staging Complete: {report.eligible} eligible, {report.rejected} rejected.")
        else:
            campaign = existing
            print(f"Loaded existing campaign: ID={campaign.id}")

        # 3. Execute or Dry-run
        print(f"Executing campaign (dry_run={dry_run})...")
        summary = await campaign_svc.execute_campaign(campaign, dry_run=dry_run)

        print("\n" + "=" * 60)
        print("CAMPAIGN OUTCOME")
        print("=" * 60)
        print(f"Campaign ID   : {summary.campaign_id}")
        print(f"Campaign Name : {summary.campaign_name}")
        print(f"Status        : {summary.status.value}")
        print(f"Total Staged  : {summary.total}")
        print(f"Total Sent    : {summary.sent}")
        print(f"Total Failed  : {summary.failed}")
        if summary.auto_paused:
            print(f"AUTO-PAUSED   : True (Reason: {summary.pause_reason})")
        print("=" * 60)

    await whatsapp_client.aclose()
    await engine.dispose()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Create and execute an outbound campaign.")
    parser.add_argument(
        "--name", type=str, default="june_appointment_reminders", help="Campaign name"
    )
    parser.add_argument(
        "--template", type=str, default="appointment_reminder_v1", help="Template name"
    )
    parser.add_argument(
        "--file",
        type=Path,
        default=PROJECT_ROOT / "data" / "recipients.example.csv",
        help="Recipients CSV file",
    )
    parser.add_argument("--dry-run", action="store_true", help="Simulate without sending")
    parser.add_argument("--confirm", action="store_true", help="Confirm real outbound sending")
    args = parser.parse_args()

    return asyncio.run(
        run_campaign_cli(
            name=args.name,
            template_name=args.template,
            csv_file=args.file,
            dry_run=args.dry_run,
            confirm=args.confirm,
        )
    )


if __name__ == "__main__":
    sys.exit(main())
