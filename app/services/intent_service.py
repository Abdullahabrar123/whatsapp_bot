"""Intent detection and prompt assembly.

Order of authority, highest first:

1. Deterministic keyword rules for consent and handover. These run *before* the
   model and their outcome is final -- an opt-out is never subject to model
   judgement.
2. Retrieval, which decides whether there is any grounded basis for an answer.
3. The LLM, which may only classify and draft within that grounding.
4. Post-validation, which can downgrade the model's confidence but never
   upgrade it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from app.core.logging import get_logger
from app.domain.config_schema import BotConfig, BusinessConfig
from app.domain.enums import Intent, SafetyFlag
from app.domain.rules import detect_handover_request, detect_opt_in, detect_opt_out
from app.domain.schemas import IntentResult, RetrievalResult

logger = get_logger(__name__)

_PROMPT_DIR = Path(__file__).resolve().parents[1] / "prompts"

#: Patterns that attempt to override the system prompt or extract secrets.
_INJECTION_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"ignore\s+(?:all\s+)?(?:the\s+)?(?:previous|prior|above|earlier)\s+"
        r"(?:instructions?|prompts?|rules?)",
        r"disregard\s+(?:all\s+)?(?:previous|prior|your)\s+",
        r"(?:reveal|show|print|repeat|output|tell\s+me)\s+(?:me\s+)?(?:your\s+)?"
        r"(?:system\s+)?(?:prompt|instructions?|configuration|config|rules)",
        r"you\s+are\s+now\s+(?:a|an|the)\s+",
        r"(?:act|behave|pretend)\s+as\s+(?:if\s+you\s+are\s+)?(?:a|an|the)\s+",
        r"developer\s+mode",
        r"jailbreak",
        r"\bDAN\b\s+mode",
        r"(?:what|show)\s+(?:is\s+)?your\s+(?:api\s+)?(?:key|token|secret|password)",
        r"</?(?:system|assistant|instructions?)>",
        r"new\s+(?:system\s+)?instructions?\s*:",
    )
)


def detect_prompt_injection(text: str) -> bool:
    """True when the message looks like an attempt to subvert the system rules."""
    if not text:
        return False
    return any(pattern.search(text) for pattern in _INJECTION_PATTERNS)


@lru_cache(maxsize=8)
def _load_prompt(name: str) -> str:
    return (_PROMPT_DIR / name).read_text(encoding="utf-8")


@dataclass(frozen=True, slots=True)
class DeterministicOutcome:
    """A decision made without consulting the model."""

    intent: Intent
    matched_keyword: str | None = None


def classify_deterministic(text: str, bot: BotConfig) -> DeterministicOutcome | None:
    """Resolve consent and handover intents by rule, before any model call.

    Opt-out is checked first and unconditionally: a message that is an opt-out
    must never be reinterpreted by a model as, say, a booking request.
    """
    if not text:
        return None

    matched = detect_opt_out(text, bot)
    if matched:
        return DeterministicOutcome(Intent.OPT_OUT, matched)

    matched = detect_opt_in(text, bot)
    if matched:
        return DeterministicOutcome(Intent.OPT_IN, matched)

    matched = detect_handover_request(text, bot)
    if matched:
        return DeterministicOutcome(Intent.HUMAN_AGENT_REQUEST, matched)

    return None


class PromptBuilder:
    """Assembles system and user prompts from configuration and retrieval."""

    def __init__(self, business: BusinessConfig, bot: BotConfig) -> None:
        self._business = business
        self._bot = bot

    def system_prompt(self, *, language: str | None = None) -> str:
        restricted = (
            "; ".join(self._business.restricted_subjects)
            if self._business.restricted_subjects
            else "none configured"
        )
        return _load_prompt("system_prompt.md").format(
            business_name=self._business.name,
            industry=self._business.industry,
            brand_voice=self._business.brand_voice,
            response_tone=self._bot.response_tone,
            language=language or self._business.default_language,
            max_chars=self._bot.max_response_chars,
            human_agent_name=self._business.escalation.human_agent_name,
            restricted_subjects=restricted,
        )

    def user_prompt(
        self,
        message: str,
        *,
        retrieval: RetrievalResult,
        history: list[tuple[str, str]] | None = None,
        extra_instructions: str = "",
    ) -> str:
        """Compose the user turn.

        The customer's text is placed last, after an explicit marker, so both
        the model and the deterministic provider can tell customer input apart
        from retrieved data.
        """
        intents = "\n".join(f"- {item.value}" for item in self._bot.enabled_intents)
        intent_block = _load_prompt("intent_prompt.md").format(
            intents=intents,
            escalate_threshold=self._bot.thresholds.escalate,
        )

        context = (
            retrieval.as_context_block()
            if retrieval.has_context
            else "(no relevant business knowledge found for this question)"
        )

        history_lines = "(no earlier messages)"
        if history:
            trimmed = history[-self._bot.history_limit :] if self._bot.history_limit else []
            if trimmed:
                history_lines = "\n".join(
                    f"{role}: {text}" for role, text in trimmed
                )

        body = _load_prompt("response_prompt.md").format(
            context=context,
            history=history_lines,
            extra_instructions=extra_instructions,
            message=message,
        )
        return f"{intent_block}\n\n{body}"


class IntentService:
    """Produces a validated :class:`IntentResult` for one inbound message."""

    def __init__(
        self,
        business: BusinessConfig,
        bot: BotConfig,
        llm_client: object,
        prompt_builder: PromptBuilder | None = None,
    ) -> None:
        self._business = business
        self._bot = bot
        self._llm = llm_client
        self._prompts = prompt_builder or PromptBuilder(business, bot)

    async def analyse(
        self,
        message: str,
        *,
        retrieval: RetrievalResult,
        history: list[tuple[str, str]] | None = None,
        language: str | None = None,
    ) -> IntentResult:
        """Classify the message and draft a grounded reply."""
        # 1. Rules first. Consent decisions are never delegated to a model.
        deterministic = classify_deterministic(message, self._bot)
        if deterministic is not None:
            logger.info(
                "intent_deterministic",
                intent=deterministic.intent.value,
                matched=deterministic.matched_keyword,
            )
            return IntentResult(
                intent=deterministic.intent,
                confidence=1.0,
                requires_human=deterministic.intent is Intent.HUMAN_AGENT_REQUEST,
                proposed_response="",
            )

        # 2. Injection screening happens before the message reaches the model.
        if detect_prompt_injection(message):
            logger.warning("prompt_injection_detected")
            return IntentResult(
                intent=Intent.UNKNOWN,
                confidence=0.0,
                requires_human=True,
                safety_flags=[SafetyFlag.PROMPT_INJECTION],
                proposed_response="",
            )

        # 3. Model call.
        analyse = getattr(self._llm, "analyse", None)
        if analyse is None:
            raise TypeError("LLM client does not implement analyse().")

        result: IntentResult = await analyse(
            system_prompt=self._prompts.system_prompt(language=language),
            user_prompt=self._prompts.user_prompt(
                message, retrieval=retrieval, history=history
            ),
            timeout=self._bot.model_timeout_seconds,
        )

        # 4. Post-validation. Confidence may only be lowered here.
        return self._post_validate(result, retrieval=retrieval)

    def _post_validate(
        self, result: IntentResult, *, retrieval: RetrievalResult
    ) -> IntentResult:
        """Downgrade unsupported claims and enforce the enabled-intent list."""
        updates: dict[str, object] = {}

        if result.intent not in self._bot.enabled_intents:
            logger.info("intent_not_enabled", intent=result.intent.value)
            updates["intent"] = Intent.UNKNOWN
            updates["confidence"] = min(result.confidence, self._bot.thresholds.escalate)

        # An answer with no retrieval support cannot be trusted, whatever the
        # model claimed about its own confidence.
        if not retrieval.has_context and result.intent not in (
            Intent.GREETING,
            Intent.OPT_IN,
            Intent.OPT_OUT,
            Intent.HUMAN_AGENT_REQUEST,
        ):
            current_confidence = updates.get("confidence", result.confidence)
            updates["confidence"] = min(
                float(current_confidence) if isinstance(current_confidence, int | float) else 0.0,
                self._bot.thresholds.escalate,
            )
            updates["sources"] = []

        # Claimed sources must exist in the retrieval result; a model that
        # invents a citation loses its grounding claim.
        if result.sources:
            valid = set(retrieval.source_ids())
            kept = [source for source in result.sources if source in valid]
            if len(kept) != len(result.sources):
                logger.info(
                    "citation_hallucinated",
                    claimed=len(result.sources),
                    kept=len(kept),
                )
                updates["sources"] = kept
                if not kept:
                    pending = updates.get("confidence", result.confidence)
                    updates["confidence"] = min(
                        float(pending) if isinstance(pending, int | float) else 0.0,
                        self._bot.thresholds.escalate,
                    )

        if not updates:
            return result
        return result.model_copy(update=updates)
