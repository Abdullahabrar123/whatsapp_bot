"""Import and validate contacts from CSV without sending any messages.

Strict compliance controls:
* Normalizes phone numbers to E.164.
* Rejects invalid, duplicate, or future-dated consent records.
* Never automatically marks imported contacts as opted in without explicit evidence.
* Sensitive fields are encrypted, and phone hashes are stored for fast lookups.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import sys
from datetime import UTC, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.core.config_loader import AppConfig
from app.core.db import create_engine, create_session_factory, session_scope
from app.core.security import FieldCipher
from app.core.settings import get_settings
from app.domain.enums import ConsentPurpose, ConsentStatus
from app.domain.phone import normalise_phone
from app.repositories.consent import ConsentRepository
from app.repositories.contacts import ContactRepository, SuppressionRepository


async def import_contacts_from_csv(file_path: Path) -> int:
    settings = get_settings()
    config = AppConfig.load(
        settings.business_config_path,
        settings.bot_config_path,
        settings.templates_config_path,
    )
    business_id = config.business.business_id

    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    cipher = FieldCipher(settings.app_secret_key.get_secret_value())

    print(f"Reading contacts from {file_path} for business {business_id}...")

    if not file_path.exists():
        print(f"File not found: {file_path}")
        return 1

    accepted_count = 0
    rejected_count = 0
    rejections: dict[str, int] = {}
    seen_hashes: set[str] = set()

    with file_path.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    print(f"Found {len(rows)} rows to process.")

    async with session_scope(session_factory) as session:
        contact_repo = ContactRepository(session)
        supp_repo = SuppressionRepository(session)
        consent_repo = ConsentRepository(session)

        now = datetime.now(UTC)

        for i, row in enumerate(rows, start=1):
            raw_phone = (row.get("phone_e164") or row.get("phone") or "").strip()
            if not raw_phone:
                rejections["missing_phone"] = rejections.get("missing_phone", 0) + 1
                rejected_count += 1
                continue

            try:
                phone = normalise_phone(
                    raw_phone, key=settings.app_secret_key.get_secret_value()
                )
            except Exception:
                rejections["invalid_phone_format"] = rejections.get("invalid_phone_format", 0) + 1
                rejected_count += 1
                continue

            if phone.hash in seen_hashes:
                rejections["duplicate_in_file"] = rejections.get("duplicate_in_file", 0) + 1
                rejected_count += 1
                continue
            seen_hashes.add(phone.hash)

            # Check suppression
            if await supp_repo.is_suppressed(business_id, phone.hash):
                rejections["already_suppressed"] = rejections.get("already_suppressed", 0) + 1
                rejected_count += 1
                continue

            # Parse consent
            raw_consent_status = (row.get("consent_status") or "unknown").strip().lower()
            consent_status = (
                ConsentStatus(raw_consent_status)
                if raw_consent_status in [s.value for s in ConsentStatus]
                else ConsentStatus.UNKNOWN
            )

            raw_ts = (row.get("consent_timestamp") or "").strip()
            consent_time = None
            if raw_ts:
                try:
                    consent_time = datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
                    if consent_time > now:
                        rejections["future_consent_timestamp"] = (
                            rejections.get("future_consent_timestamp", 0) + 1
                        )
                        rejected_count += 1
                        continue
                except Exception:
                    rejections["invalid_consent_timestamp"] = (
                        rejections.get("invalid_consent_timestamp", 0) + 1
                    )
                    rejected_count += 1
                    continue

            # Process tags
            raw_tags = (row.get("tags") or "").strip()
            tags = [t.strip() for t in raw_tags.split(";") if t.strip()]

            # Create or update contact
            contact, created = await contact_repo.get_or_create(
                business_id=business_id,
                phone_hash=phone.hash,
                phone_encrypted=cipher.encrypt(phone.e164) or "",
                phone_masked=phone.masked,
                external_id=(row.get("external_id") or "").strip() or None,
                first_name=row.get("first_name"),
                last_name=row.get("last_name"),
                locale=row.get("locale") or config.business.default_language,
                timezone_name=(row.get("timezone") or "").strip() or None,
                tags=tags,
            )

            if not created and tags:
                # Re-imports may add tags; merge rather than replace existing ones.
                existing_tags = list(contact.tags.get("values", []))
                merged = existing_tags + [t for t in tags if t not in existing_tags]
                if merged != existing_tags:
                    contact.tags = {"values": merged}

            if consent_status != ConsentStatus.UNKNOWN:
                await contact_repo.set_consent_status(contact, consent_status)
                if consent_status is ConsentStatus.OPTED_IN:
                    await consent_repo.record(
                        business_id=business_id,
                        contact_id=contact.id,
                        status=ConsentStatus.OPTED_IN,
                        purpose=ConsentPurpose.MARKETING,
                        source=row.get("consent_source") or "csv_import",
                        occurred_at=consent_time or now,
                        evidence=f"Imported from CSV row {i}",
                    )
                elif consent_status is ConsentStatus.OPTED_OUT:
                    await supp_repo.create(
                        business_id=business_id,
                        phone_hash=phone.hash,
                        phone_masked=phone.masked,
                        reason="csv_import_opted_out",
                    )

            accepted_count += 1

    await engine.dispose()

    print("\n" + "=" * 60)
    print("IMPORT CONTACTS SUMMARY REPORT")
    print("=" * 60)
    print(f"Total Rows Processed : {len(rows)}")
    print(f"Accepted & Stored    : {accepted_count}")
    print(f"Rejected Rows        : {rejected_count}")
    if rejections:
        print("\nRejection Breakdown:")
        for reason, cnt in rejections.items():
            print(f"  * {reason}: {cnt}")
    print("=" * 60)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Import contacts from CSV.")
    parser.add_argument(
        "--file",
        type=Path,
        default=PROJECT_ROOT / "data" / "recipients.example.csv",
        help="Path to CSV file",
    )
    args = parser.parse_args()
    return asyncio.run(import_contacts_from_csv(args.file))


if __name__ == "__main__":
    sys.exit(main())
