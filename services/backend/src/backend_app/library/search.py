"""Library hybrid search engine — P7.5-A4.

Implements the BM25 → vector → RRF fusion → optional cross-encoder
re-rank pipeline described in
``docs/atlas-new-design/destinations/library-prd.md`` §6.1-§6.3.

Boundary discipline (cross-audit §3.1, §1.3):

* Tenant_id is the **first** filter on every query path here. The
  service layer (:class:`LibraryService.search`) supplies the tenant
  from the verified bearer — never from the request body.
* Project-scoped reads use the canonical
  :func:`backend_app.projects.acl.is_member` predicate. No
  reimplementation of membership lives in this module.
* Embedding generation goes through an injected
  :class:`EmbeddingsClientPort`. The port's only production
  implementation calls ai-backend's
  ``POST /internal/v1/llm/embed`` with ``purpose="library_retrieval"``
  (owned by P7.5-A1). No provider SDK is imported here — the LLM
  provider guard (``tools/check_llm_provider_imports.py``) enforces it.
* Re-rank goes through an injected :class:`RerankClientPort`. When
  P7.5-A1 lands ``POST /internal/v1/llm/rerank`` the production
  adapter wraps it; until then the ``NoopRerankClient`` is the wired
  default and the route documents the fallback.

The store-side BM25 + vector queries are pluggable via
:class:`LibrarySearchIndex` — the in-memory adapter computes BM25 from
the same ``_haystack`` helper used by :meth:`InMemoryLibraryStore.list_items`
so the dev path stays consistent. The Postgres adapter (added once
P7.5-A2's tsvector + ``library_embeddings`` schema lands) implements
the same Protocol with ``ts_rank_cd`` + IVFFLAT cosine queries.

Reciprocal Rank Fusion is library-prd §6.7:

::

    score(d) = sum_i 1 / (k + rank_i(d))

with ``k = 60`` (the canonical default). One implementation — every
caller imports :func:`rrf_fuse`. Pure function, no I/O.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Protocol

from backend_app.library.store import (
    LibraryDatasetRecord,
    LibraryFileRecord,
    LibraryItemRecord,
    LibraryPageRecord,
    LibraryStore,
)
from backend_app.projects.acl import ProjectMembershipPort, is_member


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


RRF_K_DEFAULT = 60
"""Library-prd §6.7 — Reciprocal Rank Fusion smoothing constant."""

BM25_TOP_K_DEFAULT = 50
"""Top-K from the BM25 leg before fusion. Library-prd §6.1."""

VECTOR_TOP_K_DEFAULT = 50
"""Top-K from the vector leg before fusion. Library-prd §6.1."""

RERANK_TOP_N_DEFAULT = 20
"""How many fused hits we hand to the cross-encoder when rerank is on."""

EXCERPT_TARGET_CHARS = 200
"""Target excerpt length around a BM25 match (library-prd §6.2)."""

_ADMIN_ROLES = frozenset({"admin", "owner"})


# ---------------------------------------------------------------------------
# Wire models — match LibrarySearchHit / LibrarySearchResponse from
# packages/api-types/src/library.ts. Snake_case at the wire boundary; the
# route layer is responsible for marshalling these dataclasses to dicts.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SearchHit:
    """Internal hit before / after fusion.

    Carries an unresolved record reference (``record_id`` + ``kind``)
    so the fusion + rerank legs operate on identity, then we hydrate the
    final response with the actual record at the route layer.

    ``matched_in`` is "title" | "content" | "tag" per library-prd §6.2.
    Defaults to "content" — vector hits are content-centric.
    """

    record_id: str
    kind: str  # "file" | "page" | "dataset"
    score: float
    matched_in: str = "content"
    bm25_rank: int | None = None
    vector_rank: int | None = None


@dataclass(frozen=True)
class FusedHit:
    """Hit after RRF fusion. Wire shape mirrors LibrarySearchHit on the
    api-types side once the record is hydrated to an ItemRef."""

    record_id: str
    kind: str
    score: float
    matched_in: str
    bm25_rank: int | None = None
    vector_rank: int | None = None


@dataclass(frozen=True)
class SearchResult:
    """Final result envelope, mapped 1:1 to LibrarySearchResponse on wire."""

    hits: tuple[FusedHit, ...]
    total: int
    took_ms: int
    strategy: str  # "bm25_only" | "vector_only" | "hybrid"


# ---------------------------------------------------------------------------
# Ports (substitution boundary)
# ---------------------------------------------------------------------------


class EmbeddingsClientPort(Protocol):
    """Embed a query for the vector leg.

    Production: HTTP adapter onto ai-backend
    ``POST /internal/v1/llm/embed`` with ``purpose="library_retrieval"``.
    """

    def embed_query(
        self, *, query: str, tenant_id: str
    ) -> tuple[float, ...] | None:  # pragma: no cover - protocol
        """Return the query embedding, or ``None`` if embeddings are not
        configured (forces ``bm25_only`` strategy at the route)."""


class RerankClientPort(Protocol):
    """Cross-encoder re-rank.

    Production: HTTP adapter onto ai-backend
    ``POST /internal/v1/llm/rerank`` (P7.5-A1). The :class:`NoopRerankClient`
    default keeps the route working when the endpoint isn't shipped yet —
    when invoked it returns the hits unchanged so the strategy result is
    still well-formed.
    """

    def rerank(
        self,
        *,
        query: str,
        candidates: tuple[tuple[str, str], ...],  # (record_id, text)
        tenant_id: str,
        top_n: int,
    ) -> tuple[tuple[str, float], ...]:  # pragma: no cover - protocol
        """Return ``(record_id, score)`` sorted descending."""


class NoopEmbeddingsClient:
    """Default — return ``None`` to force ``bm25_only`` strategy.

    Wired by ``backend_app.app.create_app`` when P7.5-A1's
    ``/internal/v1/llm/embed`` endpoint isn't reachable. The fallback is
    intentional: search still works (just BM25-only) until embeddings ship.
    """

    def embed_query(self, *, query: str, tenant_id: str) -> tuple[float, ...] | None:
        return None


class NoopRerankClient:
    """Default — return the hits unchanged.

    Used until P7.5-A1's ``/internal/v1/llm/rerank`` endpoint lands. The
    fused order is the final order; the route still emits a
    ``library.search_reranked`` envelope on the SSE path so the wire
    contract is stable.
    """

    def rerank(
        self,
        *,
        query: str,
        candidates: tuple[tuple[str, str], ...],
        tenant_id: str,
        top_n: int,
    ) -> tuple[tuple[str, float], ...]:
        # Preserve the input ordering with a synthetic descending score.
        kept = candidates[:top_n]
        return tuple((rid, float(len(kept) - idx)) for idx, (rid, _) in enumerate(kept))


# ---------------------------------------------------------------------------
# Index port (BM25 + vector). The in-memory adapter below is the dev /
# test default; a Postgres adapter (added when P7.5-A2 ships) implements
# the same protocol with ts_rank_cd + IVFFLAT queries.
# ---------------------------------------------------------------------------


class LibrarySearchIndex(Protocol):
    """Pluggable BM25 + vector index over Library items.

    The protocol returns *unhydrated* hits — `(record_id, kind, score)`
    triples — so the service layer applies the ACL filter on the cheap
    record-id set before hydrating bodies.
    """

    def bm25_search(
        self,
        *,
        tenant_id: str,
        query: str,
        top_k: int,
        kinds: tuple[str, ...] | None,
        project_ids: tuple[str, ...] | None,
        owner_user_ids: tuple[str, ...] | None,
    ) -> tuple[SearchHit, ...]:  # pragma: no cover - protocol
        ...

    def vector_search(
        self,
        *,
        tenant_id: str,
        query_embedding: tuple[float, ...],
        top_k: int,
        kinds: tuple[str, ...] | None,
        project_ids: tuple[str, ...] | None,
        owner_user_ids: tuple[str, ...] | None,
    ) -> tuple[SearchHit, ...]:  # pragma: no cover - protocol
        ...


@dataclass
class InMemoryLibrarySearchIndex:
    """BM25 over the in-memory store's haystack.

    Dev / test adapter. Mirrors the Postgres tsvector behavior just
    enough for the route + service layer to be exercised end-to-end:

    * Stopword-stripped, lowercased term tokens.
    * BM25-lite scoring (k1=1.5, b=0.75) over the same ``_haystack``
      text the list endpoint uses — title + first 2 KB of markdown +
      tags for pages; name + tags for files; name + description +
      tags for datasets.
    * Vector leg is unsupported in-memory (returns ``()``) so the
      strategy falls back to ``bm25_only`` when this adapter is wired.
    """

    store: LibraryStore
    bm25_k1: float = 1.5
    bm25_b: float = 0.75

    def bm25_search(
        self,
        *,
        tenant_id: str,
        query: str,
        top_k: int,
        kinds: tuple[str, ...] | None,
        project_ids: tuple[str, ...] | None,
        owner_user_ids: tuple[str, ...] | None,
    ) -> tuple[SearchHit, ...]:
        tokens = _tokenize(query)
        if not tokens:
            return ()

        # Pull every tenant-scoped, live row. We rely on the
        # store-level scoping — never trust caller-supplied tenant_id.
        candidates = self._scan_tenant(
            tenant_id=tenant_id,
            kinds=kinds,
            project_ids=project_ids,
            owner_user_ids=owner_user_ids,
        )
        if not candidates:
            return ()

        # Pre-compute corpus stats for BM25.
        corpus: list[tuple[LibraryItemRecord, list[str], str]] = []
        for record in candidates:
            haystack = _record_haystack(record)
            terms = _tokenize(haystack)
            corpus.append((record, terms, haystack))
        avgdl = (
            sum(len(terms) for _, terms, _ in corpus) / max(len(corpus), 1)
            if corpus
            else 0.0
        )

        # Inverse document frequency per query token.
        idf: dict[str, float] = {}
        n_docs = len(corpus)
        for token in tokens:
            df = sum(1 for _, terms, _ in corpus if token in terms)
            # BM25 idf — capped at 0 for terms in every doc.
            idf[token] = max(math.log((n_docs - df + 0.5) / (df + 0.5) + 1.0), 0.0)

        scored: list[tuple[float, LibraryItemRecord, str]] = []
        for record, terms, haystack in corpus:
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
                scored.append((score, record, haystack))

        scored.sort(key=lambda triple: (-triple[0], triple[1].id))
        hits: list[SearchHit] = []
        for rank, (score, record, _) in enumerate(scored[:top_k], start=1):
            hits.append(
                SearchHit(
                    record_id=record.id,
                    kind=record.kind,
                    score=score,
                    matched_in=_matched_in_for(record, tokens),
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
        kinds: tuple[str, ...] | None,
        project_ids: tuple[str, ...] | None,
        owner_user_ids: tuple[str, ...] | None,
    ) -> tuple[SearchHit, ...]:
        # In-memory has no embeddings — return empty so the strategy
        # collapses to ``bm25_only`` at the route. The Postgres adapter
        # (added with P7.5-A2's ``library_embeddings`` table) implements
        # the IVFFLAT cosine query here.
        return ()

    # -- helpers ----------------------------------------------------------

    def _scan_tenant(
        self,
        *,
        tenant_id: str,
        kinds: tuple[str, ...] | None,
        project_ids: tuple[str, ...] | None,
        owner_user_ids: tuple[str, ...] | None,
    ) -> list[LibraryItemRecord]:
        # Re-use the store's ``list_items`` with permissive visibility
        # so the BM25 leg has the full candidate set. The service layer
        # applies the ACL filter post-search (the alternative —
        # pre-filtering by readable_project_ids — is what the Postgres
        # adapter does; here we accept the small overhead).
        rows, _, _ = self.store.list_items(
            tenant_id=tenant_id,
            kinds=kinds,
            project_ids=project_ids,
            owner_user_ids=owner_user_ids,
            limit=1000,
            sort="updated_at:desc",
            # admin=True bypasses visibility — we re-apply ACL in the
            # service layer using the canonical port.
            admin=True,
        )
        return list(rows)


# ---------------------------------------------------------------------------
# Pure RRF fusion (single source of truth)
# ---------------------------------------------------------------------------


def rrf_fuse(
    bm25_hits: Iterable[SearchHit],
    vector_hits: Iterable[SearchHit],
    *,
    k: int = RRF_K_DEFAULT,
) -> tuple[FusedHit, ...]:
    """Reciprocal Rank Fusion — library-prd §6.7.

    ``score(d) = sum_i 1 / (k + rank_i(d))`` over the legs that
    contributed the document. ``k=60`` is the canonical default.

    Single-leg behavior: when only one of the two lists has rows
    (e.g. ``bm25_only`` strategy), the fused score is identical to the
    one-leg reciprocal-rank score; ordering is preserved.

    Pure function — no I/O, no logging, no globals. The function is
    parameter-free beyond ``k`` so callers cannot smuggle hidden state.
    """

    bm25_list = list(bm25_hits)
    vector_list = list(vector_hits)

    # Re-rank input lists so ``bm25_rank`` / ``vector_rank`` reflect
    # the position in their respective leg, regardless of the rank
    # field the caller may have populated.
    bm25_ranked = {hit.record_id: (idx + 1, hit) for idx, hit in enumerate(bm25_list)}
    vector_ranked = {
        hit.record_id: (idx + 1, hit) for idx, hit in enumerate(vector_list)
    }

    union_ids = list(bm25_ranked.keys()) + [
        rid for rid in vector_ranked if rid not in bm25_ranked
    ]

    fused: list[FusedHit] = []
    for record_id in union_ids:
        bm25_entry = bm25_ranked.get(record_id)
        vec_entry = vector_ranked.get(record_id)
        score = 0.0
        kind = ""
        matched_in = "content"
        bm25_rank: int | None = None
        vector_rank: int | None = None
        if bm25_entry is not None:
            rank, hit = bm25_entry
            score += 1.0 / (k + rank)
            kind = hit.kind
            matched_in = hit.matched_in
            bm25_rank = rank
        if vec_entry is not None:
            rank, hit = vec_entry
            score += 1.0 / (k + rank)
            kind = kind or hit.kind
            # BM25 wins the matched_in label when both legs hit — title
            # matches are more visually informative than dense matches.
            if bm25_entry is None:
                matched_in = hit.matched_in
            vector_rank = rank
        fused.append(
            FusedHit(
                record_id=record_id,
                kind=kind,
                score=score,
                matched_in=matched_in,
                bm25_rank=bm25_rank,
                vector_rank=vector_rank,
            )
        )

    fused.sort(key=lambda h: (-h.score, h.record_id))
    return tuple(fused)


# ---------------------------------------------------------------------------
# Excerpt helper — library-prd §6.2. Extract a ~200-char snippet with
# <mark> around the highest-density BM25 match.
# ---------------------------------------------------------------------------


def excerpt(text: str, query: str, *, target_chars: int = EXCERPT_TARGET_CHARS) -> str:
    """Return a ~``target_chars`` snippet of ``text`` around the first
    matching query token, with ``<mark>...</mark>`` wrapping each match.

    Never emits HTML beyond ``<mark>`` — every other ``<`` is escaped
    so the result is safe to drop into a server-rendered snippet body.
    The route layer enforces an HTML-safe response envelope on top of
    this (the SSE wire is JSON, so the FE renders ``<mark>`` server-
    sanitised via DOMPurify per library-prd §6.2).
    """

    if not text or not query:
        return ""
    tokens = _tokenize(query)
    if not tokens:
        return ""

    lowered = text.lower()
    # Find earliest match position.
    best_pos = -1
    for token in tokens:
        pos = lowered.find(token)
        if pos != -1 and (best_pos == -1 or pos < best_pos):
            best_pos = pos
    if best_pos == -1:
        return _truncate(text, target_chars)

    half = target_chars // 2
    start = max(0, best_pos - half)
    end = min(len(text), start + target_chars)
    snippet = text[start:end]
    if start > 0:
        snippet = "…" + snippet
    if end < len(text):
        snippet = snippet + "…"

    # Wrap matches in <mark>. Escape any pre-existing ``<`` first so the
    # output cannot inject HTML beyond the marks we add. Use word-
    # boundary regex per token for case-insensitive matching.
    escaped = snippet.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    for token in tokens:
        pattern = re.compile(r"\b" + re.escape(token) + r"\b", re.IGNORECASE)
        escaped = pattern.sub(lambda m: f"<mark>{m.group(0)}</mark>", escaped)
    return escaped


# ---------------------------------------------------------------------------
# Service-level orchestrator. The route layer composes this with the
# verified identity + ACL port; the function is the single SOT for the
# pipeline so the SSE variant and the one-shot route share behavior.
# ---------------------------------------------------------------------------


@dataclass
class SearchEngine:
    """End-to-end hybrid search.

    Composition: BM25 leg → optional vector leg (when an embedding is
    produced) → RRF fusion → ACL filter → optional re-rank → hit
    hydration. The result envelope's ``strategy`` reflects which legs
    contributed:

    * ``bm25_only`` — vector leg returned 0 (no embedding, or no
      embedded rows match);
    * ``vector_only`` — BM25 returned 0 but vector did;
    * ``hybrid`` — both legs contributed at least one hit.
    """

    store: LibraryStore
    index: LibrarySearchIndex
    membership_port: ProjectMembershipPort
    embeddings: EmbeddingsClientPort = field(default_factory=NoopEmbeddingsClient)
    reranker: RerankClientPort = field(default_factory=NoopRerankClient)

    def search(
        self,
        *,
        tenant_id: str,
        caller_user_id: str,
        caller_roles: tuple[str, ...],
        query: str,
        kinds: tuple[str, ...] | None,
        project_ids: tuple[str, ...] | None,
        owner_user_ids: tuple[str, ...] | None,
        top_k: int,
        rerank: bool,
    ) -> tuple[SearchResult, tuple[LibraryItemRecord, ...]]:
        """Run the pipeline and return ``(envelope, hydrated_records)``.

        Returning records alongside the envelope keeps the route layer
        free of a second store round-trip for ``ref`` / ``excerpt``
        marshalling.
        """

        bm25_hits = self.index.bm25_search(
            tenant_id=tenant_id,
            query=query,
            top_k=BM25_TOP_K_DEFAULT,
            kinds=kinds,
            project_ids=project_ids,
            owner_user_ids=owner_user_ids,
        )

        # Vector leg — only when the embeddings client returns a vector.
        embedding = self.embeddings.embed_query(query=query, tenant_id=tenant_id)
        vector_hits: tuple[SearchHit, ...] = ()
        if embedding is not None:
            vector_hits = self.index.vector_search(
                tenant_id=tenant_id,
                query_embedding=embedding,
                top_k=VECTOR_TOP_K_DEFAULT,
                kinds=kinds,
                project_ids=project_ids,
                owner_user_ids=owner_user_ids,
            )

        fused = rrf_fuse(bm25_hits, vector_hits)

        # ACL filter — drop hits the caller can't read. Cheap because
        # we hold the unhydrated id + kind only.
        readable = self._readable_filter(
            fused,
            tenant_id=tenant_id,
            caller_user_id=caller_user_id,
            caller_roles=caller_roles,
        )

        # Optional re-rank. We only re-rank what the caller asked for
        # plus the rerank top-N — the cross-encoder is the slow leg.
        if rerank and readable:
            candidates = self._candidates_for_rerank(
                readable, top_n=RERANK_TOP_N_DEFAULT
            )
            rerank_pairs = self.reranker.rerank(
                query=query,
                candidates=candidates,
                tenant_id=tenant_id,
                top_n=RERANK_TOP_N_DEFAULT,
            )
            readable = _apply_rerank_scores(readable, rerank_pairs)

        # Final slice + hydrate.
        sliced = readable[:top_k]
        records = self._hydrate(sliced, tenant_id=tenant_id)

        strategy = _strategy_for(bm25_hits=bm25_hits, vector_hits=vector_hits)
        return (
            SearchResult(
                hits=sliced,
                total=len(readable),
                took_ms=0,  # route layer fills this from the wall clock
                strategy=strategy,
            ),
            records,
        )

    # -- internals --------------------------------------------------------

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
            record = self._lookup(tenant_id=tenant_id, record_id=hit.record_id)
            if record is None:
                # Soft-deleted between index + scan, or cross-tenant
                # leak attempted — drop silently (404-not-403).
                continue
            if record.owner_user_id == caller_user_id or admin:
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
            # Non-readers are silently dropped (404-not-403 binding).
        return tuple(kept)

    def _candidates_for_rerank(
        self, hits: tuple[FusedHit, ...], *, top_n: int
    ) -> tuple[tuple[str, str], ...]:
        # Build (record_id, text) pairs from the top-N hits. The text
        # is the haystack we used for BM25 — same input the cross-
        # encoder consumes upstream.
        out: list[tuple[str, str]] = []
        for hit in hits[:top_n]:
            record = self._lookup(tenant_id="", record_id=hit.record_id)
            if record is None:
                continue
            out.append((hit.record_id, _record_haystack(record)))
        return tuple(out)

    def _lookup(self, *, tenant_id: str, record_id: str) -> LibraryItemRecord | None:
        # When tenant_id is "" (called from rerank path after ACL has
        # already established read), we still call into the store but
        # rely on the store-level scoping that already filtered hits.
        # For the safer path, accept the slight extra lookup.
        if record_id.startswith("libfile_"):
            return self.store.get_file(
                tenant_id=tenant_id or self._tenant_for(record_id),
                file_id=record_id,
            )
        if record_id.startswith("libpage_"):
            return self.store.get_page(
                tenant_id=tenant_id or self._tenant_for(record_id),
                page_id=record_id,
            )
        if record_id.startswith("libds_"):
            return self.store.get_dataset(
                tenant_id=tenant_id or self._tenant_for(record_id),
                dataset_id=record_id,
            )
        return None

    def _tenant_for(self, record_id: str) -> str:
        # Fallback only used by the rerank path; production wiring
        # always supplies tenant_id. This is a defense-in-depth helper.
        return ""

    def _hydrate(
        self, hits: tuple[FusedHit, ...], *, tenant_id: str
    ) -> tuple[LibraryItemRecord, ...]:
        rows: list[LibraryItemRecord] = []
        for hit in hits:
            record = self._lookup(tenant_id=tenant_id, record_id=hit.record_id)
            if record is not None:
                rows.append(record)
        return tuple(rows)


# ---------------------------------------------------------------------------
# Private helpers
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


def _record_haystack(record: LibraryItemRecord) -> str:
    """Same content the BM25 leg scores over.

    Pages take title + first 2 KB of markdown + tags; files take
    name + tags; datasets take name + description + tags. Mirrors
    :func:`backend_app.library.store._haystack` so the dev BM25 +
    Postgres ``ts_rank_cd`` produce comparable shapes.
    """

    if isinstance(record, LibraryPageRecord):
        return f"{record.title}\n{record.markdown[:2048]}\n{' '.join(record.tags)}"
    if isinstance(record, LibraryDatasetRecord):
        return f"{record.name}\n{record.description or ''}\n{' '.join(record.tags)}"
    if isinstance(record, LibraryFileRecord):
        return f"{record.name}\n{' '.join(record.tags)}"
    return ""


def _record_title(record: LibraryItemRecord) -> str:
    if isinstance(record, LibraryPageRecord):
        return record.title
    return record.name  # files + datasets


def _matched_in_for(record: LibraryItemRecord, tokens: list[str]) -> str:
    """Heuristic — first match in title beats first match in tags beats body.

    Lets the FE render the small ``Matched in: title`` chip under the
    snippet (library-prd §6.2).
    """

    title = _record_title(record).lower()
    if any(token in title for token in tokens):
        return "title"
    tag_blob = " ".join(record.tags).lower()
    if any(token in tag_blob for token in tokens):
        return "tag"
    return "content"


def _apply_rerank_scores(
    hits: tuple[FusedHit, ...], rerank_pairs: tuple[tuple[str, float], ...]
) -> tuple[FusedHit, ...]:
    """Re-sort ``hits`` by the rerank scores, falling back to the
    original fused order for ids the reranker didn't return."""

    rerank_map = {rid: score for rid, score in rerank_pairs}
    annotated = [
        (rerank_map.get(hit.record_id, float("-inf")), idx, hit)
        for idx, hit in enumerate(hits)
    ]
    annotated.sort(key=lambda triple: (-triple[0], triple[1]))
    out: list[FusedHit] = []
    for score, _, hit in annotated:
        if score == float("-inf"):
            out.append(hit)
        else:
            out.append(
                FusedHit(
                    record_id=hit.record_id,
                    kind=hit.kind,
                    score=score,
                    matched_in=hit.matched_in,
                    bm25_rank=hit.bm25_rank,
                    vector_rank=hit.vector_rank,
                )
            )
    return tuple(out)


