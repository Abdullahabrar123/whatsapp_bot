"""Outbound campaign management and compliance-gated execution.

Guarantees provided:
* Every recipient passes pre-send compliance evaluation (opt-in, suppression,
  quiet hours, approved template validation).
* Sends are paced according to bot configuration to avoid burst throttling.
* Campaigns auto-pause when failure rate exceeds configured threshold.
* Staged recipients cannot be sent more than once per campaign run.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.core.exceptions import ConfigurationError, ValidationError, WhatsAppAPIError
from app.core.logging import get_correlation_id, get_logger
from app.core.security import FieldCipher
from app.domain.config_schema import BotConfig, BusinessConfig, TemplateDefinition
from app.domain.enums import (
    CampaignRecipientStatus,
    CampaignStatus,
    DeliveryStatus,
    Direction,
    MessageType,
)
from app.domain.models import Campaign
from app.domain.phone import normalise_phone
from app.integrations.whatsapp_client import WhatsAppClientProtocol
from app.repositories.campaigns import CampaignRecipientRepository, CampaignRepository
from app.repositories.contacts import ContactRepository
from app.repositories.conversations import ConversationRepository, MessageRepository
from app.services.consent_service import ConsentService

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class EligibilityReport:
    """Pre-send staging and validation summary."""

    total_recipients: int
    eligible: int
    rejected: int
    rejections: dict[str, int]


@dataclass(frozen=True, slots=True)
class CampaignExecutionSummary:
    """Outcome of a campaign dispatch run."""

    campaign_id: int
    campaign_name: str
    status: CampaignStatus
    total: int
    sent: int
    failed: int
    auto_paused: bool = False
    pause_reason: str | None = None


class CampaignService:
    """Coordinates outbound campaign lifecycle and compliance."""

    def __init__(
        self,
        *,
        business: BusinessConfig,
        bot: BotConfig,
        templates: dict[str, TemplateDefinition],
        cipher: FieldCipher,
        secret_key: str,
        campaigns: CampaignRepository,
        campaign_recipients: CampaignRecipientRepository,
        contacts: ContactRepository,
        conversations: ConversationRepository,
        messages: MessageRepository,
        consent_service: ConsentService,
        whatsapp_client: WhatsAppClientProtocol,
    ) -> None:
        self._business = business
        self._bot = bot
        self._templates = templates
        self._cipher = cipher
        self._secret = secret_key
        self._campaigns = campaigns
        self._recipients = campaign_recipients
        self._contacts = contacts
        self._conversations = conversations
        self._messages = messages
        self._consent = consent_service
        self._whatsapp = whatsapp_client

    def _get_template(self, name: str) -> TemplateDefinition:
        template: TemplateDefinition | None = None
        if hasattr(self._templates, "by_name"):
            matches = self._templates.by_name(name)
            template = matches[0] if matches else None
        elif isinstance(self._templates, dict):
            template = self._templates.get(name)

        if template is None:
            raise ConfigurationError(
                f"Template {name!r} is not registered in templates.yaml.",
                details={"template_name": name},
            )
        if not template.is_sendable():
            raise ValidationError(
                f"Template {name!r} is not approved for sending.",
                details={"template_name": name, "status": template.status.value},
            )
        return template

    async def create_campaign(
        self,
        *,
        name: str,
        template_name: str,
        template_language: str | None = None,
    ) -> Campaign:
        """Create a new campaign in DRAFT status."""
        template = self._get_template(template_name)
        language = template_language or template.language or self._business.default_language

        existing = await self._campaigns.get_by_name(self._business.business_id, name)
        if existing is not None:
            raise ValidationError(
                f"Campaign named {name!r} already exists.",
                details={"campaign_name": name},
            )

        return await self._campaigns.create(
            business_id=self._business.business_id,
            name=name,
            template_name=template.name,
            template_language=language,
            correlation_id=get_correlation_id(),
        )

    async def stage_recipients(
        self,
        campaign: Campaign,
        recipient_rows: list[dict[str, Any]],
        *,
        now: datetime | None = None,
    ) -> EligibilityReport:
        """Evaluate pre-send eligibility for imported rows and stage them in DB."""
        template = self._get_template(campaign.template_name)
        moment = now or datetime.now(UTC)

        eligible_count = 0
        rejected_count = 0
        rejection_stats: dict[str, int] = {}

        for row in recipient_rows:
            raw_phone = str(row.get("phone_e164") or row.get("phone") or "")
            if not raw_phone:
                rejection_stats["missing_phone"] = rejection_stats.get("missing_phone", 0) + 1
                rejected_count += 1
                continue

            try:
                phone = normalise_phone(raw_phone, key=self._secret)
            except Exception:  # noqa: BLE001
                rejection_stats["invalid_phone"] = rejection_stats.get("invalid_phone", 0) + 1
                rejected_count += 1
                continue

            # Ensure contact exists
            first_name = row.get("first_name")
            contact, _ = await self._contacts.get_or_create(
                business_id=self._business.business_id,
                phone_hash=phone.hash,
                phone_encrypted=self._cipher.encrypt(phone.e164) or "",
                phone_masked=phone.masked,
                first_name=first_name,
                locale=str(row.get("locale") or self._business.default_language),
            )

            # Check if contact already staged for this campaign
            if await self._recipients.exists(campaign.id, contact.id):
                rejection_stats["already_in_campaign"] = (
                    rejection_stats.get("already_in_campaign", 0) + 1
                )
                rejected_count += 1
                continue

            # Full compliance gate
            decision = await self._consent.check_eligibility(
                contact,
                template=template,
                now=moment,
                already_sent_in_campaign=False,
            )

            status = (
                CampaignRecipientStatus.ELIGIBLE
                if decision.allowed
                else CampaignRecipientStatus.REJECTED
            )

            if decision.allowed:
                eligible_count += 1
            else:
                rejected_count += 1
                code = decision.code or "unknown"
                rejection_stats[code] = rejection_stats.get(code, 0) + 1

            template_vars = row.get("template_variables") or {}
            if isinstance(row.get("template_variables_json"), str):
                import json

                try:
                    template_vars = json.loads(str(row["template_variables_json"]))
                except Exception:  # noqa: BLE001
                    template_vars = {}

            await self._recipients.create(
                campaign_id=campaign.id,
                contact_id=contact.id,
                status=status,
                template_variables=template_vars,
                rejection_reason=decision.reason if not decision.allowed else None,
            )

        await self._campaigns.refresh_counters(campaign)
        return EligibilityReport(
            total_recipients=len(recipient_rows),
            eligible=eligible_count,
            rejected=rejected_count,
            rejections=rejection_stats,
        )

    async def execute_campaign(
        self,
        campaign: Campaign,
        *,
        dry_run: bool = False,
    ) -> CampaignExecutionSummary:
        """Run or dry-run an outbound campaign."""
        if dry_run:
            await self._campaigns.refresh_counters(campaign)
            return CampaignExecutionSummary(
                campaign_id=campaign.id,
                campaign_name=campaign.name,
                status=campaign.status,
                total=campaign.total,
                sent=campaign.sent,
                failed=campaign.failed,
            )

        if campaign.status in (
            CampaignStatus.COMPLETED,
            CampaignStatus.CANCELLED,
            CampaignStatus.RUNNING,
        ):
            raise ValidationError(f"Campaign is already in status {campaign.status.value}")

        await self._campaigns.set_status(campaign, CampaignStatus.RUNNING)
        # Validation guard: raises ConfigurationError if the template is unregistered.
        self._get_template(campaign.template_name)

        batch_size = max(1, self._bot.campaign.batch_size)
        delay = max(0.0, self._bot.campaign.delay_between_sends_seconds)
        threshold_pct = float(self._bot.campaign.max_failure_rate * 100.0)

        pending = await self._recipients.list_by_status(
            campaign.id, CampaignRecipientStatus.ELIGIBLE, limit=batch_size
        )

        sent_count = 0
        failed_count = 0
        auto_paused = False
        pause_reason = None

        while pending:
            for recipient in pending:
                contact = await self._contacts.get_by_id(
                    campaign.business_id, recipient.contact_id
                )
                if contact is None:
                    continue

                # Final pre-send check (suppression could have changed mid-run)
                if await self._consent.is_suppressed(contact):
                    await self._recipients.set_status(
                        recipient, CampaignRecipientStatus.REJECTED
                    )
                    recipient.rejection_reason = "suppressed_mid_campaign"
                    continue

                phone = self._cipher.decrypt(contact.phone_encrypted)
                if not phone:
                    await self._recipients.mark_failed(
                        recipient,
                        error_code=0,
                        error_detail="phone_decryption_failed",
                    )
                    failed_count += 1
                    continue

                # Hydrate template variables
                body_vars: list[str] = []
                if isinstance(recipient.template_variables, dict):
                    body_vars = [
                        str(v)
                        for k, v in sorted(recipient.template_variables.items())
                    ]

                try:
                    result = await self._whatsapp.send_template(
                        to=phone.lstrip("+"),
                        template_name=campaign.template_name,
                        language=campaign.template_language,
                        body_variables=body_vars or None,
                    )
                    await self._recipients.mark_sent(
                        recipient, meta_message_id=result.meta_message_id
                    )

                    conversation = await self._conversations.get_or_create(
                        business_id=campaign.business_id,
                        contact_id=contact.id,
                        expiry_hours=self._bot.conversation_expiry_hours,
                        correlation_id=get_correlation_id(),
                    )

                    await self._messages.add_message(
                        business_id=campaign.business_id,
                        conversation_id=conversation.id,
                        contact_id=contact.id,
                        direction=Direction.OUTBOUND,
                        message_type=MessageType.TEMPLATE,
                        meta_message_id=result.meta_message_id,
                        template_name=campaign.template_name,
                        template_language=campaign.template_language,
                        campaign_id=campaign.id,
                        delivery_status=DeliveryStatus.SENT,
                        correlation_id=get_correlation_id(),
                    )
                    await self._contacts.touch_outbound(contact)
                    sent_count += 1

                except WhatsAppAPIError as exc:
                    await self._recipients.mark_failed(
                        recipient,
                        error_code=exc.meta_code,
                        error_detail=exc.message,
                    )
                    failed_count += 1
                    logger.error(
                        "campaign_send_failed",
                        campaign_id=campaign.id,
                        contact_id=contact.id,
                        error=exc.message,
                    )

                # Check failure threshold
                total_attempted = sent_count + failed_count
                if total_attempted >= 10:
                    current_fail_rate = (failed_count / total_attempted) * 100.0
                    if current_fail_rate >= threshold_pct:
                        auto_paused = True
                        pause_reason = (
                            "Failure threshold exceeded: "
                            f"{current_fail_rate:.1f}% >= {threshold_pct}%"
                        )
                        break

                if delay > 0:
                    await asyncio.sleep(delay)

            if auto_paused:
                await self._campaigns.set_status(
                    campaign, CampaignStatus.PAUSED, reason=pause_reason
                )
                logger.warning(
                    "campaign_auto_paused",
                    campaign_id=campaign.id,
                    reason=pause_reason,
                )
                break

            # Fetch next batch
            pending = await self._recipients.list_by_status(
                campaign.id, CampaignRecipientStatus.ELIGIBLE, limit=batch_size
            )

        if not auto_paused:
            # Check if any remaining eligible recipients
            remaining = await self._recipients.list_by_status(
                campaign.id, CampaignRecipientStatus.ELIGIBLE, limit=1
            )
            final_status = (
                CampaignStatus.PAUSED if remaining else CampaignStatus.COMPLETED
            )
            await self._campaigns.set_status(campaign, final_status)

        await self._campaigns.refresh_counters(campaign)
        return CampaignExecutionSummary(
            campaign_id=campaign.id,
            campaign_name=campaign.name,
            status=campaign.status,
            total=campaign.total,
            sent=campaign.sent,
            failed=campaign.failed,
            auto_paused=auto_paused,
            pause_reason=pause_reason,
        )
