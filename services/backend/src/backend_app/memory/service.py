"""Memory service — ACL + state machine + audit.

The route layer in :mod:`backend_app.memory.routes` is presentation-only;
every business-logic decision lives here so the in-memory
:class:`InMemoryMemoryStore` and the Postgres adapter share one set of
authorization checks.

Authorization rules — sub-PRD §6.2 binding:

* **Reads.**
  - scope='user' → owner only.
  - scope='workspace' → any tenant member.
  - project_id non-null → ``is_member(port, tenant, project, user)``
    OVERRIDES scope='user' restriction (project-shared rows).
  - Tenant admin (role in {admin, owner}) gets compliance reads on the
    whole tenant.
* **Writes.** Owner (or admin for workspace-scoped rows). 404-not-403
  (cross-audit §1.3): non-readers and non-writers both raise
  :class:`MemoryNotFound`.
* **Soft-delete cascade.** ``soft_delete_item`` flips ``deleted_at``;
  the existing Library retention sweep handles 90d hard-delete +
  cascade into ``library_embeddings`` where ``target_kind='memory'``
  (sub-PRD §5.3 — we surface the soft-delete signal so the indexer
  drops the row from search).

Audit rows append on every state change via the canonical helper
(``with store.transaction()`` block). Actions emitted:

* ``memory.created``
* ``memory.updated`` (fields changed)
* ``memory.scope_changed`` (scope flip — explicit audit per §6.2)
* ``memory.deleted``
* ``memory.touched``
* ``memory.proposal_accepted``
* ``memory.proposal_rejected``
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable

from backend_app.memory.store import (
    MemoryAuditRecord,
    MemoryItemRecord,
    MemoryKindLiteral,
    MemoryProposalRecord,
    MemoryScopeLiteral,
    MemoryStore,
    is_valid_sort_token,
)
from backend_app.projects.acl import (
    ProjectMembershipPort,
    _NoMemberProjectAdapter,
    is_member,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


_ADMIN_ROLES = frozenset({"admin", "owner"})
_VALID_KINDS = frozenset({"skill", "fact", "preference"})
_VALID_SCOPES = frozenset({"user", "workspace"})


# ---------------------------------------------------------------------------
# Service exceptions
# ---------------------------------------------------------------------------


class MemoryNotFound(Exception):
    """404 — either the row doesn't exist OR the caller has no read rights.

    The 404-not-403 binding (cross-audit §1.3) collapses both branches
    into one exception so the route layer cannot accidentally
    distinguish them.
    """


class MemoryForbidden(Exception):
    """403 — caller can READ but cannot WRITE.

    Only raised after read access is established, so 404-not-403 still
    applies for the read-doesn't-exist case.
    """


class MemoryInvalidRequest(Exception):
    """400 — client-fixable invariant violation."""


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class MemoryService:
    """Composition of the memory store + ACL + audit."""

    def __init__(
        self,
        *,
        store: MemoryStore,
        membership_port: "ProjectMembershipPort | None" = None,
        activity_bus: "Any | None" = None,
        indexer: "Any | None" = None,
    ) -> None:
        self._store = store
        # Default to no-member adapter so destinations without a Projects
        # registration still answer "not a member" instead of crashing.
        self._membership = membership_port or _NoMemberProjectAdapter()
        # Activity bus is optional — tests/dev wiring may pass ``None`` and
        # the publish helpers become no-ops. Production wiring sets this
        # to the bus stashed on ``app.state.memory_activity_bus`` so
        # mutations stream out as SSE frames.
        self._activity_bus = activity_bus
        # The indexer is the seam between memory writes and the Library
        # ``library_index_jobs`` queue (target_kind='memory'). Injected so
        # tests can observe enqueue without standing up a worker; the
        # production wiring composes a MemoryIndexer onto the shared
        # ``library_index_jobs`` store.
        self._indexer = indexer

    @property
    def activity_bus(self) -> "Any | None":
        return self._activity_bus

    # -- reads --------------------------------------------------------

    def get_item(
        self,
        *,
        tenant_id: str,
        caller_user_id: str,
        caller_roles: Iterable[str],
        item_id: str,
    ) -> MemoryItemRecord:
        record = self._store.get_item(tenant_id=tenant_id, item_id=item_id)
        if record is None:
            raise MemoryNotFound(item_id)
        if not self._can_read(record, caller_user_id, caller_roles):
            raise MemoryNotFound(item_id)
        return record

    def list_items(
        self,
        *,
        tenant_id: str,
        caller_user_id: str,
        caller_roles: Iterable[str],
        scopes: tuple[str, ...] | None = None,
        kinds: tuple[str, ...] | None = None,
        project_ids: tuple[str | None, ...] | None = None,
        tags: tuple[str, ...] | None = None,
        q: str | None = None,
        cursor: str | None = None,
        limit: int = 50,
        sort: str = "last_used:desc",
    ) -> tuple[tuple[MemoryItemRecord, ...], str | None]:
        """List the caller's readable memory rows.

        Admins (role in {admin, owner}) get compliance reads across the
        tenant. Non-admins see:

        * Their own user-scoped rows.
        * Every workspace-scoped row.
        * Project-scoped rows for projects they are a member of.

        The store does the first pass with permissive filters; the ACL
        gate runs in this method so the in-memory + Postgres adapters
        do not duplicate membership logic.
        """

        if not is_valid_sort_token(sort):
            raise MemoryInvalidRequest("invalid_sort")

        admin = any(role in _ADMIN_ROLES for role in caller_roles)
        # Permissive store fetch — the ACL filter below trims the result.
        rows, next_cursor = self._store.list_items(
            tenant_id=tenant_id,
            owner_user_id=None,
            scopes=scopes,
            kinds=kinds,
            project_ids=project_ids,
            tags=tags,
            q=q,
            cursor=cursor,
            limit=limit,
            sort=sort,
        )
        if admin:
            return rows, next_cursor
        filtered = tuple(
            r for r in rows if self._can_read(r, caller_user_id, caller_roles)
        )
        return filtered, next_cursor

    # -- writes --------------------------------------------------------

    def create_item(
        self,
        *,
        tenant_id: str,
        caller_user_id: str,
        creator: dict[str, Any] | None,
        scope: str,
        kind: str,
        title: str,
        body: str,
        tags: list[str] | None,
        project_id: str | None,
    ) -> MemoryItemRecord:
        """Insert a memory row and audit the creation."""

        self._validate_scope(scope)
        self._validate_kind(kind)
        if not title or not title.strip():
            raise MemoryInvalidRequest("title_required")
        record = MemoryItemRecord(
            tenant_id=tenant_id,
            owner_user_id=caller_user_id,
            scope=scope,  # type: ignore[arg-type]
            kind=kind,  # type: ignore[arg-type]
            title=title.strip(),
            body=body or "",
            tags=list(tags or []),
            created_by=creator or {"kind": "user", "id": caller_user_id},
            project_id=project_id,
        )
        with self._store.transaction():
            stored = self._store.insert_item(record)
            self._store.append_audit(
                MemoryAuditRecord(
                    tenant_id=tenant_id,
                    actor_user_id=caller_user_id,
                    action="memory.created",
                    target_kind="memory_item",
                    target_id=stored.id,
                    before_state=None,
                    after_state=_safe_dump(stored),
                )
            )
        self._enqueue_index(stored)
        return stored

    def update_item(
        self,
        *,
        tenant_id: str,
        caller_user_id: str,
        caller_roles: Iterable[str],
        item_id: str,
        patch: dict[str, Any],
    ) -> MemoryItemRecord:
        """Patch a memory row. Writer rule: owner OR admin.

        Re-embed is triggered when ``title``, ``body``, or ``tags``
        change (the indexer dedupes by ``content_hash``, so a no-op
        patch on those fields is cheap).
        """

        existing = self._store.get_item(tenant_id=tenant_id, item_id=item_id)
        if existing is None:
            raise MemoryNotFound(item_id)
        if not self._can_read(existing, caller_user_id, caller_roles):
            raise MemoryNotFound(item_id)
        if not self._can_write(existing, caller_user_id, caller_roles):
            raise MemoryForbidden(item_id)

        updates: dict[str, Any] = {}
        scope_changed = False
        if "scope" in patch and patch["scope"] is not None:
            self._validate_scope(patch["scope"])
            if patch["scope"] != existing.scope:
                scope_changed = True
            updates["scope"] = patch["scope"]
        if "kind" in patch and patch["kind"] is not None:
            self._validate_kind(patch["kind"])
            updates["kind"] = patch["kind"]
        if "title" in patch and patch["title"] is not None:
            title = str(patch["title"]).strip()
            if not title:
                raise MemoryInvalidRequest("title_required")
            updates["title"] = title
        if "body" in patch and patch["body"] is not None:
            updates["body"] = str(patch["body"])
        if "tags" in patch and patch["tags"] is not None:
            updates["tags"] = list(patch["tags"])
        if "project_id" in patch:
            # ``project_id`` can be set to None (un-file) or to a string.
            updates["project_id"] = patch["project_id"]
        if not updates:
            return existing
        updates["updated_at"] = _now()
        new_record = existing.model_copy(update=updates)
        before = _safe_dump(existing)
        after = _safe_dump(new_record)
        with self._store.transaction():
            stored = self._store.update_item(new_record)
            self._store.append_audit(
                MemoryAuditRecord(
                    tenant_id=tenant_id,
                    actor_user_id=caller_user_id,
                    action="memory.updated",
                    target_kind="memory_item",
                    target_id=stored.id,
                    before_state=before,
                    after_state=after,
                )
            )
            if scope_changed:
                # Explicit scope_changed audit per sub-PRD §6.2 binding —
                # so SIEM can detect scope flips without diffing the
                # ``memory.updated`` before/after blob.
                self._store.append_audit(
                    MemoryAuditRecord(
                        tenant_id=tenant_id,
                        actor_user_id=caller_user_id,
                        action="memory.scope_changed",
                        target_kind="memory_item",
                        target_id=stored.id,
                        before_state={"scope": existing.scope},
                        after_state={"scope": stored.scope},
                    )
                )
        # Re-embed only when an embedded field changed.
        if any(field in updates for field in ("title", "body", "tags")):
            self._enqueue_index(stored)
        return stored

    def delete_item(
        self,
        *,
        tenant_id: str,
        caller_user_id: str,
        caller_roles: Iterable[str],
        item_id: str,
    ) -> MemoryItemRecord:
        """Soft-delete a memory row. Cascades to ``library_embeddings``
        via the retention sweep + indexer (out-of-band).
        """

        existing = self._store.get_item(tenant_id=tenant_id, item_id=item_id)
        if existing is None:
            raise MemoryNotFound(item_id)
        if not self._can_read(existing, caller_user_id, caller_roles):
            raise MemoryNotFound(item_id)
        if not self._can_write(existing, caller_user_id, caller_roles):
            raise MemoryForbidden(item_id)
        before = _safe_dump(existing)
        with self._store.transaction():
            deleted = self._store.soft_delete_item(tenant_id=tenant_id, item_id=item_id)
            if deleted is None:  # pragma: no cover - read above guarantees row
                raise MemoryNotFound(item_id)
            self._store.append_audit(
                MemoryAuditRecord(
                    tenant_id=tenant_id,
                    actor_user_id=caller_user_id,
                    action="memory.deleted",
                    target_kind="memory_item",
                    target_id=deleted.id,
                    before_state=before,
                    after_state=_safe_dump(deleted),
                )
            )
        # Signal the indexer to drop the row's embeddings — the indexer
        # checks ``deleted_at`` and skips re-embedding while the row is
        # soft-deleted; the retention sweep handles the hard delete.
        self._enqueue_index(deleted)
        return deleted

    def touch_item(
        self,
        *,
        tenant_id: str,
        item_id: str,
    ) -> MemoryItemRecord:
        """Bump ``last_used_at`` — called from the internal /touch endpoint.

        Touch is a runtime-internal call (no caller_user_id ACL because
        the runtime acts on behalf of the row owner). The audit row
        records the runtime as the actor via ``actor_user_id`` = the
        row owner — this is the same convention the Library uses for
        ``last_accessed_at`` updates.
        """

        with self._store.transaction():
            touched = self._store.touch_item(tenant_id=tenant_id, item_id=item_id)
            if touched is None:
                raise MemoryNotFound(item_id)
            self._store.append_audit(
                MemoryAuditRecord(
                    tenant_id=tenant_id,
                    actor_user_id=touched.owner_user_id,
                    action="memory.touched",
                    target_kind="memory_item",
                    target_id=touched.id,
                    before_state=None,
                    after_state={
                        "last_used_at": touched.last_used_at.isoformat()
                        if touched.last_used_at
                        else None
                    },
                )
            )
        return touched

    # -- proposals -----------------------------------------------------

    def list_proposals(
        self,
        *,
        tenant_id: str,
        caller_user_id: str,
        statuses: tuple[str, ...] | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> tuple[tuple[MemoryProposalRecord, ...], str | None]:
        # Proposals are owner-only: the FE pulls /v1/memory/proposals for
        # the signed-in user. No admin compliance read here — proposals
        # are pre-memory and inherit the privacy stance of the chat that
        # produced them.
        return self._store.list_proposals(
            tenant_id=tenant_id,
            user_id=caller_user_id,
            statuses=statuses,
            cursor=cursor,
            limit=limit,
        )

    def accept_proposal(
        self,
        *,
        tenant_id: str,
        caller_user_id: str,
        proposal_id: str,
        title_override: str | None = None,
        body_override: str | None = None,
        scope_override: str | None = None,
        tags: list[str] | None = None,
        project_id: str | None = None,
    ) -> tuple[MemoryProposalRecord, MemoryItemRecord]:
        """Accept a proposal: create a MemoryItem + transition status."""

        proposal = self._store.get_proposal(
            tenant_id=tenant_id, proposal_id=proposal_id
        )
        if proposal is None or proposal.user_id != caller_user_id:
            raise MemoryNotFound(proposal_id)
        if proposal.status != "pending":
            raise MemoryInvalidRequest("proposal_not_pending")

        target_scope = scope_override or "user"
        self._validate_scope(target_scope)

        # Create the memory row (re-using create_item enqueues the indexer
        # + writes the canonical memory.created audit row, so SIEM sees
        # the same event shape regardless of the create path).
        memory = self.create_item(
            tenant_id=tenant_id,
            caller_user_id=caller_user_id,
            creator={"kind": "agent", "id": proposal.source.get("id", "atlas")},
            scope=target_scope,
            kind=proposal.proposed_kind,
            title=(title_override or proposal.proposed_title),
            body=(
                body_override if body_override is not None else proposal.proposed_body
            ),
            tags=tags,
            project_id=project_id,
        )

        # Transition the proposal in a single audit-ed block.
        before = _safe_dump(proposal)
        decided = proposal.model_copy(
            update={
                "status": "accepted",
                "decided_at": _now(),
                "accepted_memory_id": memory.id,
            }
        )
        with self._store.transaction():
            updated = self._store.update_proposal(decided)
            self._store.append_audit(
                MemoryAuditRecord(
                    tenant_id=tenant_id,
                    actor_user_id=caller_user_id,
                    action="memory.proposal_accepted",
                    target_kind="memory_proposal",
                    target_id=updated.id,
                    before_state=before,
                    after_state=_safe_dump(updated),
                )
            )
        return updated, memory

    def reject_proposal(
        self,
        *,
        tenant_id: str,
        caller_user_id: str,
        proposal_id: str,
    ) -> MemoryProposalRecord:
        proposal = self._store.get_proposal(
            tenant_id=tenant_id, proposal_id=proposal_id
        )
        if proposal is None or proposal.user_id != caller_user_id:
            raise MemoryNotFound(proposal_id)
        if proposal.status != "pending":
            raise MemoryInvalidRequest("proposal_not_pending")
        before = _safe_dump(proposal)
        decided = proposal.model_copy(
            update={"status": "rejected", "decided_at": _now()}
        )
        with self._store.transaction():
            updated = self._store.update_proposal(decided)
            self._store.append_audit(
                MemoryAuditRecord(
                    tenant_id=tenant_id,
                    actor_user_id=caller_user_id,
                    action="memory.proposal_rejected",
                    target_kind="memory_proposal",
                    target_id=updated.id,
                    before_state=before,
                    after_state=_safe_dump(updated),
                )
            )
        return updated

    # -- ACL helpers ---------------------------------------------------

    def _can_read(
        self,
        record: MemoryItemRecord,
        caller_user_id: str,
        caller_roles: Iterable[str],
    ) -> bool:
        if any(role in _ADMIN_ROLES for role in caller_roles):
            return True
        # Owner always reads, regardless of scope.
        if record.owner_user_id == caller_user_id:
            return True
        # Workspace scope — any tenant member reads (the store filter
        # already enforced tenant_id, so we know the caller is in-tenant).
        if record.scope == "workspace":
            return True
        # Project-scoped row — fall through to membership.
        if record.project_id is not None and is_member(
            self._membership,
            tenant_id=record.tenant_id,
            project_id=record.project_id,
            user_id=caller_user_id,
        ):
            return True
        return False

    def _can_write(
        self,
        record: MemoryItemRecord,
        caller_user_id: str,
        caller_roles: Iterable[str],
    ) -> bool:
        if record.owner_user_id == caller_user_id:
            return True
        # Admin can edit workspace-scoped rows; private user rows stay
        # owner-only even for admins (compliance-read, not compliance-write).
        if record.scope == "workspace" and any(
            role in _ADMIN_ROLES for role in caller_roles
        ):
            return True
        return False

    def _validate_scope(self, scope: str) -> None:
        if scope not in _VALID_SCOPES:
            raise MemoryInvalidRequest("invalid_scope")

    def _validate_kind(self, kind: str) -> None:
        if kind not in _VALID_KINDS:
            raise MemoryInvalidRequest("invalid_kind")

    def _enqueue_index(self, record: MemoryItemRecord) -> None:
        if self._indexer is None:
            return
        try:
            self._indexer.enqueue(
                tenant_id=record.tenant_id,
                memory_id=record.id,
            )
        except Exception:
            # Indexer failures must not block memory writes; the
            # retention sweep + manual re-enqueue is the recovery path.
            # Best-effort matches the Library service's discipline.
            pass

    # -- async event publish (memory.created / updated / deleted) -----

    async def publish_event(
        self,
        *,
        event_type: str,
        record: MemoryItemRecord | None = None,
        proposal: MemoryProposalRecord | None = None,
        deleted_id: str | None = None,
    ) -> None:
        if self._activity_bus is None:
            return
        owner_id: str | None = None
        if record is not None:
            owner_id = record.owner_user_id
        elif proposal is not None:
            owner_id = proposal.user_id
        if owner_id is None:
            return
        await self._activity_bus.publish(
            org_id=(
                record.tenant_id
                if record is not None
                else proposal.tenant_id
                if proposal is not None
                else ""
            ),
            user_id=owner_id,
            event_type=event_type,
            item=record,
            proposal=proposal,
            deleted_id=deleted_id,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _safe_dump(record: MemoryItemRecord | MemoryProposalRecord) -> dict[str, Any]:
    """JSON-safe Pydantic dump for the audit before/after blob."""

    return record.model_dump(mode="json")


__all__ = [
    "MemoryForbidden",
    "MemoryInvalidRequest",
    "MemoryKindLiteral",
    "MemoryNotFound",
    "MemoryScopeLiteral",
    "MemoryService",
]
