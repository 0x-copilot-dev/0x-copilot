"""Projects service — CRUD + ACL + member management + ownership transfer.

The route layer in ``routes.py`` is presentation-only; every business-
logic decision lives here so the in-memory ``InMemoryProjectsStore``
and the Postgres adapter share one set of authorization checks,
invariants, and audit hooks.

Authorization rules (cross-audit §1.3 + projects-prd §7, binding):

* Owner-only writes on the project (PATCH / archive / delete / member-
  add / member-remove / member-role-change / transfer).
* A member can self-remove via the dedicated ``DELETE …/members/me``
  shortcut.
* Reads: owner OR project-member OR tenant admin (compliance read;
  audited at the route layer with ``project.compliance_read``).
* Non-readers see 404, not 403 (existence not leaked).

Ownership transfer (projects-prd §3.5.3 + Q5 product decision):

* Single transactional operation:
    1. Verify caller is current owner (or admin via the separate
       admin-force-transfer endpoint).
    2. Verify new owner is already a member (any role).
    3. Update old-owner row → ``previous_owner_new_role`` (default
       ``editor``; transferor may pass ``viewer`` or ``"none"`` to remove).
    4. Update new-owner row → ``owner``.
    5. Update ``projects.owner_user_id``.
    6. Append two audit rows: ``project.ownership_transferred`` (or
       ``project.admin_force_transferred`` for admin path).

The PARTIAL UNIQUE-on-owner invariant is honored: the new-owner role
is set FIRST (the old-owner role is demoted in the same transaction so
two owners never coexist in the same row-stable read window).

Archive behavior (projects-prd §11.3 + Q4 product decision):

* Archive flips ``status='archived'`` and stamps ``archived_at``.
* Mutations after archive return 409 (route layer translates).
* In-flight runs / pending approvals / chats are NOT halted — those
  remain interactive. The "pause future fires" semantics live in the
  Routines scheduler (P5-A2), not here.

Admin force-transfer (projects-prd §12 Q1, orchestrator-approved):

* Owner-offboarded projects get an Inbox CTA to admins; the admin
  invokes ``force_transfer_ownership`` (separate from the owner-only
  ``transfer_ownership``) which bypasses the "caller must be current
  owner" check and writes ``project.admin_force_transferred`` audit.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable

from backend_app.identity.store import IdentityStore
from backend_app.projects.acl import (
    InMemoryProjectMembershipAdapter,
    ProjectMembershipPort,
    ProjectRole,
)
from backend_app.projects.store import (
    ProjectActivityCounts,
    ProjectAuditRecord,
    ProjectMembershipRecord,
    ProjectRecord,
    ProjectStarRecord,
    ProjectsStore,
    empty_counts,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


# Tenant-admin roles. Treated as untrusted unless the verified
# ``ScopedIdentity.roles`` tuple set them — the route layer passes
# through what the auth middleware verified.
_ADMIN_ROLES = frozenset({"admin", "owner"})

_VALID_STATUSES = frozenset({"active", "archived"})
_VALID_PROJECT_ROLES: frozenset[ProjectRole] = frozenset({"owner", "editor", "viewer"})
_PREV_OWNER_DEMOTION_TARGETS = frozenset({"editor", "viewer", "none"})

_NAME_MAX = 80
_DESCRIPTION_MAX = 400
_HUE_MIN = 0
_HUE_MAX = 359

# Hard cap (projects-prd §11.2). UI warns at 200; the service rejects
# additions past 500 with ``MemberCapExceeded``.
_MAX_MEMBERS_PER_PROJECT = 500


class ProjectNotFound(Exception):
    """Raised when a project doesn't exist OR the caller has no read rights.

    The 404-not-403 rule (cross-audit §1.3) collapses both branches to
    one exception so the route layer cannot accidentally distinguish
    them — the response is always 404.
    """


class ProjectForbidden(Exception):
    """Raised when the caller can READ but cannot WRITE.

    Used after read access has already been established (so 404-not-403
    still applies for the read-doesn't-exist case). The route layer
    translates this to 403.
    """


class ProjectInvalidRequest(Exception):
    """Raised for client-fixable invariant violations (400)."""


class ProjectConflict(Exception):
    """Raised for state-conflict violations (409).

    Used for: archived-project mutation, duplicate name, owner-cannot-
    be-removed, membership-exists, member-cap-exceeded.
    """

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class ProjectsService:
    """Composition of the projects store + identity store with ACL + audit."""

    def __init__(
        self,
        *,
        store: ProjectsStore,
        identity_store: IdentityStore,
        membership_port: ProjectMembershipPort | None = None,
    ) -> None:
        self._store = store
        self._identity = identity_store
        # The canonical ACL port. When the in-memory store is the
        # backing adapter, the membership-port adapter operates on a
        # shared in-memory dict so the two views stay in sync.
        #
        # The default adapter is the in-memory one; production deploys
        # inject a :class:`PostgresProjectMembershipAdapter` reading
        # from the same ``project_memberships`` table the store writes.
        self._membership_port = membership_port or _membership_adapter_for(store)

    # =================================================================
    # Reads
    # =================================================================

    def get_project(
        self,
        *,
        tenant_id: str,
        caller_user_id: str,
        caller_roles: Iterable[str],
        project_id: str,
    ) -> tuple[ProjectRecord, ProjectRole | None, bool, ProjectActivityCounts]:
        """Authorise + return a single project plus caller-relative fields.

        Returns ``(record, viewer_role, viewer_starred, counts)``. The
        caller-relative bundle is computed once at the service layer so
        the route doesn't have to re-derive ``viewer_role`` on each
        marshalling pass.

        Raises :class:`ProjectNotFound` if the caller can't see it
        (404-not-403; the route never distinguishes "not found" from
        "not authorised").
        """

        record = self._store.get_project(tenant_id=tenant_id, project_id=project_id)
        if record is None:
            raise ProjectNotFound(project_id)
        viewer_role = self._viewer_role(record, caller_user_id, caller_roles)
        if viewer_role is None and not _is_admin(caller_roles):
            raise ProjectNotFound(project_id)
        starred = self._store.is_starred(
            tenant_id=tenant_id, project_id=project_id, user_id=caller_user_id
        )
        counts = self._counts_for(record)
        return record, viewer_role, starred, counts

    def list_projects(
        self,
        *,
        tenant_id: str,
        caller_user_id: str,
        caller_roles: Iterable[str],
        statuses: tuple[str, ...] | None = None,
        owner_user_id: str | None = None,
        member_user_id: str | None = None,
        q: str | None = None,
        starred: bool = False,
        cursor: str | None = None,
        limit: int = 50,
        sort: str = "updated_at:desc",
    ) -> tuple[
        tuple[
            tuple[ProjectRecord, ProjectRole | None, bool, ProjectActivityCounts], ...
        ],
        str | None,
    ]:
        """List the caller's readable projects.

        ACL gate (cross-audit §1.3):

        * Non-admin caller sees only projects they own OR are a member
          of. ``filter[member_user_id]`` on the route layer is bound
          here: a non-admin can only filter to ``member_user_id=me``;
          a cross-user ``member_user_id`` query is rejected at the
          route layer (membership-graph harvesting protection,
          projects-prd §4.4).
        * Admin caller sees every project in the tenant; rows the admin
          doesn't otherwise have a membership on come back with
          ``viewer_role=None`` so the UI can render the
          "compliance read" banner.

        The default access path for a non-admin is the UNION of
        owner-of-record + member-of-record + (when ``starred=true``)
        starred. Filters compose on top of that.
        """

        admin = _is_admin(caller_roles)

        # Membership-scoping. Non-admins see only what they're entitled
        # to read; admins see the whole tenant (modulo explicit
        # owner/member filters).
        scoped_member_user_id: str | None
        scoped_owner_user_id: str | None = owner_user_id
        if admin:
            scoped_member_user_id = member_user_id
        else:
            # Non-admin: the ``mine`` / ``member_user_id=me`` filter
            # narrows; otherwise we still need to scope to "owner-OR-
            # member" so we don't leak cross-user rows.
            if member_user_id is not None and member_user_id != caller_user_id:
                # Non-admin tried to query someone else's memberships
                # — the route layer should already have rejected; defense
                # in depth here.
                raise ProjectForbidden("cross_user_membership_filter")
            scoped_member_user_id = caller_user_id

        starred_user = caller_user_id if starred else None

        # First pass — owner-of-record (or admin's everything).
        page, next_cursor = self._store.list_projects(
            tenant_id=tenant_id,
            owner_user_id=scoped_owner_user_id,
            member_user_id=None if admin else None,  # union pass below
            statuses=statuses,
            q=q,
            starred_by_user_id=starred_user,
            cursor=cursor,
            limit=limit,
            sort=sort,
        )

        # For non-admins we also need to include rows where the caller
        # is a member but not the owner. The in-memory store accepts a
        # ``member_user_id`` filter — we run a second query with that
        # set and union the ids.
        if not admin and scoped_owner_user_id is None:
            owner_ids = {r.id for r in page}
            member_page, _ = self._store.list_projects(
                tenant_id=tenant_id,
                member_user_id=scoped_member_user_id,
                statuses=statuses,
                q=q,
                starred_by_user_id=starred_user,
                cursor=None,
                limit=limit,
                sort=sort,
            )
            merged = list(page)
            for record in member_page:
                if record.id not in owner_ids:
                    # The owner-page already filtered for tenant /
                    # status / q; the member-page applied the same.
                    if record.owner_user_id == caller_user_id:
                        # Already in the owner page — skip.
                        continue
                    merged.append(record)
            page = tuple(merged[:limit])
        elif scoped_owner_user_id is not None and not admin:
            # Owner-filter requested by a non-admin → only keep
            # projects the caller already has read rights on.
            page = tuple(
                r
                for r in page
                if r.owner_user_id == caller_user_id
                or self._membership_port.is_member(
                    tenant_id=tenant_id,
                    project_id=r.id,
                    user_id=caller_user_id,
                )
            )

        enriched = tuple(
            (
                record,
                self._viewer_role(record, caller_user_id, caller_roles),
                self._store.is_starred(
                    tenant_id=tenant_id,
                    project_id=record.id,
                    user_id=caller_user_id,
                ),
                self._counts_for(record),
            )
            for record in page
        )
        return enriched, next_cursor

    def list_members(
        self,
        *,
        tenant_id: str,
        caller_user_id: str,
        caller_roles: Iterable[str],
        project_id: str,
        cursor: str | None = None,
        limit: int = 50,
    ) -> tuple[tuple[ProjectMembershipRecord, ...], str | None]:
        # Read gate — must be a member or admin. Non-readers 404.
        self._require_read(
            tenant_id=tenant_id,
            project_id=project_id,
            caller_user_id=caller_user_id,
            caller_roles=caller_roles,
        )
        return self._store.list_memberships_for_project(
            tenant_id=tenant_id,
            project_id=project_id,
            cursor=cursor,
            limit=limit,
        )

    # =================================================================
    # Writes — project lifecycle
    # =================================================================

    def create_project(
        self,
        *,
        tenant_id: str,
        caller_user_id: str,
        payload: dict[str, Any],
    ) -> tuple[ProjectRecord, ProjectRole, bool, ProjectActivityCounts]:
        validated = self._validate_create_payload(payload)
        # Duplicate-name guard (case-insensitive per
        # projects-prd §5.1 UNIQUE constraint).
        if (
            self._store.get_project_by_name(tenant_id=tenant_id, name=validated["name"])
            is not None
        ):
            raise ProjectConflict("duplicate_name")

        record = ProjectRecord(
            tenant_id=tenant_id,
            owner_user_id=caller_user_id,
            name=validated["name"],
            description=validated.get("description", ""),
            icon_emoji=validated.get("icon_emoji", "📁"),
            color_hue=int(validated.get("color_hue", 210)),
            status="active",
        )

        # The owner-membership row + project row are written in one
        # transaction so a partial failure can't leave a project with
        # no owner in the memberships table.
        with self._store.transaction():
            stored = self._store.insert_project(record)
            self._store.insert_membership(
                ProjectMembershipRecord(
                    project_id=stored.id,
                    user_id=caller_user_id,
                    tenant_id=tenant_id,
                    role="owner",
                    added_by=caller_user_id,
                )
            )
            # Mirror into the canonical membership port so the same
            # adapter ``is_member`` answers true on the next call —
            # only when the port is the in-memory adapter wired to the
            # in-memory store (the Postgres adapter reads the same
            # table, no mirror needed).
            _mirror_membership_to_port(
                self._membership_port,
                tenant_id=tenant_id,
                project_id=stored.id,
                user_id=caller_user_id,
                role="owner",
            )
            self._store.append_audit(
                ProjectAuditRecord(
                    tenant_id=tenant_id,
                    actor_user_id=caller_user_id,
                    action="project.created",
                    target_id=stored.id,
                    after_state=_safe_dump(stored),
                    context={
                        "name": stored.name,
                        "owner_user_id": caller_user_id,
                        "project_id": stored.id,
                    },
                )
            )
        # Counts row: members=1 (the owner row was just written). The
        # other counts stay zero — the projector populates them
        # incrementally on first cross-destination activity.
        counts = empty_counts(tenant_id=tenant_id, project_id=stored.id)
        counts = counts.model_copy(update={"members": 1})
        return stored, "owner", False, counts

    def update_project(
        self,
        *,
        tenant_id: str,
        caller_user_id: str,
        caller_roles: Iterable[str],
        project_id: str,
        patch: dict[str, Any],
    ) -> ProjectRecord:
        existing = self._store.get_project(tenant_id=tenant_id, project_id=project_id)
        if existing is None:
            raise ProjectNotFound(project_id)
        viewer_role = self._viewer_role(existing, caller_user_id, caller_roles)
        if viewer_role is None and not _is_admin(caller_roles):
            raise ProjectNotFound(project_id)
        if existing.owner_user_id != caller_user_id:
            # Read access established (project member or admin) but
            # writes are owner-only. cross-audit §1.3 + projects-prd §7.2.
            raise ProjectForbidden(project_id)
        # Archived projects: writes 409 (must activate first) —
        # EXCEPT the activate transition itself, which is the way out.
        target_status = patch.get("status")
        if existing.status == "archived" and target_status != "active":
            raise ProjectConflict("project_archived")

        updates = self._validate_patch_payload(existing, patch)
        # Duplicate-name guard on rename — case-insensitive, scoped to
        # the same tenant, ignoring this same row.
        new_name = updates.get("name")
        if new_name is not None and new_name.lower() != existing.name.lower():
            collision = self._store.get_project_by_name(
                tenant_id=tenant_id, name=new_name
            )
            if collision is not None and collision.id != existing.id:
                raise ProjectConflict("duplicate_name")
        new_record = existing.model_copy(update={**updates, "updated_at": _now()})

        # Stamp / clear ``archived_at`` based on the resulting status.
        if existing.status != new_record.status:
            if new_record.status == "archived":
                new_record = new_record.model_copy(update={"archived_at": _now()})
            else:
                new_record = new_record.model_copy(update={"archived_at": None})

        before = _safe_dump(existing)
        after = _safe_dump(new_record)
        action = _action_for_transition(existing.status, new_record.status)
        with self._store.transaction():
            stored = self._store.update_project(new_record)
            self._store.append_audit(
                ProjectAuditRecord(
                    tenant_id=tenant_id,
                    actor_user_id=caller_user_id,
                    action=action,
                    target_id=stored.id,
                    before_state=before,
                    after_state=after,
                    context={
                        "changed_fields": sorted(updates.keys()),
                        "project_id": stored.id,
                    },
                )
            )
        return stored

    def delete_project(
        self,
        *,
        tenant_id: str,
        caller_user_id: str,
        caller_roles: Iterable[str],
        project_id: str,
    ) -> None:
        existing = self._store.get_project(tenant_id=tenant_id, project_id=project_id)
        if existing is None:
            raise ProjectNotFound(project_id)
        viewer_role = self._viewer_role(existing, caller_user_id, caller_roles)
        if viewer_role is None and not _is_admin(caller_roles):
            raise ProjectNotFound(project_id)
        if existing.owner_user_id != caller_user_id:
            raise ProjectForbidden(project_id)

        before = _safe_dump(existing)
        with self._store.transaction():
            self._store.soft_delete_project(tenant_id=tenant_id, project_id=project_id)
            self._store.append_audit(
                ProjectAuditRecord(
                    tenant_id=tenant_id,
                    actor_user_id=caller_user_id,
                    action="project.deleted",
                    target_id=project_id,
                    before_state=before,
                    context={"soft": True, "project_id": project_id},
                )
            )

    def restore_project(
        self,
        *,
        tenant_id: str,
        caller_user_id: str,
        project_id: str,
    ) -> ProjectRecord:
        existing = self._store.get_project(
            tenant_id=tenant_id,
            project_id=project_id,
            include_deleted=True,
        )
        if existing is None:
            raise ProjectNotFound(project_id)
        if existing.owner_user_id != caller_user_id:
            # Restore is owner-only; non-owner sees 404 (existence not
            # leaked).
            raise ProjectNotFound(project_id)
        if existing.deleted_at is None:
            # Already live — idempotent, just return it.
            return existing
        restored = existing.model_copy(
            update={"deleted_at": None, "updated_at": _now()}
        )
        with self._store.transaction():
            stored = self._store.update_project(restored)
            self._store.append_audit(
                ProjectAuditRecord(
                    tenant_id=tenant_id,
                    actor_user_id=caller_user_id,
                    action="project.restored",
                    target_id=stored.id,
                    after_state=_safe_dump(stored),
                    context={"project_id": stored.id},
                )
            )
        return stored

    # =================================================================
    # Writes — membership
    # =================================================================

    def add_member(
        self,
        *,
        tenant_id: str,
        caller_user_id: str,
        caller_roles: Iterable[str],
        project_id: str,
        target_user_id: str,
        role: str,
    ) -> ProjectMembershipRecord:
        if role not in {"editor", "viewer"}:
            raise ProjectInvalidRequest("role_invalid")
        existing = self._store.get_project(tenant_id=tenant_id, project_id=project_id)
        if existing is None:
            raise ProjectNotFound(project_id)
        viewer_role = self._viewer_role(existing, caller_user_id, caller_roles)
        if viewer_role is None and not _is_admin(caller_roles):
            raise ProjectNotFound(project_id)
        if existing.owner_user_id != caller_user_id:
            raise ProjectForbidden(project_id)
        if existing.status == "archived":
            raise ProjectConflict("project_archived")

        # Cross-tenant guard. The target user must be in the same tenant.
        # The identity store is scoped by ``(org_id, user_id)`` so an
        # absent row IS the cross-tenant signal — there is no way to
        # accidentally accept a user from another org.
        target_user = self._identity.get_user(org_id=tenant_id, user_id=target_user_id)
        if target_user is None:
            raise ProjectInvalidRequest("cross_tenant_user")

        if (
            self._store.get_membership(
                tenant_id=tenant_id,
                project_id=project_id,
                user_id=target_user_id,
            )
            is not None
        ):
            raise ProjectConflict("membership_exists")

        # Hard member cap per projects-prd §11.2.
        existing_rows, _ = self._store.list_memberships_for_project(
            tenant_id=tenant_id,
            project_id=project_id,
            limit=_MAX_MEMBERS_PER_PROJECT + 1,
        )
        if len(existing_rows) >= _MAX_MEMBERS_PER_PROJECT:
            raise ProjectConflict("member_cap_exceeded")

        record = ProjectMembershipRecord(
            project_id=project_id,
            user_id=target_user_id,
            tenant_id=tenant_id,
            role=role,
            added_by=caller_user_id,
        )
        with self._store.transaction():
            stored = self._store.insert_membership(record)
            _mirror_membership_to_port(
                self._membership_port,
                tenant_id=tenant_id,
                project_id=project_id,
                user_id=target_user_id,
                role=role,  # type: ignore[arg-type]
            )
            self._store.append_audit(
                ProjectAuditRecord(
                    tenant_id=tenant_id,
                    actor_user_id=caller_user_id,
                    action="project.member_added",
                    target_id=project_id,
                    after_state={"user_id": target_user_id, "role": role},
                    context={
                        "user_id": target_user_id,
                        "role": role,
                        "added_by": caller_user_id,
                        "project_id": project_id,
                    },
                )
            )
        return stored

    def remove_member(
        self,
        *,
        tenant_id: str,
        caller_user_id: str,
        caller_roles: Iterable[str],
        project_id: str,
        target_user_id: str,
    ) -> None:
        existing = self._store.get_project(tenant_id=tenant_id, project_id=project_id)
        if existing is None:
            raise ProjectNotFound(project_id)
        viewer_role = self._viewer_role(existing, caller_user_id, caller_roles)
        if viewer_role is None and not _is_admin(caller_roles):
            raise ProjectNotFound(project_id)

        # Two callers: owner removing anyone, OR member self-removing.
        if (
            target_user_id != caller_user_id
            and existing.owner_user_id != caller_user_id
        ):
            raise ProjectForbidden(project_id)
        if target_user_id == existing.owner_user_id:
            # Owner cannot be removed — must transfer first.
            raise ProjectConflict("owner_cannot_be_removed")

        membership = self._store.get_membership(
            tenant_id=tenant_id, project_id=project_id, user_id=target_user_id
        )
        if membership is None:
            raise ProjectNotFound(project_id)
        with self._store.transaction():
            self._store.delete_membership(
                tenant_id=tenant_id,
                project_id=project_id,
                user_id=target_user_id,
            )
            _unmirror_membership_from_port(
                self._membership_port,
                tenant_id=tenant_id,
                project_id=project_id,
                user_id=target_user_id,
            )
            self._store.append_audit(
                ProjectAuditRecord(
                    tenant_id=tenant_id,
                    actor_user_id=caller_user_id,
                    action="project.member_removed",
                    target_id=project_id,
                    before_state={
                        "user_id": target_user_id,
                        "role": membership.role,
                    },
                    context={
                        "user_id": target_user_id,
                        "removed_by": caller_user_id,
                        "previous_role": membership.role,
                        "project_id": project_id,
                    },
                )
            )

    def change_member_role(
        self,
        *,
        tenant_id: str,
        caller_user_id: str,
        caller_roles: Iterable[str],
        project_id: str,
        target_user_id: str,
        role: str,
    ) -> ProjectMembershipRecord:
        if role not in {"editor", "viewer"}:
            # Owner can only be set via transfer.
            raise ProjectInvalidRequest("role_invalid")
        existing = self._store.get_project(tenant_id=tenant_id, project_id=project_id)
        if existing is None:
            raise ProjectNotFound(project_id)
        viewer_role = self._viewer_role(existing, caller_user_id, caller_roles)
        if viewer_role is None and not _is_admin(caller_roles):
            raise ProjectNotFound(project_id)
        if existing.owner_user_id != caller_user_id:
            raise ProjectForbidden(project_id)
        if target_user_id == existing.owner_user_id:
            raise ProjectConflict("owner_role_via_transfer_only")
        membership = self._store.get_membership(
            tenant_id=tenant_id, project_id=project_id, user_id=target_user_id
        )
        if membership is None:
            raise ProjectNotFound(project_id)
        previous_role = membership.role
        with self._store.transaction():
            updated = self._store.update_membership_role(
                tenant_id=tenant_id,
                project_id=project_id,
                user_id=target_user_id,
                role=role,
            )
            if updated is None:
                raise ProjectNotFound(project_id)
            _mirror_membership_to_port(
                self._membership_port,
                tenant_id=tenant_id,
                project_id=project_id,
                user_id=target_user_id,
                role=role,  # type: ignore[arg-type]
            )
            self._store.append_audit(
                ProjectAuditRecord(
                    tenant_id=tenant_id,
                    actor_user_id=caller_user_id,
                    action="project.member_role_changed",
                    target_id=project_id,
                    before_state={"user_id": target_user_id, "role": previous_role},
                    after_state={"user_id": target_user_id, "role": role},
                    context={
                        "user_id": target_user_id,
                        "from_role": previous_role,
                        "to_role": role,
                        "changed_by": caller_user_id,
                        "project_id": project_id,
                    },
                )
            )
        return updated

    # =================================================================
    # Ownership transfer
    # =================================================================

    def transfer_ownership(
        self,
        *,
        tenant_id: str,
        caller_user_id: str,
        caller_roles: Iterable[str],
        project_id: str,
        new_owner_user_id: str,
        previous_owner_new_role: str = "editor",
    ) -> ProjectRecord:
        """Owner-initiated ownership transfer.

        For the admin-driven path (owner offboarded / unreachable), call
        :meth:`force_transfer_ownership` instead — it bypasses the
        "caller must be current owner" check and writes a different
        audit action.
        """

        return self._do_transfer(
            tenant_id=tenant_id,
            caller_user_id=caller_user_id,
            caller_roles=caller_roles,
            project_id=project_id,
            new_owner_user_id=new_owner_user_id,
            previous_owner_new_role=previous_owner_new_role,
            admin_force=False,
        )

    def force_transfer_ownership(
        self,
        *,
        tenant_id: str,
        caller_user_id: str,
        caller_roles: Iterable[str],
        project_id: str,
        new_owner_user_id: str,
        previous_owner_new_role: str = "editor",
        reason: str | None = None,
    ) -> ProjectRecord:
        """Admin-only force-transfer per projects-prd §12 Q1.

        Used when the current owner is offboarded (IdP ``disabled_at``)
        and the routine Inbox CTA fired. Caller MUST be a tenant admin
        (route layer enforces); this method records both the old and
        new owner ids in the audit ``context`` for the compliance trail.
        """

        if not _is_admin(caller_roles):
            raise ProjectForbidden("admin_required")
        return self._do_transfer(
            tenant_id=tenant_id,
            caller_user_id=caller_user_id,
            caller_roles=caller_roles,
            project_id=project_id,
            new_owner_user_id=new_owner_user_id,
            previous_owner_new_role=previous_owner_new_role,
            admin_force=True,
            reason=reason,
        )

    def _do_transfer(
        self,
        *,
        tenant_id: str,
        caller_user_id: str,
        caller_roles: Iterable[str],
        project_id: str,
        new_owner_user_id: str,
        previous_owner_new_role: str,
        admin_force: bool,
        reason: str | None = None,
    ) -> ProjectRecord:
        if previous_owner_new_role not in _PREV_OWNER_DEMOTION_TARGETS:
            raise ProjectInvalidRequest("previous_owner_new_role_invalid")

        existing = self._store.get_project(tenant_id=tenant_id, project_id=project_id)
        if existing is None:
            raise ProjectNotFound(project_id)
        # For the owner path, the caller must already be the owner.
        # For the admin-force path we've already checked _is_admin
        # above; defense in depth — if neither, 404 not 403 (the
        # caller doesn't even have read rights to assert about).
        if not admin_force:
            viewer_role = self._viewer_role(existing, caller_user_id, caller_roles)
            if viewer_role is None and not _is_admin(caller_roles):
                raise ProjectNotFound(project_id)
            if existing.owner_user_id != caller_user_id:
                raise ProjectForbidden(project_id)
        if existing.status == "archived":
            raise ProjectConflict("project_archived")

        if new_owner_user_id == existing.owner_user_id:
            # No-op transfer to self — reject as invalid.
            raise ProjectInvalidRequest("new_owner_is_current_owner")

        # New owner must already be a member.
        new_owner_membership = self._store.get_membership(
            tenant_id=tenant_id,
            project_id=project_id,
            user_id=new_owner_user_id,
        )
        if new_owner_membership is None:
            raise ProjectInvalidRequest("new_owner_not_member")

        old_owner_user_id = existing.owner_user_id
        before = _safe_dump(existing)

        with self._store.transaction():
            # Step 1: demote old owner FIRST (or remove if "none").
            # This is the only window where the project has no owner-
            # roled membership row — the next step (promote new owner)
            # restores the invariant. The atomic transaction means no
            # other reader sees the gap.
            if previous_owner_new_role == "none":
                self._store.delete_membership(
                    tenant_id=tenant_id,
                    project_id=project_id,
                    user_id=old_owner_user_id,
                )
                _unmirror_membership_from_port(
                    self._membership_port,
                    tenant_id=tenant_id,
                    project_id=project_id,
                    user_id=old_owner_user_id,
                )
            else:
                self._store.update_membership_role(
                    tenant_id=tenant_id,
                    project_id=project_id,
                    user_id=old_owner_user_id,
                    role=previous_owner_new_role,
                )
                _mirror_membership_to_port(
                    self._membership_port,
                    tenant_id=tenant_id,
                    project_id=project_id,
                    user_id=old_owner_user_id,
                    role=previous_owner_new_role,  # type: ignore[arg-type]
                )

            # Step 2: promote new owner.
            self._store.update_membership_role(
                tenant_id=tenant_id,
                project_id=project_id,
                user_id=new_owner_user_id,
                role="owner",
            )
            _mirror_membership_to_port(
                self._membership_port,
                tenant_id=tenant_id,
                project_id=project_id,
                user_id=new_owner_user_id,
                role="owner",
            )

            # Step 3: flip the project's owner pointer.
            updated_record = existing.model_copy(
                update={
                    "owner_user_id": new_owner_user_id,
                    "updated_at": _now(),
                }
            )
            stored = self._store.update_project(updated_record)
            self._store.append_audit(
                ProjectAuditRecord(
                    tenant_id=tenant_id,
                    actor_user_id=caller_user_id,
                    action=(
                        "project.admin_force_transferred"
                        if admin_force
                        else "project.ownership_transferred"
                    ),
                    target_id=project_id,
                    before_state=before,
                    after_state=_safe_dump(stored),
                    context={
                        "from_user_id": old_owner_user_id,
                        "to_user_id": new_owner_user_id,
                        "previous_owner_new_role": previous_owner_new_role,
                        "admin_force": admin_force,
                        "reason": reason,
                        "project_id": project_id,
                    },
                )
            )
        return stored

    # =================================================================
    # Stars
    # =================================================================

    def star(
        self,
        *,
        tenant_id: str,
        caller_user_id: str,
        caller_roles: Iterable[str],
        project_id: str,
    ) -> None:
        # Any member can star; non-members 404.
        self._require_read(
            tenant_id=tenant_id,
            project_id=project_id,
            caller_user_id=caller_user_id,
            caller_roles=caller_roles,
        )
        self._store.upsert_star(
            ProjectStarRecord(
                tenant_id=tenant_id,
                user_id=caller_user_id,
                project_id=project_id,
            )
        )
        self._store.append_audit(
            ProjectAuditRecord(
                tenant_id=tenant_id,
                actor_user_id=caller_user_id,
                action="project.starred",
                target_id=project_id,
                context={"user_id": caller_user_id, "project_id": project_id},
            )
        )

    def unstar(
        self,
        *,
        tenant_id: str,
        caller_user_id: str,
        caller_roles: Iterable[str],
        project_id: str,
    ) -> None:
        self._require_read(
            tenant_id=tenant_id,
            project_id=project_id,
            caller_user_id=caller_user_id,
            caller_roles=caller_roles,
        )
        self._store.delete_star(
            tenant_id=tenant_id,
            project_id=project_id,
            user_id=caller_user_id,
        )
        self._store.append_audit(
            ProjectAuditRecord(
                tenant_id=tenant_id,
                actor_user_id=caller_user_id,
                action="project.unstarred",
                target_id=project_id,
                context={"user_id": caller_user_id, "project_id": project_id},
            )
        )

    # =================================================================
    # Helpers
    # =================================================================

    def _viewer_role(
        self,
        record: ProjectRecord,
        caller_user_id: str,
        caller_roles: Iterable[str],
    ) -> ProjectRole | None:
        # Owner is always a member by construction (the owner-membership
        # row is written on create + transfer); double-check via the
        # stored row so a corrupted dataset doesn't drop reads.
        if record.owner_user_id == caller_user_id:
            return "owner"
        stored = self._store.get_membership(
            tenant_id=record.tenant_id,
            project_id=record.id,
            user_id=caller_user_id,
        )
        if stored is not None:
            return stored.role  # type: ignore[return-value]
        # The membership port is the canonical resolver — in production
        # it reads from the same table; here it provides the same answer
        # for the in-memory adapter.
        return self._membership_port.member_role(
            tenant_id=record.tenant_id,
            project_id=record.id,
            user_id=caller_user_id,
        )

    def _require_read(
        self,
        *,
        tenant_id: str,
        project_id: str,
        caller_user_id: str,
        caller_roles: Iterable[str],
    ) -> ProjectRecord:
        record = self._store.get_project(tenant_id=tenant_id, project_id=project_id)
        if record is None:
            raise ProjectNotFound(project_id)
        viewer_role = self._viewer_role(record, caller_user_id, caller_roles)
        if viewer_role is None and not _is_admin(caller_roles):
            raise ProjectNotFound(project_id)
        return record

    def _counts_for(self, record: ProjectRecord) -> ProjectActivityCounts:
        stored = self._store.get_counts(
            tenant_id=record.tenant_id, project_id=record.id
        )
        if stored is not None:
            return stored
        # Project that hasn't been touched by the projector yet —
        # synthesize the all-zeros shape (plus 1 member for the owner,
        # because the owner row exists by construction).
        rows, _ = self._store.list_memberships_for_project(
            tenant_id=record.tenant_id, project_id=record.id, limit=501
        )
        members = len(rows)
        return ProjectActivityCounts(
            tenant_id=record.tenant_id,
            project_id=record.id,
            members=members,
        )

    # ----- validation ----------------------------------------------------

    def _validate_create_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ProjectInvalidRequest("invalid_payload")
        name = payload.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ProjectInvalidRequest("name_required")
        name = name.strip()
        if len(name) > _NAME_MAX:
            raise ProjectInvalidRequest("name_too_long")
        description = payload.get("description", "")
        if not isinstance(description, str):
            raise ProjectInvalidRequest("description_invalid")
        if len(description) > _DESCRIPTION_MAX:
            raise ProjectInvalidRequest("description_too_long")
        icon = payload.get("icon_emoji")
        if icon is not None and not isinstance(icon, str):
            raise ProjectInvalidRequest("icon_invalid")
        if icon is not None and len(icon) > 16:
            # Generous bound; the storage layer caps; the wire validation
            # for "single glyph w/ ZWJ" lands when the icon picker ships.
            raise ProjectInvalidRequest("icon_too_long")
        hue = payload.get("color_hue", 210)
        if not isinstance(hue, int) or not (_HUE_MIN <= hue <= _HUE_MAX):
            raise ProjectInvalidRequest("color_hue_invalid")
        return {
            "name": name,
            "description": description,
            "icon_emoji": icon or "📁",
            "color_hue": hue,
        }

    def _validate_patch_payload(
        self, existing: ProjectRecord, patch: dict[str, Any]
    ) -> dict[str, Any]:
        if not isinstance(patch, dict):
            raise ProjectInvalidRequest("invalid_payload")
        updates: dict[str, Any] = {}
        if "name" in patch:
            name = patch["name"]
            if not isinstance(name, str) or not name.strip():
                raise ProjectInvalidRequest("name_required")
            name = name.strip()
            if len(name) > _NAME_MAX:
                raise ProjectInvalidRequest("name_too_long")
            updates["name"] = name
        if "description" in patch:
            description = patch["description"]
            if description is None:
                description = ""
            if not isinstance(description, str):
                raise ProjectInvalidRequest("description_invalid")
            if len(description) > _DESCRIPTION_MAX:
                raise ProjectInvalidRequest("description_too_long")
            updates["description"] = description
        if "icon_emoji" in patch:
            icon = patch["icon_emoji"]
            if not isinstance(icon, str) or not icon:
                raise ProjectInvalidRequest("icon_invalid")
            if len(icon) > 16:
                raise ProjectInvalidRequest("icon_too_long")
            updates["icon_emoji"] = icon
        if "color_hue" in patch:
            hue = patch["color_hue"]
            if not isinstance(hue, int) or not (_HUE_MIN <= hue <= _HUE_MAX):
                raise ProjectInvalidRequest("color_hue_invalid")
            updates["color_hue"] = hue
        if "status" in patch:
            status = patch["status"]
            if status not in _VALID_STATUSES:
                raise ProjectInvalidRequest("status_invalid")
            updates["status"] = status
        return updates


# ---------------------------------------------------------------------------
# Helpers (module-private)
# ---------------------------------------------------------------------------


def _is_admin(caller_roles: Iterable[str]) -> bool:
    return any(role in _ADMIN_ROLES for role in caller_roles)


def _action_for_transition(before: str, after: str) -> str:
    """Map a status transition to its dotted audit action.

    Single-bit statuses (active / archived) → only meaningful transitions
    are archive / activate. Same-status patches (rename, recolor, etc.)
    fall through to ``project.updated``.
    """

    if before == after:
        return "project.updated"
    if before == "active" and after == "archived":
        return "project.archived"
    if before == "archived" and after == "active":
        return "project.activated"
    return "project.updated"


def _safe_dump(record: ProjectRecord) -> dict[str, Any]:
    """Dump a project record to a JSON-serialisable dict for audit rows.

    Project has no inherently sensitive fields (no secrets, no PII body
    text) — full-fidelity dump is safe. If a future field carries
    sensitive content the redaction lands here, same pattern as
    ``routines.service._safe_dump``.
    """

    return record.model_dump(mode="json")


def _membership_adapter_for(store: ProjectsStore) -> ProjectMembershipPort:
    """Default the membership port to an adapter that proxies the store.

    When the store is the in-memory adapter, the adapter shares the
    same memberships dict so the canonical port and the store stay in
    sync without two copies of the data. For other stores (e.g. the
    Postgres adapter), callers should inject the matching port.
    """

    # The :class:`InMemoryProjectMembershipAdapter` is dict-backed; we
    # bind it to a fresh dict and rely on ``_mirror_membership_to_port``
    # to keep the two views consistent. (Reading the source-of-truth
    # via the store would require coupling the adapter to the store's
    # concrete type, which we avoid for Protocol cleanliness.)
    return _StoreBackedMembershipAdapter(store)


class _StoreBackedMembershipAdapter:
    """Read-through membership adapter sitting on top of a ``ProjectsStore``.

    Default port the in-memory service uses when no custom port is
    injected. Every method delegates to the store's memberships table —
    so :class:`InMemoryProjectsStore` and the canonical ACL stay in
    lockstep without a parallel dict.

    Production deploys MUST inject the
    :class:`PostgresProjectMembershipAdapter` (which reads from the same
    table the service writes) so cross-destination consumers
    (Todos / Inbox / Routines / Library / Memory) see the same answer
    as the service.
    """

    def __init__(self, store: ProjectsStore) -> None:
        self._store = store

    def is_member(self, *, tenant_id: str, project_id: str, user_id: str) -> bool:
        return (
            self._store.get_membership(
                tenant_id=tenant_id, project_id=project_id, user_id=user_id
            )
            is not None
        )

    def member_role(
        self, *, tenant_id: str, project_id: str, user_id: str
    ) -> ProjectRole | None:
        row = self._store.get_membership(
            tenant_id=tenant_id, project_id=project_id, user_id=user_id
        )
        if row is None:
            return None
        return row.role  # type: ignore[return-value]

    def list_projects_for_user(
        self, *, tenant_id: str, user_id: str
    ) -> tuple[str, ...]:
        rows = self._store.list_memberships_for_user(
            tenant_id=tenant_id, user_id=user_id
        )
        return tuple(r.project_id for r in rows)

    def is_project_member(
        self, *, tenant_id: str, project_id: str, user_id: str
    ) -> bool:
        return self.is_member(
            tenant_id=tenant_id, project_id=project_id, user_id=user_id
        )


def _mirror_membership_to_port(
    port: ProjectMembershipPort,
    *,
    tenant_id: str,
    project_id: str,
    user_id: str,
    role: ProjectRole,
) -> None:
    """When the port carries its own membership dict (the in-memory
    adapter used by P6-A2's rewire pattern in todos/inbox/routines),
    keep the two views in sync. Read-through adapters (the store-backed
    default) are no-ops.
    """

    if isinstance(port, InMemoryProjectMembershipAdapter):
        port.add(
            tenant_id=tenant_id,
            project_id=project_id,
            user_id=user_id,
            role=role,
        )


def _unmirror_membership_from_port(
    port: ProjectMembershipPort,
    *,
    tenant_id: str,
    project_id: str,
    user_id: str,
) -> None:
    if isinstance(port, InMemoryProjectMembershipAdapter):
        port.remove(tenant_id=tenant_id, project_id=project_id, user_id=user_id)


__all__ = [
    "ProjectConflict",
    "ProjectForbidden",
    "ProjectInvalidRequest",
    "ProjectNotFound",
    "ProjectsService",
]
