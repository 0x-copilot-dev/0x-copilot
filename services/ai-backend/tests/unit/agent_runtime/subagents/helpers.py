from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from agent_runtime.agent.contracts import AgentRuntimeContext
from agent_runtime.subagents import (
    AsyncSubagentLifecycle,
    AsyncSubagentLaunch,
    AsyncTaskState,
    AsyncTaskStatus,
    DynamicSubagentCatalog,
    SubagentDefinition,
    SubagentResult,
    SubagentTask,
)
from agent_runtime.subagents.contracts import RuntimeContextReference


class SubagentTestMixin:
    @dataclass
    class FakeDefinitionProvider:
        definitions: Sequence[SubagentDefinition | Mapping[str, object]]

        def list_subagent_definitions(
            self,
        ) -> Sequence[SubagentDefinition | Mapping[str, object]]:
            return self.definitions

    @dataclass
    class FakeRunner:
        launch: AsyncSubagentLaunch | Mapping[str, object]
        next_result: SubagentResult | Mapping[str, object] | None = None
        fail_on_start: bool = False
        fail_on_check: bool = False
        started_tasks: list[SubagentTask] = field(default_factory=list)
        updated_tasks: list[SubagentTask] = field(default_factory=list)
        cancelled_states: list[AsyncTaskState] = field(default_factory=list)

        async def start(
            self,
            definition: SubagentDefinition,
            task: SubagentTask,
        ) -> AsyncSubagentLaunch | Mapping[str, object]:
            self.started_tasks.append(task)
            if self.fail_on_start:
                raise RuntimeError(SubagentTestMixin.Values.SECRET_ERROR)
            return self.launch

        async def check(self, state: AsyncTaskState) -> SubagentResult | Mapping[str, object] | None:
            if self.fail_on_check:
                raise RuntimeError(SubagentTestMixin.Values.SECRET_ERROR)
            return self.next_result

        async def update(self, state: AsyncTaskState, task: SubagentTask) -> None:
            self.updated_tasks.append(task)

        async def cancel(self, state: AsyncTaskState) -> None:
            self.cancelled_states.append(state)

    @dataclass
    class MutableClock:
        value: datetime

        def __call__(self) -> datetime:
            return self.value

        def advance(self, seconds: int) -> None:
            self.value = self.value + timedelta(seconds=seconds)

    class Values:
        ARTIFACT_REF = "memory://subagents/researcher/result.md"
        CHAT_READ_SCOPE = "chat:read"
        CONSTRAINT = "Keep the answer grounded in source-backed findings."
        DOC_SEARCH_TOOL = "doc_search"
        DOCS_READ_SCOPE = "docs:read"
        EXECUTION_SUMMARY = "Searched Drive and summarized matching source snippets."
        GRAPH_ID = "researcher_graph"
        MALFORMED_GRAPH_ID = "Researcher Graph"
        OBJECTIVE = "Find the launch readiness risks for the executive team."
        PLAN_SUMMARY = "Next verify owner assignments and unresolved launch blockers."
        RAW_RESEARCHER_NAME = "Researcher"
        RELEVANT_SUMMARY = "The supervisor needs compact launch risk research."
        RESEARCH_SKILL = "research"
        RESEARCHER_DESCRIPTION = (
            "Investigates enterprise sources and returns concise grounded research summaries."
        )
        RESEARCHER_NAME = "researcher"
        RESPONSE = "The main launch risk is incomplete owner assignment."
        RUN_ID = "run_123"
        SECRET_ERROR = "runner token=super-secret"
        SLACK_SEARCH_TOOL = "slack_search"
        THREAD_ID = "thread_123"
        TRACE_ID = "trace_123"
        UNKNOWN_TASK_ID = "missing_task"

    def make_definition(
        self,
        *,
        name: str = Values.RESEARCHER_NAME,
        description: str = Values.RESEARCHER_DESCRIPTION,
        graph_id: str = Values.GRAPH_ID,
        tools: object = (Values.DOC_SEARCH_TOOL, Values.SLACK_SEARCH_TOOL),
        skills: object = (Values.RESEARCH_SKILL,),
        required_scopes: object = (Values.DOCS_READ_SCOPE,),
        timeout_seconds: int = 120,
        concurrency_limit: int = 2,
        enabled: bool = True,
    ) -> SubagentDefinition:
        return SubagentDefinition(
            name=name,
            description=description,
            graph_id=graph_id,
            tools=tools,
            skills=skills,
            required_scopes=required_scopes,
            timeout_seconds=timeout_seconds,
            concurrency_limit=concurrency_limit,
            enabled=enabled,
        )

    def make_task(self, context: AgentRuntimeContext) -> SubagentTask:
        return SubagentTask(
            objective=self.Values.OBJECTIVE,
            relevant_summary=self.Values.RELEVANT_SUMMARY,
            constraints=(self.Values.CONSTRAINT,),
            runtime_context_ref=RuntimeContextReference.from_context(context),
            allowed_tools=(self.Values.DOC_SEARCH_TOOL,),
            allowed_skills=(self.Values.RESEARCH_SKILL,),
        )

    def make_result(self) -> SubagentResult:
        return SubagentResult.ok(
            response=self.Values.RESPONSE,
            execution_summary=self.Values.EXECUTION_SUMMARY,
            plan_summary=self.Values.PLAN_SUMMARY,
        )

    def make_catalog(
        self,
        definitions: Sequence[SubagentDefinition | Mapping[str, object]],
    ) -> DynamicSubagentCatalog:
        return DynamicSubagentCatalog(providers=(self.FakeDefinitionProvider(definitions),))

    def make_runner(
        self,
        *,
        next_result: SubagentResult | Mapping[str, object] | None = None,
        fail_on_start: bool = False,
        fail_on_check: bool = False,
    ) -> FakeRunner:
        return self.FakeRunner(
            launch=AsyncSubagentLaunch(
                thread_id=self.Values.THREAD_ID,
                run_id=self.Values.RUN_ID,
                status=AsyncTaskStatus.RUNNING,
            ),
            next_result=next_result,
            fail_on_start=fail_on_start,
            fail_on_check=fail_on_check,
        )

    def make_lifecycle(
        self,
        *,
        definitions: Sequence[SubagentDefinition | Mapping[str, object]] | None = None,
        runner: FakeRunner | None = None,
        clock: MutableClock | None = None,
    ) -> AsyncSubagentLifecycle:
        return AsyncSubagentLifecycle(
            catalog=self.make_catalog(definitions or (self.make_definition(),)),
            runner=runner or self.make_runner(),
            clock=clock or self.MutableClock(datetime(2026, 4, 30, tzinfo=UTC)),
        )
