from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from agent_runtime.capabilities.discovery import (
    AuthorizedCatalogBuilder,
    CapabilityCatalogScope,
    CapabilitySearchFilters,
    CapabilitySearchRequest,
    CapabilitySource,
    DeterministicLexicalRanker,
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
