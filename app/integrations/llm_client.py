"""LLM providers behind a single interface.

Two implementations ship:

``OllamaClient``
    Talks to a local Ollama server (default model ``qwen3:4b``). Nothing leaves
    the host, so customer messages are never sent to a third-party API.

``DeterministicClient``
    A rule-based provider with no model at all. It is the default in tests and
    the automatic fallback when Ollama is unavailable, which means the whole
    suite runs offline and a model outage degrades the bot rather than breaking
    it.

Both return a validated :class:`IntentResult`; callers cannot tell which
produced it, which is what makes the provider swappable.
"""

from __future__ import annotations

import asyncio
import json
import re
from functools import lru_cache
from typing import Any, Protocol, Self

import httpx

from app.core.exceptions import LLMTimeoutError, LLMUnavailableError
from app.core.logging import get_logger
from app.core.settings import Settings
from app.domain.enums import Intent, SafetyFlag
from app.domain.schemas import INTENT_JSON_SCHEMA, IntentResult

logger = get_logger(__name__)

# qwen3 emits reasoning inside <think>...</think>; it must never reach a customer.
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def strip_thinking(text: str) -> str:
    """Remove reasoning blocks and code fences from a model response."""
    return _THINK_RE.sub("", text).strip()


def extract_json_object(text: str) -> dict[str, Any] | None:
    """Pull the first JSON object out of a model response.

    Models wrap JSON in prose or fences even when told not to, so this tries
    fenced content first, then a brace-balanced scan.
    """
    cleaned = strip_thinking(text)
    if not cleaned:
        return None

    fenced = _JSON_FENCE_RE.search(cleaned)
    candidates: list[str] = []
    if fenced:
        candidates.append(fenced.group(1).strip())
    candidates.append(cleaned)

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except (ValueError, TypeError):
            pass
        else:
            if isinstance(parsed, dict):
                return parsed

    # Brace-balanced scan for the first complete object.
    start = cleaned.find("{")
    while start != -1:
        depth = 0
        in_string = False
        escaped = False
        for index in range(start, len(cleaned)):
            char = cleaned[index]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    try:
                        parsed = json.loads(cleaned[start : index + 1])
                    except (ValueError, TypeError):
                        break
                    if isinstance(parsed, dict):
                        return parsed
                    break
        start = cleaned.find("{", start + 1)
    return None


# =================================================================== interface
class LLMClientProtocol(Protocol):
    """Interface every provider implements."""

    @property
    def name(self) -> str: ...

    async def analyse(
        self, *, system_prompt: str, user_prompt: str, timeout: float | None = None
    ) -> IntentResult: ...

    async def embed(self, texts: list[str]) -> list[list[float]]: ...

    async def health_check(self) -> bool: ...

    async def aclose(self) -> None: ...


