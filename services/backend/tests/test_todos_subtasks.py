"""Subtask cascade + nesting-rejection tests for the Todos destination.

Scope: implementation-plan §11.2 (Subtasks — one level of nesting).

Coverage:

* **Cascade-delete**: deleting a parent removes every child (DB-level
  ``ON DELETE CASCADE`` on the ``parent_id`` self-FK).
* **Nesting rejection**: creating a subtask whose proposed parent already
  has ``parent_id IS NOT NULL`` returns 400 (one-level rule).
* **Project inheritance**: subtasks inherit their parent's ``project_id``
  on create — server enforces, ignores caller-supplied value if it
  disagrees.
* **Parent.done hint**: when all children are done, the parent's
  computed-done hint is true.
* **sort_index_within_parent**: persisted and ordered ascending under
  each parent.

P3-A1 dependency
----------------

P3-A1 owns the canonical ``todos`` schema, the routes module, and the
service-layer model. None of that has merged at the time these tests
were written. To prevent these tests from blocking on the merge, the
fixture stands up a minimal schema in **sqlite** that mirrors the
shape declared in implementation-plan §11.1 + §11.2 — specifically:

* ``todos.id`` PK
* ``todos.tenant_id`` NOT NULL  (tenant-first)
* ``todos.project_id`` nullable
* ``todos.parent_id`` self-FK with ``ON DELETE CASCADE``
* ``todos.series_id`` nullable, ``todos.due_date`` nullable
* ``UNIQUE(series_id, due_date)`` partial unique (the materializer
  idempotency invariant)
* ``todo_series`` placeholder table (referenced by series_id; we don't
  test its columns here, only that the FK exists conceptually)

When P3-A1 merges, the local stub SCHEMA constant should be replaced by
``open(... 'services/backend/src/backend_app/todos/schema.sql').read()``
— the assertions in this file are written against the **invariants**
P3-A1 must preserve, not against incidental column names. Search for
the marker ``# P3-A1-MERGE-POINT`` to locate the swap.

The service-layer assertions (nesting rejection, project inheritance,
parent.done hint) use a tiny ``_TodoSubtaskService`` stand-in that
encodes the rules implementation-plan §11.2 calls out. When P3-A1's
real service ships, replace the stand-in with ``from
backend_app.todos.service import TodoSubtaskService`` — the test
assertions stay valid.
"""

from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass

import pytest


# ---------------------------------------------------------------------------
# Schema stub — REPLACE WHEN P3-A1 LANDS (search: P3-A1-MERGE-POINT)
# ---------------------------------------------------------------------------


# P3-A1-MERGE-POINT: when P3-A1's schema.sql lands at
# ``services/backend/src/backend_app/todos/schema.sql``, replace this
# constant with ``Path(... / "schema.sql").read_text()`` and drop the
# sqlite ``_TodoTestSchema`` shim. The invariants we test must survive
# that swap unchanged.
_TODOS_SCHEMA_SQLITE = """
CREATE TABLE todo_series (
    id          TEXT PRIMARY KEY,
    tenant_id   TEXT NOT NULL,
    rule        TEXT NOT NULL,
    spec        TEXT NOT NULL,
    deleted_at  TEXT
);

CREATE TABLE todos (
    id              TEXT PRIMARY KEY,
    tenant_id       TEXT NOT NULL,
    owner_user_id   TEXT NOT NULL,
    text            TEXT NOT NULL,
    done            INTEGER NOT NULL DEFAULT 0,
    project_id      TEXT,
    parent_id       TEXT,
    series_id       TEXT,
    due_date        TEXT,
    sort_index_within_parent REAL,
    created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (parent_id) REFERENCES todos(id) ON DELETE CASCADE,
    FOREIGN KEY (series_id) REFERENCES todo_series(id) ON DELETE SET NULL
);

-- Materializer idempotency invariant — partial unique on (series_id, due_date)
-- when both are present. SQLite emits this as a regular unique index over
-- non-NULL pairs (multiple NULLs are allowed by SQLite UNIQUE semantics, which
-- matches Postgres's NULLS NOT DISTINCT default for our use).
CREATE UNIQUE INDEX todos_series_due_uniq
    ON todos(series_id, due_date)
    WHERE series_id IS NOT NULL AND due_date IS NOT NULL;

CREATE INDEX todos_parent_idx
    ON todos(tenant_id, parent_id)
    WHERE parent_id IS NOT NULL;
"""


