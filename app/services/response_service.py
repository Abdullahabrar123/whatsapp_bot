"""Turning an :class:`IntentResult` into the message actually sent.

The model's ``proposed_response`` is a suggestion, not a decision. This service:

* substitutes deterministic, configuration-derived text for every consent and
  consequential intent;
* refuses to send an ungrounded answer to a factual question;
* strips anything that looks like leaked instructions or secrets;
* enforces length, formatting and disclaimer rules;
* provides a safe fallback when the model is unavailable.
"""

from __future__ import annotations

import re
from datetime import datetime

from app.core.logging import get_logger
from app.domain.config_schema import BotConfig, BusinessConfig
from app.domain.enums import EscalationReason, Intent, SafetyFlag
from app.domain.rules import is_within_business_hours
from app.domain.schemas import BotReply, IntentResult, RetrievalResult

logger = get_logger(__name__)

#: Factual intents that must never be answered without retrieval support.
_GROUNDING_REQUIRED: frozenset[Intent] = frozenset(
    {
        Intent.PRODUCT_INQUIRY,
        Intent.PRICING_INQUIRY,
        Intent.AVAILABILITY,
        Intent.ORDER_STATUS,
        Intent.LOCATION_HOURS,
    }
)

#: Text the assistant must never emit, even if the model produces it.
_LEAK_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"BUSINESS KNOWLEDGE",
        r"CUSTOMER MESSAGE:",
        r"system prompt",
        r"\bchunk[_ ]?id\b",
        r"\bapi[_ ]?key\b",
        r"\baccess[_ ]?token\b",
        r"\bapp[_ ]?secret\b",
        r"\bEA[A-Za-z0-9]{20,}\b",
        r"\[(?:svc|faq|policy|file):[^\]]*\]",
    )
)

_MARKDOWN_RE = re.compile(r"(\*\*|__|`{1,3}|^#{1,6}\s*)", re.MULTILINE)


def sanitise_reply(text: str, *, max_chars: int) -> str:
    """Strip leaked internals and markdown, then clamp the length."""
    cleaned = text.strip()
    for pattern in _LEAK_PATTERNS:
        cleaned = pattern.sub("", cleaned)
    cleaned = _MARKDOWN_RE.sub("", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned).strip()

    if len(cleaned) > max_chars:
        # Cut on a sentence boundary where possible so replies do not end
        # mid-word.
        window = cleaned[:max_chars]
        boundary = max(window.rfind(". "), window.rfind("! "), window.rfind("? "))
        cleaned = window[: boundary + 1] if boundary > max_chars * 0.5 else window.rstrip()
    return cleaned