# ==================================================================== ollama
class OllamaClient:
    """Local Ollama provider using the ``/api/chat`` endpoint.

    ``format`` is set to the JSON Schema of :class:`IntentResult` so Ollama
    constrains generation to valid JSON rather than relying on prompt obedience.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings
        self._owns_client = http_client is None
        self._client = http_client or httpx.AsyncClient(
            base_url=settings.ollama_base_url,
            timeout=httpx.Timeout(settings.llm_timeout_seconds),
        )
        self._semaphore = asyncio.Semaphore(8)

    @property
    def name(self) -> str:
        return f"ollama:{self._settings.llm_model}"

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def analyse(
        self, *, system_prompt: str, user_prompt: str, timeout: float | None = None
    ) -> IntentResult:
        """Run one constrained-JSON completion and validate the result."""
        payload: dict[str, Any] = {
            "model": self._settings.llm_model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "stream": False,
            "format": INTENT_JSON_SCHEMA,
            "options": {
                "temperature": self._settings.llm_temperature,
                "num_ctx": self._settings.llm_num_ctx,
            },
        }
        # qwen3 supports an explicit thinking switch; off keeps replies fast.
        if not self._settings.llm_enable_thinking:
            payload["think"] = False

        attempts = self._settings.llm_max_retries + 1
        last_error: Exception | None = None

        for attempt in range(attempts):
            try:
                async with self._semaphore:
                    response = await self._client.post(
                        "/api/chat",
                        json=payload,
                        timeout=timeout or self._settings.llm_timeout_seconds,
                    )
            except httpx.TimeoutException as exc:
                last_error = LLMTimeoutError(f"Ollama timed out after {attempt + 1} attempt(s).")
                logger.warning("llm_timeout", provider=self.name, attempt=attempt + 1)
                _ = exc
            except httpx.TransportError as exc:
                last_error = LLMUnavailableError(
                    f"Cannot reach Ollama at {self._settings.ollama_base_url}: "
                    f"{type(exc).__name__}"
                )
                logger.warning("llm_unreachable", provider=self.name, attempt=attempt + 1)
            else:
                if response.status_code >= 400:
                    last_error = LLMUnavailableError(
                        f"Ollama returned HTTP {response.status_code}."
                    )
                    logger.warning(
                        "llm_http_error",
                        provider=self.name,
                        status_code=response.status_code,
                        attempt=attempt + 1,
                    )
                else:
                    return self._parse_response(response.json())

            if attempt < attempts - 1:
                await asyncio.sleep(min(2.0**attempt, 5.0))

        if last_error is None:  # pragma: no cover - loop always sets it
            raise LLMUnavailableError("Ollama request failed without a recorded error.")
        raise last_error

    @staticmethod
    def _parse_response(body: dict[str, Any]) -> IntentResult:
        """Validate the model's JSON, failing closed on anything unusable."""
        content = ""
        message = body.get("message")
        if isinstance(message, dict):
            content = str(message.get("content") or "")
        if not content:
            content = str(body.get("response") or "")

        data = extract_json_object(content)
        if data is None:
            logger.warning("llm_unparseable_output", chars=len(content))
            raise LLMUnavailableError("Model did not return parseable JSON.")

        try:
            return IntentResult.model_validate(data)
        except Exception as exc:  # noqa: BLE001 - any invalid shape is a provider fault
            logger.warning("llm_invalid_schema", error=type(exc).__name__)
            raise LLMUnavailableError("Model output failed schema validation.") from exc

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed texts with the configured embedding model.

        Returns an empty list on any failure so retrieval silently degrades to
        lexical-only rather than failing the customer's request.
        """
        if not texts:
            return []
        try:
            response = await self._client.post(
                "/api/embed",
                json={"model": self._settings.llm_embedding_model, "input": texts},
                timeout=self._settings.llm_timeout_seconds,
            )
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.info("embedding_unavailable", error=type(exc).__name__)
            return []

        embeddings = body.get("embeddings")
        if isinstance(embeddings, list) and all(isinstance(item, list) for item in embeddings):
            return [[float(value) for value in vector] for vector in embeddings]
        return []

    async def health_check(self) -> bool:
        try:
            response = await self._client.get("/api/tags", timeout=5.0)
        except httpx.HTTPError:
            return False
        return response.status_code == 200


# ============================================================== deterministic
#: Keyword signals for the rule-based provider. Ordered by specificity.
_INTENT_KEYWORDS: tuple[tuple[Intent, tuple[str, ...]], ...] = (
    (Intent.OPT_OUT, ("stop", "unsubscribe", "opt out", "remove me", "no more messages")),
    (Intent.OPT_IN, ("subscribe", "opt in", "sign me up")),
    (
        Intent.HUMAN_AGENT_REQUEST,
        ("human", "agent", "real person", "speak to someone", "representative", "manager"),
    ),
    (
        Intent.COMPLAINT,
        ("complaint", "complain", "unacceptable", "terrible", "awful", "solicitor", "sue",
         "legal action", "lawyer", "ombudsman"),
    ),
    (Intent.REFUND_REQUEST, ("refund", "money back", "reimburse")),
    (Intent.CANCELLATION, ("cancel my", "cancel the", "cancel appointment", "reschedule")),
    (Intent.ORDER_STATUS, ("order status", "my order", "tracking", "where is my")),
    (Intent.BOOKING_REQUEST, ("book", "appointment", "schedule", "slot", "reserve")),
    (Intent.PRICING_INQUIRY, ("price", "cost", "how much", "fee", "charge", "quote")),
    (Intent.AVAILABILITY, ("available", "availability", "free on", "any slots")),
    (
        Intent.LOCATION_HOURS,
        ("open", "opening", "hours", "where are you", "address", "location", "closing",
         "parking"),
    ),
    (Intent.PRODUCT_INQUIRY, ("do you offer", "do you do", "services", "treatment", "product")),
    (Intent.SUPPORT_REQUEST, ("help", "problem", "issue", "not working", "support")),
    (Intent.GREETING, ("hello", "hi", "hey", "good morning", "good afternoon", "good evening")),
)

_SAFETY_KEYWORDS: tuple[tuple[SafetyFlag, tuple[str, ...]], ...] = (
    (SafetyFlag.SELF_HARM, ("kill myself", "suicide", "end my life", "self harm")),
    (SafetyFlag.LEGAL_ADVICE, ("sue", "solicitor", "lawyer", "legal action", "court")),
    (
        SafetyFlag.MEDICAL_ADVICE,
        ("diagnose", "prescription", "antibiotic", "is it infected", "what medication"),
    ),
    (
        SafetyFlag.PROMPT_INJECTION,
        ("ignore previous", "ignore all previous", "system prompt", "reveal your instructions",
         "you are now", "disregard your", "print your prompt", "show your configuration",
         "developer mode", "jailbreak"),
    ),
)


@lru_cache(maxsize=512)
def _phrase_pattern(phrase: str) -> re.Pattern[str]:
    return re.compile(rf"(?<!\w){re.escape(phrase)}(?!\w)")


def _contains_phrase(haystack: str, phrase: str) -> bool:
    """Whole-word phrase match.

    Plain substring matching misfires badly on short keywords: "hi" matches
    "children", "book" matches "bookkeeping". Word boundaries prevent that.
    """
    return bool(_phrase_pattern(phrase).search(haystack))


class DeterministicClient:
    """Rule-based provider: no model, no network, fully reproducible.

    Used as the test default and as the runtime fallback, so the platform keeps
    answering safely when Ollama is down.
    """

    def __init__(self, *, confidence: float = 0.72) -> None:
        self._confidence = confidence

    @property
    def name(self) -> str:
        return "deterministic"

    async def analyse(
        self, *, system_prompt: str, user_prompt: str, timeout: float | None = None
    ) -> IntentResult:
        _ = system_prompt, timeout
        # The prompt builder marks the customer's words; analyse only those so
        # instructions embedded in retrieved context cannot steer the result.
        message = _extract_customer_message(user_prompt).lower()

        flags: list[SafetyFlag] = [
            flag
            for flag, keywords in _SAFETY_KEYWORDS
            if any(_contains_phrase(message, keyword) for keyword in keywords)
        ]

        intent = Intent.UNKNOWN
        confidence = 0.25
        for candidate, keywords in _INTENT_KEYWORDS:
            if any(_contains_phrase(message, keyword) for keyword in keywords):
                intent = candidate
                confidence = self._confidence
                break

        requires_human = bool(flags) or intent in (
            Intent.COMPLAINT,
            Intent.HUMAN_AGENT_REQUEST,
            Intent.REFUND_REQUEST,
        )

        # Ground factual answers in the retrieved context by quoting it
        # verbatim. Because nothing is generated, this provider cannot invent a
        # price or a policy -- it either answers from configuration or defers.
        proposed = ""
        sources: list[str] = []
        if intent in _FACTUAL_INTENTS and not flags:
            chunks = _extract_context_chunks(user_prompt)
            if chunks:
                chunk_id, _title, body = chunks[0]
                proposed = body
                sources = [chunk_id]
            else:
                confidence = min(confidence, 0.3)

        return IntentResult(
            intent=intent,
            confidence=confidence,
            proposed_response=proposed,
            requires_human=requires_human,
            safety_flags=flags,
            sources=sources,
        )

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """No embeddings: retrieval falls back to lexical scoring."""
        _ = texts
        return []

    async def health_check(self) -> bool:
        return True

    async def aclose(self) -> None:
        return None


_CUSTOMER_MARKER = "CUSTOMER MESSAGE:"
_CONTEXT_START = "BUSINESS KNOWLEDGE"
_CONTEXT_END = "CONVERSATION SO FAR"

#: Intents the deterministic provider may answer directly from retrieved text.
_FACTUAL_INTENTS: frozenset[Intent] = frozenset(
    {
        Intent.PRICING_INQUIRY,
        Intent.PRODUCT_INQUIRY,
        Intent.LOCATION_HOURS,
        Intent.AVAILABILITY,
        Intent.SUPPORT_REQUEST,
    }
)

# Chunks are rendered as "[chunk_id] Title\nbody".
_CHUNK_RE = re.compile(r"^\[([^\]\s]+)\]\s*(.*)$")


def _extract_customer_message(user_prompt: str) -> str:
    """Return only the customer's own words from a composed prompt."""
    index = user_prompt.rfind(_CUSTOMER_MARKER)
    if index == -1:
        return user_prompt
    return user_prompt[index + len(_CUSTOMER_MARKER) :].strip()


