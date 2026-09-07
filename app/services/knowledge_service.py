"""Knowledge indexing and retrieval.

Design choice and trade-off
---------------------------
Retrieval is **BM25 lexical by default, with optional Ollama embeddings blended
in**. BM25 is implemented in-process (no service, no index server) because the
corpus for a single business is small -- typically tens to low hundreds of
chunks drawn from ``business.yaml`` plus a handful of markdown files.

* BM25 is exact, deterministic and instantly rebuildable, which makes answers
  reproducible and test assertions stable.
* Its weakness is vocabulary mismatch ("how much" vs "pricing"), so when
  ``features.embeddings_enabled`` is on and Ollama is reachable, cosine
  similarity from ``nomic-embed-text`` is blended in at a fixed weight.
* Embeddings are strictly additive: if Ollama is down the blend degrades to
  pure BM25 rather than failing.

A dedicated vector database would add operational weight without measurable
benefit at this corpus size. The :class:`KnowledgeIndex` interface leaves that
door open.

Isolation: every chunk carries ``business_id`` and the index is built per
business, so one deployment can never retrieve another's knowledge.
"""

from __future__ import annotations

import json
import math
import re
import time
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from app.core.logging import get_logger
from app.domain.config_schema import BusinessConfig
from app.domain.schemas import KnowledgeChunk, RetrievalHit, RetrievalResult

logger = get_logger(__name__)

# BM25 parameters: standard defaults, adequate for short business documents.
BM25_K1 = 1.5
BM25_B = 0.75

#: Weight given to semantic similarity when embeddings are available.
SEMANTIC_WEIGHT = 0.4
LEXICAL_WEIGHT = 0.6

MAX_FILE_BYTES = 512 * 1024
MAX_CHUNK_CHARS = 900
MIN_CHUNK_CHARS = 40

_TOKEN_RE = re.compile(r"[a-z0-9]+")

_STOPWORDS = frozenset(
    """a an and are as at be but by do does for from had has have how i if in is it its me my
    of on or our so than that the their then there these they this to was we were what when
    where which who why will with you your can could would should""".split()
)


def tokenize(text: str) -> list[str]:
    """Lowercase, strip accents, split to alphanumeric tokens, drop stopwords."""
    decomposed = unicodedata.normalize("NFKD", text.lower())
    folded = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return [token for token in _TOKEN_RE.findall(folded) if token not in _STOPWORDS]


# ==================================================================== chunking
def _chunk_text(text: str, *, max_chars: int = MAX_CHUNK_CHARS) -> list[str]:
    """Split text on paragraph boundaries, packing up to ``max_chars``."""
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
    chunks: list[str] = []
    buffer = ""
    for paragraph in paragraphs:
        if len(paragraph) > max_chars:
            if buffer:
                chunks.append(buffer)
                buffer = ""
            # Hard-split an oversized paragraph on sentence boundaries.
            sentence_buffer = ""
            for sentence in re.split(r"(?<=[.!?])\s+", paragraph):
                if len(sentence_buffer) + len(sentence) + 1 > max_chars:
                    if sentence_buffer:
                        chunks.append(sentence_buffer.strip())
                    sentence_buffer = sentence[:max_chars]
                else:
                    sentence_buffer = f"{sentence_buffer} {sentence}".strip()
            if sentence_buffer:
                chunks.append(sentence_buffer.strip())
        elif len(buffer) + len(paragraph) + 2 > max_chars:
            if buffer:
                chunks.append(buffer)
            buffer = paragraph
        else:
            buffer = f"{buffer}\n\n{paragraph}".strip()
    if buffer:
        chunks.append(buffer)
    return [chunk for chunk in chunks if len(chunk) >= MIN_CHUNK_CHARS or not chunks]


