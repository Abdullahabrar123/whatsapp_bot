"""Validate a recipients CSV file and generate a pre-send compliance and eligibility report."""

from __future__ import annotations

import argparse
import csv
import sys
from datetime import UTC, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.core.config_loader import AppConfig
from app.domain.config_schema import TemplateDefinition
from app.domain.enums import ConsentPurpose, ConsentStatus
from app.domain.phone import normalise_phone
from app.domain.rules import SendContext, evaluate_send_eligibility


def validate_recipients_file(file_path: Path) -> int:
    print("=" * 60)
    print("PRE-SEND RECIPIENTS VALIDATION & ELIGIBILITY REPORT")
    print("=" * 60)

    config = AppConfig.load(
        PROJECT_ROOT / "config" / "business.yaml",
        PROJECT_ROOT / "config" / "bot.yaml",
        PROJECT_ROOT / "config" / "templates.yaml",
    )
    secret_key = "test_key_for_offline_validation_minimum_32_chars_ok"

    if not file_path.exists():
        print(f"Error: File {file_path} not found.")
        return 1

    with file_path.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    print(f"Analyzing {len(rows)} recipient rows from {file_path}...")

    now = datetime.now(UTC)
    eligible_count = 0
    ineligible_count = 0
    reasons: dict[str, int] = {}

    for i, row in enumerate(rows, start=1):
        raw_phone = (row.get("phone_e164") or row.get("phone") or "").strip()
        template_name = (row.get("template_name") or "appointment_reminder_v1").strip()
        matches = config.templates.by_name(template_name)
        template: TemplateDefinition | None = matches[0] if matches else None

        if not raw_phone:
            reasons["missing_phone"] = reasons.get("missing_phone", 0) + 1
            ineligible_count += 1
            continue

        try:
            phone = normalise_phone(raw_phone, key=secret_key)
        except Exception:
            reasons["invalid_phone_format"] = reasons.get("invalid_phone_format", 0) + 1
            ineligible_count += 1
            continue

        raw_consent = (row.get("consent_status") or "unknown").strip().lower()
        consent_status = (
            ConsentStatus(raw_consent)
            if raw_consent in [s.value for s in ConsentStatus]
            else ConsentStatus.UNKNOWN
        )

        purpose = (
            ConsentPurpose.MARKETING if consent_status is ConsentStatus.OPTED_IN else None
        )

        context = SendContext(
            consent_status=consent_status,
            consent_purpose=purpose,
            suppressed=consent_status is ConsentStatus.OPTED_OUT,
            template=template,
            now=now,
            last_inbound_at=None,
            already_sent_in_campaign=False,
            contact_locale=row.get("locale") or config.business.default_language,
        )

        decision = evaluate_send_eligibility(context, config.business, config.bot)
        if decision.allowed:
            eligible_count += 1
            print(f"Row {i:02d}: [ELIGIBLE] {phone.masked} -> Template: {template_name}")
        else:
            ineligible_count += 1
            code = decision.code or "unknown"
            reasons[code] = reasons.get(code, 0) + 1
            print(f"Row {i:02d}: [BLOCKED ] {phone.masked} -> Reason: {decision.reason} ({code})")

    print("\n" + "=" * 60)
    print("VALIDATION SUMMARY")
    print("=" * 60)
    print(f"Total Rows Checked : {len(rows)}")
    print(f"Eligible for Send  : {eligible_count}")
    print(f"Blocked / Rejected : {ineligible_count}")
    if reasons:
        print("\nBlocking Breakdown:")
        for r, c in reasons.items():
            print(f"  * {r}: {c}")
    print("=" * 60)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate recipients against compliance rules.")
    parser.add_argument(
        "--file",
        type=Path,
        default=PROJECT_ROOT / "data" / "recipients.example.csv",
        help="Path to CSV file",
    )
    args = parser.parse_args()
    return validate_recipients_file(args.file)


if __name__ == "__main__":
    sys.exit(main())
