"""Library embeddings — chunking + extraction + insert/query (P7.5-A2).

One implementation reused by every kind (files / pages / datasets):

* :func:`chunk_text` — deterministic word-based chunker. No LLM call,
  no tiktoken: tokens are approximated as whitespace-separated word
  groups, which is good enough for the ~800-token / 100-token-overlap
  spec (library-prd §6.3). Deterministic output is load-bearing so the
  indexer can compare ``(target, ordinal)`` keys across runs.

* :func:`extract_text` — per-kind text extractor. Pages and datasets
  resolve their text from the metadata row (markdown body / per-row
  rendering); files defer to the caller-provided bytes + mime. The
  worker passes a None blob when the per-mime extractor isn't shipped
  yet (P7.5-A2 lands the text/plain + markdown extractors only; PDF /
  Office extractors ship in a follow-up so the embedding module can
  go in first).

* :class:`EmbeddingRow` + :func:`insert_embeddings` —
  bulk insert with idempotent ON CONFLICT semantics, mirroring the
  ``library_embeddings_target_unique`` constraint declared in
  ``schema.sql``. The store layer owns the SQL — this module owns the
  payload + insert / query shape.

Substitutability rule: this module never imports an LLM provider SDK.
The indexer worker (``library_indexer.py``) is the boundary that calls
``POST /internal/v1/llm/embed`` on ai-backend; this module only
transports the embedding vectors that come back.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Iterable, Literal, Protocol
from uuid import uuid4

from backend_app.library.store import (
    LibraryDatasetRecord,
    LibraryFileRecord,
    LibraryItemRecord,
    LibraryPageRecord,
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Per library-prd §6.3 + §6.5 — 800-token chunks with 100-token overlap.
# We approximate tokens as whitespace-separated words; the OpenAI
# tokenizer is roughly 0.75 tokens per word for English, so ~800
# "tokens" ≈ ~600 words. Choose word counts that keep the chunk well
# under the model's 8192-token context budget while staying close to
# the spec.
DEFAULT_CHUNK_SIZE_WORDS = 600
DEFAULT_CHUNK_OVERLAP_WORDS = 75

# Chunks longer than 4 KB get truncated before storage (the
# ``library_embeddings.chunk_text`` column has a 4 KB CHECK).
_CHUNK_TEXT_MAX_BYTES = 4096

# Default embedding model for Phase 7.5 (library-prd §6.5).
DEFAULT_EMBEDDING_MODEL_ID = "text-embedding-3-small"
DEFAULT_EMBEDDING_DIMENSIONS = 1536

TargetKindLiteral = Literal["file", "page", "dataset"]


# ---------------------------------------------------------------------------
# Chunk + payload records
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Chunk:
    """One chunk of extracted text + its position in the source.

    Frozen so the indexer can hash + compare chunks across runs.
    ``ordinal`` is monotonic per (target_kind, target_id) and starts at 0.
    """

    ordinal: int
    text: str


@dataclass(frozen=True)
class EmbeddingRow:
    """One row destined for ``library_embeddings``.

    Construction is two-phase: the chunker produces the (ordinal, text)
    pair; the indexer fills in the vector after the round-trip to
    ai-backend's ``/internal/v1/llm/embed`` endpoint.
    """

    tenant_id: str
    target_kind: TargetKindLiteral
    target_id: str
    chunk_ordinal: int
    chunk_text: str
    embedding: tuple[float, ...]
    model_id: str

    @property
    def row_id(self) -> str:
        """Stable id derived from the natural key.

        Idempotency on the SQL side is via the UNIQUE constraint; the
        derived id keeps the in-memory adapter's dict keys collision-free
        for the same natural key. Falls through ``ON CONFLICT`` in
        Postgres.
        """

        digest = hashlib.sha256(
            "|".join(
                [
                    self.tenant_id,
                    self.target_kind,
                    self.target_id,
                    str(self.chunk_ordinal),
                    self.model_id,
                ]
            ).encode("utf-8")
        ).hexdigest()
        return f"libemb_{digest[:32]}"


# ---------------------------------------------------------------------------
# Chunker
# ---------------------------------------------------------------------------


def chunk_text(
    text: str,
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE_WORDS,
    overlap: int = DEFAULT_CHUNK_OVERLAP_WORDS,
) -> list[Chunk]:
    """Split ``text`` into ~``chunk_size``-word chunks with ``overlap``
    overlap.

    Pure tokenize-by-words. Deterministic — same input + parameters
    always produces the same chunks. Whitespace runs collapse to a
    single space; the original line breaks are not preserved (the
    embedding model doesn't care).

    Returns an empty list for empty/whitespace-only input — the indexer
    treats that as "nothing to embed; mark indexed with content_hash
    of empty string".
    """

    if chunk_size <= 0:
        raise ValueError("chunk_size must be > 0")
    if overlap < 0:
        raise ValueError("overlap must be >= 0")
    if overlap >= chunk_size:
        # Overlap must leave forward progress; otherwise the loop
        # cannot terminate. Mirrors the spec's 800/100 ratio.
        raise ValueError("overlap must be < chunk_size")

    words = text.split()
    if not words:
        return []

    chunks: list[Chunk] = []
    step = chunk_size - overlap
    ordinal = 0
    start = 0
    while start < len(words):
        end = min(start + chunk_size, len(words))
        slice_text = " ".join(words[start:end])
        # Cap chunk byte size to fit the column CHECK. Truncation is
        # word-aware so we don't slice in the middle of a multibyte
        # codepoint.
        if len(slice_text.encode("utf-8")) > _CHUNK_TEXT_MAX_BYTES:
            slice_text = _truncate_utf8(slice_text, _CHUNK_TEXT_MAX_BYTES)
        chunks.append(Chunk(ordinal=ordinal, text=slice_text))
        ordinal += 1
        if end == len(words):
            break
        start += step
    return chunks


def _truncate_utf8(text: str, max_bytes: int) -> str:
    """Truncate ``text`` so its UTF-8 encoding fits in ``max_bytes``.

    Walks back from the byte boundary to avoid splitting a codepoint.
    """

    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    cut = max_bytes
    while cut > 0 and (encoded[cut] & 0xC0) == 0x80:
        cut -= 1
    return encoded[:cut].decode("utf-8", errors="ignore")


# ---------------------------------------------------------------------------
# Text extraction
# ---------------------------------------------------------------------------


def extract_text(
    *,
    record: LibraryItemRecord,
    blob: bytes | None = None,
    mime: str | None = None,
) -> str:
    """Extract the indexable text for ``record``.

    Per-kind dispatch:

    * **Page** — markdown body verbatim. Title is prepended so isolated
      chunks remain query-able (library-prd §6.3).
    * **Dataset** — schema-summary rendering. The full row enumeration
      lands when the Parquet reader ships (out of scope here); the
      Phase 7.5 indexer embeds the schema + description so search hits
      the dataset on column/field queries.
    * **File** — when ``blob`` is None we fall back to ``name + tags``
      (matches the conservative Phase 7 baseline per §6.4: PDFs without
      OCR get name/tag-only retrieval). When a text-flavoured blob is
      supplied (``text/plain``, ``text/markdown``, ``text/csv``,
      ``application/json``) we decode + index. PDF / Office extractors
      ship in a follow-up.
    """

    if isinstance(record, LibraryPageRecord):
        # Prepend the title so a chunk taken from the middle of the body
        # still surfaces under title-matching queries.
        return f"{record.title}\n\n{record.markdown}".strip()

    if isinstance(record, LibraryDatasetRecord):
        return _render_dataset_text(record)

    if isinstance(record, LibraryFileRecord):
        return _render_file_text(record, blob=blob, mime=mime or record.mime)

    raise TypeError(f"unsupported record type: {type(record).__name__}")


def _render_dataset_text(record: LibraryDatasetRecord) -> str:
    """Dataset → schema + description summary.

    Per-row embedding requires the Parquet reader; until that ships we
    embed a single "dataset summary" chunk so retrieval can still hit
    the dataset by name / column / description (library-prd §6.3).
    """

    parts: list[str] = [record.name]
    if record.description:
        parts.append(record.description)
    if record.tags:
        parts.append(" ".join(record.tags))
    column_summary = _format_dataset_columns(record.columns_schema)
    if column_summary:
        parts.append(column_summary)
    return "\n".join(part for part in parts if part).strip()


def _format_dataset_columns(columns: list[dict[str, Any]]) -> str:
    if not columns:
        return ""
    formatted: list[str] = []
    for col in columns:
        name = col.get("name")
        col_type = col.get("type")
        if not isinstance(name, str) or not name:
            continue
        if isinstance(col_type, str) and col_type:
            formatted.append(f"{name}: {col_type}")
        else:
            formatted.append(name)
    if not formatted:
        return ""
    return "Columns: " + " · ".join(formatted)


def _render_file_text(
    record: LibraryFileRecord,
    *,
    blob: bytes | None,
    mime: str,
) -> str:
    """Resolve the indexable text for a file row."""

    name_part = record.name
    tag_part = " ".join(record.tags) if record.tags else ""
    metadata_baseline = f"{name_part}\n{tag_part}".strip()

    if blob is None:
        # No bytes available — embed name + tags. Matches the
        # conservative §6.4 fallback for image-only PDFs.
        return metadata_baseline

    text_payload = _decode_text_blob(blob, mime=mime)
    if not text_payload:
        return metadata_baseline
    return f"{metadata_baseline}\n\n{text_payload}".strip()


_TEXT_MIMES: frozenset[str] = frozenset(
    {
        "text/plain",
        "text/markdown",
        "text/csv",
        "application/json",
    }
)


def _decode_text_blob(blob: bytes, *, mime: str) -> str:
    """Decode a text-flavoured blob.

    Unknown / binary mimes return empty — the caller falls back to
    name/tag-only indexing. Future PDF / Office extractors slot in
    here as the mime list grows.
    """

    if mime not in _TEXT_MIMES:
        return ""
    try:
        return blob.decode("utf-8")
    except UnicodeDecodeError:
        # Fallback: replace undecodable bytes rather than crashing the
        # indexer on a malformed upload.
        return blob.decode("utf-8", errors="replace")


def compute_content_hash(text: str) -> str:
    """Stable hash for the "did the indexable text change?" check.

    Used by the indexer's idempotency gate: when the new hash matches
    the persisted ``library_index_jobs.content_hash``, the row is
    already embedded with the same content and we skip the LLM call.
    """

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Embeddings store contract + in-memory adapter
# ---------------------------------------------------------------------------


class EmbeddingsStore(Protocol):
    """Insert / cascade / query contract for ``library_embeddings``.

    The Postgres adapter implements this against the schema in
    ``schema.sql``; the in-memory adapter below is the dev / test
    default.
    """

    def insert_embeddings(self, rows: Iterable[EmbeddingRow]) -> int: ...

    def delete_embeddings_for_target(
        self,
        *,
        tenant_id: str,
        target_kind: TargetKindLiteral,
        target_id: str,
        model_id: str | None = None,
    ) -> int: ...

    def list_embeddings_for_target(
        self,
        *,
        tenant_id: str,
        target_kind: TargetKindLiteral,
        target_id: str,
    ) -> tuple[EmbeddingRow, ...]: ...


class InMemoryEmbeddingsStore:
    """Dict-backed embeddings store (dev / test default).

    Idempotent on ``(tenant_id, target_kind, target_id, chunk_ordinal,
    model_id)`` — mirrors the UNIQUE constraint on the Postgres side.
    """

    def __init__(self) -> None:
        # Key is the natural composite; value is the row.
        self._rows: dict[tuple[str, str, str, int, str], EmbeddingRow] = {}

    def insert_embeddings(self, rows: Iterable[EmbeddingRow]) -> int:
        count = 0
        for row in rows:
            key = (
                row.tenant_id,
                row.target_kind,
                row.target_id,
                row.chunk_ordinal,
                row.model_id,
            )
            self._rows[key] = row
            count += 1
        return count

    def delete_embeddings_for_target(
        self,
        *,
        tenant_id: str,
        target_kind: TargetKindLiteral,
        target_id: str,
        model_id: str | None = None,
    ) -> int:
        to_delete = [
            key
            for key in self._rows
            if key[0] == tenant_id
            and key[1] == target_kind
            and key[2] == target_id
            and (model_id is None or key[4] == model_id)
        ]
        for key in to_delete:
            del self._rows[key]
        return len(to_delete)

    def list_embeddings_for_target(
        self,
        *,
        tenant_id: str,
        target_kind: TargetKindLiteral,
        target_id: str,
    ) -> tuple[EmbeddingRow, ...]:
        return tuple(
            sorted(
                (
                    row
                    for (t, k, tid, _, _), row in self._rows.items()
                    if t == tenant_id and k == target_kind and tid == target_id
                ),
                key=lambda r: (r.chunk_ordinal, r.model_id),
            )
        )

    # Test-helper — full snapshot for assertions.
    def snapshot(self) -> tuple[EmbeddingRow, ...]:
        return tuple(self._rows.values())


def insert_embeddings(
    store: EmbeddingsStore,
    rows: Iterable[EmbeddingRow],
) -> int:
    """Bulk-insert helper. Idempotent on the natural key (Postgres) or
    dict key (in-memory)."""

    return store.insert_embeddings(rows)


def build_embedding_rows(
    *,
    tenant_id: str,
    target_kind: TargetKindLiteral,
    target_id: str,
    chunks: list[Chunk],
    vectors: list[tuple[float, ...]],
    model_id: str,
) -> list[EmbeddingRow]:
    """Pair chunks with their freshly-computed vectors.

    Raises ``ValueError`` if the chunk count != vector count — that's a
    bug in the worker's stitching, never a recoverable error. The
    indexer should mark the job ``failed`` in that case.
    """

    if len(chunks) != len(vectors):
        raise ValueError(
            f"chunk/vector count mismatch: {len(chunks)} chunks vs {len(vectors)} "
            "vectors"
        )
    return [
        EmbeddingRow(
            tenant_id=tenant_id,
            target_kind=target_kind,
            target_id=target_id,
            chunk_ordinal=chunk.ordinal,
            chunk_text=chunk.text,
            embedding=tuple(vector),
            model_id=model_id,
        )
        for chunk, vector in zip(chunks, vectors, strict=True)
    ]


def _embedding_id() -> str:
    """Public for the Postgres adapter; the in-memory store uses the
    derived ``EmbeddingRow.row_id`` as its dict key."""

    return f"libemb_{uuid4().hex}"


__all__ = [
    "Chunk",
    "DEFAULT_CHUNK_OVERLAP_WORDS",
    "DEFAULT_CHUNK_SIZE_WORDS",
    "DEFAULT_EMBEDDING_DIMENSIONS",
    "DEFAULT_EMBEDDING_MODEL_ID",
    "EmbeddingRow",
    "EmbeddingsStore",
    "InMemoryEmbeddingsStore",
    "TargetKindLiteral",
    "build_embedding_rows",
    "chunk_text",
    "compute_content_hash",
    "extract_text",
    "insert_embeddings",
]
