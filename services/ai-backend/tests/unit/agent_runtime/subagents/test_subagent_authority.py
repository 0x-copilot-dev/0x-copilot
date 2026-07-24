from __future__ import annotations

import asyncio

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
    SubagentCapabilityGrant,
    SubagentHandoffBuilder,
    SubagentOperationIdentityFactory,
    SubagentPolicyGrant,
    SubagentTask,
)
from agent_runtime.delegation.subagents.contracts import SubagentErrorCode
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

    def test_lifecycle_rejects_cross_tenant_handoff_before_runner_starts(
        self, runtime_context_admin
    ) -> None:
        task = self.make_task(runtime_context_admin).model_copy(
            update={
                "runtime_context_ref": RuntimeContextReference(
                    user_id=runtime_context_admin.user_id,
                    org_id="other_org",
                    trace_id=runtime_context_admin.trace_id,
                    permission_scopes=runtime_context_admin.permission_scopes,
                )
            }
        )
        runner = self.make_runner()
        lifecycle = self.make_lifecycle(runner=runner)

        outcome = asyncio.run(
            lifecycle.start(
                context=runtime_context_admin,
                subagent_name=self.Values.RESEARCHER_NAME,
                task=task,
            )
        )

        assert outcome.error is not None
        assert outcome.error.code is SubagentErrorCode.PERMISSION_DENIED
        assert runner.started_tasks == []

    def test_lifecycle_rebuilds_forged_task_against_parent_grant(
        self, runtime_context_admin
    ) -> None:
        definition = self.make_definition(
            tools=(self.Values.DOC_SEARCH_TOOL, "write_docs"),
            skills=(self.Values.RESEARCH_SKILL, "writer"),
        )
        runner = self.make_runner()
        lifecycle = self.make_lifecycle(definitions=(definition,), runner=runner)
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

        started = asyncio.run(
            lifecycle.start(
                context=runtime_context_admin,
                subagent_name=self.Values.RESEARCHER_NAME,
                task=forged,
                parent_grant=parent_grant,
            )
        )

        assert started.state is not None
        assert runner.started_tasks[0].allowed_tools == frozenset(
            {self.Values.DOC_SEARCH_TOOL}
        )
        assert runner.started_tasks[0].allowed_skills == frozenset(
            {self.Values.RESEARCH_SKILL}
        )
        assert runner.started_tasks[0].authority == parent_grant
        assert runner.started_tasks[
            0
        ].runtime_context_ref.permission_scopes == frozenset(
            {self.Values.DOCS_READ_SCOPE}
        )

    def test_update_cannot_widen_authority_after_start(
        self, runtime_context_admin
    ) -> None:
        definition = self.make_definition(
            tools=(self.Values.DOC_SEARCH_TOOL, "write_docs"),
            skills=(self.Values.RESEARCH_SKILL, "writer"),
        )
        runner = self.make_runner()
        lifecycle = self.make_lifecycle(definitions=(definition,), runner=runner)
        parent_grant = SubagentCapabilityGrant(
            capabilities={"subagent"},
            tools={self.Values.DOC_SEARCH_TOOL},
            skills={self.Values.RESEARCH_SKILL},
            permission_scopes={self.Values.DOCS_READ_SCOPE},
        )
        started = asyncio.run(
            lifecycle.start(
                context=runtime_context_admin,
                subagent_name=self.Values.RESEARCHER_NAME,
                task=self.make_task(runtime_context_admin),
                parent_grant=parent_grant,
            )
        )
        assert started.state is not None
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

        updated = asyncio.run(lifecycle.update(started.state.task_id, widening_update))

        assert updated.state is not None
        assert runner.updated_tasks[0].authority == parent_grant
        assert runner.updated_tasks[0].allowed_tools == frozenset(
            {self.Values.DOC_SEARCH_TOOL}
        )
        assert runner.updated_tasks[0].allowed_skills == frozenset(
            {self.Values.RESEARCH_SKILL}
        )
