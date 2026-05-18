"""Routines service — ACL + state machine + quota + audit.

The route layer in ``routes.py`` is presentation-only; every
business-logic decision lives here so the in-memory
``InMemoryRoutinesStore`` and the postgres adapter share one set of
authorization checks and state-machine transitions.

Authorization rules (cross-audit §1.3 + routines-prd §7, binding):

* Owner-only writes (PATCH / DELETE / activate / pause / state change).
* Reads: owner OR (project_id member when project_id is set) OR tenant
  admin (compliance read-only — admins cannot mutate another user's
  routine per routines-prd §7.2).
* Non-readers see 404, not 403 (existence not leaked).
* Manual-fire ACL: owner by default; widened to project-members or
  every tenant member via ``routine.permissions.manual_fire``
  (cross-audit §9.7 Q2).

State machine (routines-prd §3 + api-types/src/routines.ts):

    draft ──activate──▶ active ──pause──▶ paused ──activate──▶ active
                            │                ▲
                            ├──error──▶ errored
                            │
    (errored | paused) ──reset──▶ draft  (PATCH status=draft so the
                                          owner can edit before
                                          re-activating)

Quota (cross-audit §9.7 Q8): 100 active routines per USER, not per
tenant. Enforced at create (when status='active') and at any
state-machine transition that ends in 'active'.

Audit rows are append-only.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable, Protocol, runtime_checkable

from backend_app.identity.store import IdentityStore
from backend_app.projects.acl import (
    ProjectMembershipPort,
    _NoMemberProjectAdapter,
)
from backend_app.routines.store import (
    RoutineAuditRecord,
    RoutineFireRecord,
    RoutineRecord,
    RoutinesStore,
)


# ---------------------------------------------------------------------------
# P6.5-A2 — project connector-allowlist inheritance at routine create.
#
# The lookup is in-process (same Python service owns both Projects and
# Routines), so we model it as a small ``Protocol`` rather than an HTTP
# port. The default :class:`_NullProjectAllowlistLookup` returns
# ``None`` so test/dev environments that haven't wired the Projects
# destination still construct the service cleanly — routines created
# in those environments fall through to "no inheritance" (existing
# behavior).
#
# Cross-tenant guard: implementations MUST scope by ``tenant_id`` and
# return ``None`` for projects that don't exist in the caller's tenant.
# The default adapter at wiring time bridges to
# :class:`ProjectsService.get_project` which enforces the canonical
# 404-not-403 ACL (tenant + project-member or admin). Returning
# ``None`` from a missing / forbidden project mirrors the conversation
# resolver — never fail the routine create on a bad project id.
# ---------------------------------------------------------------------------


@runtime_checkable
class ProjectAllowlistLookup(Protocol):
    """In-process lookup for a project's ``default_connector_allowlist``.

    Implementations must return ``None`` (never raise) when the project
    is missing for the tenant, the caller lacks read rights, or the
    column / value is not set — the service degrades to "no project
    inheritance" rather than refusing the create.
    """

    def fetch_connector_allowlist(
        self,
        *,
        tenant_id: str,
        caller_user_id: str,
        project_id: str,
    ) -> tuple[str, ...] | None:
        """Return the project's allowlist (possibly empty), or ``None``."""


class _NullProjectAllowlistLookup:
    """No-op lookup that always returns ``None``.

    Used as the default when the Projects destination is not wired into
    the routines service (tests, early-phase deployments). The routine
    create flow falls through to "no inheritance" — the existing
    pre-§5.4 behavior.
    """

    def fetch_connector_allowlist(
        self,
        *,
        tenant_id: str,
        caller_user_id: str,
        project_id: str,
    ) -> tuple[str, ...] | None:
        """Return ``None`` unconditionally."""
        return None


def _now() -> datetime:
    return datetime.now(timezone.utc)


# Roles with tenant-admin read access. Treated as untrusted unless the
# verified ``ScopedIdentity.roles`` tuple set them — the route layer
# passes through what the auth middleware verified.
_ADMIN_ROLES = frozenset({"admin", "owner"})

