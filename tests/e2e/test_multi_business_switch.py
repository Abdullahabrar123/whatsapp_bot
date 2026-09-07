"""End-to-end test proving that switching between two different business configurations
requires ZERO code modifications and immediately customizes the bot's persona and knowledge.
"""

from datetime import UTC, datetime
from pathlib import Path

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
    FAQItem,
    ServiceItem,
)
from app.domain.enums import Intent
from app.integrations.llm_client import DeterministicClient
from app.services.intent_service import IntentService
from app.services.knowledge_service import KnowledgeService
from app.services.response_service import ResponseService

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def bot_config() -> BotConfig:
    return BotConfig(
        enabled_intents=[
            Intent.GREETING,
            Intent.PRICING_INQUIRY,
            Intent.PRODUCT_INQUIRY,
            Intent.LOCATION_HOURS,
            Intent.HUMAN_AGENT_REQUEST,
            Intent.OPT_OUT,
        ],
        enabled_languages=["en"],
        supported_message_types=["text", "interactive", "button"],
        stop_words=["stop"],
        handover_keywords=["human", "agent"],
        thresholds=ConfidenceThresholds(reply=0.60, escalate=0.40),
    )


@pytest.fixture
def dental_config() -> BusinessConfig:
    hours = DayHours(closed=False, open="08:30", close="17:30")
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
        business_id="brightsmile_dental",
        name="BrightSmile Dental Care",
        industry="Healthcare / Dental",
        description="Premier family and cosmetic dental practice in Central London.",
        website="https://www.brightsmile-example.co.uk",
        timezone="Europe/London",
        supported_languages=["en"],
        default_language="en",
        brand_voice="Warm, professional, reassuring, and clear healthcare provider",
        greeting="Hello and welcome to BrightSmile Dental Care.",
        address=Address(line1="1 High St", city="London", postal_code="SW1A 1AA", country="GB"),
        contact=ContactInfo(phone_display="+44 20 7946 0000", email="info@brightsmile.co.uk"),
        business_hours=b_hours,
        services=[
            ServiceItem(
                id="svc:checkup",
                name="Routine Check-up",
                description="Full dental examination including scale and polish assessment.",
                price="£65.00",
            )
        ],
        faqs=[
            FAQItem(
                id="faq:children",
                question="Do you treat children?",
                answer="Yes, we welcome patients of all ages including children.",
            )
        ],
        escalation=EscalationRules(
            human_agent_name="Reception Team",
            human_agent_contact="+44 20 7946 0000",
        ),
        legal_disclaimer="BrightSmile is registered in England & Wales.",
    )


@pytest.fixture
def auto_config() -> BusinessConfig:
    hours = DayHours(closed=False, open="08:00", close="18:00")
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
        business_id="apex_auto",
        name="Apex Auto Garage",
        industry="Automotive Repair",
        description="Specialist European vehicle maintenance and diagnostics garage.",
        website="https://www.apexauto-example.co.uk",
        timezone="Europe/London",
        supported_languages=["en"],
        default_language="en",
        brand_voice="Direct, technical, honest, and efficient mechanic",
        greeting="Hello and welcome to Apex Auto Garage.",
        address=Address(
            line1="42 Industrial Way", city="London", postal_code="E1 6AN", country="GB"
        ),
        contact=ContactInfo(phone_display="+44 20 7946 0999", email="service@apexauto.co.uk"),
        business_hours=b_hours,
        services=[
            ServiceItem(
                id="svc:oil_service",
                name="Full Synthetic Oil Service",
                description="Oil and filter replacement with 50-point safety check.",
                price="£89.00",
            )
        ],
        faqs=[
            FAQItem(
                id="faq:courtesy",
                question="Do you provide courtesy cars?",
                answer="Yes, courtesy cars are available upon booking.",
            )
        ],
        escalation=EscalationRules(
            human_agent_name="Service Desk",
            human_agent_contact="+44 20 7946 0999",
        ),
        legal_disclaimer="Apex Auto Garage Ltd registered in England & Wales.",
    )


@pytest.mark.asyncio
async def test_multi_business_configuration_switch(
    bot_config: BotConfig,
    dental_config: BusinessConfig,
    auto_config: BusinessConfig,
):
    now = datetime(2026, 6, 15, 12, 0, tzinfo=UTC)
    llm = DeterministicClient()

    # ========================================================== Business 1: Dental
    ks1 = KnowledgeService(
        dental_config, project_root=PROJECT_ROOT, llm_client=llm, embeddings_enabled=False
    )
    ks1.build_index()
    is1 = IntentService(dental_config, bot_config, llm)
    rs1 = ResponseService(dental_config, bot_config)

    # 1. Greeting test
    r1_greet = await ks1.search("Hello")
    ir1_greet = await is1.analyse("Hello there", retrieval=r1_greet, history=[])
    reply1_greet = rs1.compose(ir1_greet, retrieval=r1_greet, now=now)
    assert "BrightSmile Dental Care" in reply1_greet.text

    # 2. Dental Pricing inquiry
    r1_price = await ks1.search("How much is a check-up?")
    ir1_price = await is1.analyse("How much is a check-up?", retrieval=r1_price, history=[])
    reply1_price = rs1.compose(ir1_price, retrieval=r1_price, now=now)
    assert ir1_price.intent == Intent.PRICING_INQUIRY
    assert "Routine Check-up" in reply1_price.text or "£65.00" in reply1_price.text

    # ========================================================== Business 2: Auto Garage
    ks2 = KnowledgeService(
        auto_config, project_root=PROJECT_ROOT, llm_client=llm, embeddings_enabled=False
    )
    ks2.build_index()
    is2 = IntentService(auto_config, bot_config, llm)
    rs2 = ResponseService(auto_config, bot_config)

    # 1. Greeting test
    r2_greet = await ks2.search("Hello")
    ir2_greet = await is2.analyse("Hello there", retrieval=r2_greet, history=[])
    reply2_greet = rs2.compose(ir2_greet, retrieval=r2_greet, now=now)
    assert "Apex Auto Garage" in reply2_greet.text
    assert "BrightSmile" not in reply2_greet.text

    # 2. Auto Pricing inquiry
    r2_price = await ks2.search("How much is an oil service?")
    ir2_price = await is2.analyse("How much is an oil service?", retrieval=r2_price, history=[])
    reply2_price = rs2.compose(ir2_price, retrieval=r2_price, now=now)
    assert ir2_price.intent == Intent.PRICING_INQUIRY
    assert "Oil Service" in reply2_price.text or "£89.00" in reply2_price.text
