"""Proposal accept / reject tests — Phase 12 P12-A3.

Coverage:

* Accept transitions ``status → "accepted"`` AND creates a MemoryItem.
* The created item is owned by the accepting user, kind matches the
  proposal, and the title/body are passed through (with overrides
  respected).
* Reject transitions ``status → "rejected"`` and does NOT create a
  memory item.
* Accept/reject on a non-pending proposal raises invalid_request.
* Proposals are owner-scoped: another user cannot accept/reject.
"""

from __future__ import annotations

import pytest

from backend_app.memory.service import (
    MemoryInvalidRequest,
    MemoryNotFound,
    MemoryService,
)
from backend_app.memory.store import InMemoryMemoryStore, MemoryProposalRecord


def _seed_proposal(
    store: InMemoryMemoryStore,
    *,
    user_id: str = "usr_sarah",
    kind: str = "preference",
    title: str = "Sign off with 'Best, Sarah'",
    body: str = "",
) -> MemoryProposalRecord:
    return store.insert_proposal(
        MemoryProposalRecord(
            tenant_id="org_acme",
            user_id=user_id,
            proposed_kind=kind,  # type: ignore[arg-type]
            proposed_title=title,
            proposed_body=body,
            source={"kind": "chat", "id": "chat_abc"},
        )
    )


def _svc() -> tuple[MemoryService, InMemoryMemoryStore]:
    store = InMemoryMemoryStore()
    return MemoryService(store=store), store


def test_accept_creates_memory_and_marks_accepted() -> None:
    svc, store = _svc()
    proposal = _seed_proposal(store)
    decided, memory = svc.accept_proposal(
        tenant_id="org_acme",
        caller_user_id="usr_sarah",
        proposal_id=proposal.id,
    )
    assert decided.status == "accepted"
    assert decided.decided_at is not None
    assert decided.accepted_memory_id == memory.id
    # Memory row carries the proposal's kind + title.
    assert memory.kind == "preference"
    assert memory.title == proposal.proposed_title
    # created_by is an "agent" entry (the chat that surfaced the proposal).
    assert memory.created_by.get("kind") == "agent"


def test_accept_with_overrides() -> None:
    svc, store = _svc()
    proposal = _seed_proposal(store, title="original")
    _, memory = svc.accept_proposal(
        tenant_id="org_acme",
        caller_user_id="usr_sarah",
        proposal_id=proposal.id,
        title_override="user edit",
        scope_override="workspace",
        tags=["from-chat"],
    )
    assert memory.title == "user edit"
    assert memory.scope == "workspace"
    assert "from-chat" in memory.tags


def test_reject_marks_status_without_creating_memory() -> None:
    svc, store = _svc()
    proposal = _seed_proposal(store)
    decided = svc.reject_proposal(
        tenant_id="org_acme",
        caller_user_id="usr_sarah",
        proposal_id=proposal.id,
    )
    assert decided.status == "rejected"
    assert decided.decided_at is not None
    # No memory rows landed.
    assert store.items == {}


def test_accept_non_pending_proposal_400() -> None:
    svc, store = _svc()
    proposal = _seed_proposal(store)
    svc.accept_proposal(
        tenant_id="org_acme",
        caller_user_id="usr_sarah",
        proposal_id=proposal.id,
    )
    # Second accept attempt — already in terminal state.
    with pytest.raises(MemoryInvalidRequest):
        svc.accept_proposal(
            tenant_id="org_acme",
            caller_user_id="usr_sarah",
            proposal_id=proposal.id,
        )


def test_cross_user_accept_404() -> None:
    svc, store = _svc()
    proposal = _seed_proposal(store, user_id="usr_sarah")
    with pytest.raises(MemoryNotFound):
        svc.accept_proposal(
            tenant_id="org_acme",
            caller_user_id="usr_bob",  # different user
            proposal_id=proposal.id,
        )


def test_list_proposals_owner_scoped() -> None:
    svc, store = _svc()
    _seed_proposal(store, user_id="usr_sarah", title="for sarah")
    _seed_proposal(store, user_id="usr_bob", title="for bob")
    rows, _ = svc.list_proposals(
        tenant_id="org_acme",
        caller_user_id="usr_sarah",
        statuses=("pending",),
    )
    titles = {r.proposed_title for r in rows}
    assert "for sarah" in titles
    assert "for bob" not in titles