_VALID_STATUSES = frozenset({"draft", "active", "paused", "errored"})
_VALID_PAUSE_REASONS = frozenset({"manual", "permission_shrinkage", "error"})
_VALID_MISSED_FIRE_POLICIES = frozenset({"fire_once", "fire_all", "skip"})
_VALID_MANUAL_FIRE_SCOPES = frozenset({"owner", "project_members", "tenant"})
_VALID_TRIGGER_KINDS = frozenset({"cron", "event", "webhook", "manual"})

# cross-audit §9.7 Q8 — per-USER quota (not per-tenant). 100 active
# routines is the v1 cap; bumping it requires explicit product review.
ACTIVE_ROUTINES_PER_USER_LIMIT = 100

# State transition allowlist. Each value is the set of destination
# statuses reachable from the key. State changes outside the
# allowlist raise :class:`RoutineInvalidTransition` (translated to 409
# at the route layer).
_ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "draft": frozenset({"draft", "active"}),
    # Pause is a manual or auto-pause op; both reachable from active.
    "active": frozenset({"active", "paused", "errored"}),
    # Resume goes back to active; reset goes to draft so the owner
    # can edit the definition.
    "paused": frozenset({"paused", "active", "draft", "errored"}),
    # Errored routines must be reset to draft (so the owner edits the
    # definition) before re-activating. The state-machine refuses
    # errored → active directly; this matches the user expectation of
    # "errored is broken, fix it first".
    "errored": frozenset({"errored", "draft"}),
}


class RoutineNotFound(Exception):
    """Raised when a routine doesn't exist OR the caller has no read rights.

    The 404-not-403 rule (cross-audit §1.3) collapses both branches to
    one exception so the route layer cannot accidentally distinguish
    them — the response is always 404.
    """


class RoutineForbidden(Exception):
    """Raised when the caller can READ but cannot WRITE.

    Used after read access has already been established (so 404-not-403
    still applies for the read-doesn't-exist case). The route layer
    translates this to 403.
    """


class RoutineInvalidRequest(Exception):
    """Raised for client-fixable invariant violations (400)."""


class RoutineInvalidTransition(Exception):
    """Raised when the requested state-machine transition is illegal.

    Route layer translates to 409 Conflict (the request is well-formed
    but the routine is in a state that doesn't permit the move).
    """


class RoutineQuotaExceeded(Exception):
    """Raised when activating a routine would breach the per-user quota.

    Route layer translates to 409 Conflict with a body distinguishable
    from invalid-transition (the cap is configurable per deployment).
    """


