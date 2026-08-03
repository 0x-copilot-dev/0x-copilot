from __future__ import annotations

import pytest

from agent_runtime.capabilities.operations.context import (
    OperationContext,
    VerifiedOperationIdentity,
)
from agent_runtime.capabilities.operations.contracts import OperationGatewayMode
from agent_runtime.capabilities.tools.permissions import (
    ToolUsePolicyMode,
    ToolUsePolicySnapshot,
)
from agent_runtime.delegation.subagents.atlas_task_tool import (
    build_subagent_invocation_config,
)
from agent_runtime.delegation.subagents import (
    RuntimeContextReference,
    SubagentAuthorityError,
    SubagentCapabilityGrant,
    SubagentHandoffBuilder,
    SubagentHandoffPolicy,
    SubagentOperationIdentityFactory,
    SubagentPolicyGrant,
    SubagentTask,
)
from agent_runtime.delegation.subagents.operation_identity import (
    SUBAGENT_DELEGATION_OPERATION_ID_KEY,
    SUBAGENT_PARENT_OPERATION_ID_KEY,
    SUBAGENT_ROOT_OPERATION_ID_KEY,
    SUPERVISOR_TASK_CALL_ID_KEY,
)
from tests.unit.agent_runtime.subagents.helpers import SubagentTestMixin


