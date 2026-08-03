"""The global default tool budget must have exactly one definition.

It used to be a hand-copied literal in four places — the in-memory store,
the file store, the SQL seed, and the ``RUNTIME_TOOL_CALL_BUDGET`` setting
that renders the cap into the model's prompt. Drift between the last two is
especially bad: the model is *told* a cap in its system prompt, so a prompt
copy that disagrees with the enforced copy makes the agent hit a wall it was
told did not exist.
"""

from __future__ import annotations


from agent_runtime.persistence.records import DefaultToolBudget, ToolBudgetEnforcement
from agent_runtime.settings import RuntimeSettings
from runtime_adapters.in_memory.runtime_api_store import InMemoryRuntimeApiStore


class TestDefaultToolBudgetIsSingleSourced:
    def test_seed_record_shape(self) -> None:
        record = DefaultToolBudget.record({})
        assert record.id == "seed_default"
        assert record.org_id is None
        assert record.tool_name == "*"
        assert record.enforcement is ToolBudgetEnforcement.HARD
        assert record.max_calls_per_run == DefaultToolBudget.MAX_CALLS_PER_RUN

    def test_in_memory_store_seeds_from_the_shared_definition(self) -> None:
        seeded = InMemoryRuntimeApiStore().tool_budgets[DefaultToolBudget.ID]
        assert seeded.max_calls_per_run == DefaultToolBudget.MAX_CALLS_PER_RUN

    def test_prompt_budget_matches_the_enforced_budget(self) -> None:
        """The cap rendered into the prompt is the cap the middleware applies.

        Deep Agents' prompt suffix interpolates
        ``settings.execution.tool_call_budget`` and instructs the model to
        stop after that many calls. If it exceeded the enforced row, the
        model would be actively encouraged to walk into a refusal.
        """

        settings = RuntimeSettings.load(
            env_file="/dev/null", template_file="/dev/null", environ={}
        )
        assert (
            settings.execution.tool_call_budget == DefaultToolBudget.MAX_CALLS_PER_RUN
        )


class TestDefaultToolBudgetEnvOverride:
    def test_env_override_is_honored(self) -> None:
        assert (
            DefaultToolBudget.max_calls_per_run({"RUNTIME_TOOL_CALL_BUDGET": "25"})
            == 25
        )

    def test_invalid_values_fall_back_instead_of_raising(self) -> None:
        """A typo in a desktop .env must not take the runtime down."""

        for bad in ("", "   ", "abc", "0", "-3", "1.5"):
            assert (
                DefaultToolBudget.max_calls_per_run({"RUNTIME_TOOL_CALL_BUDGET": bad})
                == DefaultToolBudget.MAX_CALLS_PER_RUN
            ), bad

    def test_unset_env_uses_the_default(self) -> None:
        assert (
            DefaultToolBudget.max_calls_per_run({})
            == DefaultToolBudget.MAX_CALLS_PER_RUN
        )