class RoutinesService:
    """Composition of the routines store + identity store with ACL + audit."""

    def __init__(
        self,
        *,
        store: RoutinesStore,
        identity_store: IdentityStore,
        project_membership: "ProjectMembershipPort | None" = None,
        project_allowlist_lookup: ProjectAllowlistLookup | None = None,
        active_quota_per_user: int = ACTIVE_ROUTINES_PER_USER_LIMIT,
    ) -> None:
        self._store = store
        self._identity = identity_store
        # Project-membership lookup is injected so the in-memory tests
        # don't need the (not-yet-shipped) Projects destination. Defaults
        # to a no-member adapter — owner-only behaviour until the
        # Projects destination lands and registers a real adapter.
        self._project_membership = project_membership or _NoMemberProjectAdapter()
        # P6.5-A2 — project ``default_connector_allowlist`` lookup. The
        # default no-op returns ``None`` so deployments / tests that
        # haven't wired the Projects destination still see the existing
        # (no-inheritance) behavior. App wiring bridges this to
        # ``ProjectsService.get_project`` so the lookup honours the
        # canonical 404-not-403 ACL and never crosses tenants.
        self._project_allowlist_lookup: ProjectAllowlistLookup = (
            project_allowlist_lookup or _NullProjectAllowlistLookup()
        )
        self._active_quota = active_quota_per_user

    # -- reads ---------------------------------------------------------

    def get_routine(
        self,
        *,
        tenant_id: str,
        caller_user_id: str,
        caller_roles: Iterable[str],
        routine_id: str,
    ) -> RoutineRecord:
        """Authorise + return a single routine.

        Raises :class:`RoutineNotFound` if the caller can't see it
        (404-not-403; the route never distinguishes "not found" from
        "not authorised").
        """

        record = self._store.get_routine(tenant_id=tenant_id, routine_id=routine_id)
        if record is None:
            raise RoutineNotFound(routine_id)
        if not self._can_read(record, caller_user_id, caller_roles):
            raise RoutineNotFound(routine_id)
        return record

    def list_routines(
        self,
        *,
        tenant_id: str,
        caller_user_id: str,
        caller_roles: Iterable[str],
        statuses: tuple[str, ...] | None = None,
        project_ids: tuple[str | None, ...] | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> tuple[tuple[RoutineRecord, ...], str | None]:
        """List the caller's readable routines.

        Composition mirrors :class:`InboxService.list_items`:

        1. Routines owned by the caller (the common path).
        2. Routines in projects the caller is a member of (read-only).
        3. (Admin only) every routine in the tenant (compliance read).
        """

        admin = any(role in _ADMIN_ROLES for role in caller_roles)
        if admin:
            return self._store.list_routines(
                tenant_id=tenant_id,
                owner_user_id=None,
                statuses=statuses,
                project_ids=project_ids,
                cursor=cursor,
                limit=limit,
            )

        owner_page, next_cursor = self._store.list_routines(
            tenant_id=tenant_id,
            owner_user_id=caller_user_id,
            statuses=statuses,
            project_ids=project_ids,
            cursor=cursor,
            limit=limit,
        )
        member_projects = self._project_membership.list_projects_for_user(
            tenant_id=tenant_id, user_id=caller_user_id
        )
        if not member_projects:
            return owner_page, next_cursor

        # Project-member reads: only listing rows whose project_id is in
        # the member-project set AND owner ≠ caller (already in owner
        # bucket). Cursor pagination over the union is approximated by
        # taking the owner page as canonical and appending project
        # rows; full keyset merge is a postgres-layer concern.
        project_page, _project_next = self._store.list_project_member_routines(
            tenant_id=tenant_id,
            project_ids=member_projects,
            cursor=None,
            limit=limit,
        )
        seen = {r.id for r in owner_page}
        merged = list(owner_page) + [r for r in project_page if r.id not in seen]
        if statuses is not None:
            merged = [r for r in merged if r.status in statuses]
        if project_ids is not None:
            merged = [r for r in merged if r.project_id in project_ids]
        return tuple(merged[:limit]), next_cursor

    # -- writes --------------------------------------------------------

    def create_routine(
        self,
        *,
        tenant_id: str,
        caller_user_id: str,
        payload: dict[str, Any],
    ) -> RoutineRecord:
        """Create a new routine owned by ``caller_user_id``.

        Status defaults to ``"draft"``; callers who pass ``status="active"``
        get the quota gate immediately. Body validation enforces the
        wire-shape invariants (manual_fire scope, trigger kinds,
        missed_fire_policy enum) before the row lands.

        P6.5-A2 — when ``project_id`` is set AND the caller did NOT
        pass an explicit ``connectors_scope`` key (key absent OR value
        is ``None``), the service inherits the project's
        ``default_connector_allowlist`` per PRD §5.4. The caller's
        explicit scope (including an explicit empty ``{}``) always wins.
        """

        # PRD §5.4: "Only when the caller did not pass an explicit
        # connectors list." Track key presence BEFORE validation
        # collapses missing/None into ``{}``.
        caller_passed_explicit_scope = (
            "connectors_scope" in payload and payload["connectors_scope"] is not None
        )

        validated = self._validate_create_payload(payload)
        status = validated.get("status", "draft")
        if status not in _VALID_STATUSES:
            raise RoutineInvalidRequest("invalid_status")

        # Quota gate: per-USER cap on ACTIVE routines (cross-audit §9.7 Q8).
        if status == "active":
            if (
                self._store.count_active_for_user(
                    tenant_id=tenant_id, owner_user_id=caller_user_id
                )
                >= self._active_quota
            ):
                raise RoutineQuotaExceeded(
                    f"active_routine_quota_exceeded:{self._active_quota}"
                )

        # P6.5-A2 — project allowlist inheritance. Only fires when:
        #   * a project_id is set on the routine, AND
        #   * the caller did NOT pass connectors_scope explicitly, AND
        #   * the project has a non-``None`` ``default_connector_allowlist``.
        # Empty allowlist (``()``) materializes to ``{}`` — explicit
        # denial (PRD §5.4). The lookup returns ``None`` for missing /
        # cross-tenant projects so a bad id never blocks create — we
        # just fall through to the no-inheritance path.
        #
        # The materialized scope ends up on the stored row's
        # ``connectors_scope``; the audit ``after_state`` carries it
        # downstream, so we don't need a separate inheritance flag here
        # — the audit consumers reconstruct "what was applied" from
        # the row dump.
        connectors_scope: dict[str, Any] = dict(
            validated.get("connectors_scope", {}) or {}
        )
        project_id_value = validated.get("project_id")
        if not caller_passed_explicit_scope and project_id_value is not None:
            allowlist = self._project_allowlist_lookup.fetch_connector_allowlist(
                tenant_id=tenant_id,
                caller_user_id=caller_user_id,
                project_id=project_id_value,
            )
            if allowlist is not None:
                # Each slug → active with no extra scope strings. The
                # fire-time permission intersection (P5-A4) gates the
                # actual run; this layer only seeds the policy.
                connectors_scope = {slug: [] for slug in allowlist}

        record = RoutineRecord(
            tenant_id=tenant_id,
            owner_user_id=caller_user_id,
            project_id=validated.get("project_id"),
            name=validated["name"],
            instructions=validated.get("instructions", ""),
            agent_id=validated["agent_id"],
            agent_version_pin=validated.get("agent_version_pin"),
            triggers=list(validated.get("triggers", [])),
            connectors_scope=connectors_scope,
            behavior=dict(validated.get("behavior", {})),
            permissions=dict(validated["permissions"]),
            code=validated.get("code"),
            status=status,
            pause_reason=validated.get("pause_reason"),
            missed_fire_policy=validated.get("missed_fire_policy", "fire_once"),
        )

        with self._store.transaction():
            stored = self._store.insert_routine(record)
            self._store.append_audit(
                RoutineAuditRecord(
                    tenant_id=tenant_id,
                    actor_user_id=caller_user_id,
                    action="routine.created",
                    target_id=stored.id,
                    after_state=_safe_dump(stored),
                )
            )
        return stored

    def update_routine(
        self,
        *,
        tenant_id: str,
        caller_user_id: str,
        caller_roles: Iterable[str],
        routine_id: str,
        patch: dict[str, Any],
    ) -> RoutineRecord:
        """Patch a routine's definition. Owner-only writes.

        ``patch`` carries any subset of the ``UpdateRoutineRequest``
        wire shape. State transitions are validated against the
        state-machine; invalid moves raise
        :class:`RoutineInvalidTransition`. Activation transitions are
        quota-gated.
        """

        existing = self._store.get_routine(tenant_id=tenant_id, routine_id=routine_id)
        if existing is None or not self._can_read(
            existing, caller_user_id, caller_roles
        ):
            # 404-not-403 on both "missing" and "no read rights".
            raise RoutineNotFound(routine_id)
        if existing.owner_user_id != caller_user_id:
            # Read access established (project member or admin) but
            # writes are owner-only. cross-audit §1.3 + routines-prd §7.2.
            raise RoutineForbidden(routine_id)

        updates = self._validate_patch_payload(existing, patch)
        new_record = existing.model_copy(update={**updates, "updated_at": _now()})

        # Quota gate on transitions ending in 'active'. Only count if
        # the move is an activation (existing.status != 'active'); a
        # patch on an already-active row that keeps status='active'
        # doesn't grow the count.
        if (
            new_record.status == "active"
            and existing.status != "active"
            and self._store.count_active_for_user(
                tenant_id=tenant_id, owner_user_id=caller_user_id
            )
            >= self._active_quota
        ):
            raise RoutineQuotaExceeded(
                f"active_routine_quota_exceeded:{self._active_quota}"
            )

        before = _safe_dump(existing)
        after = _safe_dump(new_record)
        action = _action_for_transition(existing.status, new_record.status)
        with self._store.transaction():
            stored = self._store.update_routine(new_record)
            self._store.append_audit(
                RoutineAuditRecord(
                    tenant_id=tenant_id,
                    actor_user_id=caller_user_id,
                    action=action,
                    target_id=stored.id,
                    before_state=before,
                    after_state=after,
                )
            )
        return stored

    def delete_routine(
        self,
        *,
        tenant_id: str,
        caller_user_id: str,
        caller_roles: Iterable[str],
        routine_id: str,
    ) -> None:
        """Soft-delete a routine. Owner-only.

        The row stays in the table (compliance reads can still find it
        via ``include_deleted=True``); the cleanup job in P5-A2 hard-
        deletes after the retention window.
        """

        existing = self._store.get_routine(tenant_id=tenant_id, routine_id=routine_id)
        if existing is None or not self._can_read(
            existing, caller_user_id, caller_roles
        ):
            raise RoutineNotFound(routine_id)
        if existing.owner_user_id != caller_user_id:
            raise RoutineForbidden(routine_id)

        before = _safe_dump(existing)
        with self._store.transaction():
            self._store.soft_delete_routine(tenant_id=tenant_id, routine_id=routine_id)
            self._store.append_audit(
                RoutineAuditRecord(
                    tenant_id=tenant_id,
                    actor_user_id=caller_user_id,
                    action="routine.deleted",
                    target_id=routine_id,
                    before_state=before,
                )
            )

    def manual_fire(
        self,
        *,
        tenant_id: str,
        caller_user_id: str,
        caller_roles: Iterable[str],
        routine_id: str,
    ) -> RoutineFireRecord:
        """Record a manual fire ("Run now").

        ACL per cross-audit §9.7 Q2 — default owner-only, widened by
        ``permissions.manual_fire``. The actual run handoff lives in
        P5-A2 (run-coordinator); this method writes the fire-metadata
        row + audit + returns the fire id so the frontend can
        progress-poll / SSE.
        """

        existing = self._store.get_routine(tenant_id=tenant_id, routine_id=routine_id)
        if existing is None or not self._can_read(
            existing, caller_user_id, caller_roles
        ):
            raise RoutineNotFound(routine_id)
        if not self._can_manual_fire(existing, caller_user_id, caller_roles):
            raise RoutineForbidden(routine_id)
        if existing.status in {"errored"}:
            # Manual fire is disabled while the routine is errored —
            # the owner has to clear the error first. Paused routines
            # CAN be manually fired (the schedule is paused, not the
            # ability to run on demand).
            raise RoutineInvalidTransition("routine_errored")

        fire = RoutineFireRecord(
            tenant_id=tenant_id,
            routine_id=routine_id,
            trigger_kind="manual",
            status="queued",
        )
        with self._store.transaction():
            stored = self._store.insert_fire(fire)
            self._store.append_audit(
                RoutineAuditRecord(
                    tenant_id=tenant_id,
                    actor_user_id=caller_user_id,
                    action="routine.manual_fired",
                    target_id=routine_id,
                    after_state={
                        "fire_id": stored.id,
                        "trigger_kind": stored.trigger_kind,
                    },
                )
            )
        return stored

    # -- helpers -------------------------------------------------------

    def _can_read(
        self,
        record: RoutineRecord,
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

    def _can_manual_fire(
        self,
        record: RoutineRecord,
        caller_user_id: str,
        caller_roles: Iterable[str],
    ) -> bool:
        # Owner always can (irrespective of the manual_fire scope —
        # widening only adds callers, never removes the owner).
        if record.owner_user_id == caller_user_id:
            return True
        scope = record.permissions.get("manual_fire", "owner")
        if scope not in _VALID_MANUAL_FIRE_SCOPES:
            # Defensive: corrupted scope falls back to owner-only.
            return False
        if scope == "owner":
            return False
        if scope == "tenant":
            # Caller is already tenant-scoped because ``_can_read``
            # gated the read; any tenant member with read access can
            # manual-fire.
            return True
        # project_members: caller must be a member of the routine's
        # project (and the routine must have a project_id; otherwise
        # the scope is unsatisfiable and we fall back to owner-only).
        if scope == "project_members" and record.project_id is not None:
            return self._project_membership.is_project_member(
                tenant_id=record.tenant_id,
                project_id=record.project_id,
                user_id=caller_user_id,
            )
        return False

    # -- payload validation -------------------------------------------

    def _validate_create_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise RoutineInvalidRequest("invalid_payload")
        name = payload.get("name")
        if not isinstance(name, str) or not name.strip():
            raise RoutineInvalidRequest("name_required")
        if len(name) > 80:
            raise RoutineInvalidRequest("name_too_long")
        instructions = payload.get("instructions", "")
        if not isinstance(instructions, str):
            raise RoutineInvalidRequest("instructions_invalid")
        if len(instructions) > 16384:
            raise RoutineInvalidRequest("instructions_too_long")
        agent_id = payload.get("agent_id")
        if not isinstance(agent_id, str) or not agent_id.strip():
            raise RoutineInvalidRequest("agent_id_required")

        triggers = payload.get("triggers", [])
        if not isinstance(triggers, list):
            raise RoutineInvalidRequest("triggers_invalid")
        validated_triggers = [self._validate_trigger(t) for t in triggers]

        permissions = payload.get("permissions") or {}
        if not isinstance(permissions, dict):
            raise RoutineInvalidRequest("permissions_invalid")
        manual_fire = permissions.get("manual_fire", "owner")
        if manual_fire not in _VALID_MANUAL_FIRE_SCOPES:
            raise RoutineInvalidRequest("manual_fire_scope_invalid")

        missed_fire_policy = payload.get("missed_fire_policy", "fire_once")
        if missed_fire_policy not in _VALID_MISSED_FIRE_POLICIES:
            raise RoutineInvalidRequest("missed_fire_policy_invalid")

        project_id = payload.get("project_id")
        if project_id is not None and not isinstance(project_id, str):
            raise RoutineInvalidRequest("project_id_invalid")
        # project_members manual-fire scope requires a project_id; reject
        # the combo at create time so the owner doesn't ship an
        # unsatisfiable ACL.
        if manual_fire == "project_members" and not project_id:
            raise RoutineInvalidRequest("manual_fire_project_members_requires_project")

        agent_version_pin = payload.get("agent_version_pin")
        if agent_version_pin is not None and not isinstance(agent_version_pin, str):
            raise RoutineInvalidRequest("agent_version_pin_invalid")

        code = payload.get("code")
        if code is not None and not isinstance(code, dict):
            raise RoutineInvalidRequest("code_invalid")

        connectors_scope = payload.get("connectors_scope") or {}
        if not isinstance(connectors_scope, dict):
            raise RoutineInvalidRequest("connectors_scope_invalid")
        behavior = payload.get("behavior") or {}
        if not isinstance(behavior, dict):
            raise RoutineInvalidRequest("behavior_invalid")

        return {
            "name": name.strip(),
            "instructions": instructions,
            "agent_id": agent_id.strip(),
            "agent_version_pin": agent_version_pin,
            "triggers": validated_triggers,
            "connectors_scope": connectors_scope,
            "behavior": behavior,
            "permissions": {"manual_fire": manual_fire},
            "code": code,
            "missed_fire_policy": missed_fire_policy,
            "project_id": project_id,
            "status": payload.get("status", "draft"),
            "pause_reason": payload.get("pause_reason"),
        }

    def _validate_patch_payload(
        self, existing: RoutineRecord, patch: dict[str, Any]
    ) -> dict[str, Any]:
        if not isinstance(patch, dict):
            raise RoutineInvalidRequest("invalid_payload")
        updates: dict[str, Any] = {}
        if "name" in patch:
            name = patch["name"]
            if not isinstance(name, str) or not name.strip():
                raise RoutineInvalidRequest("name_required")
            if len(name) > 80:
                raise RoutineInvalidRequest("name_too_long")
            updates["name"] = name.strip()
        if "instructions" in patch:
            instructions = patch["instructions"]
            if not isinstance(instructions, str):
                raise RoutineInvalidRequest("instructions_invalid")
            if len(instructions) > 16384:
                raise RoutineInvalidRequest("instructions_too_long")
            updates["instructions"] = instructions
        if "agent_id" in patch:
            agent_id = patch["agent_id"]
            if not isinstance(agent_id, str) or not agent_id.strip():
                raise RoutineInvalidRequest("agent_id_required")
            updates["agent_id"] = agent_id.strip()
        if "agent_version_pin" in patch:
            pin = patch["agent_version_pin"]
            if pin is not None and not isinstance(pin, str):
                raise RoutineInvalidRequest("agent_version_pin_invalid")
            updates["agent_version_pin"] = pin
        if "triggers" in patch:
            triggers = patch["triggers"]
            if not isinstance(triggers, list):
                raise RoutineInvalidRequest("triggers_invalid")
            updates["triggers"] = [self._validate_trigger(t) for t in triggers]
        if "connectors_scope" in patch:
            cs = patch["connectors_scope"]
            if not isinstance(cs, dict):
                raise RoutineInvalidRequest("connectors_scope_invalid")
            updates["connectors_scope"] = cs
        if "behavior" in patch:
            beh = patch["behavior"]
            if not isinstance(beh, dict):
                raise RoutineInvalidRequest("behavior_invalid")
            updates["behavior"] = beh
        if "permissions" in patch:
            perms = patch["permissions"] or {}
            if not isinstance(perms, dict):
                raise RoutineInvalidRequest("permissions_invalid")
            merged = dict(existing.permissions)
            if "manual_fire" in perms:
                manual_fire = perms["manual_fire"]
                if manual_fire not in _VALID_MANUAL_FIRE_SCOPES:
                    raise RoutineInvalidRequest("manual_fire_scope_invalid")
                target_project = (
                    patch.get("project_id", existing.project_id)
                    if "project_id" in patch
                    else existing.project_id
                )
                if manual_fire == "project_members" and not target_project:
                    raise RoutineInvalidRequest(
                        "manual_fire_project_members_requires_project"
                    )
                merged["manual_fire"] = manual_fire
            updates["permissions"] = merged
        if "missed_fire_policy" in patch:
            mfp = patch["missed_fire_policy"]
            if mfp not in _VALID_MISSED_FIRE_POLICIES:
                raise RoutineInvalidRequest("missed_fire_policy_invalid")
            updates["missed_fire_policy"] = mfp
        if "project_id" in patch:
            project_id = patch["project_id"]
            if project_id is not None and not isinstance(project_id, str):
                raise RoutineInvalidRequest("project_id_invalid")
            updates["project_id"] = project_id
        if "code" in patch:
            code = patch["code"]
            if code is not None and not isinstance(code, dict):
                raise RoutineInvalidRequest("code_invalid")
            updates["code"] = code
        if "status" in patch:
            new_status = patch["status"]
            if new_status not in _VALID_STATUSES:
                raise RoutineInvalidRequest("status_invalid")
            allowed = _ALLOWED_TRANSITIONS.get(existing.status, frozenset())
            if new_status not in allowed:
                raise RoutineInvalidTransition(
                    f"transition_not_allowed:{existing.status}->{new_status}"
                )
            updates["status"] = new_status
            # Reset pause_reason when leaving paused/errored unless the
            # patch supplied one explicitly. Avoids the "row is active
            # but pause_reason still set" foot-gun for SIEM.
            if new_status not in {"paused", "errored"} and "pause_reason" not in patch:
                updates["pause_reason"] = None
        if "pause_reason" in patch:
            reason = patch["pause_reason"]
            if reason is not None and reason not in _VALID_PAUSE_REASONS:
                raise RoutineInvalidRequest("pause_reason_invalid")
            # Allowed when the resulting status is paused/errored;
            # otherwise drop to None to keep the invariant tight.
            target_status = updates.get("status", existing.status)
            if reason is not None and target_status not in {"paused", "errored"}:
                raise RoutineInvalidRequest("pause_reason_requires_paused_or_errored")
            updates["pause_reason"] = reason
        return updates

    def _validate_trigger(self, trigger: Any) -> dict[str, Any]:
        if not isinstance(trigger, dict):
            raise RoutineInvalidRequest("trigger_invalid")
        kind = trigger.get("kind")
        if kind not in _VALID_TRIGGER_KINDS:
            raise RoutineInvalidRequest("trigger_kind_invalid")
        if kind == "cron":
            spec = trigger.get("spec")
            if not isinstance(spec, str) or not spec.strip():
                raise RoutineInvalidRequest("cron_spec_required")
            tz = trigger.get("timezone")
            if tz is not None and not isinstance(tz, str):
                raise RoutineInvalidRequest("cron_timezone_invalid")
            result: dict[str, Any] = {"kind": "cron", "spec": spec.strip()}
            if tz:
                result["timezone"] = tz
            return result
        if kind == "event":
            source = trigger.get("source")
            event_name = trigger.get("event_name")
            if not isinstance(source, str) or not source.strip():
                raise RoutineInvalidRequest("event_source_required")
            if not isinstance(event_name, str) or not event_name.strip():
                raise RoutineInvalidRequest("event_name_required")
            return {
                "kind": "event",
                "source": source.strip(),
                "event_name": event_name.strip(),
            }
        if kind == "webhook":
            trigger_id = trigger.get("trigger_id")
            if not isinstance(trigger_id, str) or not trigger_id.strip():
                raise RoutineInvalidRequest("webhook_trigger_id_required")
            return {"kind": "webhook", "trigger_id": trigger_id.strip()}
        # manual triggers have no payload; the kind alone is sufficient.
        return {"kind": "manual"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _safe_dump(record: RoutineRecord) -> dict[str, Any]:
    """Dump the record to a JSON-serialisable dict for audit rows.

    The ``instructions`` field is treated as sensitive (routines-prd
    §7.5) — audit rows store the length + a content fingerprint
    rather than the raw text. Telemetry redaction lives at the
    SIEM exporter; this is the storage-layer redaction so the raw
    text never lands in the audit table.
    """

    dumped = record.model_dump(mode="json")
    instructions = dumped.get("instructions", "")
    if isinstance(instructions, str) and instructions:
        # Don't ship raw instructions to the audit row. Store the
        # length only; SIEM compliance can fetch the live row via the
        # audit export pipeline if a content-level review is needed.
        dumped["instructions"] = {
            "redacted": True,
            "length": len(instructions),
        }
    return dumped


def _action_for_transition(before: str, after: str) -> str:
    """Map a state transition to its dotted audit action."""

    if before == after:
        return "routine.updated"
    if after == "active":
        return "routine.activated"
    if after == "paused":
        return "routine.paused"
    if after == "errored":
        return "routine.errored"
    if after == "draft":
        return "routine.reset_to_draft"
    return "routine.updated"


__all__ = [
    "ACTIVE_ROUTINES_PER_USER_LIMIT",
    "RoutineForbidden",
    "RoutineInvalidRequest",
    "RoutineInvalidTransition",
    "RoutineNotFound",
    "RoutineQuotaExceeded",
    "RoutinesService",
]
