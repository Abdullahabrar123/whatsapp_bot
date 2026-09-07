"""Unit tests for compliance rules, consent eligibility, opt-out detection, and quiet hours."""

from datetime import UTC, datetime

import pytest

from app.domain.config_schema import (
    Address,
    BotConfig,
    BusinessConfig,
    BusinessHours,
    ConfidenceThresholds,
    ContactInfo,
    DayHours,
    EscalationRules,
    QuietHours,
    TemplateDefinition,
)
from app.domain.enums import ConsentStatus, Intent, TemplateCategory, TemplateStatus
from app.domain.rules import (
    SendContext,
    detect_opt_out,
    evaluate_send_eligibility,
    service_window_open,
)


@pytest.fixture
def dummy_business() -> BusinessConfig:
    hours = DayHours(closed=False, open="09:00", close="17:00")
    b_hours = BusinessHours(
        monday=hours,
        tuesday=hours,
        wednesday=hours,
        thursday=hours,
        friday=hours,
        saturday=DayHours(closed=True),
        sunday=DayHours(closed=True),
    )
    return BusinessConfig(
        business_id="test_biz",
        name="Test Clinic",
        industry="Healthcare",
        description="A premier test clinic in Central London providing quality care.",
        website="https://example.com",
        timezone="Europe/London",
        supported_languages=["en"],
        default_language="en",
        brand_voice="Warm and professional healthcare provider",
        greeting="Hello and welcome to Test Clinic.",
        address=Address(line1="1 High St", city="London", postal_code="SW1A 1AA", country="GB"),
        contact=ContactInfo(phone_display="+44 20 7946 0000", email="info@example.com"),
        business_hours=b_hours,
        escalation=EscalationRules(
            human_agent_name="Reception Team",
            human_agent_contact="+44 20 7946 0000",
        ),
        legal_disclaimer="BrightSmile is registered in England & Wales.",
    )


@pytest.fixture
def dummy_bot() -> BotConfig:
    return BotConfig(
        enabled_intents=[Intent.GREETING, Intent.OPT_OUT, Intent.HUMAN_AGENT_REQUEST],
        enabled_languages=["en"],
        supported_message_types=["text", "interactive", "button"],
        stop_words=["stop", "unsubscribe", "cancel"],
        handover_keywords=["human", "agent", "speak to someone"],
        thresholds=ConfidenceThresholds(reply=0.6, escalate=0.4),
        quiet_hours=QuietHours(enabled=True, start="22:00", end="08:00"),
    )


def test_detect_opt_out(dummy_bot: BotConfig):
    assert detect_opt_out("Please STOP sending messages", dummy_bot) == "stop"
    assert detect_opt_out("unsubscribe me now", dummy_bot) == "unsubscribe"
    assert detect_opt_out("I want to stop by your office", dummy_bot) is None


def test_service_window_open():
    now = datetime(2026, 6, 15, 12, 0, tzinfo=UTC)
    inbound_recent = datetime(2026, 6, 15, 10, 0, tzinfo=UTC)
    inbound_old = datetime(2026, 6, 14, 10, 0, tzinfo=UTC)

    assert service_window_open(inbound_recent, now) is True
    assert service_window_open(inbound_old, now) is False
    assert service_window_open(None, now) is False


def test_evaluate_send_eligibility_suppressed(dummy_business: BusinessConfig, dummy_bot: BotConfig):
    now = datetime(2026, 6, 15, 12, 0, tzinfo=UTC)
    template = TemplateDefinition(
        name="test_tmpl",
        category=TemplateCategory.MARKETING,
        language="en",
        status=TemplateStatus.APPROVED,
        purpose="Marketing announcement",
        body_preview="Hello world",
        requires_opt_in=True,
    )
    context = SendContext(
        consent_status=ConsentStatus.OPTED_OUT,
        consent_purpose=None,
        suppressed=True,
        template=template,
        now=now,
        last_inbound_at=None,
        already_sent_in_campaign=False,
        contact_locale="en",
    )
    decision = evaluate_send_eligibility(context, dummy_business, dummy_bot)
    assert decision.allowed is False
    assert decision.code == "suppressed"
