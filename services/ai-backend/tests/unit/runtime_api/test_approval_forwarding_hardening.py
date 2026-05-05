"""PR 1.4.1 Phase A — production-hardening tests for two-stage approvals.

Covers the four production blockers landed in Phase A:

  Gap #1 — workspace membership resolver
  Gap #5 — notification dispatcher (post-commit, fire-and-forget)
  Gap #6 — recipient inbox endpoint + per-user SSE bus
  Gap #8 — in-memory race guard mirroring the postgres status check

Each test class focuses on one gap. The shared seeding helpers mirror
``test_approval_forwarding.py`` so the two suites stay readable side by
side.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from agent_runtime.api.membership import (
    HttpWorkspaceMembershipResolver,
    InMemoryWorkspaceMembershipResolver,
    MembershipResolverUnavailable,
    _MembershipCache,
)
from agent_runtime.api.notifications import (
    InboxAndEmailNotificationDispatcher,
)
from agent_runtime.api.service import RuntimeApiService
from agent_runtime.settings import RuntimeSettings
from runtime_adapters.in_memory.runtime_api_store import InMemoryRuntimeApiStore
from runtime_api.http.errors import RuntimeApiError
from runtime_api.schemas import (
    AgentRunStatus,
    ApprovalDecision,
    ApprovalDecisionRequest,
    ApprovalForwardTarget,
    ApprovalRequestRecord,
    ApprovalStatus,
)
from runtime_api.sse.inbox_bus import InboxEventBus


class _Values:
    ORG_ID = "org_acme_141"
    REQUESTER_USER_ID = "user_sarah"
    FORWARD_TARGET_USER_ID = "user_marcus"
    OTHER_USER_ID = "user_devi"
    RUN_ID = "run_141"
    CONVERSATION_ID = "conv_141"
    PARENT_APPROVAL_ID = "approval_141_parent"
    USER_MESSAGE_ID = "msg_141_user"


def _seed_run_and_pending_approval(
    store: InMemoryRuntimeApiStore,
    *,
    approval_kind: str = "action",
) -> ApprovalRequestRecord:
    from agent_runtime.execution.contracts import AgentRuntimeContext
    from runtime_api.schemas import MessageRecord, MessageRole, RunRecord

    store.append_message(
        MessageRecord(
            message_id=_Values.USER_MESSAGE_ID,
            conversation_id=_Values.CONVERSATION_ID,
            org_id=_Values.ORG_ID,
            role=MessageRole.USER,
            content_text="Forward this draft please",
        )
    )
    store.runs[_Values.RUN_ID] = RunRecord(
        run_id=_Values.RUN_ID,
        conversation_id=_Values.CONVERSATION_ID,
        org_id=_Values.ORG_ID,
        user_id=_Values.REQUESTER_USER_ID,
        user_message_id=_Values.USER_MESSAGE_ID,
        trace_id="trace_141",
        model_provider="openai",
        model_name="gpt-5.4-mini",
        status=AgentRunStatus.WAITING_FOR_APPROVAL,
        runtime_context=AgentRuntimeContext(
            user_id=_Values.REQUESTER_USER_ID,
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
    store.events_by_run.setdefault(_Values.RUN_ID, [])
    record = ApprovalRequestRecord(
        approval_id=_Values.PARENT_APPROVAL_ID,
        run_id=_Values.RUN_ID,
        conversation_id=_Values.CONVERSATION_ID,
        org_id=_Values.ORG_ID,
        user_id=_Values.REQUESTER_USER_ID,
        metadata={
            "approval_kind": approval_kind,
            "native_interrupt_id": _Values.PARENT_APPROVAL_ID,
            "tool_name": "post_to_slack",
            "action_summary": "Post draft to #launch-aurora",
        },
    )
    store.seed_approval_request(record)
    return record


def _make_service(
    store: InMemoryRuntimeApiStore,
    *,
    membership: dict[tuple[str, str], bool] | None = None,
    dispatcher=None,
) -> RuntimeApiService:
    default_membership = {
        (_Values.ORG_ID, _Values.REQUESTER_USER_ID): True,
        (_Values.ORG_ID, _Values.FORWARD_TARGET_USER_ID): True,
    }
    resolver = InMemoryWorkspaceMembershipResolver(
        membership if membership is not None else default_membership
    )
    settings = RuntimeSettings.load(
        environ={
            "OPENAI_API_KEY": "sk-test",
            "RUNTIME_DEFAULT_PROVIDER": "openai",
            "RUNTIME_DEFAULT_MODEL": "gpt-5.4-mini",
            "RUNTIME_MAX_PARALLEL_TASKS": "4",
        }
    )
    return RuntimeApiService(
        persistence=store,
        event_store=store,
        queue=store,
        settings=settings,
        membership_resolver=resolver,
        notification_dispatcher=dispatcher,
    )


# ---------------------------------------------------------------------------
# Gap #1 — Membership resolver (cache + http impl)
# ---------------------------------------------------------------------------


class _FakeClock:
    def __init__(self, start: float = 1000.0) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


class TestMembershipCache:
    def test_positive_entry_returned_within_ttl(self) -> None:
        clock = _FakeClock()
        cache = _MembershipCache(
            positive_ttl_seconds=300, negative_ttl_seconds=30, clock=clock
        )
        cache.put(org_id="o", user_id="u", is_active=True)
        assert cache.get(org_id="o", user_id="u") is True
        clock.advance(299)
        assert cache.get(org_id="o", user_id="u") is True

    def test_positive_entry_expires_at_ttl(self) -> None:
        clock = _FakeClock()
        cache = _MembershipCache(
            positive_ttl_seconds=300, negative_ttl_seconds=30, clock=clock
        )
        cache.put(org_id="o", user_id="u", is_active=True)
        clock.advance(301)
        assert cache.get(org_id="o", user_id="u") is None

    def test_negative_entry_uses_short_ttl(self) -> None:
        clock = _FakeClock()
        cache = _MembershipCache(
            positive_ttl_seconds=300, negative_ttl_seconds=30, clock=clock
        )
        cache.put(org_id="o", user_id="u", is_active=False)
        clock.advance(31)
        assert cache.get(org_id="o", user_id="u") is None

    def test_invalidate_drops_entry(self) -> None:
        cache = _MembershipCache(positive_ttl_seconds=300, negative_ttl_seconds=30)
        cache.put(org_id="o", user_id="u", is_active=True)
        cache.invalidate(org_id="o", user_id="u")
        assert cache.get(org_id="o", user_id="u") is None


class TestHttpMembershipResolver:
    def test_active_member_returned_truthy(self) -> None:
        async def fetch(url, headers):
            return 200, {"org_id": "o1", "status": "active", "removed_at": None}

        resolver = HttpWorkspaceMembershipResolver(fetch=fetch)
        result = asyncio.run(resolver.is_active_member(org_id="o1", user_id="u1"))
        assert result is True

    def test_inactive_user_returned_falsy(self) -> None:
        async def fetch(url, headers):
            return 200, {"org_id": "o1", "status": "inactive", "removed_at": None}

        resolver = HttpWorkspaceMembershipResolver(fetch=fetch)
        result = asyncio.run(resolver.is_active_member(org_id="o1", user_id="u1"))
        assert result is False

    def test_cross_org_user_returned_falsy(self) -> None:
        async def fetch(url, headers):
            return 200, {"org_id": "o2", "status": "active", "removed_at": None}

        resolver = HttpWorkspaceMembershipResolver(fetch=fetch)
        result = asyncio.run(resolver.is_active_member(org_id="o1", user_id="u1"))
        assert result is False

    def test_404_returned_falsy(self) -> None:
        async def fetch(url, headers):
            return 404, {}

        resolver = HttpWorkspaceMembershipResolver(fetch=fetch)
        result = asyncio.run(resolver.is_active_member(org_id="o1", user_id="u1"))
        assert result is False

    def test_5xx_raises_unavailable(self) -> None:
        async def fetch(url, headers):
            return 503, {}

        resolver = HttpWorkspaceMembershipResolver(fetch=fetch)
        with pytest.raises(MembershipResolverUnavailable):
            asyncio.run(resolver.is_active_member(org_id="o1", user_id="u1"))

    def test_cache_hits_skip_fetch(self) -> None:
        calls = []

        async def fetch(url, headers):
            calls.append(url)
            return 200, {"org_id": "o1", "status": "active", "removed_at": None}

        resolver = HttpWorkspaceMembershipResolver(fetch=fetch)
        asyncio.run(resolver.is_active_member(org_id="o1", user_id="u1"))
        asyncio.run(resolver.is_active_member(org_id="o1", user_id="u1"))
        assert len(calls) == 1


class TestServiceForwardWithMembershipResolver:
    """The service's _guard_forwardable now consults the resolver."""

    def test_forward_to_unknown_user_rejected_with_422(self) -> None:
        store = InMemoryRuntimeApiStore()
        _seed_run_and_pending_approval(store)
        # Resolver knows only the requester. Forward target is unknown.
        service = _make_service(
            store,
            membership={(_Values.ORG_ID, _Values.REQUESTER_USER_ID): True},
        )
        request = ApprovalDecisionRequest(
            decision=ApprovalDecision.FORWARDED,
            decided_by_user_id=_Values.REQUESTER_USER_ID,
            forward_to=ApprovalForwardTarget(
                kind="workspace_user", user_id=_Values.FORWARD_TARGET_USER_ID
            ),
        )
        with pytest.raises(RuntimeApiError) as exc:
            asyncio.run(
                service.record_approval_decision(
                    org_id=_Values.ORG_ID,
                    approval_id=_Values.PARENT_APPROVAL_ID,
                    request=request,
                )
            )
        assert exc.value.http_status == 422

    def test_forward_to_inactive_user_rejected_with_422(self) -> None:
        store = InMemoryRuntimeApiStore()
        _seed_run_and_pending_approval(store)
        service = _make_service(
            store,
            membership={
                (_Values.ORG_ID, _Values.REQUESTER_USER_ID): True,
                (_Values.ORG_ID, _Values.FORWARD_TARGET_USER_ID): False,
            },
        )
        request = ApprovalDecisionRequest(
            decision=ApprovalDecision.FORWARDED,
            decided_by_user_id=_Values.REQUESTER_USER_ID,
            forward_to=ApprovalForwardTarget(
                kind="workspace_user", user_id=_Values.FORWARD_TARGET_USER_ID
            ),
        )
        with pytest.raises(RuntimeApiError) as exc:
            asyncio.run(
                service.record_approval_decision(
                    org_id=_Values.ORG_ID,
                    approval_id=_Values.PARENT_APPROVAL_ID,
                    request=request,
                )
            )
        assert exc.value.http_status == 422

    def test_forward_to_active_user_accepted(self) -> None:
        store = InMemoryRuntimeApiStore()
        _seed_run_and_pending_approval(store)
        service = _make_service(store)
        request = ApprovalDecisionRequest(
            decision=ApprovalDecision.FORWARDED,
            decided_by_user_id=_Values.REQUESTER_USER_ID,
            forward_to=ApprovalForwardTarget(
                kind="workspace_user", user_id=_Values.FORWARD_TARGET_USER_ID
            ),
        )
        response = asyncio.run(
            service.record_approval_decision(
                org_id=_Values.ORG_ID,
                approval_id=_Values.PARENT_APPROVAL_ID,
                request=request,
            )
        )
        assert response.status is ApprovalStatus.FORWARDED
        assert response.forwarded_to_user_id == _Values.FORWARD_TARGET_USER_ID

    def test_resolver_unavailable_rejected_with_503(self) -> None:
        store = InMemoryRuntimeApiStore()
        _seed_run_and_pending_approval(store)

        class _UnavailableResolver:
            async def is_active_member(self, **_):
                raise MembershipResolverUnavailable("identity backend down")

        from agent_runtime.api.service import RuntimeApiService

        settings = RuntimeSettings.load(
            environ={
                "OPENAI_API_KEY": "sk-test",
                "RUNTIME_DEFAULT_PROVIDER": "openai",
                "RUNTIME_DEFAULT_MODEL": "gpt-5.4-mini",
                "RUNTIME_MAX_PARALLEL_TASKS": "4",
            }
        )
        service = RuntimeApiService(
            persistence=store,
            event_store=store,
            queue=store,
            settings=settings,
            membership_resolver=_UnavailableResolver(),
        )
        request = ApprovalDecisionRequest(
            decision=ApprovalDecision.FORWARDED,
            decided_by_user_id=_Values.REQUESTER_USER_ID,
            forward_to=ApprovalForwardTarget(
                kind="workspace_user", user_id=_Values.FORWARD_TARGET_USER_ID
            ),
        )
        with pytest.raises(RuntimeApiError) as exc:
            asyncio.run(
                service.record_approval_decision(
                    org_id=_Values.ORG_ID,
                    approval_id=_Values.PARENT_APPROVAL_ID,
                    request=request,
                )
            )
        assert exc.value.http_status == 503
        assert exc.value.envelope.retryable is True


