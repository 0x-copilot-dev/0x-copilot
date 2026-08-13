"""Compact handoff construction for supervisor-to-subagent delegation."""

from __future__ import annotations

from collections.abc import Sequence

from agent_runtime.execution.contracts import AgentRuntimeContext
from agent_runtime.delegation.subagents.authority import (
    SubagentAuthorityPolicy,
    SubagentCapabilityGrant,
    SubagentPolicyGrant,
)
from agent_runtime.delegation.subagents.contracts import (
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
        parent_grant: SubagentCapabilityGrant | None = None,
    ) -> SubagentTask:
        """Create the task contract sent to a subagent.

        The conversation history parameter is accepted at the API boundary so callers can
        pass their current state, but it is deliberately not serialized into the task.
        """

        SubagentHandoffPolicy.ignore_raw_history(conversation_history)
        authority = SubagentHandoffPolicy.narrow_authority(
            context=context,
            definition=definition,
            requested_tools=requested_tools,
            requested_skills=requested_skills,
            parent_grant=parent_grant,
        )
        return SubagentTask(
            objective=objective,
            relevant_summary=relevant_summary,
            constraints=tuple(constraints),
            runtime_context_ref=RuntimeContextReference(
                user_id=context.user_id,
                org_id=context.org_id,
                trace_id=context.trace_id,
                permission_scopes=authority.permission_scopes,
            ),
            allowed_tools=authority.tools,
            allowed_skills=authority.skills,
            authority=authority,
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
    def parent_policy_ceiling(cls, context: AgentRuntimeContext) -> SubagentPolicyGrant:
        """Return the delegating parent's own resolved approval posture.

        The import is function-local, for the same reason
        ``DelegationDepthPolicy.snapshot`` defers its one:
        ``tool_use_enforcement`` reaches ``capabilities.mcp.constants``, and
        ``capabilities.mcp`` reaches back here through ``coordination`` — so a
        module-level import closes that cycle and breaks *every* import of
        ``agent_runtime`` depending only on which module is entered first.
        Deferring it to call time leaves both packages fully loaded.

        Resolving rather than re-parsing matters: ``ToolUsePolicyResolver`` is
        the single place that knows how the backend's aggregate is shaped and
        how it fails open. A second parser here would be a second answer to
        "what is this run's posture", and the two would drift.
        """

        from agent_runtime.capabilities.tools.tool_use_enforcement import (  # noqa: PLC0415
            ToolUsePolicyResolver,
        )

        return SubagentPolicyGrant.from_policy_snapshot(
            ToolUsePolicyResolver.resolve(context)
        )

    @classmethod
    def narrow_authority(
        cls,
        *,
        context: AgentRuntimeContext,
        definition: SubagentDefinition,
        requested_tools: Sequence[str],
        requested_skills: Sequence[str],
        parent_grant: SubagentCapabilityGrant | None,
    ) -> SubagentCapabilityGrant:
        """Return the only authority that may cross the delegation boundary.

        The parent's sealed filesystem-bypass decision is applied here, not at
        the call sites: every handoff — first dispatch and follow-up update
        alike — must be clamped by the same rule, and a rule applied by each
        caller is a rule one caller forgets.

        When no verified parent grant is supplied the ceiling is synthesised,
        and its approval posture comes from the run's own resolved tool-use
        policy — never from a fresh default, which would silently widen a
        parent stricter than the deployment default. This is the live half of
        the permission floor documented in ``authority``.
        """

        parent = parent_grant or SubagentAuthorityPolicy.inherited_parent_grant(
            context_scopes=context.permission_scopes,
            definition_tools=definition.tools,
            definition_skills=definition.skills,
            parent_policy=cls.parent_policy_ceiling(context),
        )
        parent = SubagentAuthorityPolicy.delegable_parent_grant(
            parent,
            bypass_active=context.filesystem_bypass.skips_approval_pause,
        )
        return SubagentAuthorityPolicy.narrow(
            parent=parent,
            definition_tools=definition.tools,
            definition_skills=definition.skills,
            definition_allowed_scopes=definition.allowed_scopes,
            definition_policy=definition.policy,
            requested_tools=requested_tools,
            requested_skills=requested_skills,
            context_scopes=context.permission_scopes,
        )

    @classmethod
    def enforce_existing_task(
        cls,
        *,
        context: AgentRuntimeContext,
        definition: SubagentDefinition,
        task: SubagentTask,
        parent_grant: SubagentCapabilityGrant | None,
    ) -> SubagentTask:
        """Rebuild a task from verified state immediately before dispatch.

        The handoff object is treated as untrusted at this seam: its identity,
        scopes, and authority are never forwarded as supplied.  A caller may
        request fewer tools/skills, but cannot widen beyond the parent grant or
        definition ceiling.
        """

        SubagentAuthorityPolicy.require_same_tenant(
            expected_user_id=context.user_id,
            expected_org_id=context.org_id,
            expected_trace_id=context.trace_id,
            received_user_id=task.runtime_context_ref.user_id,
            received_org_id=task.runtime_context_ref.org_id,
            received_trace_id=task.runtime_context_ref.trace_id,
        )
        authority = cls.narrow_authority(
            context=context,
            definition=definition,
            requested_tools=tuple(task.allowed_tools),
            requested_skills=tuple(task.allowed_skills),
            parent_grant=parent_grant,
        )
        return task.model_copy(
            update={
                "runtime_context_ref": RuntimeContextReference(
                    user_id=context.user_id,
                    org_id=context.org_id,
                    trace_id=context.trace_id,
                    permission_scopes=authority.permission_scopes,
                ),
                "allowed_tools": authority.tools,
                "allowed_skills": authority.skills,
                "authority": authority,
            }
        )

    @classmethod
    def enforce_task_update(
        cls,
        *,
        task: SubagentTask,
        runtime_context_ref: RuntimeContextReference,
        authority: SubagentCapabilityGrant,
    ) -> SubagentTask:
        """Apply an existing effective grant to a follow-up task update."""

        SubagentAuthorityPolicy.require_same_tenant(
            expected_user_id=runtime_context_ref.user_id,
            expected_org_id=runtime_context_ref.org_id,
            expected_trace_id=runtime_context_ref.trace_id,
            received_user_id=task.runtime_context_ref.user_id,
            received_org_id=task.runtime_context_ref.org_id,
            received_trace_id=task.runtime_context_ref.trace_id,
        )
        narrowed = SubagentAuthorityPolicy.narrow(
            parent=authority,
            definition_tools=authority.tools,
            definition_skills=authority.skills,
            definition_allowed_scopes=authority.permission_scopes,
            definition_policy=authority.policy,
            requested_tools=tuple(task.allowed_tools),
            requested_skills=tuple(task.allowed_skills),
            context_scopes=runtime_context_ref.permission_scopes,
        )
        return task.model_copy(
            update={
                "runtime_context_ref": runtime_context_ref,
                "allowed_tools": narrowed.tools,
                "allowed_skills": narrowed.skills,
                "authority": narrowed,
            }
        )

    @classmethod
    def ignore_raw_history(cls, conversation_history: Sequence[object]) -> None:
        """Document the intentional default: raw history never enters `SubagentTask`."""

        _ = conversation_history
