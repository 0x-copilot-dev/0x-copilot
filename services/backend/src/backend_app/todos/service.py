"""Todos service — ACL + audit + subtask invariants.

The route layer in ``routes.py`` is presentation-only; every
business-logic decision lives here so the in-memory ``InMemoryTodosStore``
and the postgres adapter share one set of authorization checks.

Authorization rules (cross-audit §1.3, binding):

* Owner-only writes.
* Reads: owner OR (project_id member when project_id is set) OR tenant
  admin (audited via the same audit row stream, ``action=todo.read_admin``).
* Non-readers see 404, not 403 (existence not leaked).

Subtask invariants (impl-plan §11.2):

* One level of nesting only — parent referenced by ``parent_id`` must
  itself have ``parent_id IS NULL``.
* Subtask ``project_id`` is inherited from the parent on create.
* Delete cascades to children (one level).

Audit rows are append-only; bulk actions stamp a shared
``correlation_id`` on every row so SIEM can reconstruct the bulk.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Iterable

from pydantic import BaseModel, ConfigDict, Field

from backend_app.identity.store import IdentityStore
from backend_app.projects.acl import (
    ProjectMembershipPort,
    _NoMemberProjectAdapter,
)
from backend_app.todos.store import (
    TodoAuditRecord,
    TodoRecord,
    TodoSeriesRecord,
    TodosStore,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


# Roles with tenant-admin read access. Treated as untrusted unless the
# verified ``ScopedIdentity.roles`` tuple set them — the route layer
# passes through what the auth middleware verified.
_ADMIN_ROLES = frozenset({"admin", "owner"})


# ---------------------------------------------------------------------------
# Recurrence rule evaluator (backend-local mirror of the worker's evaluator)
# ---------------------------------------------------------------------------
#
# Lives here because service boundaries forbid importing
# ``runtime_worker`` (ai-backend) from ``backend``. The grammar is
# identical to ``services/ai-backend/src/runtime_worker/jobs/
# todo_recurrence_materializer.py::RecurrenceRuleEvaluator`` — three rule
# kinds, RFC 5545 subset for ``rrule`` (FREQ DAILY/WEEKLY, BYDAY,
# INTERVAL). Both implementations are kept narrow + tested so they
# cannot drift unobserved.


class RecurrenceRuleError(ValueError):
    """Malformed / unsupported recurrence rule + spec pair."""


class _Weekday:
    """RFC 5545 two-letter codes → ``date.weekday()`` (Mon=0)."""

    CODES: dict[str, int] = {
        "MO": 0,
        "TU": 1,
        "WE": 2,
        "TH": 3,
        "FR": 4,
        "SA": 5,
        "SU": 6,
    }


class RecurrenceRuleEvaluator:
    """Compute the next due date strictly after ``previous_due``.

    Stateless; same grammar as the worker's evaluator (see module
    comment above). Errors raise ``RecurrenceRuleError`` so the caller
    can skip the offending series without aborting the whole pass.
    """

    RULE_RRULE = "rrule"
    RULE_EVERY_N_DAYS = "every_N_days"
    RULE_EVERY_WEEKDAY = "every_weekday"
    SUPPORTED_RULES: tuple[str, ...] = (
        RULE_RRULE,
        RULE_EVERY_N_DAYS,
        RULE_EVERY_WEEKDAY,
    )

    _MAX_SCAN_DAYS = 366

    def next_due(self, *, rule: str, spec: str, previous_due: date) -> date:
        if rule not in self.SUPPORTED_RULES:
            raise RecurrenceRuleError(f"unsupported rule: {rule}")
        if rule == self.RULE_EVERY_WEEKDAY:
            return self._next_weekday(previous_due)
        if rule == self.RULE_EVERY_N_DAYS:
            return self._next_every_n_days(spec=spec, previous_due=previous_due)
        return self._next_rrule(spec=spec, previous_due=previous_due)

    def _next_weekday(self, previous_due: date) -> date:
        candidate = previous_due + timedelta(days=1)
        while candidate.weekday() >= 5:
            candidate = candidate + timedelta(days=1)
        return candidate

    def _next_every_n_days(self, *, spec: str, previous_due: date) -> date:
        prefix = "every_N_days:"
        if not spec.startswith(prefix):
            raise RecurrenceRuleError(
                f"every_N_days spec must start with '{prefix}', got '{spec}'"
            )
        tail = spec[len(prefix) :].strip()
        try:
            n = int(tail)
        except ValueError as exc:
            raise RecurrenceRuleError(
                f"every_N_days spec must be a positive int, got '{tail}'"
            ) from exc
        if n <= 0:
            raise RecurrenceRuleError(f"every_N_days spec must be > 0, got {n}")
        return previous_due + timedelta(days=n)

    def _next_rrule(self, *, spec: str, previous_due: date) -> date:
        parsed = self._parse_rrule_spec(spec)
        freq = parsed["FREQ"]
        interval = parsed["INTERVAL"]
        byday = parsed["BYDAY"]
        if freq == "DAILY":
            return previous_due + timedelta(days=interval)
        if freq != "WEEKLY":
            raise RecurrenceRuleError(
                f"rrule FREQ must be DAILY or WEEKLY, got '{freq}'"
            )
        if not byday:
            return previous_due + timedelta(days=7 * interval)
        prev_week_start = previous_due - timedelta(days=previous_due.weekday())
        target_weekdays = {_Weekday.CODES[code] for code in byday}
        for offset in range(1, self._MAX_SCAN_DAYS + 1):
            candidate = previous_due + timedelta(days=offset)
            if candidate.weekday() not in target_weekdays:
                continue
            candidate_week_start = candidate - timedelta(days=candidate.weekday())
            week_delta_days = (candidate_week_start - prev_week_start).days
            if week_delta_days % (7 * interval) != 0:
                continue
            return candidate
        raise RecurrenceRuleError(
            f"no rrule match within {self._MAX_SCAN_DAYS} days for spec '{spec}'"
        )

    def _parse_rrule_spec(self, spec: str) -> dict[str, object]:
        parts = [piece for piece in spec.split(";") if piece]
        out: dict[str, object] = {"INTERVAL": 1, "BYDAY": ()}
        for piece in parts:
            if "=" not in piece:
                raise RecurrenceRuleError(
                    f"malformed rrule fragment '{piece}' in spec '{spec}'"
                )
            key, value = piece.split("=", 1)
            key = key.strip().upper()
            value = value.strip().upper()
            if key == "FREQ":
                out["FREQ"] = value
            elif key == "INTERVAL":
                try:
                    interval = int(value)
                except ValueError as exc:
                    raise RecurrenceRuleError(
                        f"rrule INTERVAL must be int, got '{value}'"
                    ) from exc
                if interval <= 0:
                    raise RecurrenceRuleError(
                        f"rrule INTERVAL must be > 0, got {interval}"
                    )
                out["INTERVAL"] = interval
            elif key == "BYDAY":
                codes = tuple(code.strip() for code in value.split(",") if code.strip())
                for code in codes:
                    if code not in _Weekday.CODES:
                        raise RecurrenceRuleError(
                            f"unknown BYDAY code '{code}' in spec '{spec}'"
                        )
                out["BYDAY"] = codes
            else:
                raise RecurrenceRuleError(
                    f"unsupported rrule key '{key}' in spec '{spec}'"
                )
        if "FREQ" not in out:
            raise RecurrenceRuleError(f"rrule spec missing required FREQ: '{spec}'")
        return out


class MaterializeOutcome(BaseModel):
    """Counts returned by :meth:`TodosService.materialize_due_series`."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    materialized: int = Field(default=0, ge=0)
    skipped_duplicates: int = Field(default=0, ge=0)
    series_processed: int = Field(default=0, ge=0)