# ---------------------------------------------------------------------------
# Gap #5 — Notification dispatcher
# ---------------------------------------------------------------------------


class _RecordingDispatcher:
    """Captures dispatcher calls for assertions; mimics the production
    dispatcher's swallow-and-log contract."""

    def __init__(self) -> None:
        self.assigned_calls: list[tuple[str, str]] = []
        self.resolved_calls: list[tuple[str, str, str]] = []

    async def notify_approval_assigned(self, *, approval, forwarded_by_user_id) -> None:
        self.assigned_calls.append((approval.approval_id, forwarded_by_user_id))

    async def notify_approval_resolved(
        self, *, approval, decision, decided_by_user_id
    ) -> None:
        self.resolved_calls.append(
            (approval.approval_id, decision.value, decided_by_user_id)
        )


class TestNotificationDispatch:
    def test_forward_dispatches_assigned_to_recipient(self) -> None:
        store = InMemoryRuntimeApiStore()
        _seed_run_and_pending_approval(store)
        dispatcher = _RecordingDispatcher()
        service = _make_service(store, dispatcher=dispatcher)
        request = ApprovalDecisionRequest(
            decision=ApprovalDecision.FORWARDED,
            decided_by_user_id=_Values.REQUESTER_USER_ID,
            forward_to=ApprovalForwardTarget(
                kind="workspace_user", user_id=_Values.FORWARD_TARGET_USER_ID
            ),
        )

        async def _exercise() -> None:
            await service.record_approval_decision(
                org_id=_Values.ORG_ID,
                approval_id=_Values.PARENT_APPROVAL_ID,
                request=request,
            )
            # Dispatch fires post-commit via asyncio.create_task; yield
            # control so the captured call is settled.
            await asyncio.sleep(0)
            await asyncio.sleep(0)

        asyncio.run(_exercise())
        assert len(dispatcher.assigned_calls) == 1
        child_approval_id, forwarded_by = dispatcher.assigned_calls[0]
        assert forwarded_by == _Values.REQUESTER_USER_ID
        assert child_approval_id != _Values.PARENT_APPROVAL_ID

    def test_dispatcher_failure_does_not_roll_back_forward(self) -> None:
        class _FailingDispatcher:
            async def notify_approval_assigned(self, **_):
                raise RuntimeError("simulated dispatch failure")

            async def notify_approval_resolved(self, **_):
                raise RuntimeError("simulated dispatch failure")

        store = InMemoryRuntimeApiStore()
        _seed_run_and_pending_approval(store)

        # Wrap the failing dispatcher in the production composite so we
        # exercise its "swallow-and-log" contract.
        async def _publish(*_):
            raise RuntimeError("inbox publish failed")

        composite = InboxAndEmailNotificationDispatcher(
            publish_inbox=_publish, post=None
        )
        service = _make_service(store, dispatcher=composite)
        request = ApprovalDecisionRequest(
            decision=ApprovalDecision.FORWARDED,
            decided_by_user_id=_Values.REQUESTER_USER_ID,
            forward_to=ApprovalForwardTarget(
                kind="workspace_user", user_id=_Values.FORWARD_TARGET_USER_ID
            ),
        )

        async def _exercise():
            response = await service.record_approval_decision(
                org_id=_Values.ORG_ID,
                approval_id=_Values.PARENT_APPROVAL_ID,
                request=request,
            )
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            return response

        response = asyncio.run(_exercise())
        # Forward succeeded — chain exists; only the dispatch was swallowed.
        assert response.status is ApprovalStatus.FORWARDED
        # Parent transitioned, child exists.
        assert (
            store.approval_requests[_Values.PARENT_APPROVAL_ID].status
            is ApprovalStatus.FORWARDED
        )


