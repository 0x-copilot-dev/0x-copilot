from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from agent_runtime.capabilities.discovery import (
    ApprovalCue,
    AuthorizedCatalogBuilder,
    CapabilityCandidate,
    CapabilityCatalogScope,
    CapabilitySearchFilters,
    CapabilitySearchRequest,
    CapabilitySource,
    CatalogEffectClass,
    DeterministicLexicalRanker,
)
from agent_runtime.capabilities.discovery.ranker import (
    BoundedTopKSelector,
    RankedCapabilitySelection,
)
from agent_runtime.capabilities.tools.cards import ToolCard, ToolRiskLevel
from agent_runtime.execution.contracts import AgentRuntimeContext, ModelConfig

_NOW = datetime(2026, 7, 27, 12, tzinfo=UTC)


def _context() -> AgentRuntimeContext:
    return AgentRuntimeContext(
        user_id="user_1",
        org_id="org_1",
        roles={"member"},
        permission_scopes={"docs:read"},
        connector_scopes={"drive": frozenset({"docs:read"})},
        model_profile=ModelConfig(
            provider="openai",
            model_name="gpt-test",
            max_input_tokens=32_000,
            timeout_seconds=30,
            temperature=0,
        ),
        run_id="run_ranker",
    )


def _card(
    name: str,
    *,
    description: str,
    tags: frozenset[str] = frozenset(),
) -> ToolCard:
    return ToolCard(
        name=name,
        display_name=name.replace("_", " ").title(),
        short_description=description,
        connector="drive",
        tags=tags,
        required_scopes={"docs:read"},
        risk_level=ToolRiskLevel.LOW,
        load_cost=1,
    )


def _catalog(cards: tuple[ToolCard, ...]):
    context = _context()
    return AuthorizedCatalogBuilder(reference_key=b"x" * 32).build(
        context=context,
        scope=CapabilityCatalogScope.from_context(
            context,
            profile_id="default",
            policy_revision="policy_1",
            connector_scope_revision="scope_1",
        ),
        tool_cards=cards,
        expires_at=_NOW + timedelta(minutes=15),
    )


class TestDeterministicLexicalRanker:
    def test_exact_name_beats_intent_and_description_matches(self) -> None:
        catalog = _catalog(
            (
                _card(
                    "document_search",
                    description="Search all company documents.",
                ),
                _card(
                    "lookup_records",
                    description="Look up records by key.",
                    tags=frozenset({"document_search"}),
                ),
                _card(
                    "browse_files",
                    description="Document search across files.",
                ),
            )
        )

        result = DeterministicLexicalRanker().search(
            catalog,
            CapabilitySearchRequest(query="document_search"),
        )

        assert [candidate.stable_name for candidate in result.candidates] == [
            "document_search",
            "lookup_records",
            "browse_files",
        ]
        assert result.candidates[0].score > result.candidates[1].score
        assert result.query_digest.startswith("sha256:")
        assert "document_search" not in result.query_digest

    def test_result_order_is_stable_for_equivalent_catalog_order(self) -> None:
        cards = (
            _card("zeta_search", description="Search docs."),
            _card("alpha_search", description="Search docs."),
        )

        first = DeterministicLexicalRanker().search(
            _catalog(cards),
            CapabilitySearchRequest(query="search"),
        )
        second = DeterministicLexicalRanker().search(
            _catalog(tuple(reversed(cards))),
            CapabilitySearchRequest(query="search"),
        )

        assert first == second
        assert [candidate.stable_name for candidate in first.candidates] == [
            "alpha_search",
            "zeta_search",
        ]

    def test_filters_only_narrow_catalog_membership(self) -> None:
        catalog = _catalog(
            (
                _card("drive_search", description="Search drive."),
                _card("drive_lookup", description="Search records."),
            )
        )

        result = DeterministicLexicalRanker().search(
            catalog,
            CapabilitySearchRequest(
                query="search",
                filters=CapabilitySearchFilters(
                    sources={CapabilitySource.MCP_SERVER},
                ),
            ),
        )

        assert result.scanned_count == 0
        assert result.candidates == ()

    def test_zero_score_entries_are_not_returned(self) -> None:
        catalog = _catalog(
            (
                _card("drive_search", description="Search drive."),
                _card("calendar_list", description="List calendar events."),
            )
        )

        result = DeterministicLexicalRanker().search(
            catalog,
            CapabilitySearchRequest(query="calendar"),
        )

        assert [candidate.stable_name for candidate in result.candidates] == [
            "calendar_list"
        ]

    def test_search_is_bounded_to_ten_candidates_at_one_thousand_entries(
        self,
    ) -> None:
        catalog = _catalog(
            tuple(
                _card(
                    f"search_{index:04d}",
                    description="Search one compact record.",
                )
                for index in range(1_000)
            )
        )

        result = DeterministicLexicalRanker().search(
            catalog,
            CapabilitySearchRequest(query="search", limit=10),
        )

        assert result.scanned_count == 1_000
        assert len(result.candidates) == 10
        assert result.candidates[0].stable_name == "search_0000"

    def test_request_rejects_unbounded_limit(self) -> None:
        with pytest.raises(ValueError):
            CapabilitySearchRequest(query="search", limit=11)

    def test_invalid_filter_fails_instead_of_broadening_search(self) -> None:
        with pytest.raises(ValueError, match="non-empty strings"):
            CapabilitySearchFilters(connector_labels={"drive", ""})


