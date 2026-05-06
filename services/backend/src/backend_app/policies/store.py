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


__all__ = [
    "InMemoryToolUsePolicyStore",
    "ToolUsePolicyKind",
    "ToolUsePolicyMode",
    "ToolUsePolicyRow",
    "ToolUsePolicyStore",
]