class TestSubagentAuthority(SubagentTestMixin):
    def test_supervisor_call_mapping_is_deterministic_and_concurrent_safe(self) -> None:
        identity = VerifiedOperationIdentity(
            org_id="org_456",
            user_id="user_123",
            conversation_id="conversation_123",
            run_id="run_123",
        )

        first = SubagentOperationIdentityFactory.from_identity(
            identity=identity,
            supervisor_task_call_id="call_research",
        )
        replay = SubagentOperationIdentityFactory.from_identity(
            identity=identity,
            supervisor_task_call_id="call_research",
        )
        concurrent_sibling = SubagentOperationIdentityFactory.from_identity(
            identity=identity,
            supervisor_task_call_id="call_writer",
        )

        assert first == replay
        assert first.delegation_operation_id != first.child_root_operation_id
        assert (
            first.delegation_operation_id != concurrent_sibling.delegation_operation_id
        )
        assert (
            first.child_root_operation_id != concurrent_sibling.child_root_operation_id
        )

    def test_task_config_carries_exact_parent_child_mapping(self) -> None:
        identity = VerifiedOperationIdentity(
            org_id="org_456",
            user_id="user_123",
            conversation_id="conversation_123",
            run_id="run_123",
        )
        token = OperationContext.bind_for_run(
            identity=identity,
            policy_snapshot=ToolUsePolicySnapshot({}),
            ledger_emitter=None,
            artifact_service=None,
            mode=OperationGatewayMode.OFF,
        )
        try:
            config = build_subagent_invocation_config("call_research")
        finally:
            OperationContext.unbind(token)

        expected = SubagentOperationIdentityFactory.from_identity(
            identity=identity,
            supervisor_task_call_id="call_research",
        )
        metadata = config["metadata"]
        configurable = config["configurable"]
        assert metadata[SUPERVISOR_TASK_CALL_ID_KEY] == "call_research"
        assert (
            metadata[SUBAGENT_DELEGATION_OPERATION_ID_KEY]
            == expected.delegation_operation_id
        )
        assert (
            metadata[SUBAGENT_PARENT_OPERATION_ID_KEY]
            == expected.delegation_operation_id
        )
        assert (
            metadata[SUBAGENT_ROOT_OPERATION_ID_KEY] == expected.child_root_operation_id
        )
        assert (
            configurable[SUBAGENT_ROOT_OPERATION_ID_KEY]
            == expected.child_root_operation_id
        )

    def test_builder_intersects_parent_definition_request_and_policy(
        self, runtime_context_admin
    ) -> None:
        definition = self.make_definition(
            tools=(self.Values.DOC_SEARCH_TOOL, "write_docs"),
            skills=(self.Values.RESEARCH_SKILL, "writer"),
            required_scopes=(self.Values.DOCS_READ_SCOPE,),
        ).model_copy(
            update={
                "allowed_scopes": frozenset({self.Values.DOCS_READ_SCOPE}),
                "policy": SubagentPolicyGrant(
                    write=ToolUsePolicyMode.BLOCK,
                    destructive=ToolUsePolicyMode.BLOCK,
                ),
            }
        )
        parent_grant = SubagentCapabilityGrant(
            capabilities={"subagent", "workspace"},
            tools={self.Values.DOC_SEARCH_TOOL, "admin_delete"},
            skills={self.Values.RESEARCH_SKILL, "private_skill"},
            permission_scopes={self.Values.DOCS_READ_SCOPE, "search:read"},
            policy=SubagentPolicyGrant(
                write=ToolUsePolicyMode.ASK,
                destructive=ToolUsePolicyMode.REQUIRE,
            ),
        )

        task = SubagentHandoffBuilder().build_task(
            context=runtime_context_admin,
            definition=definition,
            objective=self.Values.OBJECTIVE,
            relevant_summary=self.Values.RELEVANT_SUMMARY,
            requested_tools=(self.Values.DOC_SEARCH_TOOL, "write_docs", "admin_delete"),
            requested_skills=(self.Values.RESEARCH_SKILL, "writer", "private_skill"),
            parent_grant=parent_grant,
        )

        assert task.allowed_tools == frozenset({self.Values.DOC_SEARCH_TOOL})
        assert task.allowed_skills == frozenset({self.Values.RESEARCH_SKILL})
        assert task.authority.capabilities == frozenset({"subagent"})
        assert task.runtime_context_ref.permission_scopes == frozenset(
            {self.Values.DOCS_READ_SCOPE}
        )
        assert task.authority.policy.write is ToolUsePolicyMode.BLOCK
        assert task.authority.policy.destructive is ToolUsePolicyMode.BLOCK

    def test_dispatch_rejects_a_cross_tenant_handoff(
        self, runtime_context_admin
    ) -> None:
        """`enforce_existing_task` is the seam that refuses a foreign context.

        Previously covered through `AsyncSubagentLifecycle.start`; the lifecycle
        is gone, so this drives the surviving policy directly.
        """

        definition = self.make_definition()
        foreign = self.make_task(runtime_context_admin).model_copy(
            update={
                "runtime_context_ref": RuntimeContextReference(
                    user_id=runtime_context_admin.user_id,
                    org_id="other_org",
                    trace_id=runtime_context_admin.trace_id,
                    permission_scopes=runtime_context_admin.permission_scopes,
                )
            }
        )

        with pytest.raises(SubagentAuthorityError):
            SubagentHandoffPolicy.enforce_existing_task(
                context=runtime_context_admin,
                definition=definition,
                task=foreign,
                parent_grant=None,
            )

    def test_dispatch_rebuilds_a_forged_task_against_the_parent_grant(
        self, runtime_context_admin
    ) -> None:
        definition = self.make_definition(
            tools=(self.Values.DOC_SEARCH_TOOL, "write_docs"),
            skills=(self.Values.RESEARCH_SKILL, "writer"),
        )
        forged = SubagentTask(
            objective=self.Values.OBJECTIVE,
            relevant_summary=self.Values.RELEVANT_SUMMARY,
            runtime_context_ref=RuntimeContextReference.from_context(
                runtime_context_admin
            ),
            allowed_tools={self.Values.DOC_SEARCH_TOOL, "write_docs", "admin_delete"},
            allowed_skills={self.Values.RESEARCH_SKILL, "writer", "private_skill"},
            authority=SubagentCapabilityGrant(
                capabilities={"subagent", "workspace"},
                tools={self.Values.DOC_SEARCH_TOOL, "write_docs", "admin_delete"},
                skills={self.Values.RESEARCH_SKILL, "writer", "private_skill"},
                permission_scopes=runtime_context_admin.permission_scopes,
                policy=SubagentPolicyGrant(
                    write=ToolUsePolicyMode.AUTO,
                    destructive=ToolUsePolicyMode.AUTO,
                ),
            ),
        )
        parent_grant = SubagentCapabilityGrant(
            capabilities={"subagent"},
            tools={self.Values.DOC_SEARCH_TOOL},
            skills={self.Values.RESEARCH_SKILL},
            permission_scopes={self.Values.DOCS_READ_SCOPE},
            policy=SubagentPolicyGrant(
                write=ToolUsePolicyMode.REQUIRE,
                destructive=ToolUsePolicyMode.BLOCK,
            ),
        )

        enforced = SubagentHandoffPolicy.enforce_existing_task(
            context=runtime_context_admin,
            definition=definition,
            task=forged,
            parent_grant=parent_grant,
        )

        assert enforced.allowed_tools == frozenset({self.Values.DOC_SEARCH_TOOL})
        assert enforced.allowed_skills == frozenset({self.Values.RESEARCH_SKILL})
        assert enforced.authority == parent_grant
        assert enforced.runtime_context_ref.permission_scopes == frozenset(
            {self.Values.DOCS_READ_SCOPE}
        )

    def test_update_cannot_widen_authority_after_dispatch(
        self, runtime_context_admin
    ) -> None:
        effective_grant = SubagentCapabilityGrant(
            capabilities={"subagent"},
            tools={self.Values.DOC_SEARCH_TOOL},
            skills={self.Values.RESEARCH_SKILL},
            permission_scopes={self.Values.DOCS_READ_SCOPE},
        )
        effective_ref = RuntimeContextReference(
            user_id=runtime_context_admin.user_id,
            org_id=runtime_context_admin.org_id,
            trace_id=runtime_context_admin.trace_id,
            permission_scopes=frozenset({self.Values.DOCS_READ_SCOPE}),
        )
        widening_update = SubagentTask(
            objective=self.Values.OBJECTIVE,
            relevant_summary=self.Values.RELEVANT_SUMMARY,
            runtime_context_ref=RuntimeContextReference.from_context(
                runtime_context_admin
            ),
            allowed_tools={self.Values.DOC_SEARCH_TOOL, "write_docs"},
            allowed_skills={self.Values.RESEARCH_SKILL, "writer"},
            authority=SubagentCapabilityGrant(
                capabilities={"workspace"},
                tools={"write_docs"},
                skills={"writer"},
                permission_scopes={"search:read"},
                policy=SubagentPolicyGrant(write=ToolUsePolicyMode.AUTO),
            ),
        )

        updated = SubagentHandoffPolicy.enforce_task_update(
            task=widening_update,
            runtime_context_ref=effective_ref,
            authority=effective_grant,
        )

        assert updated.authority == effective_grant
        assert updated.allowed_tools == frozenset({self.Values.DOC_SEARCH_TOOL})
        assert updated.allowed_skills == frozenset({self.Values.RESEARCH_SKILL})
