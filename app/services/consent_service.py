"""Consent lifecycle: opt-in, opt-out, suppression and eligibility.

Guarantees this service provides:

* An opt-out takes effect before anything else is processed, and is written in
  the same transaction as the suppression entry -- there is no window in which
  someone is opted out but still sendable.
* Consent is never inferred. It is created only by an explicit event with a
  recorded source, timestamp and purpose.
* Re-subscription requires a fresh, explicit opt-in; it is never automatic.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.core.exceptions import ValidationError
from app.core.logging import get_correlation_id, get_logger
from app.domain.config_schema import BotConfig, BusinessConfig, TemplateDefinition
from app.domain.enums import ConsentPurpose, ConsentStatus
from app.domain.models import Contact
from app.domain.rules import EligibilityDecision, SendContext, evaluate_send_eligibility
from app.repositories.consent import ConsentRepository
from app.repositories.contacts import ContactRepository, SuppressionRepository

logger = get_logger(__name__)

#: Reason recorded when a customer opts out through a WhatsApp message.
REASON_CUSTOMER_OPT_OUT = "customer_opt_out"
REASON_OPERATOR = "operator_suppression"
REASON_HARD_BOUNCE = "delivery_failure"


class ConsentService:
    """All consent state transitions live here."""

    def __init__(
        self,
        *,
        business: BusinessConfig,
        bot: BotConfig,
        contacts: ContactRepository,
        consents: ConsentRepository,
        suppressions: SuppressionRepository,
    ) -> None:
        self._business = business
        self._bot = bot
        self._contacts = contacts
        self._consents = consents
        self._suppressions = suppressions

    # ------------------------------------------------------------------ opt-out
    async def opt_out(
        self,
        contact: Contact,
        *,
        source: str = "whatsapp_message",
        evidence: str | None = None,
        reason: str = REASON_CUSTOMER_OPT_OUT,
        occurred_at: datetime | None = None,
    ) -> None:
        """Record an opt-out and suppress the contact permanently.

        Both writes happen in the caller's transaction, so a crash cannot leave
        the contact opted out but unsuppressed (or the reverse).
        """
        moment = occurred_at or datetime.now(UTC)

        contact.consent_status = ConsentStatus.OPTED_OUT
        await self._consents.record(
            business_id=contact.business_id,
            contact_id=contact.id,
            status=ConsentStatus.OPTED_OUT,
            purpose=ConsentPurpose.MARKETING,
            source=source,
            occurred_at=moment,
            evidence=evidence,
            correlation_id=get_correlation_id(),
        )
        await self._suppressions.create(
            business_id=contact.business_id,
            phone_hash=contact.phone_hash,
            phone_masked=contact.phone_masked,
            reason=reason,
            detail=evidence,
        )
        logger.info(
            "consent_opt_out_recorded",
            contact_id=contact.id,
            source=source,
            reason=reason,
        )

    # ------------------------------------------------------------------- opt-in
    async def opt_in(
        self,
        contact: Contact,
        *,
        source: str,
        purpose: ConsentPurpose = ConsentPurpose.MARKETING,
        evidence: str | None = None,
        occurred_at: datetime | None = None,
    ) -> None:
        """Record an explicit opt-in, lifting any prior suppression.

        Suppression is only lifted here, on a deliberate opt-in event, and the
        act is recorded as consent evidence.
        """
        moment = occurred_at or datetime.now(UTC)
        if moment > datetime.now(UTC):
            raise ValidationError("Consent timestamp cannot be in the future.")

        contact.consent_status = ConsentStatus.OPTED_IN
        await self._consents.record(
            business_id=contact.business_id,
            contact_id=contact.id,
            status=ConsentStatus.OPTED_IN,
            purpose=purpose,
            source=source,
            occurred_at=moment,
            evidence=evidence,
            correlation_id=get_correlation_id(),
        )
        await self._suppressions.remove(contact.business_id, contact.phone_hash)
        logger.info(
            "consent_opt_in_recorded",
            contact_id=contact.id,
            source=source,
            purpose=purpose.value,
        )

    # -------------------------------------------------------------- suppression
    async def suppress(
        self, contact: Contact, *, reason: str, detail: str | None = None
    ) -> None:
        """Suppress without a customer-initiated opt-out (e.g. hard bounce)."""
        contact.consent_status = ConsentStatus.SUPPRESSED
        await self._suppressions.create(
            business_id=contact.business_id,
            phone_hash=contact.phone_hash,
            phone_masked=contact.phone_masked,
            reason=reason,
            detail=detail,
        )
        logger.info("contact_suppressed", contact_id=contact.id, reason=reason)

    async def is_suppressed(self, contact: Contact) -> bool:
        return await self._suppressions.is_suppressed(
            contact.business_id, contact.phone_hash
        )

    # -------------------------------------------------------------- eligibility
    async def check_eligibility(
        self,
        contact: Contact,
        *,
        template: TemplateDefinition | None,
        now: datetime,
        already_sent_in_campaign: bool = False,
    ) -> EligibilityDecision:
        """Full pre-send compliance gate for one business-initiated message."""
        suppressed = await self.is_suppressed(contact)

        purpose: ConsentPurpose | None = None
        if template is not None:
            latest = await self._consents.latest_for_purpose(
                contact.id, ConsentPurpose.MARKETING
            )
            if latest is not None and latest.status is ConsentStatus.OPTED_IN:
                purpose = latest.purpose

        context = SendContext(
            consent_status=contact.consent_status,
            consent_purpose=purpose,
            suppressed=suppressed,
            template=template,
            now=now,
            last_inbound_at=contact.last_inbound_at,
            already_sent_in_campaign=already_sent_in_campaign,
            contact_locale=contact.locale,
        )
        decision = evaluate_send_eligibility(context, self._business, self._bot)
        if not decision.allowed:
            logger.info(
                "send_blocked",
                contact_id=contact.id,
                code=decision.code,
                template=template.name if template else None,
            )
        return decision

    async def history(self, contact: Contact, *, limit: int = 50) -> list[object]:
        return list(await self._consents.history(contact.id, limit=limit))
