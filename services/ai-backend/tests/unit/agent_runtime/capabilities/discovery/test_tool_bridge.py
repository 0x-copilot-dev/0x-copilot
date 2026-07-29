from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json

import pytest

from agent_runtime.capabilities.discovery import (
    AuthorizedCatalogBuilder,
    CapabilityCatalog,
    CapabilityCatalogAccess,
    CapabilityCatalogScope,
    CapabilityDescribeTool,
    CapabilityDiscoveryErrorCode,
    CapabilityIndexEntry,
    CapabilitySearchTool,
)
from agent_runtime.capabilities.mcp.cards import (
    McpAuthMode,
    McpServerCard,
    McpServerHealth,
    McpTransport,
)
from agent_runtime.execution.contracts import AgentRuntimeContext, ModelConfig

_NOW = datetime(2026, 7, 27, 12, tzinfo=UTC)
_SELECTION_REF = f"task-policy-selection://run_discovery/default/sha256/{'e' * 64}"


def _context(*, run_id: str = "run_discovery") -> AgentRuntimeContext:
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
        run_id=run_id,
    )


def _card(index: int) -> McpServerCard:
    return McpServerCard(
        name=f"document_search_{index:02d}",
        display_name=f"Document Search {index:02d}",
        short_description="Search authorized documents and return matching records.",
        transport=McpTransport.HTTP,
        auth_mode=McpAuthMode.OAUTH2,
        required_scopes={"docs:read"},
        health=McpServerHealth.HEALTHY,
        load_cost=2,
        connector_slug="drive",
    )


def _catalog(
    context: AgentRuntimeContext,
    *,
    expires_at: datetime = _NOW + timedelta(minutes=15),
    count: int = 1,
) -> CapabilityCatalog:
    return AuthorizedCatalogBuilder(reference_key=b"d" * 32).build(
        context=context,
        scope=CapabilityCatalogScope.from_context(
            context,
            profile_id="default",
            policy_revision="policy_1",
            connector_scope_revision="scope_1",
        ),
        task_policy_selection_ref=_SELECTION_REF,
        mcp_server_cards=tuple(_card(index) for index in range(count)),
        expires_at=expires_at,
    )


def _access(
    catalog: CapabilityCatalog,
    context: AgentRuntimeContext,
) -> CapabilityCatalogAccess:
    return CapabilityCatalogAccess(
        catalog=catalog,
        runtime_context=context,
        clock=lambda: _NOW,
    )


def _entry_with_parameters(
    base: CapabilityIndexEntry,
    *,
    count: int,
    name_chars: int = 20,
) -> CapabilityIndexEntry:
    """Return ``base`` carrying exactly ``count`` parameters of a given width."""

    return CapabilityIndexEntry(
        **base.model_dump(exclude={"parameter_names", "parameter_types"}),
        parameter_names=tuple(
            f"p{index:03d}".ljust(name_chars, "n") for index in range(count)
        ),
        parameter_types=tuple(f"t{index:03d}" for index in range(count)),
    )


def _with_entry(
    catalog: CapabilityCatalog,
    entry: CapabilityIndexEntry,
) -> CapabilityCatalog:
    return CapabilityCatalog(
        scope=catalog.scope,
        revision=catalog.revision,
        entries=(entry,),
    )


class TestCapabilitySearchTool:
    def test_search_is_deterministic_and_bounded(self) -> None:
        context = _context()
        catalog = _catalog(context, count=20)
        tool = CapabilitySearchTool(access=_access(catalog, context))

        first = tool.invoke({"query": "document search", "limit": 10})
        second = tool.invoke({"query": "document search", "limit": 10})

        assert first == second
        assert len(first["search"]["candidates"]) == 10
        assert [
            candidate["stable_name"] for candidate in first["search"]["candidates"]
        ] == [f"document_search_{index:02d}" for index in range(10)]

    @pytest.mark.parametrize("tool_kind", ["search", "describe"])
    def test_cross_run_catalog_is_rejected_without_catalog_metadata(
        self,
        tool_kind: str,
    ) -> None:
        owner = _context(run_id="run_owner")
        other_run = _context(run_id="run_other")
        catalog = _catalog(owner)
        access = _access(catalog, other_run)

        if tool_kind == "search":
            result = CapabilitySearchTool(access=access).invoke("documents")
        else:
            result = CapabilityDescribeTool(access=access).invoke(
                catalog.entries[0].capability_ref
            )

        assert result["error"]["code"] == CapabilityDiscoveryErrorCode.CATALOG_INACTIVE
        assert "catalog_id" not in json.dumps(result)
        assert "catalog_revision" not in json.dumps(result)

    @pytest.mark.parametrize("tool_kind", ["search", "describe"])
    def test_expired_catalog_is_rejected(
        self,
        tool_kind: str,
    ) -> None:
        context = _context()
        catalog = _catalog(
            context,
            expires_at=_NOW - timedelta(microseconds=1),
        )
        access = _access(catalog, context)

        if tool_kind == "search":
            result = CapabilitySearchTool(access=access).invoke("documents")
        else:
            result = CapabilityDescribeTool(access=access).invoke(
                catalog.entries[0].capability_ref
            )

        assert result["error"]["code"] == CapabilityDiscoveryErrorCode.CATALOG_INACTIVE

    def test_long_matched_term_is_bounded_in_output(self) -> None:
        context = _context()
        catalog = _catalog(context)
        long_term = "x" * 200
        base_entry = catalog.entries[0]
        entry = CapabilityIndexEntry(
            **base_entry.model_dump(
                exclude={"concise_description"},
            ),
            concise_description=long_term,
        )
        catalog = CapabilityCatalog(
            scope=catalog.scope,
            revision=catalog.revision,
            entries=(entry,),
        )

        result = CapabilitySearchTool(access=_access(catalog, context)).invoke(
            long_term
        )

        assert len(result["search"]["candidates"][0]["matched_terms"][0]) == 96