# ---------------------------------------------------------------------------
# Service-layer stand-in — REPLACE WHEN P3-A1 LANDS
# ---------------------------------------------------------------------------


class TodoSubtaskRejection(ValueError):
    """Server-layer 400 raised when a subtask creation violates a rule."""


@dataclass(frozen=True)
class _NewSubtask:
    text: str
    parent_id: str
    project_id: str | None = None  # caller hint; service overrides with parent's


class _TodoSubtaskService:
    """Stand-in for the P3-A1 service-layer subtask rules.

    Encodes implementation-plan §11.2 invariants. The real
    ``TodoSubtaskService`` from P3-A1 must preserve every assertion
    in this file — swap the import when it lands.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def create_subtask(
        self,
        *,
        tenant_id: str,
        owner_user_id: str,
        spec: _NewSubtask,
    ) -> str:
        parent = self._fetch_parent(tenant_id=tenant_id, todo_id=spec.parent_id)
        # Rule 1: parent must exist + belong to tenant.
        if parent is None:
            raise TodoSubtaskRejection("parent_not_found")
        # Rule 2: no nested subtasks. One level only.
        if parent["parent_id"] is not None:
            raise TodoSubtaskRejection("nested_subtask_forbidden")
        # Rule 3: subtask inherits parent's project_id; caller's
        # project_id is ignored if it disagrees.
        project_id = parent["project_id"]
        todo_id = f"todo_{uuid.uuid4().hex[:12]}"
        self._conn.execute(
            "INSERT INTO todos (id, tenant_id, owner_user_id, text, "
            "project_id, parent_id) VALUES (?, ?, ?, ?, ?, ?)",
            (
                todo_id,
                tenant_id,
                owner_user_id,
                spec.text,
                project_id,
                spec.parent_id,
            ),
        )
        return todo_id

    def parent_done_hint(self, *, tenant_id: str, parent_id: str) -> bool:
        cursor = self._conn.execute(
            "SELECT COUNT(*) AS total, "
            "  SUM(CASE WHEN done = 1 THEN 1 ELSE 0 END) AS done_count "
            "FROM todos WHERE tenant_id = ? AND parent_id = ?",
            (tenant_id, parent_id),
        )
        row = cursor.fetchone()
        total = row["total"] or 0
        done_count = row["done_count"] or 0
        # Hint is true only when there are children AND all are done.
        return total > 0 and total == done_count

    def _fetch_parent(self, *, tenant_id: str, todo_id: str) -> sqlite3.Row | None:
        cursor = self._conn.execute(
            "SELECT id, parent_id, project_id FROM todos "
            "WHERE id = ? AND tenant_id = ?",
            (todo_id, tenant_id),
        )
        return cursor.fetchone()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def conn() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    # SQLite needs the PRAGMA to actually enforce FKs (incl. ON DELETE CASCADE).
    connection.execute("PRAGMA foreign_keys = ON;")
    connection.executescript(_TODOS_SCHEMA_SQLITE)
    try:
        yield connection
    finally:
        connection.close()


def _insert_top_level_todo(
    conn: sqlite3.Connection,
    *,
    tenant_id: str = "ten_acme",
    owner_user_id: str = "usr_sarah",
    project_id: str | None = None,
    text: str = "ship it",
    done: bool = False,
    todo_id: str | None = None,
) -> str:
    todo_id = todo_id or f"todo_{uuid.uuid4().hex[:12]}"
    conn.execute(
        "INSERT INTO todos (id, tenant_id, owner_user_id, text, done, "
        "project_id, parent_id) VALUES (?, ?, ?, ?, ?, ?, NULL)",
        (todo_id, tenant_id, owner_user_id, text, int(done), project_id),
    )
    return todo_id


# ---------------------------------------------------------------------------
# DB-level cascade tests
# ---------------------------------------------------------------------------


class TestCascadeDelete:
    def test_deleting_parent_deletes_all_children(
        self, conn: sqlite3.Connection
    ) -> None:
        parent_id = _insert_top_level_todo(conn, text="parent")
        service = _TodoSubtaskService(conn)
        service.create_subtask(
            tenant_id="ten_acme",
            owner_user_id="usr_sarah",
            spec=_NewSubtask(text="child 1", parent_id=parent_id),
        )
        service.create_subtask(
            tenant_id="ten_acme",
            owner_user_id="usr_sarah",
            spec=_NewSubtask(text="child 2", parent_id=parent_id),
        )
        # Sanity — children present.
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM todos WHERE parent_id = ?",
                (parent_id,),
            ).fetchone()[0]
            == 2
        )

        # Hard delete the parent. ON DELETE CASCADE must purge children.
        conn.execute("DELETE FROM todos WHERE id = ?", (parent_id,))
        remaining = conn.execute("SELECT id FROM todos").fetchall()
        assert remaining == []

    def test_deleting_parent_does_not_affect_sibling_top_level(
        self, conn: sqlite3.Connection
    ) -> None:
        parent_id = _insert_top_level_todo(conn, text="parent")
        unrelated_id = _insert_top_level_todo(conn, text="unrelated")
        service = _TodoSubtaskService(conn)
        service.create_subtask(
            tenant_id="ten_acme",
            owner_user_id="usr_sarah",
            spec=_NewSubtask(text="child", parent_id=parent_id),
        )
        conn.execute("DELETE FROM todos WHERE id = ?", (parent_id,))
        rows = conn.execute("SELECT id FROM todos").fetchall()
        assert [row["id"] for row in rows] == [unrelated_id]


# ---------------------------------------------------------------------------
# Service-level rule tests
# ---------------------------------------------------------------------------


class TestNestedSubtaskRejected:
    def test_cannot_create_subtask_under_a_subtask(
        self, conn: sqlite3.Connection
    ) -> None:
        parent_id = _insert_top_level_todo(conn, text="parent")
        service = _TodoSubtaskService(conn)
        child_id = service.create_subtask(
            tenant_id="ten_acme",
            owner_user_id="usr_sarah",
            spec=_NewSubtask(text="child", parent_id=parent_id),
        )
        with pytest.raises(TodoSubtaskRejection) as excinfo:
            service.create_subtask(
                tenant_id="ten_acme",
                owner_user_id="usr_sarah",
                spec=_NewSubtask(text="grandchild", parent_id=child_id),
            )
        assert "nested_subtask_forbidden" in str(excinfo.value)

    def test_unknown_parent_rejected(self, conn: sqlite3.Connection) -> None:
        service = _TodoSubtaskService(conn)
        with pytest.raises(TodoSubtaskRejection) as excinfo:
            service.create_subtask(
                tenant_id="ten_acme",
                owner_user_id="usr_sarah",
                spec=_NewSubtask(text="orphan", parent_id="todo_missing"),
            )
        assert "parent_not_found" in str(excinfo.value)


class TestProjectInheritance:
    def test_subtask_inherits_parent_project_id(self, conn: sqlite3.Connection) -> None:
        parent_id = _insert_top_level_todo(conn, project_id="proj_alpha", text="parent")
        service = _TodoSubtaskService(conn)
        child_id = service.create_subtask(
            tenant_id="ten_acme",
            owner_user_id="usr_sarah",
            spec=_NewSubtask(text="child", parent_id=parent_id),
        )
        row = conn.execute(
            "SELECT project_id FROM todos WHERE id = ?", (child_id,)
        ).fetchone()
        assert row["project_id"] == "proj_alpha"

    def test_caller_supplied_project_id_is_overridden(
        self, conn: sqlite3.Connection
    ) -> None:
        parent_id = _insert_top_level_todo(conn, project_id="proj_alpha", text="parent")
        service = _TodoSubtaskService(conn)
        child_id = service.create_subtask(
            tenant_id="ten_acme",
            owner_user_id="usr_sarah",
            spec=_NewSubtask(
                text="child",
                parent_id=parent_id,
                project_id="proj_BOGUS",  # ignored — server enforces parent's
            ),
        )
        row = conn.execute(
            "SELECT project_id FROM todos WHERE id = ?", (child_id,)
        ).fetchone()
        assert row["project_id"] == "proj_alpha"

    def test_null_parent_project_inherits_null(self, conn: sqlite3.Connection) -> None:
        parent_id = _insert_top_level_todo(conn, project_id=None, text="parent")
        service = _TodoSubtaskService(conn)
        child_id = service.create_subtask(
            tenant_id="ten_acme",
            owner_user_id="usr_sarah",
            spec=_NewSubtask(text="child", parent_id=parent_id),
        )
        row = conn.execute(
            "SELECT project_id FROM todos WHERE id = ?", (child_id,)
        ).fetchone()
        assert row["project_id"] is None


class TestParentDoneHint:
    def test_hint_false_when_no_subtasks(self, conn: sqlite3.Connection) -> None:
        parent_id = _insert_top_level_todo(conn, text="parent")
        service = _TodoSubtaskService(conn)
        assert (
            service.parent_done_hint(tenant_id="ten_acme", parent_id=parent_id) is False
        )

    def test_hint_false_when_some_subtasks_open(self, conn: sqlite3.Connection) -> None:
        parent_id = _insert_top_level_todo(conn, text="parent")
        service = _TodoSubtaskService(conn)
        child_a = service.create_subtask(
            tenant_id="ten_acme",
            owner_user_id="usr_sarah",
            spec=_NewSubtask(text="a", parent_id=parent_id),
        )
        service.create_subtask(
            tenant_id="ten_acme",
            owner_user_id="usr_sarah",
            spec=_NewSubtask(text="b", parent_id=parent_id),
        )
        # Mark only one done.
        conn.execute("UPDATE todos SET done = 1 WHERE id = ?", (child_a,))
        assert (
            service.parent_done_hint(tenant_id="ten_acme", parent_id=parent_id) is False
        )

    def test_hint_true_when_all_subtasks_done(self, conn: sqlite3.Connection) -> None:
        parent_id = _insert_top_level_todo(conn, text="parent")
        service = _TodoSubtaskService(conn)
        for label in ("a", "b", "c"):
            child = service.create_subtask(
                tenant_id="ten_acme",
                owner_user_id="usr_sarah",
                spec=_NewSubtask(text=label, parent_id=parent_id),
            )
            conn.execute("UPDATE todos SET done = 1 WHERE id = ?", (child,))
        assert (
            service.parent_done_hint(tenant_id="ten_acme", parent_id=parent_id) is True
        )


class TestSortIndexWithinParent:
    def test_sort_index_persisted_and_ordered(self, conn: sqlite3.Connection) -> None:
        parent_id = _insert_top_level_todo(conn, text="parent")
        # Three subtasks with explicit sort_index_within_parent values that
        # are out of insertion order. The query must return them in
        # ``ORDER BY sort_index_within_parent ASC`` order regardless.
        for label, sort_idx in (("c", 30.0), ("a", 10.0), ("b", 20.0)):
            conn.execute(
                "INSERT INTO todos (id, tenant_id, owner_user_id, text, "
                "parent_id, sort_index_within_parent) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    f"todo_{label}",
                    "ten_acme",
                    "usr_sarah",
                    label,
                    parent_id,
                    sort_idx,
                ),
            )
        rows = conn.execute(
            "SELECT text, sort_index_within_parent FROM todos "
            "WHERE parent_id = ? "
            "ORDER BY sort_index_within_parent ASC",
            (parent_id,),
        ).fetchall()
        assert [row["text"] for row in rows] == ["a", "b", "c"]
        assert [row["sort_index_within_parent"] for row in rows] == [
            10.0,
            20.0,
            30.0,
        ]

    def test_float_indices_allow_insert_between(self, conn: sqlite3.Connection) -> None:
        """The float pattern lets the frontend insert between neighbours
        without re-indexing the whole list (PRD §10 optimistic drag)."""
        parent_id = _insert_top_level_todo(conn, text="parent")
        for label, sort_idx in (("a", 10.0), ("c", 30.0)):
            conn.execute(
                "INSERT INTO todos (id, tenant_id, owner_user_id, text, "
                "parent_id, sort_index_within_parent) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    f"todo_{label}",
                    "ten_acme",
                    "usr_sarah",
                    label,
                    parent_id,
                    sort_idx,
                ),
            )
        # Drop "b" between a and c using the midpoint.
        conn.execute(
            "INSERT INTO todos (id, tenant_id, owner_user_id, text, "
            "parent_id, sort_index_within_parent) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                "todo_b",
                "ten_acme",
                "usr_sarah",
                "b",
                parent_id,
                (10.0 + 30.0) / 2,
            ),
        )
        rows = conn.execute(
            "SELECT text FROM todos WHERE parent_id = ? "
            "ORDER BY sort_index_within_parent ASC",
            (parent_id,),
        ).fetchall()
        assert [row["text"] for row in rows] == ["a", "b", "c"]


# ---------------------------------------------------------------------------
# Idempotency invariant — covers the materializer's UNIQUE contract too
# ---------------------------------------------------------------------------


class TestSeriesDueUnique:
    """Asserts the ``UNIQUE(series_id, due_date)`` invariant the materializer relies on.

    This is the DB-level wall the recurrence materializer leans on for
    idempotency: re-running it twice on the same series creates only
    one row per due date. The corresponding worker-level assertion
    lives in
    ``services/ai-backend/tests/unit/runtime_worker/test_todo_recurrence_materializer.py::test_idempotency_second_tick_skips_already_materialized``.
    """

    def test_duplicate_series_due_rejected(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            "INSERT INTO todo_series (id, tenant_id, rule, spec) VALUES (?, ?, ?, ?)",
            ("series_a", "ten_acme", "every_weekday", ""),
        )
        conn.execute(
            "INSERT INTO todos (id, tenant_id, owner_user_id, text, "
            "series_id, due_date) VALUES (?, ?, ?, ?, ?, ?)",
            (
                "todo_1",
                "ten_acme",
                "usr_sarah",
                "first",
                "series_a",
                "2026-05-18",
            ),
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO todos (id, tenant_id, owner_user_id, text, "
                "series_id, due_date) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    "todo_2",
                    "ten_acme",
                    "usr_sarah",
                    "duplicate",
                    "series_a",
                    "2026-05-18",
                ),
            )

    def test_distinct_due_dates_allowed(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            "INSERT INTO todo_series (id, tenant_id, rule, spec) VALUES (?, ?, ?, ?)",
            ("series_a", "ten_acme", "every_weekday", ""),
        )
        for due in ("2026-05-18", "2026-05-19", "2026-05-20"):
            conn.execute(
                "INSERT INTO todos (id, tenant_id, owner_user_id, text, "
                "series_id, due_date) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    f"todo_{due}",
                    "ten_acme",
                    "usr_sarah",
                    "ok",
                    "series_a",
                    due,
                ),
            )
        count = conn.execute(
            "SELECT COUNT(*) FROM todos WHERE series_id = ?", ("series_a",)
        ).fetchone()[0]
        assert count == 3