class ResponseService:
    """Composes the final outbound reply."""

    def __init__(self, business: BusinessConfig, bot: BotConfig) -> None:
        self._business = business
        self._bot = bot

    # ------------------------------------------------------------- fragments
    def _human_handoff_sentence(self) -> str:
        escalation = self._business.escalation
        return (
            f"I'm passing this to our {escalation.human_agent_name} "
            f"who will get back to you."
        )

    def greeting_text(self) -> str:
        return self._business.greeting

    def optout_confirmation(self) -> str:
        return (
            "You've been unsubscribed and won't receive further promotional "
            f"messages from {self._business.name}. You can still message us "
            "any time if you need help."
        )

    def optin_confirmation(self) -> str:
        return (
            f"Thanks - you're subscribed to updates from {self._business.name}. "
            "Reply STOP at any time to unsubscribe."
        )

    def unavailable_text(self) -> str:
        """Fallback used when the model is down. Never blames the technology."""
        return (
            f"Thanks for your message. I can't answer that automatically right now, "
            f"so {self._human_handoff_sentence().lower()}"
        )

    def unsupported_message_text(self) -> str:
        return (
            "Sorry, I can't open that type of message. "
            "Could you send your question as text instead?"
        )

    def emergency_text(self) -> str | None:
        if self._business.emergency.enabled:
            return self._business.emergency.message
        return None

    def clarification_text(self, missing: list[str]) -> str:
        if missing:
            first = missing[0].rstrip(".?")
            return f"Happy to help. Could you tell me {first.lower()}?"
        return (
            "I want to make sure I get this right - could you tell me a little "
            "more about what you need?"
        )

    def no_knowledge_text(self) -> str:
        return (
            "I don't have that information to hand, so I don't want to guess. "
            f"{self._human_handoff_sentence()}"
        )

    def outside_hours_suffix(self, now: datetime) -> str:
        if is_within_business_hours(now, self._business):
            return ""
        if not self._bot.business_hours_behaviour.reply_outside_hours:
            return ""
        return f"\n\n{self._bot.business_hours_behaviour.outside_hours_message}"

    # ---------------------------------------------------------------- compose
    def compose(
        self,
        result: IntentResult,
        *,
        retrieval: RetrievalResult,
        now: datetime,
        llm_failed: bool = False,
    ) -> BotReply:
        """Decide the final reply for one inbound message."""
        max_chars = self._bot.max_response_chars

        # 1. Model unavailable -> safe, human-routed fallback.
        if llm_failed:
            return BotReply(
                text=sanitise_reply(self.unavailable_text(), max_chars=max_chars),
                intent=result.intent,
                confidence=0.0,
                escalate=True,
                escalation_reason=EscalationReason.LLM_UNAVAILABLE,
                deterministic=True,
            )

        # 2. Safety flags -> never let the model answer.
        if result.safety_flags:
            flag = result.safety_flags[0]
            if flag is SafetyFlag.SELF_HARM:
                text = self.emergency_text() or self.no_knowledge_text()
                reason = EscalationReason.SAFETY_CONCERN
            elif flag is SafetyFlag.PROMPT_INJECTION:
                # Give nothing away: reply as if it were an ordinary question
                # we cannot answer.
                text = self.no_knowledge_text()
                reason = EscalationReason.RESTRICTED_TOPIC
            elif result.intent is Intent.COMPLAINT or flag in (
                SafetyFlag.LEGAL_ADVICE,
                SafetyFlag.ABUSE,
            ):
                # Someone raising a grievance or threatening legal action must
                # not be told "I don't have that information" -- acknowledge the
                # complaint and route it to a person.
                text = (
                    "I'm sorry to hear that, and I want to get it sorted properly. "
                    f"{self._human_handoff_sentence()}"
                )
                reason = (
                    EscalationReason.LEGAL_THREAT
                    if flag is SafetyFlag.LEGAL_ADVICE
                    else EscalationReason.COMPLAINT
                )
            else:
                text = self.no_knowledge_text()
                reason = EscalationReason.RESTRICTED_TOPIC
            return BotReply(
                text=sanitise_reply(text, max_chars=max_chars),
                intent=result.intent,
                confidence=result.confidence,
                escalate=True,
                escalation_reason=reason,
                deterministic=True,
            )

        # 3. Consent intents are answered by fixed text only.
        if result.intent is Intent.OPT_OUT:
            return BotReply(
                text=sanitise_reply(self.optout_confirmation(), max_chars=max_chars),
                intent=Intent.OPT_OUT,
                confidence=1.0,
                deterministic=True,
            )
        if result.intent is Intent.OPT_IN:
            return BotReply(
                text=sanitise_reply(self.optin_confirmation(), max_chars=max_chars),
                intent=Intent.OPT_IN,
                confidence=1.0,
                deterministic=True,
            )

        # 4. Explicit request for a person.
        if result.intent is Intent.HUMAN_AGENT_REQUEST:
            return BotReply(
                text=sanitise_reply(
                    f"Of course. {self._human_handoff_sentence()}", max_chars=max_chars
                ),
                intent=Intent.HUMAN_AGENT_REQUEST,
                confidence=1.0,
                escalate=True,
                escalation_reason=EscalationReason.CUSTOMER_REQUEST,
                pause_bot=True,
                deterministic=True,
            )

        # 5. Consequential intents: acknowledge, never action.
        if result.intent in (Intent.COMPLAINT, Intent.REFUND_REQUEST, Intent.CANCELLATION):
            reason = {
                Intent.COMPLAINT: EscalationReason.COMPLAINT,
                Intent.REFUND_REQUEST: EscalationReason.SENSITIVE_ACTION,
                Intent.CANCELLATION: EscalationReason.SENSITIVE_ACTION,
            }[result.intent]
            acknowledgement = {
                Intent.COMPLAINT: "I'm sorry to hear that, and I want to get it sorted properly.",
                Intent.REFUND_REQUEST: (
                    "I can't approve refunds myself, but I can get this looked at."
                ),
                Intent.CANCELLATION: "I can't change bookings myself, but I can pass this on.",
            }[result.intent]
            return BotReply(
                text=sanitise_reply(
                    f"{acknowledgement} {self._human_handoff_sentence()}",
                    max_chars=max_chars,
                ),
                intent=result.intent,
                confidence=max(result.confidence, 0.9),
                escalate=True,
                escalation_reason=reason,
                pause_bot=result.intent is Intent.COMPLAINT,
                deterministic=True,
            )

        # 6. Greeting: configured text, no model needed.
        if result.intent is Intent.GREETING and not result.proposed_response.strip():
            return BotReply(
                text=sanitise_reply(
                    self.greeting_text() + self.outside_hours_suffix(now),
                    max_chars=max_chars,
                ),
                intent=Intent.GREETING,
                confidence=max(result.confidence, 0.9),
                deterministic=True,
            )

        # 7. Below the escalation threshold -> hand over.
        if result.confidence < self._bot.thresholds.escalate or result.requires_human:
            return BotReply(
                text=sanitise_reply(self.no_knowledge_text(), max_chars=max_chars),
                intent=result.intent,
                confidence=result.confidence,
                escalate=True,
                escalation_reason=EscalationReason.LOW_CONFIDENCE,
                deterministic=True,
            )

        # 8. Between thresholds -> ask a clarifying question instead of guessing.
        if result.confidence < self._bot.thresholds.reply:
            return BotReply(
                text=sanitise_reply(
                    self.clarification_text(result.missing_information), max_chars=max_chars
                ),
                intent=result.intent,
                confidence=result.confidence,
                deterministic=True,
            )

        # 9. Factual questions must be grounded in retrieved knowledge.
        if result.intent in _GROUNDING_REQUIRED and not (
            retrieval.has_context and result.sources
        ):
            logger.info("ungrounded_factual_answer_blocked", intent=result.intent.value)
            return BotReply(
                text=sanitise_reply(self.no_knowledge_text(), max_chars=max_chars),
                intent=result.intent,
                confidence=result.confidence,
                escalate=True,
                escalation_reason=EscalationReason.NO_KNOWLEDGE,
                deterministic=True,
            )

        # 10. Model answer, sanitised. If nothing usable survives, fall back.
        text = sanitise_reply(result.proposed_response, max_chars=max_chars)
        if not text:
            text = sanitise_reply(self.no_knowledge_text(), max_chars=max_chars)
            return BotReply(
                text=text,
                intent=result.intent,
                confidence=result.confidence,
                escalate=True,
                escalation_reason=EscalationReason.NO_KNOWLEDGE,
                deterministic=True,
            )

        suffix = self.outside_hours_suffix(now)
        if suffix and len(text) + len(suffix) <= max_chars:
            text = text + suffix

        return BotReply(
            text=text,
            intent=result.intent,
            confidence=result.confidence,
            escalate=False,
            sources=result.sources,
            deterministic=False,
        )
