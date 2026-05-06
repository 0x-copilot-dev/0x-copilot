"""Tool-use policy store (PR B1 / 8.0.3d).

Backs the per-workspace + per-user `tool_use_policies` table introduced
by `services/backend/migrations/0021_tool_use_policies.sql`. Three policy
axes — ``read`` / ``write`` / ``destructive`` — each with one of four
modes — ``auto`` / ``ask`` / ``require`` / ``block``.

Workspace default and per-user override live in the same table:

* ``user_id IS NULL``    → workspace default
* ``user_id IS NOT NULL`` → user override (wins for that user)

The store is *not* the policy *evaluator*. Evaluation lives in the AI
backend's ``ToolPermissionChecker``; this store is the source of truth
the evaluator fetches once per run start.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ToolUsePolicyKind(StrEnum):
    """The three policy axes the FE selects between (per-row enum)."""

    READ = "read"
    WRITE = "write"
    DESTRUCTIVE = "destructive"


class ToolUsePolicyMode(StrEnum):
    """Allowed modes per axis."""

    AUTO = "auto"
    ASK = "ask"
    REQUIRE = "require"
    BLOCK = "block"


class ToolUsePolicyRow(BaseModel):
    """One ``tool_use_policies`` row.

    A workspace-default row sets ``user_id=None``; a per-user override
    row sets ``user_id`` to the target user. The unique index on
    ``(org_id, COALESCE(user_id, '__org__'), kind)`` enforces "exactly
    one row per (scope, kind)" — the upsert path relies on that.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    org_id: str
    user_id: str | None = None
    kind: ToolUsePolicyKind
    mode: ToolUsePolicyMode
    updated_at: datetime = Field(default_factory=_now)
    updated_by_user_id: str | None = None


class ToolUsePolicyStore(Protocol):
    """Adapter contract — every adapter implements every method."""

    @contextmanager
    def transaction(self) -> Iterator[None]: ...  # pragma: no cover

    def list_for_scope(
        self,
        *,
        org_id: str,
        user_id: str | None,
    ) -> tuple[ToolUsePolicyRow, ...]:
        """Return the policy rows for a single scope (workspace OR user).

        Workspace fetch passes ``user_id=None``; user-override fetch
        passes the target user. Returns the (possibly empty) tuple of
        rows — the route layer hydrates missing rows with deployment
        defaults so the FE always sees a complete shape.
        """

    def upsert(
        self,
        row: ToolUsePolicyRow,
        *,
        conn: Any | None = None,
    ) -> ToolUsePolicyRow:
        """Insert or update a policy row, returning the saved value."""

    def delete_for_scope(
        self,
        *,
        org_id: str,
        user_id: str | None,
        conn: Any | None = None,
    ) -> int:
        """Drop every row for a scope (used to revert a user-override
        block back to the workspace default). Returns the row count."""


@dataclass
class InMemoryToolUsePolicyStore:
    """Dict-backed adapter for tests + dev. Mirrors postgres semantics.

    Keyed on ``(org_id, scope_key, kind)`` where ``scope_key`` is
    ``user_id`` for user overrides and ``"__org__"`` for the workspace
    default — same coalescence the unique index uses.
    """

    rows: dict[tuple[str, str, ToolUsePolicyKind], ToolUsePolicyRow] = field(
        default_factory=dict
    )

    @contextmanager
    def transaction(self) -> Iterator[None]:
        yield None

    def list_for_scope(
        self,
        *,
        org_id: str,
        user_id: str | None,
    ) -> tuple[ToolUsePolicyRow, ...]:
        scope = user_id or _ORG_SCOPE
        return tuple(
            row
            for (row_org, row_scope, _kind), row in self.rows.items()
            if row_org == org_id and row_scope == scope
        )

    def upsert(
        self,
        row: ToolUsePolicyRow,
        *,
        conn: Any | None = None,
    ) -> ToolUsePolicyRow:
        del conn
        scope = row.user_id or _ORG_SCOPE
        saved = row.model_copy(update={"updated_at": _now()})
        self.rows[(row.org_id, scope, row.kind)] = saved
        return saved

    def delete_for_scope(
        self,
        *,
        org_id: str,
        user_id: str | None,
        conn: Any | None = None,
    ) -> int:
        del conn
        scope = user_id or _ORG_SCOPE
        keys = [key for key in self.rows if key[0] == org_id and key[1] == scope]
        for key in keys:
            del self.rows[key]
        return len(keys)


_ORG_SCOPE = "__org__"


# ---------------------------------------------------------------------------
# Postgres adapter
# ---------------------------------------------------------------------------


class PostgresToolUsePolicyStore:
    """PR 8.0.5 — postgres-backed adapter for ``tool_use_policies``.

    Mirrors the in-memory store's semantics verbatim. The unique
    index on ``(org_id, COALESCE(user_id, '__org__'), kind)`` (set
    in migration 0021) is what makes the ``ON CONFLICT DO UPDATE``
    work for both workspace-default and user-override rows without
    branching on user_id at the SQL layer.
    """

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    @contextmanager
    def transaction(self) -> Iterator[Any]:
        with self._pool.connection() as conn:
            with conn.transaction():
                yield conn

    @contextmanager
    def _cursor(self, conn: Any | None) -> Iterator[Any]:
        if conn is not None:
            with conn.cursor() as cur:
                yield cur
            return
        with self._pool.connection() as owned:
            with owned.cursor() as cur:
                yield cur

    def list_for_scope(
        self,
        *,
        org_id: str,
        user_id: str | None,
    ) -> tuple[ToolUsePolicyRow, ...]:
        scope = user_id or _ORG_SCOPE
        with self._cursor(None) as cur:
            cur.execute(
                """
                SELECT org_id, user_id, kind, mode, updated_at, updated_by_user_id
                FROM tool_use_policies
                WHERE org_id = %s AND COALESCE(user_id, '__org__') = %s
                """,
                (org_id, scope),
            )
            rows = cur.fetchall()
        return tuple(ToolUsePolicyRow.model_validate(dict(row)) for row in rows)

    def upsert(
        self,
        row: ToolUsePolicyRow,
        *,
        conn: Any | None = None,
    ) -> ToolUsePolicyRow:
        saved = row.model_copy(update={"updated_at": _now()})
        with self._cursor(conn) as cur:
            cur.execute(
                """
                INSERT INTO tool_use_policies (
                    org_id, user_id, kind, mode, updated_at, updated_by_user_id
                ) VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (org_id, COALESCE(user_id, '__org__'), kind) DO UPDATE SET
                    mode = EXCLUDED.mode,
                    updated_at = EXCLUDED.updated_at,
                    updated_by_user_id = EXCLUDED.updated_by_user_id
                """,
                (
                    saved.org_id,
                    saved.user_id,
                    saved.kind.value,
                    saved.mode.value,
                    saved.updated_at,
                    saved.updated_by_user_id,
                ),
            )
        return saved

    def delete_for_scope(
        self,
        *,
        org_id: str,
        user_id: str | None,
        conn: Any | None = None,
    ) -> int:
        scope = user_id or _ORG_SCOPE
        with self._cursor(conn) as cur:
            cur.execute(
                """
                DELETE FROM tool_use_policies
                WHERE org_id = %s AND COALESCE(user_id, '__org__') = %s
                """,
                (org_id, scope),
            )
            return cur.rowcount or 0


__all__ = [
    "InMemoryToolUsePolicyStore",
    "PostgresToolUsePolicyStore",
    "ToolUsePolicyKind",
    "ToolUsePolicyMode",
    "ToolUsePolicyRow",
    "ToolUsePolicyStore",
]
