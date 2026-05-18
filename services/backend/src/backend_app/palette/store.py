"""Pluggable storage for the denormalized palette index.

Two-implementation contract per the cross-audit §3.1 substitution rule:

* :class:`InMemoryPaletteStore` — dev / test default. BM25-lite over the
  rendered ``title + body + tags`` haystack with a stable insertion order
  tiebreak.
* Postgres adapter (future) — implements the same Protocol against the
  ``palette_index`` table; uses ``ts_rank_cd`` for BM25 and IVFFLAT
  cosine for vector recall.

Reads are tenant-scoped at the **first** filter. The service layer
(:class:`backend_app.palette.service.PaletteService`) supplies the
tenant from the verified bearer — never from the request body.

Wire types for the rows live nowhere — these are server-internal records
that the service hydrates into ``PaletteHit`` shapes from
``packages/api-types/src/palette.ts``.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


class EntityKind:
    """Canonical entity_kind strings. One per palette-eligible destination.

    Mirrored on the FE via the ``icon_hint`` mapping in
    ``packages/chat-surface/src/shell/CommandPalette.tsx`` (when that
    file lands). The strings are wire-stable; renaming is a breaking
    change.
    """

    CHAT = "chat"
    PROJECT = "project"
    LIBRARY_ITEM = "library_item"
    AGENT = "agent"
    TOOL = "tool"
    CONNECTOR = "connector"
    PERSON = "person"
    MEMORY = "memory"
    ROUTINE = "routine"

    ALL: tuple[str, ...] = (
        CHAT,
        PROJECT,
        LIBRARY_ITEM,
        AGENT,
        TOOL,
        CONNECTOR,
        PERSON,
        MEMORY,
        ROUTINE,
    )


_BM25_K1 = 1.5
_BM25_B = 0.75
_STOPWORDS = frozenset({"the", "a", "an", "and", "or", "of", "for", "to", "in"})
_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")


# ---------------------------------------------------------------------------
# Row + hit shapes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PaletteEntry:
    """One denormalized row from the palette_index table.

    The dispatcher writes these; the search service reads them. Each
    destination owns the projection from its source-of-truth row to
    this shape (refresh.py wires that mapping).
    """

    tenant_id: str
    entity_kind: str
    entity_id: str
    title: str
    body: str = ""
    tags: tuple[str, ...] = ()
    route: str = ""
    owner_user_id: str | None = None
    project_id: str | None = None
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True)
class PaletteSearchHit:
    """An entry plus its BM25 score, returned in score-descending order."""

    entry: PaletteEntry
    score: float


# ---------------------------------------------------------------------------
# Port
# ---------------------------------------------------------------------------


class PaletteStorePort(Protocol):
    """Adapter contract for the palette index.

    The store is the **only** write surface for ``palette_index``; the
    canonical dispatcher (:mod:`backend_app.palette.refresh`) is the
    only caller of ``upsert_entry`` / ``delete_entry`` in production
    (destinations call the dispatcher, not the store).
    """

    def upsert_entry(self, entry: PaletteEntry) -> None:  # pragma: no cover - protocol
        """Insert-or-replace a row keyed on (tenant_id, entity_kind, entity_id)."""

    def delete_entry(
        self, *, tenant_id: str, entity_kind: str, entity_id: str
    ) -> None:  # pragma: no cover - protocol
        """Remove a row by composite key. No-op if absent."""

    def bulk_query(
        self,
        *,
        tenant_id: str,
        query: str,
        entity_kinds: tuple[str, ...] | None,
        top_k: int,
    ) -> tuple[PaletteSearchHit, ...]:  # pragma: no cover - protocol
        """Return up to ``top_k`` BM25 hits scoped to ``tenant_id``.

        ``entity_kinds=None`` means "all kinds"; otherwise only rows
        whose ``entity_kind`` is in the tuple are considered.
        """


# ---------------------------------------------------------------------------
# In-memory adapter
# ---------------------------------------------------------------------------


@dataclass
class InMemoryPaletteStore:
    """Dev / test adapter. BM25-lite over the rendered haystack."""

    _entries: dict[tuple[str, str, str], PaletteEntry] = field(default_factory=dict)

    def upsert_entry(self, entry: PaletteEntry) -> None:
        key = (entry.tenant_id, entry.entity_kind, entry.entity_id)
        self._entries[key] = entry

    def delete_entry(self, *, tenant_id: str, entity_kind: str, entity_id: str) -> None:
        self._entries.pop((tenant_id, entity_kind, entity_id), None)

    def get_entry(
        self, *, tenant_id: str, entity_kind: str, entity_id: str
    ) -> PaletteEntry | None:
        """Test / debug accessor. Not part of the production read path."""
        return self._entries.get((tenant_id, entity_kind, entity_id))

    def all_entries(self, *, tenant_id: str) -> tuple[PaletteEntry, ...]:
        """Test accessor — tenant-scoped list."""
        return tuple(
            entry for entry in self._entries.values() if entry.tenant_id == tenant_id
        )

    def bulk_query(
        self,
        *,
        tenant_id: str,
        query: str,
        entity_kinds: tuple[str, ...] | None,
        top_k: int,
    ) -> tuple[PaletteSearchHit, ...]:
        tokens = self._tokenize(query)
        candidates = self._tenant_candidates(
            tenant_id=tenant_id, entity_kinds=entity_kinds
        )
        if not candidates:
            return ()

        if not tokens:
            # No query → recency-ordered.
            sorted_by_recency = sorted(
                candidates, key=lambda entry: entry.updated_at, reverse=True
            )
            return tuple(
                PaletteSearchHit(entry=entry, score=0.0)
                for entry in sorted_by_recency[:top_k]
            )

        corpus = [
            (entry, self._tokenize(self._haystack(entry))) for entry in candidates
        ]
        n_docs = len(corpus)
        avgdl = (
            sum(len(terms) for _, terms in corpus) / max(n_docs, 1) if corpus else 0.0
        )

        idf: dict[str, float] = {}
        for token in tokens:
            df = sum(1 for _, terms in corpus if token in terms)
            idf[token] = max(math.log((n_docs - df + 0.5) / (df + 0.5) + 1.0), 0.0)

        scored: list[PaletteSearchHit] = []
        for entry, terms in corpus:
            doc_len = len(terms)
            score = 0.0
            for token in tokens:
                tf = terms.count(token)
                if tf == 0:
                    continue
                if avgdl <= 0.0:
                    norm = 0.0
                else:
                    norm = 1 - _BM25_B + _BM25_B * (doc_len / avgdl)
                tf_term = (tf * (_BM25_K1 + 1.0)) / (tf + _BM25_K1 * norm)
                score += idf[token] * tf_term
            if score > 0:
                scored.append(PaletteSearchHit(entry=entry, score=score))

        scored.sort(key=lambda hit: (-hit.score, hit.entry.entity_id))
        return tuple(scored[:top_k])

    # -- internals ----------------------------------------------------------

    def _tenant_candidates(
        self, *, tenant_id: str, entity_kinds: tuple[str, ...] | None
    ) -> list[PaletteEntry]:
        kinds_filter = frozenset(entity_kinds) if entity_kinds else None
        result: list[PaletteEntry] = []
        for entry in self._entries.values():
            if entry.tenant_id != tenant_id:
                continue
            if kinds_filter is not None and entry.entity_kind not in kinds_filter:
                continue
            result.append(entry)
        return result

    @staticmethod
    def _haystack(entry: PaletteEntry) -> str:
        """Combined indexable text for the entry.

        Mirrors the ``tsv`` column on the Postgres adapter: title carries
        the strongest weight (concatenated twice as a cheap boost), then
        body, then tags.
        """
        return " ".join(
            [
                entry.title,
                entry.title,  # title boost
                entry.body or "",
                " ".join(entry.tags or ()),
            ]
        )

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        if not text:
            return []
        return [
            token.lower()
            for token in _TOKEN_RE.findall(text)
            if token.lower() not in _STOPWORDS and len(token) > 1
        ]
