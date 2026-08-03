from __future__ import annotations

import pytest
from pydantic import ValidationError

from agent_runtime.execution.contracts import AgentRuntimeContext, RuntimeErrorCode
from agent_runtime.execution.errors import AgentRuntimeError
from agent_runtime.delegation.subagents import (
    SubagentHandoffBuilder,
    SubagentResult,
)
from agent_runtime.delegation.subagents.constants import Messages
from tests.unit.agent_runtime.subagents.helpers import SubagentTestMixin


class TestSubagentsAndAsyncAgents(SubagentTestMixin):
    def test_subagent_contracts_validate_compact_handoffs_and_results(
        self,
        runtime_context_admin: AgentRuntimeContext,
    ) -> None:
        definition = self.make_definition(
            name=self.Values.RAW_RESEARCHER_NAME,
            required_scopes={self.Values.DOCS_READ_SCOPE},
        )
        task = self.make_task(runtime_context_admin)
        result = self.make_result()

        assert definition.name == self.Values.RESEARCHER_NAME
        assert task.runtime_context_ref.trace_id == self.Values.TRACE_ID
        assert task.output_contract.required_fields == frozenset(
            {"response", "execution_summary", "plan_summary"}
        )
        assert "conversation_history" not in task.model_dump()
        assert result.execution_summary == self.Values.EXECUTION_SUMMARY

        with pytest.raises(ValidationError):
            self.make_definition(description="too short")

        with pytest.raises(ValidationError):
            self.make_definition(graph_id=self.Values.MALFORMED_GRAPH_ID)

        with pytest.raises(ValidationError):
            SubagentResult(response=self.Values.RESPONSE)

    def test_handoff_builder_excludes_raw_history_and_narrows_capabilities(
        self,
        runtime_context_admin: AgentRuntimeContext,
    ) -> None:
        definition = self.make_definition()
        task = SubagentHandoffBuilder().build_task(
            context=runtime_context_admin,
            definition=definition,
            objective=self.Values.OBJECTIVE,
            relevant_summary=self.Values.RELEVANT_SUMMARY,
            constraints=(self.Values.CONSTRAINT,),
            requested_tools=(self.Values.DOC_SEARCH_TOOL, "admin_delete"),
            requested_skills=(self.Values.RESEARCH_SKILL, "private_skill"),
            conversation_history=(
                {"role": "user", "content": "full raw chat must not be copied"},
            ),
        )

        assert task.allowed_tools == frozenset({self.Values.DOC_SEARCH_TOOL})
        assert task.allowed_skills == frozenset({self.Values.RESEARCH_SKILL})
        assert (
            task.runtime_context_ref.permission_scopes
            == runtime_context_admin.permission_scopes
        )
        assert "full raw chat" not in str(task.model_dump())

    def test_catalog_filters_disabled_and_unauthorized_definitions(
        self,
        runtime_context_admin: AgentRuntimeContext,
    ) -> None:
        catalog = self.make_catalog(
            (
                self.make_definition(name=self.Values.RESEARCHER_NAME),
                self.make_definition(
                    name="slack_researcher",
                    required_scopes={self.Values.CHAT_READ_SCOPE},
                ),
                self.make_definition(name="disabled_researcher", enabled=False),
            )
        )

        definitions = catalog.list_subagent_definitions(runtime_context_admin)

        assert tuple(definition.name for definition in definitions) == (
            self.Values.RESEARCHER_NAME,
        )

        duplicate_catalog = self.make_catalog(
            (
                self.make_definition(name=self.Values.RESEARCHER_NAME),
                self.make_definition(name=self.Values.RESEARCHER_NAME),
            )
        )
        with pytest.raises(AgentRuntimeError) as exc_info:
            duplicate_catalog.list_subagent_definitions(runtime_context_admin)

        assert exc_info.value.code == RuntimeErrorCode.CONFIGURATION_ERROR
        assert exc_info.value.safe_message == Messages.Catalog.DUPLICATE_SUBAGENT_NAME