class TestCapabilityDescribeTool:
    def test_describe_returns_only_bounded_schema_free_metadata(self) -> None:
        context = _context()
        catalog = _catalog(context)
        base_entry = catalog.entries[0]
        entry = CapabilityIndexEntry(
            **base_entry.model_dump(
                exclude={"intent_tags", "parameter_names", "parameter_types"},
            ),
            intent_tags=tuple(f"{index:02d}-{'t' * 120}" for index in range(64)),
            parameter_names=tuple(
                f"parameter_{index:03d}_{'n' * 120}" for index in range(128)
            ),
            parameter_types=tuple(
                f"type_{index:03d}_{'v' * 120}" for index in range(128)
            ),
        )
        catalog = CapabilityCatalog(
            scope=catalog.scope,
            revision=catalog.revision,
            entries=(entry,),
        )

        result = CapabilityDescribeTool(access=_access(catalog, context)).invoke(
            entry.capability_ref
        )

        description = result["description"]["capability"]
        # Tags are search cues, so they are still trimmed to the bound.
        assert len(description["intent_tags"]) == 16
        assert max(map(len, description["intent_tags"])) == 64
        assert description["metadata_truncated"] is True
        # Parameters are the invocation contract. With no publisher wired there
        # is nowhere to defer them to, so the schema is reported unavailable --
        # never as a prefix that would look like the whole thing.
        assert description["parameters"] == []
        assert description["schema_availability"] == "unavailable"
        assert "schema_artifact" not in description
        encoded = json.dumps(result, sort_keys=True)
        assert len(encoded.encode()) < 12_000
        assert "args_schema" not in encoded
        assert "return_schema" not in encoded

    def test_a_schema_within_the_bound_is_still_inlined_whole(self) -> None:
        context = _context()
        catalog = _catalog(context)
        entry = _entry_with_parameters(catalog.entries[0], count=32)
        catalog = _with_entry(catalog, entry)

        result = CapabilityDescribeTool(access=_access(catalog, context)).invoke(
            entry.capability_ref
        )

        description = result["description"]["capability"]
        assert description["schema_availability"] == "inline"
        assert "schema_artifact" not in description
        assert [item["name"] for item in description["parameters"]] == list(
            entry.parameter_names
        )
        assert [item["type_hint"] for item in description["parameters"]] == list(
            entry.parameter_types
        )

    def test_unknown_opaque_ref_does_not_fall_back_to_name_lookup(self) -> None:
        context = _context()
        catalog = _catalog(context)
        tool = CapabilityDescribeTool(access=_access(catalog, context))

        result = tool.invoke(f"cap_{'0' * 32}")

        assert (
            result["error"]["code"] == CapabilityDiscoveryErrorCode.CAPABILITY_NOT_FOUND
        )
        assert "description" not in result

    @pytest.mark.parametrize(
        ("tool", "payload"),
        [
            ("search", {"query": "", "limit": 100}),
            ("describe", {"capability_ref": "document_search_00"}),
        ],
    )
    def test_invalid_input_returns_safe_typed_failure(
        self,
        tool: str,
        payload: dict[str, object],
    ) -> None:
        context = _context()
        catalog = _catalog(context)
        access = _access(catalog, context)

        if tool == "search":
            result = CapabilitySearchTool(access=access).invoke(payload)
        else:
            result = CapabilityDescribeTool(access=access).invoke(payload)

        assert result == {
            "error": {
                "code": CapabilityDiscoveryErrorCode.INVALID_REQUEST,
                "safe_message": "The capability discovery request is invalid.",
            }
        }
