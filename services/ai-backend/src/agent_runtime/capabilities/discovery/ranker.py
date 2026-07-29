"""Deterministic, authorization-neutral lexical ranking over a scoped catalog.

Ranking is a *projection* over records the caller is already authorized for.
It can reorder and truncate; it can never add a member, relax a filter, or
disclose a capability the catalog does not already contain.

Selection meets the §17 budget of ``O(NQ + R log K)``: every admitted entry is
scored once against the ``Q`` query terms (``O(NQ)``), and the ``R`` entries
that scored above zero flow through :class:`BoundedTopKSelector`, which holds
at most ``K`` candidates and does ``O(log K)`` work per offer instead of
sorting all ``R``.  Nothing here materializes a full descriptor schema, so
ranking never duplicates schema bytes into the prompt.
"""

from __future__ import annotations

import hashlib
import heapq
import re
from collections.abc import Iterable
from dataclasses import dataclass

from agent_runtime.capabilities.discovery.contracts import (
    CapabilityCandidate,
    CapabilityCatalog,
    CapabilityIndexEntry,
    CapabilitySearchRequest,
    CapabilitySearchResult,
    RankedCapabilitySelection,
)

_TOKEN_PATTERN = re.compile(r"[^\W_]+", flags=re.UNICODE)
_MATCHED_TERM_MAX_CHARS = 96


@dataclass(frozen=True, slots=True)
class _RankedSlot:
    """Heap slot ordered weakest-first so a bounded min-heap evicts the weakest.

    ``rank_key`` is a total order — ``capability_ref`` is unique within a
    selection — so equal-scoring candidates can never reorder between runs.
    The comparison is reversed because :mod:`heapq` is a min-heap and the
    element that must be evicted first is the *weakest* retained candidate.
    """

    rank_key: tuple[int, str, str]
    candidate: CapabilityCandidate

    def __lt__(self, other: "_RankedSlot") -> bool:
        return other.rank_key < self.rank_key


class BoundedTopKSelector:
    """Keep only the strongest ``limit`` candidates in ``O(K)`` space.

    ``offer`` is ``O(log K)``, so admitting ``R`` scored entries costs
    ``O(R log K)`` rather than the ``O(R log R)`` of a full sort, and peak
    retention never exceeds ``limit`` regardless of how many entries matched.
    """

    class Messages:
        """Safe public messages for bounded-selection misuse."""

        NON_POSITIVE_LIMIT = "top-k selection requires a positive limit"

    def __init__(self, limit: int) -> None:
        if limit <= 0:
            raise ValueError(self.Messages.NON_POSITIVE_LIMIT)
        self._limit = int(limit)
        self._heap: list[_RankedSlot] = []

    @property
    def limit(self) -> int:
        """Return the configured retention bound."""

        return self._limit

    @property
    def retained(self) -> int:
        """Return how many candidates are held; never more than ``limit``."""

        return len(self._heap)

    def offer(self, candidate: CapabilityCandidate) -> None:
        """Admit one candidate without ever growing the retained set past ``K``."""

        slot = _RankedSlot(
            rank_key=self.rank_key(candidate),
            candidate=candidate,
        )
        if len(self._heap) < self._limit:
            heapq.heappush(self._heap, slot)
            return
        if self._heap[0] < slot:
            heapq.heapreplace(self._heap, slot)

    def ordered(self) -> tuple[CapabilityCandidate, ...]:
        """Return retained candidates strongest-first in ``O(K log K)``."""

        return tuple(
            slot.candidate
            for slot in sorted(self._heap, key=lambda slot: slot.rank_key)
        )

    @staticmethod
    def rank_key(candidate: CapabilityCandidate) -> tuple[int, str, str]:
        """Return the total order used for both selection and tie-breaking."""

        return (
            -candidate.score,
            candidate.stable_name,
            candidate.capability_ref,
        )