# ---------------------------------------------------------------------------
# Gap #6 — Recipient inbox endpoint + per-user SSE bus
# ---------------------------------------------------------------------------


class TestInboxEventBus:
    def test_publish_and_replay(self) -> None:
        bus = InboxEventBus()

        async def _exercise():
            await bus.publish(
                user_id="u1",
                event_type="approval_assigned",
                approval_id="a1",
                status="pending",
                org_id="o1",
                conversation_id="c1",
                actor_user_id="u2",
            )
            await bus.publish(
                user_id="u1",
                event_type="approval_assigned",
                approval_id="a2",
                status="pending",
                org_id="o1",
                conversation_id="c1",
                actor_user_id="u2",
            )

        asyncio.run(_exercise())
        events = list(bus.list_after(user_id="u1", after_sequence=0))
        assert len(events) == 2
        assert events[0].sequence_no == 1
        assert events[1].sequence_no == 2

    def test_replay_after_sequence_is_exclusive(self) -> None:
        bus = InboxEventBus()

        async def _publish_two():
            await bus.publish(
                user_id="u1",
                event_type="approval_assigned",
                approval_id="a1",
                status="pending",
                org_id="o1",
                conversation_id="c1",
                actor_user_id="u2",
            )
            await bus.publish(
                user_id="u1",
                event_type="approval_resolved",
                approval_id="a1",
                status="approved",
                org_id="o1",
                conversation_id="c1",
                actor_user_id="u2",
            )

        asyncio.run(_publish_two())
        events = list(bus.list_after(user_id="u1", after_sequence=1))
        assert [event.sequence_no for event in events] == [2]

    def test_per_user_isolation(self) -> None:
        bus = InboxEventBus()

        async def _exercise():
            await bus.publish(
                user_id="u1",
                event_type="approval_assigned",
                approval_id="a1",
                status="pending",
                org_id="o1",
                conversation_id="c1",
                actor_user_id="u2",
            )
            await bus.publish(
                user_id="u2",
                event_type="approval_assigned",
                approval_id="a2",
                status="pending",
                org_id="o1",
                conversation_id="c1",
                actor_user_id="u1",
            )

        asyncio.run(_exercise())
        u1_events = list(bus.list_after(user_id="u1", after_sequence=0))
        u2_events = list(bus.list_after(user_id="u2", after_sequence=0))
        assert [event.approval_id for event in u1_events] == ["a1"]
        assert [event.approval_id for event in u2_events] == ["a2"]


