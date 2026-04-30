"""Compact handoff construction for supervisor-to-subagent delegation."""

from __future__ import annotations

from collections.abc import Sequence

from agent_runtime.agent.contracts import AgentRuntimeContext
from agent_runtime.subagents.contracts import (
    RuntimeContextReference,
    SubagentDefinition,
    SubagentOutputContract,
    SubagentTask,
    SubagentValueNormalizer,
)


class SubagentHandoffBuilder:
    """Build compact task handoffs without copying raw chat history."""

    def build_task(
        self,
        *,
        context: AgentRuntimeContext,
        definition: SubagentDefinition,
        objective: str,
        relevant_summary: str,
        constraints: Sequence[str] = (),
        requested_tools: Sequence[str] = (),
        requested_skills: Sequence[str] = (),
        output_contract: SubagentOutputContract | None = None,
        conversation_history: Sequence[object] = (),
    ) -> SubagentTask:
        """Create the task contract sent to a subagent.

        The conversation history parameter is accepted at the API boundary so callers can
        pass their current state, but it is deliberately not serialized into the task.
        """

        SubagentHandoffPolicy.ignore_raw_history(conversation_history)
        return SubagentTask(
            objective=objective,
            relevant_summary=relevant_summary,
            constraints=tuple(constraints),
            runtime_context_ref=RuntimeContextReference.from_context(context),
            allowed_tools=SubagentHandoffPolicy.allowed_slugs(
                requested_slugs=requested_tools,
                configured_slugs=definition.tools,
            ),
            allowed_skills=SubagentHandoffPolicy.allowed_slugs(
                requested_slugs=requested_skills,
                configured_slugs=definition.skills,
            ),
            output_contract=output_contract or SubagentOutputContract(),
        )


class SubagentHandoffPolicy:
    """Capability narrowing rules for subagent task handoffs."""

    @classmethod
    def allowed_slugs(
        cls,
        *,
        requested_slugs: Sequence[str],
        configured_slugs: frozenset[str],
    ) -> frozenset[str]:
        if not requested_slugs:
            return configured_slugs
        normalized_requested = frozenset(
            SubagentValueNormalizer.normalize_slug(item, "requested_slugs")
            for item in requested_slugs
        )
        return normalized_requested.intersection(configured_slugs)

    @classmethod
    def ignore_raw_history(cls, conversation_history: Sequence[object]) -> None:
        """Document the intentional default: raw history never enters `SubagentTask`."""

        _ = conversation_history
