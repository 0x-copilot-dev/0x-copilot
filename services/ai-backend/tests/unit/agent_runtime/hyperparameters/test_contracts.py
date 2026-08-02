"""Contract tests for the hyperparameter sections.

Three properties are load-bearing and each has its own class here: the models
are closed and frozen (an unknown key fails at boot rather than silently at
first use), a tunable's ceiling is the invariant it came from (the JSON cannot
widen a contract), and the cross-field validators reject the configurations that
would otherwise apply *silently wrong*.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agent_runtime.delegation.subagents.constants import Limits as SubagentLimits
from agent_runtime.hyperparameters.contracts import (
    ContextHyperparameters,
    DeferLoadingPolicy,
    ExecutionHyperparameters,
    Hyperparameters,
    HyperparameterBounds,
    McpCatalogHyperparameters,
    McpLoadingHyperparameters,
    ModelMapperHyperparameters,
    ReadHyperparameters,
    RetryHyperparameters,
    SubagentHyperparameters,
)


class SectionInventoryMixin:
    """Every section class, so a new section is covered without a new test."""

    SECTIONS = (
        McpLoadingHyperparameters,
        McpCatalogHyperparameters,
        ReadHyperparameters,
        RetryHyperparameters,
        ExecutionHyperparameters,
        SubagentHyperparameters,
        ContextHyperparameters,
        ModelMapperHyperparameters,
    )


class TestSectionShape(SectionInventoryMixin):
    def test_every_section_is_frozen(self) -> None:
        """A section handed to a consumer cannot be mutated by that consumer."""

        for section in self.SECTIONS:
            instance = section()
            field = next(iter(section.model_fields))
            with pytest.raises(ValidationError):
                setattr(instance, field, getattr(instance, field))

    def test_every_section_rejects_an_unknown_key(self) -> None:
        """``extra="forbid"`` is the boot-time guard against a renamed field."""

        for section in self.SECTIONS:
            with pytest.raises(ValidationError) as caught:
                section.model_validate({"not_a_hyperparameter": 1})
            assert "not_a_hyperparameter" in str(caught.value)

    def test_root_rejects_an_unknown_section(self) -> None:
        with pytest.raises(ValidationError) as caught:
            Hyperparameters.model_validate({"billing": {}})
        assert "billing" in str(caught.value)

    def test_root_is_frozen(self) -> None:
        document = Hyperparameters()
        with pytest.raises(ValidationError):
            document.schema_version = 1

    def test_schema_version_refuses_a_future_document(self) -> None:
        """A future shape must fail on this field, not be half-applied."""

        with pytest.raises(ValidationError) as caught:
            Hyperparameters.model_validate({"schema_version": 2})
        assert "schema_version" in str(caught.value)

    def test_root_defaults_construct_every_section(self) -> None:
        document = Hyperparameters()
        assert document.execution.max_parallel_runs == 4
        assert document.subagents.concurrency_limit == 2
        assert document.mcp_loading.defer_loading_policy is DeferLoadingPolicy.OFF


class TestBoundsSourcedFromInvariants:
    """The tunable is policed by the invariant it came from, at the boundary."""

    def test_subagent_timeout_ceiling_is_the_delegation_invariant(self) -> None:
        assert (
            SubagentHyperparameters(
                timeout_seconds=SubagentLimits.TIMEOUT_MAX_SECONDS
            ).timeout_seconds
            == SubagentLimits.TIMEOUT_MAX_SECONDS
        )
        with pytest.raises(ValidationError):
            SubagentHyperparameters(
                timeout_seconds=SubagentLimits.TIMEOUT_MAX_SECONDS + 1
            )

    def test_subagent_concurrency_ceiling_is_the_delegation_invariant(self) -> None:
        assert (
            SubagentHyperparameters(
                concurrency_limit=SubagentLimits.CONCURRENCY_LIMIT_MAX
            ).concurrency_limit
            == SubagentLimits.CONCURRENCY_LIMIT_MAX
        )
        with pytest.raises(ValidationError):
            SubagentHyperparameters(
                concurrency_limit=SubagentLimits.CONCURRENCY_LIMIT_MAX + 1
            )

    def test_execution_parallelism_keeps_todays_envelope(self) -> None:
        """Preserving the accept/reject envelope is part of "no behaviour change".

        ``RuntimeExecutionSettings`` bounds these at 100 today; narrowing the
        ceiling here would reject a deployment that boots on the current code.
        """

        ceiling = HyperparameterBounds.PARALLELISM_MAX
        assert (
            ExecutionHyperparameters(
                max_parallel_subagents=ceiling
            ).max_parallel_subagents
            == ceiling
        )
        with pytest.raises(ValidationError):
            ExecutionHyperparameters(max_parallel_subagents=ceiling + 1)
        with pytest.raises(ValidationError):
            ExecutionHyperparameters(max_parallel_subagents=0)

    def test_mapper_temperature_keeps_the_model_config_envelope(self) -> None:
        ceiling = HyperparameterBounds.TEMPERATURE_MAX
        assert ModelMapperHyperparameters(temperature=ceiling).temperature == ceiling
        with pytest.raises(ValidationError):
            ModelMapperHyperparameters(temperature=ceiling + 0.1)
        with pytest.raises(ValidationError):
            ModelMapperHyperparameters(temperature=-0.1)

    def test_timeouts_share_the_six_hundred_second_envelope(self) -> None:
        ceiling = HyperparameterBounds.TIMEOUT_SECONDS_MAX
        assert ExecutionHyperparameters(default_timeout_seconds=ceiling)
        with pytest.raises(ValidationError):
            ExecutionHyperparameters(default_timeout_seconds=ceiling + 1)
        with pytest.raises(ValidationError):
            McpLoadingHyperparameters(timeout_seconds=ceiling + 1)
        with pytest.raises(ValidationError):
            McpLoadingHyperparameters(timeout_seconds=0)

    def test_catalog_budget_cannot_exceed_the_always_loaded_ceiling(self) -> None:
        """A mis-tuned budget must not be able to re-create the 70 KB blob."""

        with pytest.raises(ValidationError):
            McpCatalogHyperparameters(
                server_markdown_max_bytes=(
                    HyperparameterBounds.ALWAYS_LOADED_BYTES_MAX + 1
                )
            )


class TestCrossFieldInvariants:
    """Each case here would otherwise be a setting that appears to apply."""

    def test_header_reserve_may_not_consume_the_whole_file_budget(self) -> None:
        with pytest.raises(ValidationError) as caught:
            McpCatalogHyperparameters(
                server_markdown_max_bytes=1_000, header_reserve_bytes=1_000
            )
        assert "header_reserve_bytes" in str(caught.value)

    def test_index_summary_minimum_may_not_exceed_its_maximum(self) -> None:
        with pytest.raises(ValidationError) as caught:
            McpCatalogHyperparameters(
                index_summary_min_bytes=200, index_summary_max_bytes=96
            )
        assert "index_summary_min_bytes" in str(caught.value)

    def test_offloaded_read_budget_may_not_be_the_smaller_one(self) -> None:
        with pytest.raises(ValidationError) as caught:
            ReadHyperparameters(
                default_line_limit=2_000, offloaded_result_line_limit=100
            )
        assert "offloaded_result_line_limit" in str(caught.value)

    def test_initial_backoff_may_not_exceed_the_ceiling_that_clamps_it(self) -> None:
        with pytest.raises(ValidationError) as caught:
            RetryHyperparameters(initial_backoff_seconds=10.0, max_backoff_seconds=4.0)
        assert "initial_backoff_seconds" in str(caught.value)

    def test_recent_window_may_not_reach_the_compaction_trigger(self) -> None:
        with pytest.raises(ValidationError) as caught:
            ContextHyperparameters(
                recent_context_ratio=0.9, summary_threshold_ratio=0.85
            )
        assert "recent_context_ratio" in str(caught.value)

    def test_the_checked_in_ratios_satisfy_the_invariant(self) -> None:
        context = ContextHyperparameters()
        assert context.recent_context_ratio < context.summary_threshold_ratio


class TestDerivedValues:
    def test_threshold_tokens_mirror_the_budget_evaluator(self) -> None:
        """Same expression, floor included, as the runtime's own snapshot."""

        context = ContextHyperparameters()
        assert context.summary_threshold_tokens == int(
            context.max_input_tokens * context.summary_threshold_ratio
        )
        assert context.recent_context_tokens == int(
            context.max_input_tokens * context.recent_context_ratio
        )

    def test_threshold_tokens_never_fall_to_zero(self) -> None:
        context = ContextHyperparameters(
            max_input_tokens=1, recent_context_ratio=0.01, summary_threshold_ratio=0.02
        )
        assert context.summary_threshold_tokens == 1
        assert context.recent_context_tokens == 1

    def test_index_body_budget_is_the_file_budget_less_the_preamble(self) -> None:
        catalog = McpCatalogHyperparameters()
        assert catalog.index_body_budget_bytes == (
            catalog.server_markdown_max_bytes - catalog.header_reserve_bytes
        )
        assert catalog.index_body_budget_bytes > 0


class TestDeferLoadingPolicy:
    def test_three_states_exist_and_off_is_the_default(self) -> None:
        assert {member.value for member in DeferLoadingPolicy} == {
            "off",
            "mcp_only",
            "all",
        }
        assert McpLoadingHyperparameters().defer_loading_policy is (
            DeferLoadingPolicy.OFF
        )

    def test_an_unknown_policy_is_rejected(self) -> None:
        with pytest.raises(ValidationError) as caught:
            McpLoadingHyperparameters(defer_loading_policy="sometimes")
        assert "defer_loading_policy" in str(caught.value)

    def test_a_known_policy_parses_from_its_wire_string(self) -> None:
        section = McpLoadingHyperparameters(defer_loading_policy="mcp_only")
        assert section.defer_loading_policy is DeferLoadingPolicy.MCP_ONLY