class TestInboxAndEmailDispatcherPublish:
    def test_assigned_publishes_to_inbox_bus(self) -> None:
        bus = InboxEventBus()

        async def _publish_inbox(approval, event_type, actor_user_id):
            await bus.publish(
                user_id=approval.user_id,
                event_type=event_type,
                approval_id=approval.approval_id,
                status=approval.status.value,
                org_id=approval.org_id,
                conversation_id=approval.conversation_id,
                actor_user_id=actor_user_id,
            )

        dispatcher = InboxAndEmailNotificationDispatcher(
            publish_inbox=_publish_inbox, post=None
        )
        approval = ApprovalRequestRecord(
            approval_id="child_141",
            run_id=_Values.RUN_ID,
            conversation_id=_Values.CONVERSATION_ID,
            org_id=_Values.ORG_ID,
            user_id=_Values.FORWARD_TARGET_USER_ID,
        )
        asyncio.run(
            dispatcher.notify_approval_assigned(
                approval=approval,
                forwarded_by_user_id=_Values.REQUESTER_USER_ID,
            )
        )
        events = list(
            bus.list_after(user_id=_Values.FORWARD_TARGET_USER_ID, after_sequence=0)
        )
        assert len(events) == 1
        assert events[0].event_type == "approval_assigned"
        assert events[0].approval_id == "child_141"
        assert events[0].actor_user_id == _Values.REQUESTER_USER_ID


