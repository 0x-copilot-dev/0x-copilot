"""Tools service — CRUD + ACL + audit + usage projection (Phase 10 P10-A2).

Route layer is presentation-only; every business-logic decision lives
here so the in-memory + Postgres adapters share one set of authorization
checks, invariants, and audit hooks.

Authorization (tools-prd §6 + cross-audit §1.3 binding 2026-05-17):

* **Reads.** Tenant member when ``project_id`` is null; OWNER or
  project-member (via the canonical ``backend_app.projects.acl.is_member``
  predicate — no reimplementation here) OR tenant admin (compliance read;
  audited at the route layer) when ``project_id`` is set. Non-readers see
  404, not 403 (existence-not-leaked default).
* **Writes.** Owner OR tenant admin. Project members get reads only;
  invocation happens via runs (audited via the existing
  ``runtime_tool_invocations`` path), not via the tools mutation routes.
* **Cross-tenant.** Tenant scoping is the verified bearer's tenant
  claim — never the request body (cross-audit §3.1).

Usage projection (tools-prd §3.2 — single-tracker invariant TU-1):

* :meth:`compute_usage` groups over ``runtime_tool_invocations`` (the
  existing Phase 0 table — read via the store's ``list_invocations``
  helper) and bolts the rolled-up shape onto the ``Tool`` wire row at
  read time. There is NO parallel ``tool_usage_daily`` table.
* For tools that wrap an LLM step, the same projection also reads
  ``runtime_model_call_usage`` (Phase 0). The wiring lands in P10-A3;
  P10-A2 returns the invocations-only projection (LLM stats zero when
  no model rows exist).

Sandbox / test-call:

* :meth:`run_test_call` returns a 501-style stub envelope until P10-A3
  lands the code-routine sandbox executor. The route layer translates
  the stub to HTTP 501.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Any, Iterable

from backend_app.projects.acl import (
    ProjectMembershipPort,
    is_member,
)
from backend_app.tools.store import (
    ToolAuditRecord,
    ToolInvocationRecord,
    ToolRecord,
    ToolsStore,
    VALID_SORTS,
)


_LOGGER = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


_ADMIN_ROLES = frozenset({"admin", "owner"})

_NAME_MAX = 200
_DESCRIPTION_MAX = 2000
_TAG_MAX_LEN = 64
_TAG_COUNT_MAX = 50
_STATUS_REASON_MAX = 500
_ARGS_SUMMARY_MAX = 240

# Status values valid on POST/PATCH. ``error`` / ``pending_review`` are
# server-set (transport adapters / scope-review pipeline) — clients may
# read them, but cannot write them on the public route.
_CLIENT_PATCHABLE_STATUSES: frozenset[str] = frozenset({"enabled", "disabled"})
_VALID_STATUSES: frozenset[str] = frozenset(
    {"enabled", "disabled", "error", "pending_review"}
)
_VALID_KINDS: frozenset[str] = frozenset({"mcp", "openapi", "builtin", "code", "skill"})
_VALID_SCOPES: frozenset[str] = frozenset({"read", "write", "both"})
_VALID_TRANSPORT_KINDS: frozenset[str] = frozenset(
    {"mcp", "http", "in_process", "sandbox"}
)
# Threshold at which the runtime auto-flips ``status`` to ``error`` after
# consecutive failures (tools-prd §1.6). Server-side only; the public
# routes never reset the counter directly.
ERROR_THRESHOLD_DEFAULT = 5


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class ToolNotFound(Exception):
    """Raised when a tool doesn't exist OR the caller has no read rights.

    Collapses both branches so the route layer cannot accidentally
    distinguish them — response is always 404 (cross-audit §1.3
    binding).
    """


class ToolForbidden(Exception):
    """Raised when the caller can READ but cannot WRITE.

    Used after read access has been established (so the 404-not-403
    rule still applies for the no-read case). Route layer → 403.
    """


class ToolInvalidRequest(Exception):
    """Client-fixable invariant violation (400)."""


class ToolConflict(Exception):
    """State-conflict violation (409).

    Used for: re-enable when status is ``pending_review``, scope-widen
    without admin review, etc.
    """

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class ToolNotImplemented(Exception):
    """Server-recognised but not yet wired (P10-A2 → P10-A3 handoff)."""

    def __init__(self, code: str = "not_implemented") -> None:
        super().__init__(code)
        self.code = code


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class ToolsService:
    """CRUD + ACL + audit + usage projection.

    Consumes the canonical :class:`ProjectMembershipPort` for project-
    scoped reads — never reimplements the membership predicate.
    """

    def __init__(
        self,
        *,
        store: ToolsStore,
        membership_port: ProjectMembershipPort,
        error_threshold: int = ERROR_THRESHOLD_DEFAULT,
    ) -> None:
        self._store = store
        self._membership = membership_port
        self._error_threshold = error_threshold

    # =================================================================
    # Reads
    # =================================================================

    def get_tool(
        self,
        *,
        tenant_id: str,
        caller_user_id: str,
        caller_roles: Iterable[str],
        tool_id: str,
    ) -> ToolRecord:
        """Authorise + return a single tool.

        Raises :class:`ToolNotFound` if the caller can't see it (404-not-
        403; the route never distinguishes "not found" from "not
        authorised").
        """

        record = self._store.get_tool(tenant_id=tenant_id, tool_id=tool_id)
        if record is None:
            raise ToolNotFound(tool_id)
        if not self._can_read(record, caller_user_id, caller_roles):
            raise ToolNotFound(tool_id)
        return record

    def list_tools(
        self,
        *,
        tenant_id: str,
        caller_user_id: str,
        caller_roles: Iterable[str],
        kinds: tuple[str, ...] | None = None,
        scopes: tuple[str, ...] | None = None,
        statuses: tuple[str, ...] | None = None,
        project_ids: tuple[str, ...] | None = None,
        owner_user_ids: tuple[str, ...] | None = None,
        tags: tuple[str, ...] | None = None,
        q: str | None = None,
        cursor: str | None = None,
        limit: int = 50,
        sort: str = "name",
    ) -> tuple[tuple[ToolRecord, ...], str | None]:
        """List tools visible to the caller.

        The store pre-computes the visibility predicate from
        ``readable_project_ids`` (the union of every project the caller
        is a member of) so we make exactly ONE call to the membership
        port. Admins short-circuit visibility (compliance read).
        """

        if sort not in VALID_SORTS:
            raise ToolInvalidRequest("sort_invalid")

        admin = _is_admin(caller_roles)
        readable_project_ids: tuple[str, ...]
        if admin:
            readable_project_ids = ()
        else:
            readable_project_ids = self._membership.list_projects_for_user(
                tenant_id=tenant_id, user_id=caller_user_id
            )

        return self._store.list_tools(
            tenant_id=tenant_id,
            kinds=kinds,
            scopes=scopes,
            statuses=statuses,
            project_ids=project_ids,
            owner_user_ids=owner_user_ids,
            tags=tags,
            q=q,
            visible_to_user_id=caller_user_id,
            readable_project_ids=readable_project_ids,
            admin=admin,
            cursor=cursor,
            limit=limit,
            sort=sort,
        )

    # =================================================================
    # Writes — create
    # =================================================================

    def create_tool(
        self,
        *,
        tenant_id: str,
        caller_user_id: str,
        payload: dict[str, Any],
    ) -> ToolRecord:
        validated = self._validate_create(payload)
        record = ToolRecord(
            tenant_id=tenant_id,
            owner_user_id=caller_user_id,
            **validated,
        )
        with self._store.transaction():
            stored = self._store.insert_tool(record)
            self._store.append_audit(
                ToolAuditRecord(
                    tenant_id=tenant_id,
                    actor_user_id=caller_user_id,
                    action="tool.created",
                    target_id=stored.id,
                    after_state=_dump_for_audit(stored),
                    context={
                        "project_id": stored.project_id,
                        "kind": stored.kind,
                        "scope": stored.scope,
                    },
                )
            )
        return stored

    # =================================================================
    # Writes — PATCH
    # =================================================================

    def update_tool(
        self,
        *,
        tenant_id: str,
        caller_user_id: str,
        caller_roles: Iterable[str],
        tool_id: str,
        patch: dict[str, Any],
    ) -> ToolRecord:
        existing = self._store.get_tool(tenant_id=tenant_id, tool_id=tool_id)
        if existing is None:
            raise ToolNotFound(tool_id)
        if not self._can_read(existing, caller_user_id, caller_roles):
            raise ToolNotFound(tool_id)
        if not self._can_write(existing, caller_user_id, caller_roles):
            raise ToolForbidden(tool_id)

        validated = self._validate_patch(existing, patch)
        before = _dump_for_audit(existing)

        new_updates = dict(validated)
        new_updates["updated_at"] = _now()
        # On enable/disable cycle: clear the consecutive-error counter
        # when the operator explicitly enables (tools-prd §1.6).
        if validated.get("status") == "enabled":
            new_updates["consecutive_error_count"] = 0

        new_record = existing.model_copy(update=new_updates)

        with self._store.transaction():
            stored = self._store.update_tool(new_record)
            self._store.append_audit(
                ToolAuditRecord(
                    tenant_id=tenant_id,
                    actor_user_id=caller_user_id,
                    action=_action_for_patch(validated),
                    target_id=stored.id,
                    before_state=before,
                    after_state=_dump_for_audit(stored),
                    context={
                        "changed_fields": sorted(validated.keys()),
                        "project_id": stored.project_id,
                    },
                )
            )
            # Scope-change emits an additional ``tool.scope_changed`` audit
            # row for compliance search (tools-prd §6.3).
            if "scope" in validated and validated["scope"] != existing.scope:
                self._store.append_audit(
                    ToolAuditRecord(
                        tenant_id=tenant_id,
                        actor_user_id=caller_user_id,
                        action="tool.scope_changed",
                        target_id=stored.id,
                        before_state={"scope": existing.scope},
                        after_state={"scope": stored.scope},
                        context={"project_id": stored.project_id},
                    )
                )
        return stored

    # =================================================================
    # Writes — enable / disable
    # =================================================================

    def set_status(
        self,
        *,
        tenant_id: str,
        caller_user_id: str,
        caller_roles: Iterable[str],
        tool_id: str,
        new_status: str,
        reason: str | None = None,
    ) -> ToolRecord:
        if new_status not in _CLIENT_PATCHABLE_STATUSES:
            raise ToolInvalidRequest("status_invalid")
        return self.update_tool(
            tenant_id=tenant_id,
            caller_user_id=caller_user_id,
            caller_roles=caller_roles,
            tool_id=tool_id,
            patch={"status": new_status, "status_reason": reason},
        )

    # =================================================================
    # Writes — DELETE (soft)
    # =================================================================

    def delete_tool(
        self,
        *,
        tenant_id: str,
        caller_user_id: str,
        caller_roles: Iterable[str],
        tool_id: str,
    ) -> None:
        existing = self._store.get_tool(tenant_id=tenant_id, tool_id=tool_id)
        if existing is None:
            raise ToolNotFound(tool_id)
        if not self._can_read(existing, caller_user_id, caller_roles):
            raise ToolNotFound(tool_id)
        if not self._can_write(existing, caller_user_id, caller_roles):
            raise ToolForbidden(tool_id)

        before = _dump_for_audit(existing)
        with self._store.transaction():
            self._store.soft_delete_tool(tenant_id=tenant_id, tool_id=tool_id)
            self._store.append_audit(
                ToolAuditRecord(
                    tenant_id=tenant_id,
                    actor_user_id=caller_user_id,
                    action="tool.deleted",
                    target_id=tool_id,
                    before_state=before,
                    context={
                        "soft": True,
                        "project_id": existing.project_id,
                    },
                )
            )

    # =================================================================
    # Test call (P10-A2 stub; P10-A3 lands the sandbox executor)
    # =================================================================

    def run_test_call(
        self,
        *,
        tenant_id: str,
        caller_user_id: str,
        caller_roles: Iterable[str],
        tool_id: str,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        """Test-call entrypoint.

        P10-A2 returns a 501-style stub envelope; P10-A3 wires the actual
        sandbox executor. The audit row is still written so compliance can
        see attempted test calls even before the executor lands.
        """

        existing = self._store.get_tool(tenant_id=tenant_id, tool_id=tool_id)
        if existing is None:
            raise ToolNotFound(tool_id)
        if not self._can_read(existing, caller_user_id, caller_roles):
            raise ToolNotFound(tool_id)
        if not self._can_write(existing, caller_user_id, caller_roles):
            raise ToolForbidden(tool_id)
        if not isinstance(args, dict):
            raise ToolInvalidRequest("args_invalid")

        with self._store.transaction():
            self._store.append_audit(
                ToolAuditRecord(
                    tenant_id=tenant_id,
                    actor_user_id=caller_user_id,
                    action="tool.test_called",
                    target_id=tool_id,
                    after_state=None,
                    context={
                        "project_id": existing.project_id,
                        "args_summary": _truncate(
                            _json_preview(args), _ARGS_SUMMARY_MAX
                        ),
                        "result_summary": None,
                        # Stub flag — P10-A3 swaps this out for the
                        # actual executor latency + status.
                        "executor": "not_wired",
                    },
                )
            )
        raise ToolNotImplemented("code_sandbox_not_yet_wired")

    # =================================================================
    # Invocations + usage projection
    # =================================================================

    def list_invocations(
        self,
        *,
        tenant_id: str,
        caller_user_id: str,
        caller_roles: Iterable[str],
        tool_id: str,
        after_id: str | None = None,
        since: datetime | None = None,
        caller_kinds: tuple[str, ...] | None = None,
        statuses: tuple[str, ...] | None = None,
        limit: int = 50,
    ) -> tuple[tuple[ToolInvocationRecord, ...], str | None]:
        # ACL: same read gate as the detail view.
        self.get_tool(
            tenant_id=tenant_id,
            caller_user_id=caller_user_id,
            caller_roles=caller_roles,
            tool_id=tool_id,
        )
        return self._store.list_invocations(
            tenant_id=tenant_id,
            tool_id=tool_id,
            after_id=after_id,
            since=since,
            caller_kinds=caller_kinds,
            statuses=statuses,
            limit=limit,
        )

    def compute_usage(
        self,
        *,
        tenant_id: str,
        tool_id: str,
    ) -> dict[str, dict[str, Any]]:
        """Return the 24h / 7d / 30d windowed usage projection.

        Single-tracker invariant (cross-audit §5.5 / TU-1): this is a
        read-time GROUP BY over the existing ``runtime_tool_invocations``
        rows. No parallel ``tool_usage_daily`` table.
        """

        now = _now()
        windows = {
            "window_24h": now - timedelta(hours=24),
            "window_7d": now - timedelta(days=7),
            "window_30d": now - timedelta(days=30),
        }
        # Pull the full 30d window once; partition in Python.
        rows, _ = self._store.list_invocations(
            tenant_id=tenant_id,
            tool_id=tool_id,
            since=windows["window_30d"],
            limit=10_000,
        )
        out: dict[str, dict[str, Any]] = {}
        for label, cutoff in windows.items():
            window_rows = [r for r in rows if r.started_at >= cutoff]
            out[label] = _projection_from_rows(window_rows)
        return out

    def compute_rolled_up_usage(
        self, *, tenant_id: str, tool_id: str
    ) -> dict[str, Any]:
        """Return the projection embedded on ``Tool.usage``.

        Same source rows as :meth:`compute_usage` (single-tracker
        invariant); shape matches ``ToolUsageProjection`` in api-types.
        """

        now = _now()
        rows_30d, _ = self._store.list_invocations(
            tenant_id=tenant_id,
            tool_id=tool_id,
            since=now - timedelta(days=30),
            limit=10_000,
        )
        rows_24h = [r for r in rows_30d if r.started_at >= now - timedelta(hours=24)]
        proj_30 = _projection_from_rows(rows_30d)
        return {
            "calls_24h": len(rows_24h),
            "calls_30d": proj_30["calls"],
            "p50_latency_ms_30d": proj_30["p50_latency_ms"],
            "success_rate_30d": proj_30["success_rate"],
            "last_used_at": proj_30["last_used_at"],
        }

    # =================================================================
    # Internal — used by the internal routes; not exposed on the public surface
    # =================================================================

    def record_invocation(
        self,
        *,
        tenant_id: str,
        tool_id: str,
        record: ToolInvocationRecord,
    ) -> ToolInvocationRecord:
        """Persist a ``runtime_tool_invocations`` row.

        Called by ai-backend at every tool-call return via
        ``POST /internal/v1/tools/{id}/invocations``. The tenant + tool
        scope are validated by the caller's verified service-token
        identity.
        """

        if record.tenant_id != tenant_id or record.tool_id != tool_id:
            raise ToolInvalidRequest("scope_mismatch")
        # Truncate the long summaries at the boundary so the wire shape
        # invariant survives even if the producer skipped it.
        record = record.model_copy(
            update={
                "args_summary": _truncate(record.args_summary, _ARGS_SUMMARY_MAX),
                "result_summary": _truncate(
                    record.result_summary or "", _ARGS_SUMMARY_MAX
                )
                or None,
            }
        )
        with self._store.transaction():
            stored = self._store.insert_invocation(record)
            # Successful invocation clears the consecutive-error counter.
            if record.status == "ok":
                self._reset_error_counter(tenant_id=tenant_id, tool_id=tool_id)
        return stored

    def bump_error_counter(self, *, tenant_id: str, tool_id: str) -> ToolRecord | None:
        """Increment the consecutive-error counter and flip status when
        the threshold is exceeded.

        Returns the updated tool record, or ``None`` when the tool is
        missing / cross-tenant. Used by ai-backend transport adapters
        via ``POST /internal/v1/tools/{id}/error``.
        """

        existing = self._store.get_tool(tenant_id=tenant_id, tool_id=tool_id)
        if existing is None:
            return None
        new_count = existing.consecutive_error_count + 1
        updates: dict[str, Any] = {
            "consecutive_error_count": new_count,
            "updated_at": _now(),
        }
        flipped = False
        if new_count >= self._error_threshold and existing.status == "enabled":
            updates["status"] = "error"
            updates["status_reason"] = "consecutive_errors_exceeded_threshold"
            flipped = True
        new_record = existing.model_copy(update=updates)
        with self._store.transaction():
            stored = self._store.update_tool(new_record)
            if flipped:
                self._store.append_audit(
                    ToolAuditRecord(
                        tenant_id=tenant_id,
                        actor_user_id=existing.owner_user_id,
                        action="tool.error_threshold",
                        target_id=tool_id,
                        before_state={"status": existing.status},
                        after_state={"status": "error"},
                        context={
                            "consecutive_error_count": new_count,
                            "threshold": self._error_threshold,
                        },
                    )
                )
        return stored

    def _reset_error_counter(self, *, tenant_id: str, tool_id: str) -> None:
        existing = self._store.get_tool(tenant_id=tenant_id, tool_id=tool_id)
        if existing is None or existing.consecutive_error_count == 0:
            return
        new_record = existing.model_copy(
            update={
                "consecutive_error_count": 0,
                "updated_at": _now(),
            }
        )
        self._store.update_tool(new_record)

    # =================================================================
    # ACL helpers
    # =================================================================

    def _can_read(
        self,
        record: ToolRecord,
        caller_user_id: str,
        caller_roles: Iterable[str],
    ) -> bool:
        if record.owner_user_id == caller_user_id:
            return True
        if _is_admin(caller_roles):
            return True
        if record.project_id is None:
            # Tenant-readable when no project filter is set.
            return True
        # Canonical ACL — single source of truth at
        # backend_app.projects.acl.is_member. No reimplementation.
        return is_member(
            self._membership,
            tenant_id=record.tenant_id,
            project_id=record.project_id,
            user_id=caller_user_id,
        )

    def _can_write(
        self,
        record: ToolRecord,
        caller_user_id: str,
        caller_roles: Iterable[str],
    ) -> bool:
        if record.owner_user_id == caller_user_id:
            return True
        if _is_admin(caller_roles):
            return True
        return False

    # =================================================================
    # Validation
    # =================================================================

    def _validate_create(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ToolInvalidRequest("invalid_payload")
        kind = payload.get("kind")
        if kind not in _VALID_KINDS:
            raise ToolInvalidRequest("kind_invalid")
        name = _validate_name(payload.get("name"))
        description = _validate_description(payload.get("description"))
        scope = payload.get("scope")
        if scope not in _VALID_SCOPES:
            raise ToolInvalidRequest("scope_invalid")
        transport = _validate_transport(payload.get("transport"))
        args_schema = _validate_json_schema(
            payload.get("args_schema"), field_name="args_schema"
        )
        returns_schema = _validate_json_schema(
            payload.get("returns_schema"), field_name="returns_schema"
        )
        tags = _validate_tags(payload.get("tags"))
        project_id = _validate_project_id(payload.get("project_id"))
        skill_page_ref = payload.get("skill_page_ref")
        code_ref = payload.get("code_ref")

        if kind == "skill":
            if not isinstance(skill_page_ref, dict):
                raise ToolInvalidRequest("skill_page_ref_required")
            if skill_page_ref.get("kind") != "library_page":
                raise ToolInvalidRequest("skill_page_ref_kind_invalid")
        if kind == "code":
            if not isinstance(code_ref, dict):
                raise ToolInvalidRequest("code_ref_required")
            for key in ("repo_ref", "env_ref", "entry"):
                if code_ref.get(key) is None:
                    raise ToolInvalidRequest("code_ref_incomplete")

        result: dict[str, Any] = {
            "name": name,
            "description": description,
            "kind": kind,
            "scope": scope,
            "transport": transport,
            "args_schema": args_schema,
            "returns_schema": returns_schema,
            "tags": tags,
            "project_id": project_id,
        }
        if skill_page_ref is not None:
            result["skill_page_ref"] = skill_page_ref
        if code_ref is not None:
            result["code_ref"] = code_ref
        return result

    def _validate_patch(
        self,
        existing: ToolRecord,
        patch: dict[str, Any],
    ) -> dict[str, Any]:
        if not isinstance(patch, dict):
            raise ToolInvalidRequest("invalid_payload")
        updates: dict[str, Any] = {}

        # Reject server-managed fields outright.
        for forbidden in (
            "id",
            "tenant_id",
            "owner_user_id",
            "usage",
            "consecutive_error_count",
            "created_at",
            "updated_at",
            "deleted_at",
            "kind",
        ):
            if forbidden in patch:
                raise ToolInvalidRequest(f"{forbidden}_not_patchable")

        if "name" in patch:
            updates["name"] = _validate_name(patch["name"])
        if "description" in patch:
            updates["description"] = _validate_description(patch["description"])
        if "tags" in patch:
            updates["tags"] = _validate_tags(patch["tags"])
        if "project_id" in patch:
            updates["project_id"] = _validate_project_id(patch["project_id"])
        if "status" in patch:
            status = patch["status"]
            if status not in _CLIENT_PATCHABLE_STATUSES:
                raise ToolInvalidRequest("status_invalid")
            updates["status"] = status
        if "status_reason" in patch:
            reason = patch["status_reason"]
            if reason is None:
                updates["status_reason"] = None
            else:
                if not isinstance(reason, str):
                    raise ToolInvalidRequest("status_reason_invalid")
                if len(reason) > _STATUS_REASON_MAX:
                    raise ToolInvalidRequest("status_reason_too_long")
                updates["status_reason"] = reason
        if "scope" in patch:
            new_scope = patch["scope"]
            if new_scope not in _VALID_SCOPES:
                raise ToolInvalidRequest("scope_invalid")
            # Scope-widen requires admin review (tools-prd §4.4); a non-
            # admin patch path can ONLY shrink. The route layer surfaces
            # the actor's roles; here we only enforce when the new scope
            # widens past the existing one.
            if _is_scope_widen(existing.scope, new_scope):
                raise ToolConflict("scope_widen_requires_review")
            updates["scope"] = new_scope
        if "transport" in patch:
            updates["transport"] = _validate_transport(patch["transport"])
        if "args_schema" in patch:
            if existing.kind != "code":
                raise ToolInvalidRequest("args_schema_only_patchable_for_code")
            updates["args_schema"] = _validate_json_schema(
                patch["args_schema"], field_name="args_schema"
            )
        if "returns_schema" in patch:
            if existing.kind != "code":
                raise ToolInvalidRequest("returns_schema_only_patchable_for_code")
            updates["returns_schema"] = _validate_json_schema(
                patch["returns_schema"], field_name="returns_schema"
            )

        if not updates:
            raise ToolInvalidRequest("empty_patch")
        return updates


# ---------------------------------------------------------------------------
# Module-private helpers
# ---------------------------------------------------------------------------


def _is_admin(caller_roles: Iterable[str]) -> bool:
    return any(role in _ADMIN_ROLES for role in caller_roles)


def _validate_name(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ToolInvalidRequest("name_required")
    cleaned = value.strip()
    if len(cleaned) > _NAME_MAX:
        raise ToolInvalidRequest("name_too_long")
    return cleaned


def _validate_description(value: Any) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ToolInvalidRequest("description_invalid")
    if len(value) > _DESCRIPTION_MAX:
        raise ToolInvalidRequest("description_too_long")
    return value


def _validate_tags(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ToolInvalidRequest("tags_invalid")
    if len(value) > _TAG_COUNT_MAX:
        raise ToolInvalidRequest("tags_too_many")
    cleaned: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str):
            raise ToolInvalidRequest("tag_invalid_entry")
        tag = item.strip()
        if not tag:
            continue
        if len(tag) > _TAG_MAX_LEN:
            raise ToolInvalidRequest("tag_too_long")
        if tag in seen:
            continue
        seen.add(tag)
        cleaned.append(tag)
    return cleaned


def _validate_project_id(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ToolInvalidRequest("project_id_invalid")
    return value.strip()


def _validate_transport(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ToolInvalidRequest("transport_invalid")
    kind = value.get("kind")
    if kind not in _VALID_TRANSPORT_KINDS:
        raise ToolInvalidRequest("transport_kind_invalid")
    return value


def _validate_json_schema(value: Any, *, field_name: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ToolInvalidRequest(f"{field_name}_invalid")
    return value


_SCOPE_WIDTH: dict[str, int] = {"read": 1, "write": 2, "both": 3}


def _is_scope_widen(old: str, new: str) -> bool:
    return _SCOPE_WIDTH.get(new, 0) > _SCOPE_WIDTH.get(old, 0)


_STATUS_ONLY_PATCH_FIELDS: frozenset[str] = frozenset({"status", "status_reason"})


def _action_for_patch(updates: dict[str, Any]) -> str:
    """Map a validated patch dict to the canonical audit action.

    A status-flip emits ``tool.disabled`` / ``tool.enabled`` (the SIEM
    filter keys off these without parsing ``context.changed_fields``);
    anything broader is a plain ``tool.updated``.
    """
    if not updates.get("status"):
        return "tool.updated"
    # Status-only patch (with the optional reason and the server-managed
    # counter reset). Exclude server-managed fields when measuring the
    # caller-visible "what changed" set.
    server_managed = {"updated_at", "consecutive_error_count"}
    caller_fields = {k for k in updates.keys() if k not in server_managed}
    if caller_fields.issubset(_STATUS_ONLY_PATCH_FIELDS):
        if updates["status"] == "disabled":
            return "tool.disabled"
        if updates["status"] == "enabled":
            return "tool.enabled"
    return "tool.updated"


def _projection_from_rows(rows: list[ToolInvocationRecord]) -> dict[str, Any]:
    if not rows:
        return {
            "calls": 0,
            "p50_latency_ms": None,
            "success_rate": None,
            "last_used_at": None,
        }
    latencies = [r.latency_ms for r in rows if r.latency_ms is not None]
    p50 = float(median(latencies)) if latencies else None
    ok_count = sum(1 for r in rows if r.status == "ok")
    success_rate = ok_count / len(rows)
    last_used = max(r.started_at for r in rows)
    return {
        "calls": len(rows),
        "p50_latency_ms": p50,
        "success_rate": success_rate,
        "last_used_at": last_used.isoformat(),
    }


def _truncate(value: str, max_len: int) -> str:
    if value is None:
        return ""
    if len(value) <= max_len:
        return value
    # 1-char ellipsis budget so the result is exactly max_len wide.
    return value[: max_len - 1] + "…"


def _json_preview(args: dict[str, Any]) -> str:
    """Best-effort one-line summary; falls back to repr on non-JSON shapes."""
    try:
        import json

        return json.dumps(args, default=str, sort_keys=True)
    except (TypeError, ValueError):  # pragma: no cover — defensive
        return repr(args)


def _dump_for_audit(record: ToolRecord) -> dict[str, Any]:
    """Audit ``after_state`` shape. Schemas + transport blobs are kept
    intact (they're configuration, not PII); status / scope / name carry
    the compliance signal."""

    return {
        "id": record.id,
        "tenant_id": record.tenant_id,
        "name": record.name,
        "description": record.description,
        "kind": record.kind,
        "scope": record.scope,
        "status": record.status,
        "status_reason": record.status_reason,
        "owner_user_id": record.owner_user_id,
        "project_id": record.project_id,
        "tags": list(record.tags),
        "transport_kind": record.transport.get("kind"),
        "created_at": record.created_at.isoformat(),
        "updated_at": record.updated_at.isoformat(),
        "deleted_at": (record.deleted_at.isoformat() if record.deleted_at else None),
    }


__all__ = [
    "ERROR_THRESHOLD_DEFAULT",
    "ToolConflict",
    "ToolForbidden",
    "ToolInvalidRequest",
    "ToolNotFound",
    "ToolNotImplemented",
    "ToolsService",
]
