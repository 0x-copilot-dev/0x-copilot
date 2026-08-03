from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from agent_runtime.execution.contracts import AgentRuntimeContext
from agent_runtime.delegation.subagents import (
    DynamicSubagentCatalog,
    SubagentDefinition,
    SubagentResult,
    SubagentTask,
)
from agent_runtime.delegation.subagents.contracts import RuntimeContextReference


class SubagentTestMixin:
    @dataclass
    class FakeDefinitionProvider:
        definitions: Sequence[SubagentDefinition | Mapping[str, object]]

        def list_subagent_definitions(
            self,
        ) -> Sequence[SubagentDefinition | Mapping[str, object]]:
            return self.definitions

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
        RESEARCHER_DESCRIPTION = "Investigates enterprise sources and returns concise grounded research summaries."
        RESEARCHER_NAME = "researcher"
        RESPONSE = "The main launch risk is incomplete owner assignment."
        SLACK_SEARCH_TOOL = "slack_search"
        TRACE_ID = "trace_123"

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
        return DynamicSubagentCatalog(
            providers=(self.FakeDefinitionProvider(definitions),)
        )