def _strategy_for(
    *, bm25_hits: tuple[SearchHit, ...], vector_hits: tuple[SearchHit, ...]
) -> str:
    if bm25_hits and vector_hits:
        return "hybrid"
    if vector_hits and not bm25_hits:
        return "vector_only"
    return "bm25_only"


def _truncate(text: str, target_chars: int) -> str:
    if len(text) <= target_chars:
        return text
    return text[:target_chars] + "…"


def _ref_for(record: LibraryItemRecord) -> dict[str, str]:
    """ItemRef envelope per packages/api-types/src/refs.ts.

    ``library_file`` | ``library_page`` | ``library_dataset`` kinds.
    """

    return {"kind": f"library_{record.kind}", "id": record.id}


def hit_to_wire(
    hit: FusedHit, record: LibraryItemRecord, *, query: str
) -> dict[str, Any]:
    """Marshal a :class:`FusedHit` + its record into the wire shape
    declared in ``packages/api-types/src/library.ts::LibrarySearchHit``.

    The route layer calls this once per hit so the SSE variant and the
    one-shot route emit the same JSON.
    """

    body_text = _record_haystack(record)
    return {
        "ref": _ref_for(record),
        "score": round(hit.score, 6),
        "excerpt": excerpt(body_text, query),
        "matched_in": hit.matched_in,
        "kind": record.kind,
        "title": _record_title(record),
        "project_id": record.project_id,
        "owner_user_id": record.owner_user_id,
        "updated_at": record.updated_at.isoformat(),
    }


__all__ = [
    "BM25_TOP_K_DEFAULT",
    "EmbeddingsClientPort",
    "EXCERPT_TARGET_CHARS",
    "FusedHit",
    "InMemoryLibrarySearchIndex",
    "LibrarySearchIndex",
    "NoopEmbeddingsClient",
    "NoopRerankClient",
    "RERANK_TOP_N_DEFAULT",
    "RRF_K_DEFAULT",
    "RerankClientPort",
    "SearchEngine",
    "SearchHit",
    "SearchResult",
    "VECTOR_TOP_K_DEFAULT",
    "excerpt",
    "hit_to_wire",
    "rrf_fuse",
]