class TodoNotFound(Exception):
    """Raised when a todo doesn't exist OR the caller has no read rights.

    The 404-not-403 rule (cross-audit §1.3) collapses both branches to
    one exception so the route layer cannot accidentally distinguish
    them — the response is always 404.
    """


class TodoForbidden(Exception):
    """Raised when the caller can READ but cannot WRITE.

    Only used internally to gate the write path after read access has
    already been established (so 404-not-403 still applies for the
    read-doesn't-exist case). The route layer translates this to 403.
    """


class TodoInvalidRequest(Exception):
    """Raised for client-fixable invariant violations (400)."""


class TodosService:
    """Composition of the todos store + identity store with ACL + audit."""

    def __init__(
        self,
        *,
        store: TodosStore,
        identity_store: IdentityStore,
        project_membership: "ProjectMembershipPort | None" = None,
    ) -> None:
        self._store = store
        self._identity = identity_store
        # Project membership lookup is injected so the in-memory tests
        # don't need the (not-yet-shipped) Projects destination. Defaults
        # to a no-member adapter — owner-only behaviour until the
        # Projects destination lands and registers a real adapter.
        self._project_membership = project_membership or _NoMemberProjectAdapter()

    # -- reads ---------------------------------------------------------

    def get_todo(
        self,
        *,
        tenant_id: str,
        caller_user_id: str,
        caller_roles: Iterable[str],
        todo_id: str,
    ) -> TodoRecord:
        """Authorise + return a single todo.

        Raises :class:`TodoNotFound` if the caller can't see it (which
        is what 404-not-403 demands; the route never distinguishes
        "not found" from "not authorised").
        """

        record = self._store.get_todo(tenant_id=tenant_id, todo_id=todo_id)
        if record is None:
            raise TodoNotFound(todo_id)
        if not self._can_read(record, caller_user_id, caller_roles):
            raise TodoNotFound(todo_id)
        return record

    def list_todos(
        self,
        *,
        tenant_id: str,
        caller_user_id: str,
        caller_roles: Iterable[str],
        statuses: tuple[str, ...] | None = None,
        project_ids: tuple[str | None, ...] | None = None,
        parent_id: str | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> tuple[tuple[TodoRecord, ...], str | None]:
        """List the caller's readable todos.

        Composition of three buckets:

        1. Todos owned by the caller.
        2. Todos in projects the caller is a member of (read-only).
        3. (Admin only) every todo in the tenant.

        The store is tenant-scoped, then this method narrows by
        ownership / membership. Admin reads bypass the narrowing.
        """

        admin = any(role in _ADMIN_ROLES for role in caller_roles)
        if admin:
            page, next_cursor = self._store.list_todos(
                tenant_id=tenant_id,
                owner_user_id=None,
                statuses=statuses,
                project_ids=project_ids,
                parent_id=parent_id,
                cursor=cursor,
                limit=limit,
            )
            return page, next_cursor

        # Non-admin: union of owner + project-member rows. The in-memory
        # adapter applies the filters per-bucket; the postgres adapter
        # implements the same predicate with one query (see
        # schema.sql comment block on `todos_owner_or_project_idx`).
        owner_page, next_cursor = self._store.list_todos(
            tenant_id=tenant_id,
            owner_user_id=caller_user_id,
            statuses=statuses,
            project_ids=project_ids,
            parent_id=parent_id,
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
        project_page, _project_next = self._store.list_project_member_todos(
            tenant_id=tenant_id,
            project_ids=member_projects,
            cursor=None,
            limit=limit,
        )
        seen = {r.id for r in owner_page}
        merged = list(owner_page) + [r for r in project_page if r.id not in seen]
        # If owner-only filters narrowed away project rows the caller
        # would otherwise see, the page is best-effort. Postgres adapter
        # tightens this.
        if statuses is not None:
            merged = [r for r in merged if r.status in statuses]
        if project_ids is not None:
            merged = [r for r in merged if r.project_id in project_ids]
        if parent_id is not None:
            merged = [r for r in merged if r.parent_id == parent_id]
        return tuple(merged[:limit]), next_cursor

    # -- writes --------------------------------------------------------

    def create_todo(
        self,
        *,
        tenant_id: str,
        caller_user_id: str,
        text: str,
        priority: str = "med",
        due: str | None = None,
        project_id: str | None = None,
        parent_id: str | None = None,
        recurrence: dict | None = None,
        source: dict | None = None,
    ) -> TodoRecord:
        """Create a todo.

        Public callers cannot set ``source`` to anything other than
        ``{"kind": "user"}`` — the chat/agent provenance variants are
        reserved for the internal extraction-accept pipeline (PRD §4.3).
        """

        if source is None:
            source = {"kind": "user"}
        if source.get("kind") != "user":
            raise TodoInvalidRequest("non_user_source_forbidden")
        if not text or not text.strip():
            raise TodoInvalidRequest("text_required")
        if priority not in {"low", "med", "high"}:
            raise TodoInvalidRequest("invalid_priority")

        resolved_project_id = project_id
        resolved_parent_id: str | None = None
        if parent_id is not None:
            parent = self._store.get_todo(tenant_id=tenant_id, todo_id=parent_id)
            if parent is None or parent.owner_user_id != caller_user_id:
                # Only the parent's owner can attach a subtask — keeps
                # the ACL story consistent with the rest of the
                # destination (owner-only writes).
                raise TodoInvalidRequest("parent_not_found_or_not_owned")
            if parent.parent_id is not None:
                # One level of nesting only.
                raise TodoInvalidRequest("nested_subtask_forbidden")
            resolved_parent_id = parent.id
            # Subtask inherits the parent's project (server enforced
            # per impl-plan §11.2).
            resolved_project_id = parent.project_id

        series_id: str | None = None
        recurrence_blob: dict | None = None
        if recurrence is not None:
            if resolved_parent_id is not None:
                # Recurring subtasks are out of scope (impl-plan §11.1).
                raise TodoInvalidRequest("recurring_subtask_forbidden")
            series = self._store.insert_series(
                TodoSeriesRecord(
                    tenant_id=tenant_id,
                    owner_user_id=caller_user_id,
                    rule=str(recurrence.get("rule", "")),
                    spec=str(recurrence.get("spec", "")),
                )
            )
            series_id = series.id
            recurrence_blob = {
                "rule": recurrence.get("rule"),
                "spec": recurrence.get("spec"),
                # ``next_materialize_at`` is the materialiser's
                # responsibility; we leave the wire field absent on the
                # first row (the materialiser stamps it on the next
                # concrete instance).
                "next_materialize_at": recurrence.get("next_materialize_at"),
                "series_id": series_id,
            }

        record = TodoRecord(
            tenant_id=tenant_id,
            owner_user_id=caller_user_id,
            project_id=resolved_project_id,
            text=text.strip(),
            status="open",
            priority=priority,
            due=due,
            source=source,
            parent_id=resolved_parent_id,
            recurrence=recurrence_blob,
            series_id=series_id,
        )
        with self._store.transaction():
            stored = self._store.insert_todo(record)
            self._store.append_audit(
                TodoAuditRecord(
                    tenant_id=tenant_id,
                    actor_user_id=caller_user_id,
                    action="todo.create",
                    target_id=stored.id,
                    after_state=stored.model_dump(mode="json"),
                )
            )
        return stored

    def update_todo(
        self,
        *,
        tenant_id: str,
        caller_user_id: str,
        caller_roles: Iterable[str],
        todo_id: str,
        patch: dict,
    ) -> TodoRecord:
        """Patch fields on a todo. Owner-only.

        ``patch`` is a dict of changed fields; absent fields are
        untouched. ``status`` transitions stamp ``completed_at``
        (set on done, cleared on re-open).
        """

        existing = self._store.get_todo(tenant_id=tenant_id, todo_id=todo_id)
        # 404-not-403 on both "missing" and "no read rights" branches.
        if existing is None or not self._can_read(
            existing, caller_user_id, caller_roles
        ):
            raise TodoNotFound(todo_id)
        if existing.owner_user_id != caller_user_id:
            # Read access established (project member or admin) but
            # writes are owner-only. cross-audit §1.3.
            raise TodoForbidden(todo_id)

        updates: dict = {}
        action = "todo.update"
        for key in (
            "text",
            "priority",
            "due",
            "project_id",
            "sort_index_within_parent",
        ):
            if key in patch:
                updates[key] = patch[key]
        if "status" in patch:
            new_status = patch["status"]
            if new_status not in {"open", "done"}:
                raise TodoInvalidRequest("invalid_status")
            updates["status"] = new_status
            if new_status == "done" and existing.status != "done":
                updates["completed_at"] = _now()
                action = "todo.mark_done"
            elif new_status == "open" and existing.status == "done":
                updates["completed_at"] = None
                action = "todo.mark_undone"
        if "recurrence" in patch:
            # ``None`` clears, dict updates. ``series_id`` is preserved
            # so already-materialised instances remain linked.
            updates["recurrence"] = patch["recurrence"]
        if not updates:
            return existing
        updates["updated_at"] = _now()
        new_record = existing.model_copy(update=updates)

        before_state = existing.model_dump(mode="json")
        after_state = new_record.model_dump(mode="json")
        with self._store.transaction():
            stored = self._store.update_todo(new_record)
            self._store.append_audit(
                TodoAuditRecord(
                    tenant_id=tenant_id,
                    actor_user_id=caller_user_id,
                    action=action,
                    target_id=stored.id,
                    before_state=before_state,
                    after_state=after_state,
                )
            )
        return stored

    def delete_todo(
        self,
        *,
        tenant_id: str,
        caller_user_id: str,
        caller_roles: Iterable[str],
        todo_id: str,
    ) -> int:
        """Soft-delete a todo + cascade to one-level subtasks.

        Returns the number of rows deleted (parent + each child).
        """

        existing = self._store.get_todo(tenant_id=tenant_id, todo_id=todo_id)
        if existing is None or not self._can_read(
            existing, caller_user_id, caller_roles
        ):
            raise TodoNotFound(todo_id)
        if existing.owner_user_id != caller_user_id:
            raise TodoForbidden(todo_id)

        with self._store.transaction():
            deleted_ids = self._store.delete_todo(tenant_id=tenant_id, todo_id=todo_id)
            for target_id in deleted_ids:
                self._store.append_audit(
                    TodoAuditRecord(
                        tenant_id=tenant_id,
                        actor_user_id=caller_user_id,
                        action="todo.delete",
                        target_id=target_id,
                        before_state={"id": target_id},
                    )
                )
        return len(deleted_ids)

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
    ) -> int:
        """Apply ``action`` across multiple todos.

        Best-effort: ids the caller cannot write are silently skipped
        (the bulk shouldn't 404 if one row dropped out mid-flight). The
        return value counts only rows actually mutated; SIEM
        reconstruction uses the shared ``correlation_id`` stamped on
        every audit row written by this method.
        """

        if action not in {
            "mark_done",
            "mark_open",
            "delete",
            "set_priority",
            "set_project",
        }:
            raise TodoInvalidRequest("invalid_bulk_action")
        if not correlation_id or not correlation_id.strip():
            raise TodoInvalidRequest("correlation_id_required")
        payload = payload or {}

        affected = 0
        for todo_id in ids:
            record = self._store.get_todo(tenant_id=tenant_id, todo_id=todo_id)
            if record is None or record.owner_user_id != caller_user_id:
                continue
            if action == "delete":
                with self._store.transaction():
                    deleted_ids = self._store.delete_todo(
                        tenant_id=tenant_id, todo_id=todo_id
                    )
                    for target_id in deleted_ids:
                        self._store.append_audit(
                            TodoAuditRecord(
                                tenant_id=tenant_id,
                                actor_user_id=caller_user_id,
                                action="todo.delete",
                                target_id=target_id,
                                before_state={"id": target_id},
                                correlation_id=correlation_id,
                            )
                        )
                    affected += len(deleted_ids)
                continue
            patch: dict = {}
            audit_action = "todo.update"
            if action == "mark_done":
                patch["status"] = "done"
                patch["completed_at"] = _now()
                audit_action = "todo.mark_done"
            elif action == "mark_open":
                patch["status"] = "open"
                patch["completed_at"] = None
                audit_action = "todo.mark_undone"
            elif action == "set_priority":
                priority = payload.get("priority")
                if priority not in {"low", "med", "high"}:
                    raise TodoInvalidRequest("invalid_priority")
                patch["priority"] = priority
            elif action == "set_project":
                patch["project_id"] = payload.get("project_id")
            patch["updated_at"] = _now()
            before_state = record.model_dump(mode="json")
            new_record = record.model_copy(update=patch)
            with self._store.transaction():
                self._store.update_todo(new_record)
                self._store.append_audit(
                    TodoAuditRecord(
                        tenant_id=tenant_id,
                        actor_user_id=caller_user_id,
                        action=audit_action,
                        target_id=record.id,
                        before_state=before_state,
                        after_state=new_record.model_dump(mode="json"),
                        correlation_id=correlation_id,
                    )
                )
            affected += 1
        return affected

    # -- recurrence materialization -----------------------------------

    def materialize_due_series(
        self,
        *,
        now: datetime,
        rule_evaluator: RecurrenceRuleEvaluator | None = None,
    ) -> MaterializeOutcome:
        """Materialise the next concrete Todo for every due series.

        Called by the ai-backend ``todo_recurrence_materializer`` worker
        on a fixed tick. Idempotency lives at the storage layer: the
        partial UNIQUE index ``todo_series_dedup`` on
        ``(series_id, due) WHERE series_id IS NOT NULL`` (schema.sql)
        means a second call with the same clock cannot create a
        duplicate row — the service consults
        :meth:`TodosStore.find_todo_by_series_due` first and counts the
        duplicate against ``skipped_duplicates``.

        Tenant safety: each series row carries its own ``tenant_id`` and
        ``owner_user_id``; the inserted Todo + audit row inherit both,
        so no cross-tenant inserts are possible even though the call is
        system-level.

        Concurrent workers: the store's :meth:`TodosStore.claim_due_series`
        contract is ``FOR UPDATE SKIP LOCKED`` in production — two
        workers can run in parallel without re-fire because each claims
        a disjoint set of series rows.
        """

        evaluator = rule_evaluator or RecurrenceRuleEvaluator()
        materialized = 0
        skipped_duplicates = 0
        series_processed = 0

        for series in self._store.claim_due_series(now=now):
            series_processed += 1
            anchor_date = self._series_anchor_date(series)
            try:
                next_due = evaluator.next_due(
                    rule=series.rule,
                    spec=series.spec,
                    previous_due=anchor_date,
                )
            except RecurrenceRuleError:
                # Bad spec: skip + keep going so one malformed series
                # cannot stall every other tenant's recurrence.
                continue

            # Eligibility: ``next_due`` must be on or before ``now``'s
            # date in UTC. Future-dated series are left untouched.
            if next_due > now.astimezone(timezone.utc).date():
                continue

            due_iso = next_due.isoformat()

            # Idempotency: the partial UNIQUE index makes a second
            # insert with the same (series_id, due) impossible. We
            # consult the store first so we can count the dedup as a
            # ``skipped_duplicates`` rather than crash on the
            # constraint violation in postgres.
            existing = self._store.find_todo_by_series_due(
                tenant_id=series.tenant_id,
                series_id=series.id,
                due=due_iso,
            )
            if existing is not None:
                skipped_duplicates += 1
                # Still advance ``last_materialized_due`` so the next
                # tick computes from this anchor — otherwise we'd loop
                # on the same (already-materialised) date forever.
                self._store.update_series_last_materialized(
                    series_id=series.id,
                    last_materialized_due=self._anchor_datetime(next_due),
                )
                continue

            recurrence_blob = {
                "rule": series.rule,
                "spec": series.spec,
                "series_id": series.id,
            }
            record = TodoRecord(
                tenant_id=series.tenant_id,
                owner_user_id=series.owner_user_id,
                text=self._materialized_todo_text(series),
                status="open",
                priority="med",
                due=due_iso,
                source={"kind": "recurrence", "series_id": series.id},
                recurrence=recurrence_blob,
                series_id=series.id,
            )
            with self._store.transaction():
                stored = self._store.insert_todo(record)
                self._store.update_series_last_materialized(
                    series_id=series.id,
                    last_materialized_due=self._anchor_datetime(next_due),
                )
                self._store.append_audit(
                    TodoAuditRecord(
                        tenant_id=series.tenant_id,
                        actor_user_id=series.owner_user_id,
                        action="todo.materialize",
                        target_id=stored.id,
                        after_state=stored.model_dump(mode="json"),
                    )
                )
            materialized += 1

        return MaterializeOutcome(
            materialized=materialized,
            skipped_duplicates=skipped_duplicates,
            series_processed=series_processed,
        )

    @staticmethod
    def _series_anchor_date(series: TodoSeriesRecord) -> date:
        """Return the date the next-due computation starts from.

        ``last_materialized_due`` once any concrete row has fired;
        otherwise ``started_at`` (the series's seed date). Both are
        timezone-aware datetimes — we project to UTC date so the
        rule evaluator works in pure ``date`` space.
        """

        anchor_dt = series.last_materialized_due or series.started_at
        return anchor_dt.astimezone(timezone.utc).date()

    @staticmethod
    def _anchor_datetime(due: date) -> datetime:
        """Project a materialised ``date`` back to a UTC datetime anchor."""

        return datetime(due.year, due.month, due.day, tzinfo=timezone.utc)

    @staticmethod
    def _materialized_todo_text(series: TodoSeriesRecord) -> str:
        """Text for an auto-materialised recurring Todo.

        Series rows don't currently carry a parent-template text (the
        schema stores ``rule`` + ``spec`` only), so the materialised row
        uses a stable placeholder the surface can replace once the
        Phase-3.1 template-text follow-up lands. Keeping the text
        deterministic + free of PII matches the PRD §6 audit-redaction
        invariant.
        """

        return f"Recurring task ({series.rule})"

    # -- helpers -------------------------------------------------------

    def _can_read(
        self,
        record: TodoRecord,
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


__all__ = [
    "MaterializeOutcome",
    "RecurrenceRuleError",
    "RecurrenceRuleEvaluator",
    "TodoForbidden",
    "TodoInvalidRequest",
    "TodoNotFound",
    "TodosService",
]
