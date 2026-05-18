"""SSE bus tests — Phase 12 P12-A3.

Coverage:

* One envelope sequence — publish three events, list_after returns
  them in monotonic sequence order.
* Tenant isolation — a publish to (org_a, usr_1) is invisible to
  (org_b, usr_1).
* ``memory.deleted`` envelopes carry only the ``deleted_id``; ``item``
  is None.
"""

from __future__ import annotations

import asyncio

from backend_app.memory.sse import InMemoryMemoryActivityBus
from backend_app.memory.store import MemoryItemRecord


def _record(title: str = "row", scope: str = "user") -> MemoryItemRecord:
    return MemoryItemRecord(
        tenant_id="org_acme",
        owner_user_id="usr_sarah",
        scope=scope,  # type: ignore[arg-type]
        kind="skill",
        title=title,
        body="",
        created_by={"kind": "user", "id": "usr_sarah"},
    )


def test_publish_monotonic_sequence() -> None:
    bus = InMemoryMemoryActivityBus()

    async def _run() -> list[int]:
        await bus.publish(
            org_id="org_acme",
            user_id="usr_sarah",
            event_type="memory.created",
            item=_record("a"),
        )
        await bus.publish(
            org_id="org_acme",
            user_id="usr_sarah",
            event_type="memory.updated",
            item=_record("a-updated"),
        )
        await bus.publish(
            org_id="org_acme",
            user_id="usr_sarah",
            event_type="memory.deleted",
            deleted_id="mem_a",
        )
        return [
            e.sequence_no
            for e in bus.list_after(
                org_id="org_acme", user_id="usr_sarah", after_sequence=0
            )
        ]

    sequence = asyncio.run(_run())
    assert sequence == [1, 2, 3]
    # The last envelope is the deleted one — carries deleted_id, no item.
    events = list(
        bus.list_after(org_id="org_acme", user_id="usr_sarah", after_sequence=2)
    )
    assert len(events) == 1
    assert events[0].event_type == "memory.deleted"
    assert events[0].item is None
    assert events[0].deleted_id == "mem_a"


def test_tenant_isolation() -> None:
    bus = InMemoryMemoryActivityBus()

    async def _run() -> None:
        await bus.publish(
            org_id="org_acme",
            user_id="usr_sarah",
            event_type="memory.created",
            item=_record(),
        )
        await bus.publish(
            org_id="org_zeta",
            user_id="usr_sarah",
            event_type="memory.created",
            item=_record(),
        )

    asyncio.run(_run())
    acme = list(
        bus.list_after(org_id="org_acme", user_id="usr_sarah", after_sequence=0)
    )
    zeta = list(
        bus.list_after(org_id="org_zeta", user_id="usr_sarah", after_sequence=0)
    )
    assert len(acme) == 1
    assert len(zeta) == 1
    # Sequence numbers are per-channel — both at 1.
    assert acme[0].sequence_no == 1
    assert zeta[0].sequence_no == 1


def test_proposal_appended_envelope() -> None:
    bus = InMemoryMemoryActivityBus()

    async def _run() -> None:
        await bus.publish(
            org_id="org_acme",
            user_id="usr_sarah",
            event_type="memory.proposal_appended",
            proposal={"id": "memprop_x", "status": "pending"},
        )

    asyncio.run(_run())
    events = list(
        bus.list_after(org_id="org_acme", user_id="usr_sarah", after_sequence=0)
    )
    assert len(events) == 1
    assert events[0].event_type == "memory.proposal_appended"
    assert events[0].proposal is not None
    assert events[0].proposal["id"] == "memprop_x"
