"""Domain enumerations shared by models, schemas and services."""

from __future__ import annotations

from enum import StrEnum


class Direction(StrEnum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"


class MessageType(StrEnum):
    """Inbound/outbound message shapes supported by the platform."""

    TEXT = "text"
    IMAGE = "image"
    DOCUMENT = "document"
    AUDIO = "audio"
    VIDEO = "video"
    STICKER = "sticker"
    LOCATION = "location"
    CONTACTS = "contacts"
    INTERACTIVE_BUTTON_REPLY = "interactive_button_reply"
    INTERACTIVE_LIST_REPLY = "interactive_list_reply"
    BUTTON = "button"
    TEMPLATE = "template"
    REACTION = "reaction"
    ORDER = "order"
    SYSTEM = "system"
    UNSUPPORTED = "unsupported"


class DeliveryStatus(StrEnum):
    """Lifecycle of an outbound message as reported by Meta status webhooks."""

    QUEUED = "queued"
    SENT = "sent"
    ACCEPTED = "accepted"
    DELIVERED = "delivered"
    READ = "read"
    FAILED = "failed"
    DELETED = "deleted"


class ConversationState(StrEnum):
    ACTIVE = "active"
    AWAITING_CUSTOMER = "awaiting_customer"
    ESCALATED = "escalated"
    HUMAN_HANDLING = "human_handling"
    PAUSED = "paused"
    CLOSED = "closed"
    EXPIRED = "expired"


class ConsentStatus(StrEnum):
    """Recorded marketing/business-initiated messaging permission."""

    UNKNOWN = "unknown"
    PENDING = "pending"
    OPTED_IN = "opted_in"
    OPTED_OUT = "opted_out"
    SUPPRESSED = "suppressed"


class ConsentSource(StrEnum):
    WEBSITE_FORM = "website_form"
    IN_STORE = "in_store"
    WHATSAPP_OPT_IN = "whatsapp_opt_in"
    PHONE_CALL = "phone_call"
    PAPER_FORM = "paper_form"
    IMPORT_VERIFIED = "import_verified"
    CHECKOUT = "checkout"


class ConsentPurpose(StrEnum):
    MARKETING = "marketing"
    UTILITY = "utility"
    SERVICE = "service"
    AUTHENTICATION = "authentication"


class TemplateCategory(StrEnum):
    """Meta template categories. Utility must never carry marketing content."""

    MARKETING = "MARKETING"
    UTILITY = "UTILITY"
    AUTHENTICATION = "AUTHENTICATION"


class TemplateStatus(StrEnum):
    APPROVED = "APPROVED"
    PENDING = "PENDING"
    REJECTED = "REJECTED"
    PAUSED = "PAUSED"
    DISABLED = "DISABLED"


class Intent(StrEnum):
    """Intents the orchestration layer can act on."""

    GREETING = "greeting"
    PRODUCT_INQUIRY = "product_inquiry"
    PRICING_INQUIRY = "pricing_inquiry"
    AVAILABILITY = "availability"
    BOOKING_REQUEST = "booking_request"
    ORDER_STATUS = "order_status"
    SUPPORT_REQUEST = "support_request"
    COMPLAINT = "complaint"
    CANCELLATION = "cancellation"
    REFUND_REQUEST = "refund_request"
    LOCATION_HOURS = "location_hours"
    HUMAN_AGENT_REQUEST = "human_agent_request"
    OPT_IN = "opt_in"
    OPT_OUT = "opt_out"
    UNKNOWN = "unknown"


#: Intents whose handling is decided by deterministic application code, never by
#: the LLM, because they change consent state or trigger consequential actions.
DETERMINISTIC_INTENTS: frozenset[Intent] = frozenset(
    {
        Intent.OPT_IN,
        Intent.OPT_OUT,
        Intent.HUMAN_AGENT_REQUEST,
        Intent.REFUND_REQUEST,
        Intent.CANCELLATION,
    }
)

#: Intents that always route to a human regardless of model confidence.
ALWAYS_ESCALATE_INTENTS: frozenset[Intent] = frozenset(
    {
        Intent.COMPLAINT,
        Intent.HUMAN_AGENT_REQUEST,
        Intent.REFUND_REQUEST,
    }
)


class EscalationReason(StrEnum):
    LOW_CONFIDENCE = "low_confidence"
    CUSTOMER_REQUEST = "customer_request"
    COMPLAINT = "complaint"
    LEGAL_THREAT = "legal_threat"
    SAFETY_CONCERN = "safety_concern"
    REPEATED_FAILURE = "repeated_failure"
    RESTRICTED_TOPIC = "restricted_topic"
    SENSITIVE_ACTION = "sensitive_action"
    NO_KNOWLEDGE = "no_knowledge"
    LLM_UNAVAILABLE = "llm_unavailable"


class EscalationStatus(StrEnum):
    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"


class CampaignStatus(StrEnum):
    DRAFT = "draft"
    VALIDATED = "validated"
    QUEUED = "queued"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class CampaignRecipientStatus(StrEnum):
    PENDING = "pending"
    ELIGIBLE = "eligible"
    REJECTED = "rejected"
    QUEUED = "queued"
    SENT = "sent"
    DELIVERED = "delivered"
    READ = "read"
    FAILED = "failed"
    SKIPPED = "skipped"


class SafetyFlag(StrEnum):
    """Compliance/safety signals returned by the intent layer."""

    NONE = "none"
    PROMPT_INJECTION = "prompt_injection"
    RESTRICTED_TOPIC = "restricted_topic"
    MEDICAL_ADVICE = "medical_advice"
    LEGAL_ADVICE = "legal_advice"
    FINANCIAL_ADVICE = "financial_advice"
    SELF_HARM = "self_harm"
    ABUSE = "abuse"
    PII_REQUEST = "pii_request"
    OUT_OF_SCOPE = "out_of_scope"