def chunks_from_business_config(business: BusinessConfig) -> list[KnowledgeChunk]:
    """Turn the validated business config into citable knowledge chunks.

    Chunk ids are stable and human-readable (``svc:checkup``, ``faq:parking``)
    so a citation in a log or debug payload immediately identifies its source.
    """
    chunks: list[KnowledgeChunk] = []

    def add(chunk_id: str, title: str, text: str, kind: str) -> None:
        if text and text.strip():
            chunks.append(
                KnowledgeChunk(
                    chunk_id=chunk_id,
                    business_id=business.business_id,
                    source="business.yaml",
                    title=title,
                    text=text.strip(),
                    origin="business_config",
                    metadata={"kind": kind},
                )
            )

    add(
        "about",
        f"About {business.name}",
        f"{business.name} is a {business.industry} business. {business.description}"
        + (f" Website: {business.website}." if business.website else ""),
        "about",
    )

    address = business.address
    location_parts = [address.line1, address.line2, address.city, address.postal_code]
    add(
        "contact",
        "Contact and address",
        "Address: "
        + ", ".join(part for part in location_parts if part)
        + f", {address.country}. Phone: {business.contact.phone_display}. "
        + f"Email: {business.contact.email}.",
        "contact",
    )

    hours_lines: list[str] = []
    for day in (
        "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
    ):
        day_hours = getattr(business.business_hours, day)
        if day_hours.closed:
            hours_lines.append(f"{day.capitalize()}: closed")
        else:
            hours_lines.append(f"{day.capitalize()}: {day_hours.open} to {day_hours.close}")
    add(
        "hours",
        "Opening hours",
        f"Opening hours ({business.timezone}):\n" + "\n".join(hours_lines),
        "hours",
    )

    for service in business.services:
        price_text = (
            f"Price: {service.price}."
            if service.price
            else "Price varies and must be quoted after an assessment."
        )
        duration = (
            f" Duration: {service.duration_minutes} minutes."
            if service.duration_minutes
            else ""
        )
        availability = "" if service.available else " This service is currently unavailable."
        add(
            f"svc:{service.id}",
            f"Service: {service.name}",
            f"{service.name}. {service.description} {price_text}{duration}{availability}",
            "service",
        )

    if business.pricing_notes:
        add("pricing_notes", "Pricing notes", business.pricing_notes, "pricing")

    for faq in business.faqs:
        add(f"faq:{faq.id}", f"FAQ: {faq.question}", f"{faq.question}\n{faq.answer}", "faq")

    for policy in business.policies:
        add(
            f"policy:{policy.id}",
            f"Policy: {policy.title}",
            f"{policy.title}\n{policy.body}",
            "policy",
        )

    if business.delivery.offers_delivery:
        delivery_bits = [
            f"Methods: {', '.join(business.delivery.methods)}."
            if business.delivery.methods
            else "",
            f"Lead time: {business.delivery.lead_time}." if business.delivery.lead_time else "",
            f"Fees: {business.delivery.fees}." if business.delivery.fees else "",
            business.delivery.notes or "",
        ]
        add("delivery", "Delivery", " ".join(part for part in delivery_bits if part), "delivery")

    if business.appointments.enabled:
        add(
            "appointments",
            "Appointment rules",
            f"Appointments require at least {business.appointments.min_notice_hours} hours notice "
            f"and can be booked up to {business.appointments.max_advance_days} days ahead. "
            f"Cancellations need {business.appointments.cancellation_notice_hours} hours notice. "
            + (business.appointments.notes or ""),
            "appointments",
        )

    if business.refunds.enabled:
        conditions = " ".join(f"- {item}" for item in business.refunds.conditions)
        add(
            "refunds",
            "Refund policy",
            f"Refunds may be requested within {business.refunds.window_days} days. "
            f"{conditions} {business.refunds.process or ''}",
            "refunds",
        )

    if business.supported_locations:
        add(
            "locations",
            "Areas served",
            "Areas served: " + ", ".join(business.supported_locations) + ".",
            "locations",
        )

    if business.emergency.enabled and business.emergency.message:
        add("emergency", "Emergency information", business.emergency.message, "emergency")

    return chunks