class BoundedSelectionMixin:
    """Candidate factories for the ``O(R log K)`` selection tests."""

    @staticmethod
    def candidate(index: int, *, score: int) -> CapabilityCandidate:
        return CapabilityCandidate(
            capability_ref=f"cap_{index:032x}",
            stable_name=f"tool_{index:04d}",
            score=score,
            source=CapabilitySource.TOOL_CARD,
            effect_class=CatalogEffectClass.UNKNOWN,
            approval_cue=ApprovalCue.UNKNOWN,
        )

    def tied_candidates(self, count: int) -> tuple[CapabilityCandidate, ...]:
        """Return candidates that all score identically, so only ties break."""

        return tuple(self.candidate(index, score=7) for index in range(count))

    def varied_candidates(self, count: int) -> tuple[CapabilityCandidate, ...]:
        """Return deterministic scores with many collisions across the range."""

        return tuple(
            self.candidate(index, score=(index * 37) % 11 + 1) for index in range(count)
        )


class TestBoundedTopKSelector(BoundedSelectionMixin):
    def test_retention_never_exceeds_the_limit(self) -> None:
        selector = BoundedTopKSelector(5)
        peak = 0

        for candidate in self.varied_candidates(1_000):
            selector.offer(candidate)
            peak = max(peak, selector.retained)

        assert peak == 5
        assert selector.retained == 5

    def test_bounded_selection_equals_a_full_sort(self) -> None:
        candidates = self.varied_candidates(200)
        selector = BoundedTopKSelector(7)

        for candidate in candidates:
            selector.offer(candidate)

        assert selector.ordered() == tuple(
            sorted(candidates, key=BoundedTopKSelector.rank_key)[:7]
        )

    def test_equal_scores_resolve_to_the_same_set_from_any_offer_order(self) -> None:
        candidates = self.tied_candidates(50)
        orders = (
            candidates,
            tuple(reversed(candidates)),
            candidates[17:] + candidates[:17],
        )

        selections = []
        for order in orders:
            selector = BoundedTopKSelector(5)
            for candidate in order:
                selector.offer(candidate)
            selections.append(selector.ordered())

        assert selections[0] == selections[1] == selections[2]
        assert [candidate.stable_name for candidate in selections[0]] == [
            f"tool_{index:04d}" for index in range(5)
        ]

    def test_selector_rejects_a_non_positive_limit(self) -> None:
        with pytest.raises(ValueError, match="positive limit"):
            BoundedTopKSelector(0)


class TestEntryRankingAndMerge(BoundedSelectionMixin):
    def test_entry_order_never_changes_the_ranked_result(self) -> None:
        cards = tuple(
            _card(f"search_tool_{index:03d}", description="Search one record.")
            for index in range(50)
        )
        entries = _catalog(cards).entries
        request = CapabilitySearchRequest(query="search", limit=5)
        ranker = DeterministicLexicalRanker()

        forward = ranker.rank_entries(entries, request)
        reverse = ranker.rank_entries(tuple(reversed(entries)), request)
        rotated = ranker.rank_entries(entries[17:] + entries[:17], request)

        assert forward == reverse == rotated
        assert [candidate.stable_name for candidate in forward.candidates] == [
            f"search_tool_{index:03d}" for index in range(5)
        ]
        assert forward.scanned_count == 50

    def test_rank_entries_scores_records_that_are_not_catalog_members(self) -> None:
        catalog = _catalog((_card("drive_search", description="Search drive."),))
        detached = catalog.entries[0].model_copy(
            update={
                "capability_ref": "cap_" + "d" * 32,
                "stable_name": "detached_search",
            }
        )

        selection = DeterministicLexicalRanker().rank_entries(
            (detached,),
            CapabilitySearchRequest(query="search"),
        )

        assert [c.stable_name for c in selection.candidates] == ["detached_search"]
        assert selection.scanned_count == 1

    def test_merge_is_bounded_deduplicated_and_deterministic(self) -> None:
        strong = RankedCapabilitySelection(
            scanned_count=4,
            candidates=(
                self.candidate(1, score=90),
                self.candidate(2, score=10),
            ),
        )
        weak = RankedCapabilitySelection(
            scanned_count=6,
            candidates=(
                self.candidate(2, score=10),
                self.candidate(3, score=50),
            ),
        )

        merged = DeterministicLexicalRanker().merge((strong, weak), limit=2)

        assert merged.scanned_count == 10
        assert [candidate.capability_ref for candidate in merged.candidates] == [
            f"cap_{1:032x}",
            f"cap_{3:032x}",
        ]
