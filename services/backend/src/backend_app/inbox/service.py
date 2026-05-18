"""Inbox service — ACL + state machine + audit.

The route layer in ``routes.py`` is presentation-only; every
business-logic decision lives here so the in-memory ``InMemoryInboxStore``
and the postgres adapter share one set of authorization checks and
state-machine transitions.

Authorization rules (cross-audit §1.3, binding):

* Recipient-only writes (the user the item is addressed to).
* Reads: recipient OR (project_id member when project_id is set) OR
  tenant admin (read-only compliance — admins cannot mutate another
  user's inbox per inbox-prd §7.2).
* Non-readers see 404, not 403 (existence not leaked).

State machine (inbox-prd §3 + api-types/src/inbox.ts):

    unread ──read──▶ read
       │              │
       ├──snooze──▶ snoozed (with snoozed_until; cron wakes → unread)
       │
       └──dismiss──▶ dismissed (terminal soft-delete)

* `snoozed` requires `snoozed_until > now` (server-validated).
* `dismissed` is terminal — re-opening is not supported via PATCH; the
  producer can revive via the internal endpoint (P4-A2) using the same
  `external_ref` (idempotency takes the existing row).

Audit rows are append-only; bulk actions stamp a shared
``correlation_id`` on every row so SIEM can reconstruct the bulk.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable

from backend_app.identity.store import IdentityStore
from backend_app.inbox.store import (
    InboxAuditRecord,
    InboxBodyRecord,
    InboxItemRecord,
    InboxStore,
)
from backend_app.inbox.sse import InboxActivityBus, InboxEventType
from backend_app.projects.acl import (
    ProjectMembershipPort,
    _NoMemberProjectAdapter,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


# Roles with tenant-admin read access. Treated as untrusted unless the
# verified ``ScopedIdentity.roles`` tuple set them — the route layer
# passes through what the auth middleware verified.
_ADMIN_ROLES = frozenset({"admin", "owner"})

_VALID_KINDS = frozenset(
    {
        "approval_request",
        "mention",
        "error",
        "agent_question",
        "share_invite",
        "system_announcement",
        # ``system`` is the producer-surface generic system-origin kind
        # (inbox-prd §4.5); kept in the canonical allowlist so producer
        # + CRUD share one source of truth instead of diverging.
        "system",
    }
)
_VALID_STATES = frozenset({"unread", "read", "snoozed", "dismissed"})


class InboxNotFound(Exception):
    """Raised when an item doesn't exist OR the caller has no read rights.

    The 404-not-403 rule (cross-audit §1.3) collapses both branches to
    one exception so the route layer cannot accidentally distinguish
    them — the response is always 404.
    """


class InboxForbidden(Exception):
    """Raised when the caller can READ but cannot WRITE.

    Used after read access has already been established (so 404-not-403
    still applies for the read-doesn't-exist case). The route layer
    translates this to 403.
    """


class InboxInvalidRequest(Exception):
    """Raised for client-fixable invariant violations (400)."""


class InboxService:
    """Composition of the inbox store + identity store with ACL + audit."""

    def __init__(
        self,
        *,
        store: InboxStore,
        identity_store: IdentityStore,
        project_membership: "ProjectMembershipPort | None" = None,
        activity_bus: "InboxActivityBus | None" = None,
    ) -> None:
        self._store = store
        self._identity = identity_store
        # Project membership lookup is injected so the in-memory tests
        # don't need the (not-yet-shipped) Projects destination. Defaults
        # to a no-member adapter — recipient-only behaviour until the
        # Projects destination lands and registers a real adapter.
        self._project_membership = project_membership or _NoMemberProjectAdapter()
        # Activity bus is optional — tests/dev wiring may pass ``None`` and
        # the publish helpers become no-ops. Production wiring (see
        # ``backend_app.app.create_app``) sets this to the bus stashed on
        # ``app.state.inbox_activity_bus`` by ``register_inbox_sse_routes``
        # so mutations stream out as ``item_added`` / ``item_updated``
        # frames to the SSE subscribers.
        self._activity_bus = activity_bus

    @property
    def activity_bus(self) -> "InboxActivityBus | None":
        """Expose the configured bus so async route handlers can publish."""

        return self._activity_bus

    # -- reads ---------------------------------------------------------

    def get_item(
        self,
        *,
        tenant_id: str,
        caller_user_id: str,
        caller_roles: Iterable[str],
        item_id: str,
    ) -> InboxItemRecord:
        """Authorise + return a single inbox item.

        Raises :class:`InboxNotFound` if the caller can't see it (which
        is what 404-not-403 demands; the route never distinguishes
        "not found" from "not authorised").
        """

        record = self._store.get_item(tenant_id=tenant_id, item_id=item_id)
        if record is None:
            raise InboxNotFound(item_id)
        if not self._can_read(record, caller_user_id, caller_roles):
            raise InboxNotFound(item_id)
        return record

    def list_items(
        self,
        *,
        tenant_id: str,
        caller_user_id: str,
        caller_roles: Iterable[str],
        states: tuple[str, ...] | None = None,
        kinds: tuple[str, ...] | None = None,
        project_ids: tuple[str | None, ...] | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> tuple[tuple[InboxItemRecord, ...], str | None, int]:
        """List the caller's readable inbox items + the unread count.

        Composition mirrors :class:`TodosService.list_todos`:

        1. Items addressed to the caller (the common path).
        2. Items in projects the caller is a member of (read-only).
        3. (Admin only) every item in the tenant (compliance read).

        ``unread_count`` is recipient-scoped only — the rail badge
        counts the user's own unread inbox, not project-member or
        admin-compliance visibility.
        """

        admin = any(role in _ADMIN_ROLES for role in caller_roles)
        if admin:
            page, next_cursor = self._store.list_items(
                tenant_id=tenant_id,
                owner_user_id=None,
                states=states,
                kinds=kinds,
                project_ids=project_ids,
                cursor=cursor,
                limit=limit,
            )
            unread = self._store.count_unread(
                tenant_id=tenant_id, owner_user_id=caller_user_id
            )
            return page, next_cursor, unread

        owner_page, next_cursor = self._store.list_items(
            tenant_id=tenant_id,
            owner_user_id=caller_user_id,
            states=states,
            kinds=kinds,
            project_ids=project_ids,
            cursor=cursor,
            limit=limit,
        )
        member_projects = self._project_membership.list_projects_for_user(
            tenant_id=tenant_id, user_id=caller_user_id
        )
        unread = self._store.count_unread(
            tenant_id=tenant_id, owner_user_id=caller_user_id
        )
        if not member_projects:
            return owner_page, next_cursor, unread

        # Project-member reads: only listing rows whose project_id is in
        # the member-project set AND owner ≠ caller (already in owner
        # bucket). Cursor pagination over the union is approximated by
        # taking the owner page as canonical and appending project
        # rows; full keyset merge is a postgres-layer concern.
        project_page, _project_next = self._store.list_project_member_items(
            tenant_id=tenant_id,
            project_ids=member_projects,
            cursor=None,
            limit=limit,
        )
        seen = {r.id for r in owner_page}
        merged = list(owner_page) + [r for r in project_page if r.id not in seen]
        if states is not None:
            merged = [r for r in merged if r.state in states]
        if kinds is not None:
            merged = [r for r in merged if r.kind in kinds]
        if project_ids is not None:
            merged = [r for r in merged if r.project_id in project_ids]
        return tuple(merged[:limit]), next_cursor, unread

    def get_body_markdown(
        self,
        *,
        tenant_id: str,
        caller_user_id: str,
        caller_roles: Iterable[str],
        item_id: str,
    ) -> tuple[InboxItemRecord, str | None]:
        """Authorise + return ``(item, body_markdown)``.

        Body bytes are split out from the list endpoint per inbox-prd
        §3 + §10 — list responses never carry body bytes. The body is
        ACL-checked through the parent item (404-not-403 inherited).
        """

        record = self.get_item(
            tenant_id=tenant_id,
            caller_user_id=caller_user_id,
            caller_roles=caller_roles,
            item_id=item_id,
        )
        body_text: str | None = None
        if record.body_ref is not None:
            body = self._store.get_body(tenant_id=tenant_id, body_ref=record.body_ref)
            body_text = body.body_markdown if body is not None else None
        return record, body_text

    def count_unread(
        self,
        *,
        tenant_id: str,
        caller_user_id: str,
    ) -> int:
        """Return the recipient-scoped unread count (rail badge)."""

        return self._store.count_unread(
            tenant_id=tenant_id, owner_user_id=caller_user_id
        )

    # -- writes --------------------------------------------------------

    def update_item(
        self,
        *,
        tenant_id: str,
        caller_user_id: str,
        caller_roles: Iterable[str],
        item_id: str,
        patch: dict,
        correlation_id: str | None = None,
    ) -> InboxItemRecord:
        """Patch state on an inbox item. Recipient-only writes.

        ``patch`` carries ``state`` and optional ``snoozed_until``. The
        state-machine transitions are validated here; invalid moves
        (e.g. snooze without future ``snoozed_until``) raise
        :class:`InboxInvalidRequest`.
        """

        existing = self._store.get_item(tenant_id=tenant_id, item_id=item_id)
        # 404-not-403 on both "missing" and "no read rights" branches.
        if existing is None or not self._can_read(
            existing, caller_user_id, caller_roles
        ):
            raise InboxNotFound(item_id)
        if existing.owner_user_id != caller_user_id:
            # Read access established (project member or admin) but
            # writes are recipient-only. cross-audit §1.3 + inbox-prd §7.2.
            raise InboxForbidden(item_id)

        new_state = patch.get("state")
        if new_state is None:
            # No-op PATCH (no recognised field). Return existing.
            return existing
        if new_state not in _VALID_STATES:
            raise InboxInvalidRequest("invalid_state")
        if existing.state == "dismissed" and new_state != "dismissed":
            # Terminal; producer must revive via internal endpoint
            # (P4-A2 owns that path).
            raise InboxInvalidRequest("dismissed_is_terminal")

        updates: dict = {"state": new_state, "updated_at": _now()}
        audit_action = f"inbox.mark_{new_state}"
        if new_state == "read":
            updates["read_at"] = _now()
            updates["snoozed_until"] = None
        elif new_state == "unread":
            # Wake-from-snooze path (used by the cron + by the user
            # explicitly marking a read row as unread).
            updates["read_at"] = None
            updates["snoozed_until"] = None
        elif new_state == "snoozed":
            snoozed_until_raw = patch.get("snoozed_until")
            if not snoozed_until_raw:
                raise InboxInvalidRequest("snoozed_until_required")
            snoozed_until = _parse_iso(snoozed_until_raw)
            if snoozed_until is None:
                raise InboxInvalidRequest("snoozed_until_invalid")
            if snoozed_until <= _now():
                raise InboxInvalidRequest("snoozed_until_must_be_future")
            updates["snoozed_until"] = snoozed_until
        elif new_state == "dismissed":
            updates["dismissed_at"] = _now()

        new_record = existing.model_copy(update=updates)
        before_state = _safe_dump(existing)
        after_state = _safe_dump(new_record)
        with self._store.transaction():
            stored = self._store.update_item(new_record)
            self._store.append_audit(
                InboxAuditRecord(
                    tenant_id=tenant_id,
                    actor_user_id=caller_user_id,
                    action=audit_action,
                    target_id=stored.id,
                    before_state=before_state,
                    after_state=after_state,
                    correlation_id=correlation_id,
                )
            )
        return stored

    def bulk_update(
        self,
        *,
        tenant_id: str,
        caller_user_id: str,
        caller_roles: Iterable[str],
        action: str,
        ids: tuple[str, ...],
        correlation_id: str,
        payload: dict | None = None,
    ) -> tuple[int, tuple[InboxItemRecord, ...]]:
        """Apply ``action`` across multiple inbox items.

        Best-effort: ids the caller cannot write are silently skipped
        (the bulk shouldn't 404 if one row dropped out mid-flight). The
        return value is ``(affected_count, updated_records)`` — the
        records are exposed so the route layer can fan out one SSE
        ``item_updated`` per mutated row (each row may belong to a
        different bus channel in the cross-recipient bulk paths a future
        admin tool would surface, though P4-A1 already gates the bulk to
        recipient-owned rows). SIEM reconstruction uses the shared
        ``correlation_id`` stamped on every audit row written by this
        method.
        """

        if action not in {"mark_read", "mark_unread", "dismiss", "snooze"}:
            raise InboxInvalidRequest("invalid_bulk_action")
        if not correlation_id or not correlation_id.strip():
            raise InboxInvalidRequest("correlation_id_required")
        payload = payload or {}

        # Translate the bulk verb into a per-item PATCH so the
        # state-machine + audit path is shared with the single-item
        # update_item code. The audit row's correlation_id stamps the
        # bulk identity.
        if action == "mark_read":
            patch = {"state": "read"}
        elif action == "mark_unread":
            patch = {"state": "unread"}
        elif action == "dismiss":
            patch = {"state": "dismissed"}
        else:  # snooze
            snoozed_until = payload.get("snoozed_until")
            if not snoozed_until:
                raise InboxInvalidRequest("snoozed_until_required")
            patch = {"state": "snoozed", "snoozed_until": snoozed_until}

        updated: list[InboxItemRecord] = []
        for item_id in ids:
            record = self._store.get_item(tenant_id=tenant_id, item_id=item_id)
            if record is None or record.owner_user_id != caller_user_id:
                # Skip non-owned / cross-tenant ids without leaking
                # their existence in the response. cross-audit §1.3.
                continue
            try:
                mutated = self.update_item(
                    tenant_id=tenant_id,
                    caller_user_id=caller_user_id,
                    caller_roles=caller_roles,
                    item_id=item_id,
                    patch=patch,
                    correlation_id=correlation_id,
                )
            except (InboxInvalidRequest, InboxForbidden, InboxNotFound):
                continue
            updated.append(mutated)
        return len(updated), tuple(updated)

    # -- streaming (publish on the activity bus) -----------------------

    async def publish_event(
        self,
        *,
        record: InboxItemRecord,
        event_type: InboxEventType,
    ) -> None:
        """Publish an ``item_added`` / ``item_updated`` event on the bus.

        Called by the route layer *after* the service mutation returns —
        i.e. after the durable write (``with self._store.transaction():``)
        has committed and the audit row landed. Publishing post-commit
        means a rollback never leaks a "phantom" stream event.

        Tenant isolation: the channel key is ``(record.tenant_id,
        record.owner_user_id)`` — the bus's ``list_after`` filter only
        returns events that match. A cross-tenant subscriber never sees
        another tenant's frames (inbox-prd §7.1).

        PII discipline (brief rule 3 + inbox-prd §6): the payload carries
        the wire-safe :func:`to_event_payload` view — ``body_ref`` (the
        opaque pointer) but never ``body_markdown`` content. The
        subject/title is included because the FE renders it in the rail;
        if a tenant policy demands redaction, the audit-export layer
        handles it the same way it redacts audit ``after_state``.

        No-op when the bus is not configured (tests, or a deployment
        that has the destination wired without the SSE adapter).
        """

        bus = self._activity_bus
        if bus is None:
            return
        await bus.publish(
            org_id=record.tenant_id,
            user_id=record.owner_user_id,
            event_type=event_type,
            item=self.to_event_payload(record),
        )

    @staticmethod
    def to_event_payload(record: InboxItemRecord) -> dict[str, Any]:
        """Project an :class:`InboxItemRecord` into the SSE wire shape.

        Mirrors ``packages/api-types::InboxItem`` (and by extension the
        ``InboxStreamEnvelope.item`` field). Body bytes are deliberately
        omitted — only ``body_ref`` (the opaque pointer) ships, and the
        FE lazy-loads bytes via ``GET /v1/inbox/{id}`` on detail mount.
        The route layer's :func:`_to_wire` produces the same JSON shape;
        keeping the projection here means the bus payload and the REST
        wire shape can never drift (single source of truth).
        """

        return {
            "id": record.id,
            "tenant_id": record.tenant_id,
            "owner_user_id": record.owner_user_id,
            "project_id": record.project_id,
            "kind": record.kind,
            "title": record.title,
            "body_ref": record.body_ref,
            "links": list(record.links),
            "sender": dict(record.sender),
            "state": record.state,
            "received_at": record.received_at.isoformat(),
            "read_at": record.read_at.isoformat() if record.read_at else None,
            "snoozed_until": (
                record.snoozed_until.isoformat() if record.snoozed_until else None
            ),
            "dismissed_at": (
                record.dismissed_at.isoformat() if record.dismissed_at else None
            ),
        }

    # -- helpers -------------------------------------------------------

    def _can_read(
        self,
        record: InboxItemRecord,
        caller_user_id: str,
        caller_roles: Iterable[str],
    ) -> bool:
        if record.owner_user_id == caller_user_id:
            return True
        if any(role in _ADMIN_ROLES for role in caller_roles):
            return True
        if record.project_id is None:
            return False
        return self._project_membership.is_project_member(
            tenant_id=record.tenant_id,
            project_id=record.project_id,
            user_id=caller_user_id,
        )

    # -- producer (internal — for P4-A2) ------------------------------

    def insert_item_with_body(
        self,
        *,
        tenant_id: str,
        owner_user_id: str,
        kind: str,
        title: str,
        sender: dict,
        links: list[dict] | None = None,
        project_id: str | None = None,
        body_markdown: str | None = None,
        producer_id: str | None = None,
        external_ref: str | None = None,
        actor_user_id: str | None = None,
    ) -> InboxItemRecord:
        """Insert a new inbox item + optional body.

        Helper used by tests + the future producer path (P4-A2 owns the
        /internal/v1/inbox/items route). Validates `kind` against the
        wire allowlist and writes one ``inbox.item_created`` audit row.
        """

        if kind not in _VALID_KINDS:
            raise InboxInvalidRequest("invalid_kind")
        if not title or not title.strip():
            raise InboxInvalidRequest("title_required")

        body_ref: str | None = None
        with self._store.transaction():
            if body_markdown is not None:
                body = self._store.insert_body(
                    InboxBodyRecord(
                        tenant_id=tenant_id,
                        body_markdown=body_markdown,
                    )
                )
                body_ref = body.body_ref
            record = self._store.insert_item(
                InboxItemRecord(
                    tenant_id=tenant_id,
                    owner_user_id=owner_user_id,
                    project_id=project_id,
                    kind=kind,
                    title=title.strip(),
                    body_ref=body_ref,
                    links=links or [],
                    sender=sender,
                    state="unread",
                    producer_id=producer_id,
                    external_ref=external_ref,
                )
            )
            self._store.append_audit(
                InboxAuditRecord(
                    tenant_id=tenant_id,
                    # ``actor_user_id`` for producer writes is the
                    # agent's owner per inbox-prd §6.1; tests default
                    # to the recipient when the producer identity isn't
                    # known (in-memory ergonomic).
                    actor_user_id=actor_user_id or owner_user_id,
                    action="inbox.item_created",
                    target_id=record.id,
                    after_state=_safe_dump(record),
                )
            )
        return record


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_iso(value: object) -> datetime | None:
    """Parse an ISO-8601 string (with or without ``Z`` suffix) to UTC."""

    if not isinstance(value, str):
        return None
    raw = value.strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _safe_dump(record: InboxItemRecord) -> dict:
    """Dump the record to a JSON-serialisable dict for audit rows."""

    return record.model_dump(mode="json")


__all__ = [
    "InboxForbidden",
    "InboxInvalidRequest",
    "InboxNotFound",
    "InboxService",
]
