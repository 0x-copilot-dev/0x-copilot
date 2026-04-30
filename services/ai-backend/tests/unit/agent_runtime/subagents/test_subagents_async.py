from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from agent_runtime.execution.contracts import AgentRuntimeContext, RuntimeErrorCode
from agent_runtime.execution.errors import AgentRuntimeError
from agent_runtime.delegation.subagents import (
    AsyncTaskStatus,
    SubagentHandoffBuilder,
    SubagentResult,
)
from agent_runtime.delegation.subagents.constants import Limits, Messages
from agent_runtime.delegation.subagents.contracts import SubagentErrorCode
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

    def test_lifecycle_start_update_check_cancel_list_and_queue(
        self,
        runtime_context_admin: AgentRuntimeContext,
    ) -> None:
        runner = self.make_runner(next_result=self.make_result())
        lifecycle = self.make_lifecycle(runner=runner)
        task = self.make_task(runtime_context_admin)

        started = asyncio.run(
            lifecycle.start(
                context=runtime_context_admin,
                subagent_name=self.Values.RESEARCHER_NAME,
                task=task,
            )
        )
        listed = lifecycle.list_tasks()
        updated = asyncio.run(lifecycle.update(started.state.task_id, task))  # type: ignore[union-attr]
        checked = asyncio.run(lifecycle.check(started.state.task_id))  # type: ignore[union-attr]

        assert started.state is not None
        assert started.state.status is AsyncTaskStatus.RUNNING
        assert listed.tasks is not None
        assert len(listed.tasks) == 1
        assert updated.state is not None
        assert updated.state.updated_at >= started.state.updated_at
        assert checked.state is not None
        assert checked.state.status is AsyncTaskStatus.SUCCEEDED
        assert checked.result is not None
        assert checked.result.response == self.Values.RESPONSE
        assert runner.started_tasks == [task]
        assert runner.updated_tasks == [task]

        cancel_runner = self.make_runner()
        cancel_lifecycle = self.make_lifecycle(runner=cancel_runner)
        cancel_started = asyncio.run(
            cancel_lifecycle.start(
                context=runtime_context_admin,
                subagent_name=self.Values.RESEARCHER_NAME,
                task=task,
            )
        )
        cancelled = asyncio.run(cancel_lifecycle.cancel(cancel_started.state.task_id))  # type: ignore[union-attr]

        assert cancelled.state is not None
        assert cancelled.state.status is AsyncTaskStatus.CANCELLED
        assert len(cancel_runner.cancelled_states) == 1

        queued_runner = self.make_runner()
        queued_lifecycle = self.make_lifecycle(
            definitions=(self.make_definition(concurrency_limit=1),),
            runner=queued_runner,
        )
        first = asyncio.run(
            queued_lifecycle.start(
                context=runtime_context_admin,
                subagent_name=self.Values.RESEARCHER_NAME,
                task=task,
            )
        )
        second = asyncio.run(
            queued_lifecycle.start(
                context=runtime_context_admin,
                subagent_name=self.Values.RESEARCHER_NAME,
                task=task,
            )
        )

        assert first.state is not None
        assert first.state.status is AsyncTaskStatus.RUNNING
        assert second.state is not None
        assert second.state.status is AsyncTaskStatus.QUEUED
        assert len(queued_runner.started_tasks) == 1

    def test_lifecycle_handles_unavailable_stale_cancelled_timeout_and_bad_results(
        self,
        runtime_context_admin: AgentRuntimeContext,
    ) -> None:
        task = self.make_task(runtime_context_admin)
        lifecycle = self.make_lifecycle()

        unavailable = asyncio.run(
            lifecycle.start(
                context=runtime_context_admin,
                subagent_name="unknown_subagent",
                task=task,
            )
        )
        stale = asyncio.run(lifecycle.check(self.Values.UNKNOWN_TASK_ID))

        assert unavailable.error is not None
        assert unavailable.error.code == SubagentErrorCode.SUBAGENT_UNAVAILABLE
        assert stale.error is not None
        assert stale.error.code == SubagentErrorCode.STALE_TASK_ID

        cancel_started = asyncio.run(
            lifecycle.start(
                context=runtime_context_admin,
                subagent_name=self.Values.RESEARCHER_NAME,
                task=task,
            )
        )
        asyncio.run(lifecycle.cancel(cancel_started.state.task_id))  # type: ignore[union-attr]
        cancelled_check = asyncio.run(lifecycle.check(cancel_started.state.task_id))  # type: ignore[union-attr]

        assert cancelled_check.error is not None
        assert cancelled_check.error.code == SubagentErrorCode.CANCELLED

        clock = self.MutableClock(datetime(2026, 4, 30, tzinfo=UTC))
        timeout_lifecycle = self.make_lifecycle(
            definitions=(self.make_definition(timeout_seconds=1),),
            clock=clock,
        )
        timeout_started = asyncio.run(
            timeout_lifecycle.start(
                context=runtime_context_admin,
                subagent_name=self.Values.RESEARCHER_NAME,
                task=task,
            )
        )
        clock.advance(2)
        timed_out = asyncio.run(timeout_lifecycle.check(timeout_started.state.task_id))  # type: ignore[union-attr]

        assert timed_out.state is not None
        assert timed_out.state.status is AsyncTaskStatus.TIMED_OUT
        assert timed_out.result is not None
        assert timed_out.result.error is not None
        assert timed_out.result.error.code == SubagentErrorCode.TIMEOUT

        malformed_lifecycle = self.make_lifecycle(
            runner=self.make_runner(
                next_result={
                    "response": self.Values.RESPONSE,
                    "execution_summary": self.Values.EXECUTION_SUMMARY,
                }
            )
        )
        malformed_started = asyncio.run(
            malformed_lifecycle.start(
                context=runtime_context_admin,
                subagent_name=self.Values.RESEARCHER_NAME,
                task=task,
            )
        )
        malformed = asyncio.run(
            malformed_lifecycle.check(malformed_started.state.task_id)
        )  # type: ignore[union-attr]

        assert malformed.result is not None
        assert malformed.result.error is not None
        assert malformed.result.error.code == SubagentErrorCode.MALFORMED_RESULT

        oversized_lifecycle = self.make_lifecycle(
            runner=self.make_runner(
                next_result={
                    "response": "x" * (Limits.RESULT_RESPONSE_MAX_LENGTH + 1),
                    "execution_summary": self.Values.EXECUTION_SUMMARY,
                    "plan_summary": self.Values.PLAN_SUMMARY,
                }
            )
        )
        oversized_started = asyncio.run(
            oversized_lifecycle.start(
                context=runtime_context_admin,
                subagent_name=self.Values.RESEARCHER_NAME,
                task=task,
            )
        )
        oversized = asyncio.run(
            oversized_lifecycle.check(oversized_started.state.task_id)
        )  # type: ignore[union-attr]

        assert oversized.result is not None
        assert oversized.result.error is not None
        assert oversized.result.error.code == SubagentErrorCode.OVERSIZED_RESULT