class DeterministicLexicalRanker:
    """Rank only catalog members; search can never broaden authorization."""

    def search(
        self,
        catalog: CapabilityCatalog,
        request: CapabilitySearchRequest,
    ) -> CapabilitySearchResult:
        """Return stable top-K candidates in ``O(NQ + R log K)`` time."""

        selection = self.rank_entries(catalog.entries, request)
        return CapabilitySearchResult(
            catalog_id=catalog.revision.catalog_id,
            catalog_revision=catalog.revision.revision,
            query_digest=self.query_digest(request.query),
            scanned_count=selection.scanned_count,
            candidates=selection.candidates,
        )

    def rank_entries(
        self,
        entries: Iterable[CapabilityIndexEntry],
        request: CapabilitySearchRequest,
    ) -> RankedCapabilitySelection:
        """Score an already-authorized entry sequence into a bounded selection.

        The caller owns authorization.  Every supplied entry must already be a
        record the subject may see: this method only filters, scores, and
        truncates, so it can shrink the input set but never extend it.
        """

        normalized_query = self._normalized_text(request.query)
        query_terms = frozenset(self._tokens(normalized_query))
        selector = BoundedTopKSelector(request.limit)
        scanned_count = 0

        for entry in entries:
            if not self._passes_filters(entry, request):
                continue
            scanned_count += 1
            score, matched_terms = self._score(
                entry,
                normalized_query=normalized_query,
                query_terms=query_terms,
            )
            if score > 0:
                selector.offer(
                    CapabilityCandidate(
                        capability_ref=entry.capability_ref,
                        stable_name=entry.stable_name,
                        score=score,
                        matched_terms=matched_terms,
                        source=entry.source,
                        effect_class=entry.effect_class,
                        approval_cue=entry.approval_cue,
                    )
                )
        return RankedCapabilitySelection(
            scanned_count=scanned_count,
            candidates=selector.ordered(),
        )

    def merge(
        self,
        selections: Iterable[RankedCapabilitySelection],
        *,
        limit: int,
    ) -> RankedCapabilitySelection:
        """Merge bounded selections into one bounded selection.

        Merging is deterministic and narrowing: it reuses the identical rank
        key, keeps at most ``limit`` candidates, and drops a repeated
        ``capability_ref`` instead of listing it twice.
        """

        selector = BoundedTopKSelector(limit)
        seen_refs: set[str] = set()
        scanned_count = 0
        for selection in selections:
            scanned_count += selection.scanned_count
            for candidate in selection.candidates:
                if candidate.capability_ref in seen_refs:
                    continue
                seen_refs.add(candidate.capability_ref)
                selector.offer(candidate)
        return RankedCapabilitySelection(
            scanned_count=scanned_count,
            candidates=selector.ordered(),
        )

    @staticmethod
    def query_digest(query: str) -> str:
        """Return the content-free digest recorded instead of the raw query."""

        normalized = DeterministicLexicalRanker._normalized_text(query)
        digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        return f"sha256:{digest}"

    def _score(
        self,
        entry: CapabilityIndexEntry,
        *,
        normalized_query: str,
        query_terms: frozenset[str],
    ) -> tuple[int, tuple[str, ...]]:
        stable_text = self._normalized_text(entry.stable_name)
        display_text = self._normalized_text(entry.display_name)
        description_tokens = frozenset(self._tokens(entry.concise_description))
        stable_tokens = frozenset(self._tokens(entry.stable_name))
        display_tokens = frozenset(self._tokens(entry.display_name))
        intent_tokens = frozenset(
            token for value in entry.intent_tags for token in self._tokens(value)
        )
        parameter_tokens = frozenset(
            token for value in entry.parameter_names for token in self._tokens(value)
        )
        connector_tokens = frozenset(self._tokens(entry.connector_label))

        score = 0
        if normalized_query == stable_text:
            score += 1_000
        elif normalized_query == display_text:
            score += 800

        matched: set[str] = set()
        for term in query_terms:
            term_score = 0
            if term in stable_tokens:
                term_score += 140
            if term in display_tokens:
                term_score += 100
            if term in intent_tokens:
                term_score += 120
            if term in parameter_tokens:
                term_score += 70
            if term in description_tokens:
                term_score += 25
            if term in connector_tokens:
                term_score += 15
            if term_score:
                # ``query`` is bounded, but an individual unicode token can
                # still be much larger than useful model-facing diagnostics.
                # Ranking uses the complete term; only the explanatory hint is
                # bounded.
                matched.add(term[:_MATCHED_TERM_MAX_CHARS])
                score += term_score
        return score, tuple(sorted(matched))

    @staticmethod
    def _passes_filters(
        entry: CapabilityIndexEntry,
        request: CapabilitySearchRequest,
    ) -> bool:
        filters = request.filters
        if filters.sources and entry.source not in filters.sources:
            return False
        if filters.effect_classes and entry.effect_class not in filters.effect_classes:
            return False
        return not filters.connector_labels or (
            entry.connector_label.casefold() in filters.connector_labels
        )

    @staticmethod
    def _normalized_text(value: str) -> str:
        return " ".join(DeterministicLexicalRanker._tokens(value))

    @staticmethod
    def _tokens(value: str) -> tuple[str, ...]:
        return tuple(token.casefold() for token in _TOKEN_PATTERN.findall(value))


__all__ = (
    "BoundedTopKSelector",
    "DeterministicLexicalRanker",
    # Re-exported from ``contracts``, where the selection contract now lives,
    # so existing ``from ...ranker import RankedCapabilitySelection`` call
    # sites keep resolving to the one definition.
    "RankedCapabilitySelection",
)
