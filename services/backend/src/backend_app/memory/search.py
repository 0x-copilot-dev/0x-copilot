"""Memory search — wraps Library's hybrid search primitives.

Sub-PRD §4.2 binding: ``GET /v1/memory/search`` reuses Library's
hybrid (BM25 + vector) engine; memory rows ride ``library_embeddings``
under ``target_kind="memory"`` (sub-PRD §5.1).

We do NOT reimplement RRF or BM25 here — both come from
:mod:`backend_app.library.search`:

* :func:`backend_app.library.search.rrf_fuse` — pure RRF (k=60).
* :class:`backend_app.library.search.SearchHit` / ``FusedHit`` —
  shared dataclasses for the fusion pipeline.
* :class:`backend_app.library.search.EmbeddingsClientPort` — production
  adapter calls ai-backend ``/internal/v1/llm/embed`` with
  ``Purpose.MEMORY_RETRIEVAL`` (the enum value lands in sibling
  P12-A5; the port consumes the string regardless).

What lives here:

* :class:`MemorySearchIndex` Protocol + ``InMemoryMemorySearchIndex``
  that scores memory rows by tokenized BM25-lite over the haystack
  ``title + body + tags``. The Postgres adapter ships when the
  deployment composer wires it (same shape as
  :class:`InMemoryLibrarySearchIndex`).
* :class:`MemorySearchEngine` — composes the index + RRF + ACL trim.
* :class:`MemoryEmbeddingsClient` — purpose tag is
  ``"memory_retrieval"``; defaults to the no-op adapter so BM25-only
  is the fallback path.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Protocol

from backend_app.library.search import (
    EmbeddingsClientPort,
    FusedHit,
    NoopEmbeddingsClient,
    RRF_K_DEFAULT,
    SearchHit,
    rrf_fuse,
)
from backend_app.memory.store import MemoryItemRecord, MemoryStore
from backend_app.projects.acl import ProjectMembershipPort, is_member


_ADMIN_ROLES = frozenset({"admin", "owner"})

BM25_TOP_K_DEFAULT = 50
VECTOR_TOP_K_DEFAULT = 50

# Memory purpose tag — passed through to the embeddings adapter so the
# upstream LLM provider gate (TU-1) sees the correct intent (sub-PRD
# §1.5 / sibling P12-A5).
MEMORY_RETRIEVAL_PURPOSE = "memory_retrieval"


@dataclass(frozen=True)
class MemorySearchResultHit:
    """One scored, hydrated memory hit."""

    record: MemoryItemRecord
    score: float
    snippet: str


@dataclass(frozen=True)
class MemorySearchEnvelope:
    """Wire envelope mirroring api-types MemorySearchResponse."""

    hits: tuple[MemorySearchResultHit, ...]
    took_ms: int


# ---------------------------------------------------------------------------
# Ports
# ---------------------------------------------------------------------------


class MemorySearchIndex(Protocol):
    """BM25 + (optional) vector retrieval over memory rows."""

    def bm25_search(
        self,
        *,
        tenant_id: str,
        query: str,
        top_k: int,
    ) -> tuple[SearchHit, ...]: ...  # pragma: no cover - protocol

    def vector_search(
        self,
        *,
        tenant_id: str,
        query_embedding: tuple[float, ...],
        top_k: int,
    ) -> tuple[SearchHit, ...]: ...  # pragma: no cover - protocol


# ---------------------------------------------------------------------------
# In-memory adapter
# ---------------------------------------------------------------------------


_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "has",
        "have",
        "he",
        "in",
        "is",
        "it",
        "its",
        "of",
        "on",
        "or",
        "that",
        "the",
        "to",
        "was",
        "were",
        "will",
        "with",
    }
)
_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")


def _tokenize(text: str) -> list[str]:
    if not text:
        return []
    return [
        token.lower()
        for token in _TOKEN_RE.findall(text)
        if token.lower() not in _STOPWORDS
    ]


def _memory_haystack(record: MemoryItemRecord) -> str:
    """Same content the BM25 leg scores over.

    Title + body (first 2 KB) + tags. Mirrors
    :func:`backend_app.library.search._record_haystack` so the in-memory
    + Postgres adapters score comparable text. The 2 KB cap keeps the
    BM25 leg cheap on long memory bodies.
    """

    return f"{record.title}\n{record.body[:2048]}\n{' '.join(record.tags)}"


@dataclass
class InMemoryMemorySearchIndex:
    """BM25-lite over the in-memory memory store.

    Dev / test adapter. Vector leg returns ``()`` (no embeddings in
    memory); production swaps a Postgres adapter that issues an IVFFLAT
    cosine query against ``library_embeddings WHERE
    target_kind='memory'``.
    """

    store: MemoryStore
    bm25_k1: float = 1.5
    bm25_b: float = 0.75

    def bm25_search(
        self,
        *,
        tenant_id: str,
        query: str,
        top_k: int,
    ) -> tuple[SearchHit, ...]:
        tokens = _tokenize(query)
        if not tokens:
            return ()
        # Pull every tenant-scoped, live memory row. Permissive scan;
        # the engine layer applies the ACL filter on the cheap id set.
        rows, _ = self.store.list_items(
            tenant_id=tenant_id,
            owner_user_id=None,
            scopes=None,
            kinds=None,
            project_ids=None,
            tags=None,
            q=None,
            cursor=None,
            limit=1000,
            sort="updated_at:desc",
        )
        if not rows:
            return ()

        corpus: list[tuple[MemoryItemRecord, list[str]]] = []
        for record in rows:
            terms = _tokenize(_memory_haystack(record))
            corpus.append((record, terms))
        n_docs = len(corpus)
        avgdl = sum(len(terms) for _, terms in corpus) / max(n_docs, 1)
        idf: dict[str, float] = {}
        for token in tokens:
            df = sum(1 for _, terms in corpus if token in terms)
            idf[token] = max(math.log((n_docs - df + 0.5) / (df + 0.5) + 1.0), 0.0)
        scored: list[tuple[float, MemoryItemRecord]] = []
        for record, terms in corpus:
            score = 0.0
            doc_len = len(terms)
            for token in tokens:
                tf = terms.count(token)
                if tf == 0:
                    continue
                denom = tf + self.bm25_k1 * (
                    1 - self.bm25_b + self.bm25_b * (doc_len / (avgdl or 1.0))
                )
                score += idf[token] * (tf * (self.bm25_k1 + 1)) / denom
            if score > 0:
                scored.append((score, record))
        scored.sort(key=lambda triple: (-triple[0], triple[1].id))
        hits: list[SearchHit] = []
        for rank, (score, record) in enumerate(scored[:top_k], start=1):
            hits.append(
                SearchHit(
                    record_id=record.id,
                    kind="memory",
                    score=score,
                    matched_in="content",
                    bm25_rank=rank,
                )
            )
        return tuple(hits)

    def vector_search(
        self,
        *,
        tenant_id: str,
        query_embedding: tuple[float, ...],
        top_k: int,
    ) -> tuple[SearchHit, ...]:
        # In-memory has no vectors — return empty so the strategy
        # collapses to BM25-only at the engine layer.
        return ()


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


@dataclass
class MemorySearchEngine:
    """End-to-end hybrid search over memory rows.

    Composition: BM25 → optional vector → RRF fusion → ACL trim →
    hydrate to records. The embeddings port carries the
    ``Purpose.MEMORY_RETRIEVAL`` tag at the HTTP boundary (the adapter
    in the production composer wires it).
    """

    store: MemoryStore
    index: MemorySearchIndex
    membership_port: ProjectMembershipPort
    embeddings: EmbeddingsClientPort = field(default_factory=NoopEmbeddingsClient)

    def search(
        self,
        *,
        tenant_id: str,
        caller_user_id: str,
        caller_roles: tuple[str, ...],
        query: str,
        top_k: int = 20,
    ) -> MemorySearchEnvelope:
        bm25_hits = self.index.bm25_search(
            tenant_id=tenant_id,
            query=query,
            top_k=BM25_TOP_K_DEFAULT,
        )
        embedding = self.embeddings.embed_query(query=query, tenant_id=tenant_id)
        vector_hits: tuple[SearchHit, ...] = ()
        if embedding is not None:
            vector_hits = self.index.vector_search(
                tenant_id=tenant_id,
                query_embedding=embedding,
                top_k=VECTOR_TOP_K_DEFAULT,
            )
        fused = rrf_fuse(bm25_hits, vector_hits, k=RRF_K_DEFAULT)
        readable = self._readable_filter(
            fused,
            tenant_id=tenant_id,
            caller_user_id=caller_user_id,
            caller_roles=caller_roles,
        )
        sliced = readable[:top_k]
        hits = self._hydrate(sliced, tenant_id=tenant_id, query=query)
        return MemorySearchEnvelope(hits=hits, took_ms=0)

    def _readable_filter(
        self,
        fused: tuple[FusedHit, ...],
        *,
        tenant_id: str,
        caller_user_id: str,
        caller_roles: tuple[str, ...],
    ) -> tuple[FusedHit, ...]:
        admin = any(r in _ADMIN_ROLES for r in caller_roles)
        kept: list[FusedHit] = []
        for hit in fused:
            record = self.store.get_item(tenant_id=tenant_id, item_id=hit.record_id)
            if record is None:
                continue
            if admin or record.owner_user_id == caller_user_id:
                kept.append(hit)
                continue
            if record.scope == "workspace":
                kept.append(hit)
                continue
            if record.project_id is not None and is_member(
                self.membership_port,
                tenant_id=tenant_id,
                project_id=record.project_id,
                user_id=caller_user_id,
            ):
                kept.append(hit)
                continue
        return tuple(kept)

    def _hydrate(
        self,
        hits: tuple[FusedHit, ...],
        *,
        tenant_id: str,
        query: str,
    ) -> tuple[MemorySearchResultHit, ...]:
        out: list[MemorySearchResultHit] = []
        for hit in hits:
            record = self.store.get_item(tenant_id=tenant_id, item_id=hit.record_id)
            if record is None:
                continue
            snippet = _build_snippet(record.body, query)
            out.append(
                MemorySearchResultHit(
                    record=record,
                    score=round(hit.score, 6),
                    snippet=snippet,
                )
            )
        return tuple(out)


def _build_snippet(body: str, query: str, *, target_chars: int = 200) -> str:
    """Return a short ~200-char snippet around the first query match.

    Reuses Library's :func:`backend_app.library.search.excerpt` shape —
    `<mark>` highlights, HTML-escaped surroundings. Importing the helper
    directly keeps the snippet format identical across destinations.
    """

    from backend_app.library.search import excerpt  # local import to keep cycle-free

    return excerpt(body or "", query, target_chars=target_chars)


__all__ = [
    "BM25_TOP_K_DEFAULT",
    "InMemoryMemorySearchIndex",
    "MEMORY_RETRIEVAL_PURPOSE",
    "MemoryEmbeddingsClient",
    "MemorySearchEngine",
    "MemorySearchEnvelope",
    "MemorySearchIndex",
    "MemorySearchResultHit",
    "VECTOR_TOP_K_DEFAULT",
]


# Alias the no-op for backward-compat callers that want a typed memory-aware
# embeddings client; the production composer can subclass this and override
# ``embed_query`` to call ``/internal/v1/llm/embed`` with the memory purpose.
MemoryEmbeddingsClient = NoopEmbeddingsClient
