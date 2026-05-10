"""PR 1.4.1 Phase B — approval expiry sweeper tests.

The sweeper has two passes:

  1. Expiry pass — pending rows whose ``expires_at`` is past.
  2. Membership cascade pass — pending rows whose recipient is no
     longer an active member.

Both enqueue a synthetic :class:`RuntimeApprovalResolvedCommand` with
``decision=REJECTED`` + ``decided_by_user_id=Values.SYSTEM_USER_ID`` +
a short ``reason`` code; the existing approval handler consumes the
command and resolves the run via the standard reject path. The tests
assert against the queue + count semantics, not a full handler trip.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from agent_runtime.api.constants import Messages, Values
from agent_runtime.api.membership import (
    InMemoryWorkspaceMembershipResolver,
    MembershipResolverUnavailable,
)
from runtime_adapters.in_memory.runtime_api_store import InMemoryRuntimeApiStore
from runtime_api.schemas import (
    ApprovalDecision,
    ApprovalRequestRecord,
    ApprovalStatus,
)
from runtime_worker.jobs.approval_expiry_sweeper import (
    ApprovalExpirySweeper,
    ApprovalExpirySweeperEnv,
    build_default_sweeper,
)


class _Values:
    ORG_ID = "org_141"
    USER_ID = "user_marcus"
    OTHER_USER_ID = "user_priya"
    RUN_ID = "run_141"
    CONVERSATION_ID = "conv_141"


def _seed_run(store: InMemoryRuntimeApiStore) -> None:
    from agent_runtime.execution.contracts import AgentRuntimeContext
    from runtime_api.schemas import RunRecord

    store.runs[_Values.RUN_ID] = RunRecord(
        run_id=_Values.RUN_ID,
        conversation_id=_Values.CONVERSATION_ID,
        org_id=_Values.ORG_ID,
        user_id=_Values.USER_ID,
        user_message_id="msg_user",
        trace_id="trace_141",
        model_provider="openai",
        model_name="gpt-5.4-mini",
        runtime_context=AgentRuntimeContext(
            user_id=_Values.USER_ID,
            org_id=_Values.ORG_ID,
            roles=["employee"],
            run_id=_Values.RUN_ID,
            trace_id="trace_141",
            model_profile={
                "provider": "openai",
                "model_name": "gpt-5.4-mini",
                "max_input_tokens": 128000,
                "timeout_seconds": 30,
                "temperature": 0,
                "supports_streaming": True,
            },
        ),
    )


async def _seed_pending(
    store: InMemoryRuntimeApiStore,
    *,
    approval_id: str,
    user_id: str,
    expires_at: datetime | None = None,
) -> ApprovalRequestRecord:
    record = ApprovalRequestRecord(
        approval_id=approval_id,
        run_id=_Values.RUN_ID,
        conversation_id=_Values.CONVERSATION_ID,
        org_id=_Values.ORG_ID,
        user_id=user_id,
        status=ApprovalStatus.PENDING,
        expires_at=expires_at,
        metadata={"approval_kind": "action", "action_summary": "test"},
    )
    await store.seed_approval_request(record)
    return record


class TestExpiryPass:
    async def test_picks_up_expired_rows(self) -> None:
        store = InMemoryRuntimeApiStore()
        _seed_run(store)
        # One expired, one not.
        now = datetime.now(timezone.utc)
        await _seed_pending(
            store,
            approval_id="a_expired",
            user_id=_Values.USER_ID,
            expires_at=now - timedelta(minutes=5),
        )
        await _seed_pending(
            store,
            approval_id="a_not_expired",
            user_id=_Values.USER_ID,
            expires_at=now + timedelta(hours=1),
        )
        sweeper = ApprovalExpirySweeper(
            persistence=store,
            queue=store,
            membership_resolver=InMemoryWorkspaceMembershipResolver(
                {(_Values.ORG_ID, _Values.USER_ID): True}
            ),
            interval_seconds=999,
            clock=lambda: now,
        )
        expired, revoked = await sweeper.sweep_once()
        assert expired == 1
        assert revoked == 0
        # One synthetic rejection enqueued, addressed at the right approval.
        assert len(store.approval_commands) == 1
        cmd = store.approval_commands[0]
        assert cmd.approval_id == "a_expired"
        assert cmd.decision is ApprovalDecision.REJECTED
        assert cmd.decided_by_user_id == Values.SYSTEM_USER_ID
        assert cmd.reason == Messages.Audit.APPROVAL_REASON_EXPIRED

    async def test_skips_resolved_rows(self) -> None:
        store = InMemoryRuntimeApiStore()
        _seed_run(store)
        approval = await _seed_pending(
            store,
            approval_id="a_already_approved",
            user_id=_Values.USER_ID,
            expires_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        )
        store.approval_requests[approval.approval_id] = approval.model_copy(
            update={"status": ApprovalStatus.APPROVED}
        )
        sweeper = ApprovalExpirySweeper(
            persistence=store,
            queue=store,
            membership_resolver=InMemoryWorkspaceMembershipResolver(
                {(_Values.ORG_ID, _Values.USER_ID): True}
            ),
            interval_seconds=999,
        )
        expired, _ = await sweeper.sweep_once()
        assert expired == 0
        assert store.approval_commands == []


class TestMembershipCascadePass:
    async def test_rejects_inactive_recipients(self) -> None:
        store = InMemoryRuntimeApiStore()
        _seed_run(store)
        await _seed_pending(
            store, approval_id="a_revoked", user_id=_Values.OTHER_USER_ID
        )
        # Resolver knows OTHER_USER_ID was revoked, USER_ID is fine.
        resolver = InMemoryWorkspaceMembershipResolver(
            {
                (_Values.ORG_ID, _Values.USER_ID): True,
                (_Values.ORG_ID, _Values.OTHER_USER_ID): False,
            }
        )
        sweeper = ApprovalExpirySweeper(
            persistence=store,
            queue=store,
            membership_resolver=resolver,
            interval_seconds=999,
        )
        expired, revoked = await sweeper.sweep_once()
        assert expired == 0
        assert revoked == 1
        cmd = store.approval_commands[0]
        assert cmd.approval_id == "a_revoked"
        assert cmd.reason == Messages.Audit.APPROVAL_REASON_RECIPIENT_REVOKED

    async def test_skips_active_recipients(self) -> None:
        store = InMemoryRuntimeApiStore()
        _seed_run(store)
        await _seed_pending(store, approval_id="a_active", user_id=_Values.USER_ID)
        resolver = InMemoryWorkspaceMembershipResolver(
            {(_Values.ORG_ID, _Values.USER_ID): True}
        )
        sweeper = ApprovalExpirySweeper(
            persistence=store,
            queue=store,
            membership_resolver=resolver,
            interval_seconds=999,
        )
        _, revoked = await sweeper.sweep_once()
        assert revoked == 0
        assert store.approval_commands == []

    async def test_resolver_unavailable_skips_row_for_this_tick(self) -> None:
        """When the identity backend is flapping, we don't reject on
        uncertainty — we wait for the next tick. Tests the operational
        guarantee that a flapping resolver doesn't cause a flood of
        rejections.
        """

        store = InMemoryRuntimeApiStore()
        _seed_run(store)
        await _seed_pending(store, approval_id="a_uncertain", user_id=_Values.USER_ID)

        class _FlappyResolver:
            async def is_active_member(self, **_):
                raise MembershipResolverUnavailable("flap")

        sweeper = ApprovalExpirySweeper(
            persistence=store,
            queue=store,
            membership_resolver=_FlappyResolver(),
            interval_seconds=999,
        )
        _, revoked = await sweeper.sweep_once()
        assert revoked == 0
        assert store.approval_commands == []


class TestSweeperGate:
    def test_build_default_returns_none_when_disabled(self, monkeypatch) -> None:
        monkeypatch.delenv(ApprovalExpirySweeperEnv.ENABLED, raising=False)
        store = InMemoryRuntimeApiStore()
        result = build_default_sweeper(
            persistence=store,
            queue=store,
            membership_resolver=InMemoryWorkspaceMembershipResolver({}),
        )
        assert result is None

    def test_build_default_returns_sweeper_when_enabled(self, monkeypatch) -> None:
        monkeypatch.setenv(ApprovalExpirySweeperEnv.ENABLED, "true")
        store = InMemoryRuntimeApiStore()
        result = build_default_sweeper(
            persistence=store,
            queue=store,
            membership_resolver=InMemoryWorkspaceMembershipResolver({}),
        )
        assert isinstance(result, ApprovalExpirySweeper)