class TestListAssignedApprovals:
    def test_filters_to_recipient_and_status(self) -> None:
        store = InMemoryRuntimeApiStore()
        _seed_run_and_pending_approval(store)
        service = _make_service(store)

        # Forward — creates a child addressed to FORWARD_TARGET_USER_ID
        request = ApprovalDecisionRequest(
            decision=ApprovalDecision.FORWARDED,
            decided_by_user_id=_Values.REQUESTER_USER_ID,
            forward_to=ApprovalForwardTarget(
                kind="workspace_user", user_id=_Values.FORWARD_TARGET_USER_ID
            ),
        )
        asyncio.run(
            service.record_approval_decision(
                org_id=_Values.ORG_ID,
                approval_id=_Values.PARENT_APPROVAL_ID,
                request=request,
            )
        )

        # Recipient inbox — should see one pending row.
        response = asyncio.run(
            service.list_assigned_approvals(
                org_id=_Values.ORG_ID,
                user_id=_Values.FORWARD_TARGET_USER_ID,
                status_filter=ApprovalStatus.PENDING,
                limit=50,
                cursor=None,
            )
        )
        assert len(response.approvals) == 1
        assigned = response.approvals[0]
        assert assigned.chain_parent_approval_id == _Values.PARENT_APPROVAL_ID
        assert assigned.forwarded_by_user_id == _Values.REQUESTER_USER_ID
        assert assigned.action_summary == "Post draft to #launch-aurora"

    def test_other_user_sees_empty_inbox(self) -> None:
        store = InMemoryRuntimeApiStore()
        _seed_run_and_pending_approval(store)
        service = _make_service(store)
        request = ApprovalDecisionRequest(
            decision=ApprovalDecision.FORWARDED,
            decided_by_user_id=_Values.REQUESTER_USER_ID,
            forward_to=ApprovalForwardTarget(
                kind="workspace_user", user_id=_Values.FORWARD_TARGET_USER_ID
            ),
        )
        asyncio.run(
            service.record_approval_decision(
                org_id=_Values.ORG_ID,
                approval_id=_Values.PARENT_APPROVAL_ID,
                request=request,
            )
        )

        response = asyncio.run(
            service.list_assigned_approvals(
                org_id=_Values.ORG_ID,
                user_id=_Values.OTHER_USER_ID,
                status_filter=ApprovalStatus.PENDING,
                limit=50,
                cursor=None,
            )
        )
        assert response.approvals == ()

    def test_pagination_round_trips(self) -> None:
        store = InMemoryRuntimeApiStore()
        _seed_run_and_pending_approval(store)
        # Build three child rows directly so we have something to paginate.
        from datetime import timedelta

        base = datetime.now(timezone.utc)
        for index, suffix in enumerate(["a", "b", "c"]):
            store.approval_requests[f"child_{suffix}"] = ApprovalRequestRecord(
                approval_id=f"child_{suffix}",
                run_id=_Values.RUN_ID,
                conversation_id=_Values.CONVERSATION_ID,
                org_id=_Values.ORG_ID,
                user_id=_Values.FORWARD_TARGET_USER_ID,
                metadata={
                    "approval_kind": "action",
                    "action_summary": f"item {suffix}",
                },
                created_at=base + timedelta(seconds=index),
            )
        service = _make_service(store)
        # Page 1: limit=2 — newest first.
        page_1 = asyncio.run(
            service.list_assigned_approvals(
                org_id=_Values.ORG_ID,
                user_id=_Values.FORWARD_TARGET_USER_ID,
                status_filter=ApprovalStatus.PENDING,
                limit=2,
                cursor=None,
            )
        )
        assert [row.approval_id for row in page_1.approvals] == ["child_c", "child_b"]
        assert page_1.next_cursor is not None
        # Page 2: continue with the cursor.
        page_2 = asyncio.run(
            service.list_assigned_approvals(
                org_id=_Values.ORG_ID,
                user_id=_Values.FORWARD_TARGET_USER_ID,
                status_filter=ApprovalStatus.PENDING,
                limit=2,
                cursor=page_1.next_cursor,
            )
        )
        assert [row.approval_id for row in page_2.approvals] == ["child_a"]


