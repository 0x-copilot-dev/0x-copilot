"""Deterministic, authorization-neutral lexical ranking over a scoped catalog."""

from __future__ import annotations

import hashlib
import re

from agent_runtime.capabilities.discovery.contracts import (
    CapabilityCandidate,
    CapabilityCatalog,
    CapabilityIndexEntry,
    CapabilitySearchRequest,
    CapabilitySearchResult,
)

_TOKEN_PATTERN = re.compile(r"[^\W_]+", flags=re.UNICODE)


class DeterministicLexicalRanker:
    """Rank only catalog members; search can never broaden authorization."""

    def search(
        self,
        catalog: CapabilityCatalog,
        request: CapabilitySearchRequest,
    ) -> CapabilitySearchResult:
        """Return stable top-K candidates in ``O(N * Q)`` time."""

        normalized_query = self._normalized_text(request.query)
        query_terms = frozenset(self._tokens(normalized_query))
        scored: list[tuple[int, CapabilityIndexEntry, tuple[str, ...]]] = []
        scanned_count = 0

        for entry in catalog.entries:
            if not self._passes_filters(entry, request):
                continue
            scanned_count += 1
            score, matched_terms = self._score(
                entry,
                normalized_query=normalized_query,
                query_terms=query_terms,
            )
            if score > 0:
                scored.append((score, entry, matched_terms))

        scored.sort(
            key=lambda item: (
                -item[0],
                item[1].stable_name,
                item[1].capability_ref,
            )
        )
        candidates = tuple(
            CapabilityCandidate(
                capability_ref=entry.capability_ref,
                stable_name=entry.stable_name,
                score=score,
                matched_terms=matched_terms,
                source=entry.source,
                effect_class=entry.effect_class,
                approval_cue=entry.approval_cue,
            )
            for score, entry, matched_terms in scored[: request.limit]
        )
        query_digest = hashlib.sha256(normalized_query.encode("utf-8")).hexdigest()
        return CapabilitySearchResult(
            catalog_id=catalog.revision.catalog_id,
            catalog_revision=catalog.revision.revision,
            query_digest=f"sha256:{query_digest}",
            scanned_count=scanned_count,
            candidates=candidates,
        )

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
                matched.add(term)
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