def _extract_context_chunks(user_prompt: str) -> list[tuple[str, str, str]]:
    """Parse the BUSINESS KNOWLEDGE block into (chunk_id, title, body) triples."""
    start = user_prompt.find(_CONTEXT_START)
    if start == -1:
        return []
    # Skip the header line itself, otherwise it is glued to the first chunk and
    # that chunk -- the highest-scoring hit -- is silently dropped.
    header_end = user_prompt.find("\n", start)
    if header_end == -1:
        return []
    start = header_end + 1
    end = user_prompt.find(_CONTEXT_END, start)
    block = user_prompt[start:end] if end != -1 else user_prompt[start:]

    chunks: list[tuple[str, str, str]] = []
    for section in block.split("\n\n"):
        lines = section.strip().splitlines()
        if not lines:
            continue
        match = _CHUNK_RE.match(lines[0].strip())
        if not match:
            continue
        body = " ".join(line.strip() for line in lines[1:] if line.strip())
        if body:
            chunks.append((match.group(1), match.group(2).strip(), body))
    return chunks


# ==================================================================== factory
def build_llm_client(
    settings: Settings, *, http_client: httpx.AsyncClient | None = None
) -> LLMClientProtocol:
    """Construct the configured provider."""
    if settings.llm_provider == "ollama":
        return OllamaClient(settings, http_client=http_client)
    return DeterministicClient()