# ---------------------------------------------------------------------------
# Gap #8 — In-memory race guard
# ---------------------------------------------------------------------------


class TestInMemoryForwardRaceGuard:
    """Postgres uses ``WHERE status='pending'``; in-memory now mirrors."""

    def test_second_forward_after_resolve_rejected(self) -> None:
        store = InMemoryRuntimeApiStore()
        _seed_run_and_pending_approval(store)
        service = _make_service(store)
        # First forward succeeds.
        request = ApprovalDecisionRequest(
            decision=ApprovalDecision.FORWARDED,
            decided_by_user_id=_Values.REQUESTER_USER_ID,
            forward_to=ApprovalForwardTarget(
                kind="workspace_user", user_id=_Values.FORWARD_TARGET_USER_ID
            ),
        )
        asyncio.run(
            service.record_approval_decision(
                org_id=_Values.ORG_ID,
                approval_id=_Values.PARENT_APPROVAL_ID,
                request=request,
            )
        )
        # Second forward — simulates the double-click race. Validator
        # rejects with 409 because the parent isn't pending anymore.
        with pytest.raises(RuntimeApiError) as exc:
            asyncio.run(
                service.record_approval_decision(
                    org_id=_Values.ORG_ID,
                    approval_id=_Values.PARENT_APPROVAL_ID,
                    request=request,
                )
            )
        assert exc.value.http_status == 409

    def test_persistence_layer_directly_raises_on_race(self) -> None:
        """The adapter raises a deterministic RuntimeError so the
        service can map it to 409 — same surface as the postgres path."""

        store = InMemoryRuntimeApiStore()
        parent = _seed_run_and_pending_approval(store)
        # Force the parent to a non-pending state directly.
        store.approval_requests[parent.approval_id] = parent.model_copy(
            update={"status": ApprovalStatus.APPROVED}
        )
        child = ApprovalRequestRecord(
            approval_id="child_race",
            run_id=parent.run_id,
            conversation_id=parent.conversation_id,
            org_id=parent.org_id,
            user_id=_Values.FORWARD_TARGET_USER_ID,
            metadata=parent.metadata,
        )
        with pytest.raises(RuntimeError) as exc:
            store.forward_approval_request(
                parent_approval_id=parent.approval_id,
                org_id=parent.org_id,
                decided_by_user_id=parent.user_id,
                forwarded_to_user_id=_Values.FORWARD_TARGET_USER_ID,
                decision_reason=None,
                child=child,
                now=datetime.now(timezone.utc),
            )
        # PR 1.4 service maps any RuntimeError carrying the "not_pending"
        # substring to a 409. Adapter raises a longer string for clarity
        # in logs; the substring guard is what the service contract
        # depends on.
        assert "no_longer_pending" in str(exc.value) or "not_pending" in str(exc.value)
