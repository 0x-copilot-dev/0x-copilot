"""Tests for ``InboxService`` — state machine + snooze logic + body split (P4-A1).

Service-layer tests exercise the business rules without going through
HTTP; the route tests in ``test_inbox_routes.py`` cover the wire path.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend_app.identity.store import InMemoryIdentityStore
from backend_app.inbox.service import (
    InboxForbidden,
    InboxInvalidRequest,
    InboxNotFound,
    InboxService,
)
from backend_app.inbox.store import InMemoryInboxStore


def _service() -> tuple[InboxService, InMemoryInboxStore]:
    store = InMemoryInboxStore()
    service = InboxService(store=store, identity_store=InMemoryIdentityStore())
    return service, store


def _seed(
    service: InboxService,
    *,
    tenant_id: str = "t",
    owner_user_id: str = "u",
    kind: str = "mention",
    title: str = "x",
    body_markdown: str | None = None,
):
    return service.insert_item_with_body(
        tenant_id=tenant_id,
        owner_user_id=owner_user_id,
        kind=kind,
        title=title,
        sender={"ref": {"kind": "agent", "id": "agent_atlas"}},
        body_markdown=body_markdown,
    )


class TestStateMachine:
    def test_mark_read_sets_read_at(self) -> None:
        service, _ = _service()
        item = _seed(service)
        updated = service.update_item(
            tenant_id="t",
            caller_user_id="u",
            caller_roles=(),
            item_id=item.id,
            patch={"state": "read"},
        )
        assert updated.state == "read"
        assert updated.read_at is not None

    def test_snooze_requires_future_timestamp(self) -> None:
        service, _ = _service()
        item = _seed(service)
        past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        with pytest.raises(InboxInvalidRequest):
            service.update_item(
                tenant_id="t",
                caller_user_id="u",
                caller_roles=(),
                item_id=item.id,
                patch={"state": "snoozed", "snoozed_until": past},
            )

    def test_snooze_requires_explicit_snoozed_until(self) -> None:
        service, _ = _service()
        item = _seed(service)
        with pytest.raises(InboxInvalidRequest):
            service.update_item(
                tenant_id="t",
                caller_user_id="u",
                caller_roles=(),
                item_id=item.id,
                patch={"state": "snoozed"},
            )

    def test_snooze_invalid_iso_string(self) -> None:
        service, _ = _service()
        item = _seed(service)
        with pytest.raises(InboxInvalidRequest):
            service.update_item(
                tenant_id="t",
                caller_user_id="u",
                caller_roles=(),
                item_id=item.id,
                patch={"state": "snoozed", "snoozed_until": "not-a-date"},
            )

    def test_snooze_accepts_z_suffix(self) -> None:
        service, _ = _service()
        item = _seed(service)
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        updated = service.update_item(
            tenant_id="t",
            caller_user_id="u",
            caller_roles=(),
            item_id=item.id,
            patch={"state": "snoozed", "snoozed_until": future},
        )
        assert updated.state == "snoozed"
        assert updated.snoozed_until is not None

    def test_dismissed_is_terminal(self) -> None:
        service, _ = _service()
        item = _seed(service)
        service.update_item(
            tenant_id="t",
            caller_user_id="u",
            caller_roles=(),
            item_id=item.id,
            patch={"state": "dismissed"},
        )
        with pytest.raises(InboxInvalidRequest):
            service.update_item(
                tenant_id="t",
                caller_user_id="u",
                caller_roles=(),
                item_id=item.id,
                patch={"state": "unread"},
            )

    def test_mark_read_clears_snoozed_until(self) -> None:
        service, _ = _service()
        item = _seed(service)
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        service.update_item(
            tenant_id="t",
            caller_user_id="u",
            caller_roles=(),
            item_id=item.id,
            patch={"state": "snoozed", "snoozed_until": future},
        )
        updated = service.update_item(
            tenant_id="t",
            caller_user_id="u",
            caller_roles=(),
            item_id=item.id,
            patch={"state": "read"},
        )
        assert updated.snoozed_until is None

    def test_invalid_state_rejected(self) -> None:
        service, _ = _service()
        item = _seed(service)
        with pytest.raises(InboxInvalidRequest):
            service.update_item(
                tenant_id="t",
                caller_user_id="u",
                caller_roles=(),
                item_id=item.id,
                patch={"state": "exploded"},
            )


class TestBodySplit:
    def test_body_stored_in_separate_record(self) -> None:
        service, store = _service()
        item = _seed(service, body_markdown="big payload")
        assert item.body_ref is not None
        # Body lives in the bodies dict.
        assert store.bodies[item.body_ref].body_markdown == "big payload"

    def test_get_body_markdown_returns_payload(self) -> None:
        service, _ = _service()
        item = _seed(service, body_markdown="hello world")
        record, body = service.get_body_markdown(
            tenant_id="t",
            caller_user_id="u",
            caller_roles=(),
            item_id=item.id,
        )
        assert record.id == item.id
        assert body == "hello world"

    def test_get_body_markdown_returns_none_when_absent(self) -> None:
        service, _ = _service()
        item = _seed(service)  # no body_markdown
        record, body = service.get_body_markdown(
            tenant_id="t",
            caller_user_id="u",
            caller_roles=(),
            item_id=item.id,
        )
        assert record.id == item.id
        assert body is None

    def test_get_body_404_for_non_reader(self) -> None:
        service, _ = _service()
        item = _seed(service, body_markdown="secret")
        with pytest.raises(InboxNotFound):
            service.get_body_markdown(
                tenant_id="t",
                caller_user_id="other",
                caller_roles=(),
                item_id=item.id,
            )


class TestAcl:
    def test_non_recipient_gets_404(self) -> None:
        service, _ = _service()
        item = _seed(service)
        with pytest.raises(InboxNotFound):
            service.get_item(
                tenant_id="t",
                caller_user_id="other",
                caller_roles=(),
                item_id=item.id,
            )

    def test_admin_reads_but_cannot_write(self) -> None:
        service, _ = _service()
        item = _seed(service)
        # Admin can read.
        record = service.get_item(
            tenant_id="t",
            caller_user_id="admin_user",
            caller_roles=("admin",),
            item_id=item.id,
        )
        assert record.id == item.id
        # Admin cannot write.
        with pytest.raises(InboxForbidden):
            service.update_item(
                tenant_id="t",
                caller_user_id="admin_user",
                caller_roles=("admin",),
                item_id=item.id,
                patch={"state": "read"},
            )


class TestInsertValidation:
    def test_insert_rejects_invalid_kind(self) -> None:
        service, _ = _service()
        with pytest.raises(InboxInvalidRequest):
            service.insert_item_with_body(
                tenant_id="t",
                owner_user_id="u",
                kind="not_a_kind",
                title="x",
                sender={},
            )

    def test_insert_rejects_empty_title(self) -> None:
        service, _ = _service()
        with pytest.raises(InboxInvalidRequest):
            service.insert_item_with_body(
                tenant_id="t",
                owner_user_id="u",
                kind="mention",
                title="   ",
                sender={},
            )


class TestUnreadCount:
    def test_count_unread_recipient_scoped(self) -> None:
        service, store = _service()
        _seed(service)
        _seed(service)
        # Read one.
        item = _seed(service)
        service.update_item(
            tenant_id="t",
            caller_user_id="u",
            caller_roles=(),
            item_id=item.id,
            patch={"state": "read"},
        )
        assert service.count_unread(tenant_id="t", caller_user_id="u") == 2

    def test_count_unread_ignores_other_users(self) -> None:
        service, _ = _service()
        _seed(service, owner_user_id="u1")
        _seed(service, owner_user_id="u2")
        assert service.count_unread(tenant_id="t", caller_user_id="u1") == 1