def chunks_from_files(
    business: BusinessConfig, root: Path
) -> list[KnowledgeChunk]:
    """Load and chunk knowledge files listed in the business config."""
    chunks: list[KnowledgeChunk] = []
    extensions = {ext.lower() for ext in business.knowledge.file_extensions}
    # Resolve once: callers may pass a relative root, but rglob yields absolute
    # paths, and relative_to() requires both sides to be in the same form.
    root = root.resolve()

    for directory in business.knowledge.directories:
        base = (root / directory).resolve()
        # Containment check: a config path must not escape the project root.
        if not str(base).startswith(str(root)):
            logger.warning("knowledge_path_rejected", directory=directory)
            continue
        if not base.is_dir():
            continue

        for path in sorted(base.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in extensions:
                continue
            # README files are operator instructions, not business facts.
            if path.stem.lower() == "readme" or path.name.startswith("."):
                continue
            try:
                if path.stat().st_size > MAX_FILE_BYTES:
                    logger.warning("knowledge_file_too_large", file=path.name)
                    continue
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                logger.warning(
                    "knowledge_file_unreadable", file=path.name, error=type(exc).__name__
                )
                continue

            relative = path.relative_to(root).as_posix()
            for index, chunk_text in enumerate(_chunk_text(text)):
                heading = chunk_text.splitlines()[0].lstrip("# ").strip()[:120]
                chunks.append(
                    KnowledgeChunk(
                        chunk_id=f"file:{path.stem}:{index}",
                        business_id=business.business_id,
                        source=relative,
                        title=heading or path.stem,
                        text=chunk_text,
                        origin="file",
                        metadata={"file": relative, "part": str(index)},
                    )
                )
    return chunks


# ===================================================================== index
@dataclass
class KnowledgeIndex:
    """In-memory BM25 index with optional dense vectors."""

    business_id: str
    chunks: list[KnowledgeChunk] = field(default_factory=list)
    _doc_tokens: list[list[str]] = field(default_factory=list, repr=False)
    _doc_freqs: list[Counter[str]] = field(default_factory=list, repr=False)
    _df: Counter[str] = field(default_factory=Counter, repr=False)
    _avg_len: float = 0.0
    _vectors: list[list[float]] = field(default_factory=list, repr=False)

    def build(self) -> None:
        """Compute term statistics. Deterministic for a given chunk list."""
        self._doc_tokens = [tokenize(f"{chunk.title} {chunk.text}") for chunk in self.chunks]
        self._doc_freqs = [Counter(tokens) for tokens in self._doc_tokens]
        self._df = Counter()
        for tokens in self._doc_tokens:
            self._df.update(set(tokens))
        total = sum(len(tokens) for tokens in self._doc_tokens)
        self._avg_len = (total / len(self._doc_tokens)) if self._doc_tokens else 0.0

    def attach_vectors(self, vectors: list[list[float]]) -> None:
        """Attach dense vectors aligned to ``chunks`` (ignored if mismatched)."""
        self._vectors = vectors if len(vectors) == len(self.chunks) else []

    @property
    def has_vectors(self) -> bool:
        return bool(self._vectors)

    @property
    def size(self) -> int:
        return len(self.chunks)

    def _bm25(self, query_tokens: list[str]) -> list[float]:
        total_docs = len(self.chunks)
        scores = [0.0] * total_docs
        if not total_docs or not query_tokens:
            return scores
        for term in set(query_tokens):
            doc_freq = self._df.get(term, 0)
            if doc_freq == 0:
                continue
            idf = math.log(1 + (total_docs - doc_freq + 0.5) / (doc_freq + 0.5))
            for index, freqs in enumerate(self._doc_freqs):
                term_freq = freqs.get(term, 0)
                if not term_freq:
                    continue
                doc_len = len(self._doc_tokens[index]) or 1
                denominator = term_freq + BM25_K1 * (
                    1 - BM25_B + BM25_B * doc_len / (self._avg_len or 1)
                )
                scores[index] += idf * (term_freq * (BM25_K1 + 1)) / denominator
        return scores

    @staticmethod
    def _cosine(left: list[float], right: list[float]) -> float:
        if not left or not right or len(left) != len(right):
            return 0.0
        dot = sum(a * b for a, b in zip(left, right, strict=True))
        norm_left = math.sqrt(sum(a * a for a in left))
        norm_right = math.sqrt(sum(b * b for b in right))
        if norm_left == 0 or norm_right == 0:
            return 0.0
        return dot / (norm_left * norm_right)

    def search(
        self,
        query: str,
        *,
        top_k: int = 4,
        min_score: float = 0.15,
        query_vector: list[float] | None = None,
    ) -> RetrievalResult:
        """Rank chunks for a query, blending lexical and semantic scores."""
        started = time.perf_counter()
        tokens = tokenize(query)
        lexical = self._bm25(tokens)

        # Normalise BM25 to 0..1 so it can be blended with cosine similarity.
        peak = max(lexical, default=0.0)
        lexical_norm = [(value / peak) if peak > 0 else 0.0 for value in lexical]

        use_semantic = bool(query_vector) and self.has_vectors
        semantic = (
            [self._cosine(query_vector or [], vector) for vector in self._vectors]
            if use_semantic
            else [0.0] * len(self.chunks)
        )

        hits: list[RetrievalHit] = []
        for index, chunk in enumerate(self.chunks):
            combined = (
                LEXICAL_WEIGHT * lexical_norm[index] + SEMANTIC_WEIGHT * semantic[index]
                if use_semantic
                else lexical_norm[index]
            )
            if combined >= min_score:
                hits.append(
                    RetrievalHit(
                        chunk=chunk,
                        score=round(combined, 6),
                        lexical_score=round(lexical_norm[index], 6),
                        semantic_score=round(semantic[index], 6),
                    )
                )

        # Sort by score, then chunk_id, so equal scores order deterministically.
        hits.sort(key=lambda hit: (-hit.score, hit.chunk.chunk_id))
        return RetrievalResult(
            query=query,
            hits=hits[:top_k],
            strategy="hybrid_bm25_embeddings" if use_semantic else "bm25",
            embeddings_used=use_semantic,
            took_ms=round((time.perf_counter() - started) * 1000, 3),
        )


# =================================================================== service
class KnowledgeService:
    """Builds, caches and queries the per-business knowledge index."""

    def __init__(
        self,
        business: BusinessConfig,
        *,
        project_root: Path,
        llm_client: object | None = None,
        embeddings_enabled: bool = True,
    ) -> None:
        self._business = business
        self._root = project_root
        self._llm = llm_client
        self._embeddings_enabled = embeddings_enabled
        self._index: KnowledgeIndex | None = None

    @property
    def index(self) -> KnowledgeIndex:
        if self._index is None:
            self._index = self.build_index()
        return self._index

    def build_index(self) -> KnowledgeIndex:
        """(Re)build the lexical index from config and files."""
        chunks: list[KnowledgeChunk] = []
        if self._business.knowledge.include_business_config:
            chunks.extend(chunks_from_business_config(self._business))
        chunks.extend(chunks_from_files(self._business, self._root))

        index = KnowledgeIndex(business_id=self._business.business_id, chunks=chunks)
        index.build()
        logger.info(
            "knowledge_index_built",
            business_id=self._business.business_id,
            chunks=index.size,
        )
        self._index = index
        return index

    async def build_index_async(self) -> KnowledgeIndex:
        """Build the index and attach embeddings when available."""
        index = self.build_index()
        if not self._embeddings_enabled or self._llm is None:
            return index
        embed = getattr(self._llm, "embed", None)
        if embed is None:
            return index
        texts = [f"{chunk.title}\n{chunk.text}" for chunk in index.chunks]
        vectors = await embed(texts)
        if vectors:
            index.attach_vectors(vectors)
            logger.info("knowledge_vectors_attached", count=len(vectors))
        else:
            logger.info("knowledge_vectors_unavailable_lexical_only")
        return index

    async def search(self, query: str, *, top_k: int = 4) -> RetrievalResult:
        """Retrieve grounding context for a customer question."""
        index = self.index
        query_vector: list[float] | None = None
        if self._embeddings_enabled and index.has_vectors and self._llm is not None:
            embed = getattr(self._llm, "embed", None)
            if embed is not None:
                vectors = await embed([query])
                query_vector = vectors[0] if vectors else None
        return index.search(query, top_k=top_k, query_vector=query_vector)

    def export_index(self, path: Path) -> None:
        """Write index metadata for debugging a bad retrieval."""
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "business_id": self._business.business_id,
            "chunk_count": self.index.size,
            "has_vectors": self.index.has_vectors,
            "chunks": [
                {
                    "chunk_id": chunk.chunk_id,
                    "title": chunk.title,
                    "source": chunk.source,
                    "origin": chunk.origin,
                    "chars": len(chunk.text),
                }
                for chunk in self.index.chunks
            ],
        }
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
