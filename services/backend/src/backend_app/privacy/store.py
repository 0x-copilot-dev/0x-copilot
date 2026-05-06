"""Privacy & data settings store (PR B2 / 8.0.3f).

Backs ``services/backend/migrations/0022_privacy_settings.sql``.

Five toggles + one knob, two scopes:

* ``training_opt_out`` — provider do-not-train signal on every call.
* ``region`` — data residency (us-east-1 / eu-west-1 / ap-northeast-1).
  ``None`` means "use deployment default".
* ``retention_days`` — auto-delete after N days; ``None`` means
  "retain forever". The retention sweeper bakes this into the
  existing C8 retention pipeline.
* ``share_metadata`` — opt-in to admin-visible thread metadata
  (title, model, approvals); message content stays private regardless.
* ``memory_enabled`` — toggle Atlas's cross-chat memory feature.

Workspace default and per-user override live in the same table:

* ``user_id IS NULL``     → workspace default
* ``user_id IS NOT NULL`` → user override (wins for that user)

The unique index on ``(org_id, COALESCE(user_id, '__org__'))`` enforces
"exactly one row per scope" — the upsert path relies on that.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator


def _now() -> datetime:
    return datetime.now(timezone.utc)


class DataResidencyRegion(StrEnum):
    """Allowed data-residency regions."""

    US_EAST_1 = "us-east-1"
    EU_WEST_1 = "eu-west-1"
    AP_NORTHEAST_1 = "ap-northeast-1"


class PrivacySettingsRow(BaseModel):
    """One ``privacy_settings`` row.

    A workspace-default row sets ``user_id=None``; a per-user override
    sets ``user_id`` to the target user. ``retention_days`` must be a
    positive integer when set (the column-level CHECK constraint
    enforces it; the Pydantic validator mirrors that so the in-memory
    adapter rejects the same shape).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    org_id: str
    user_id: str | None = None
    training_opt_out: bool = True
    region: DataResidencyRegion | None = None
    retention_days: int | None = None
    share_metadata: bool = True
    memory_enabled: bool = True
    updated_at: datetime = Field(default_factory=_now)
    updated_by_user_id: str | None = None

    @model_validator(mode="after")
    def _validate_retention(self) -> "PrivacySettingsRow":
        if self.retention_days is not None and self.retention_days <= 0:
            raise ValueError("retention_days must be a positive integer when set")
        return self


class PrivacySettingsStore(Protocol):
    """Adapter contract — every adapter implements every method."""

    @contextmanager
    def transaction(self) -> Iterator[None]: ...  # pragma: no cover

    def get_for_scope(
        self,
        *,
        org_id: str,
        user_id: str | None,
    ) -> PrivacySettingsRow | None:
        """Return the single row for a scope (workspace OR user) or
        ``None`` if absent. The route layer hydrates deployment
        defaults under absence so the FE always sees a complete shape.
        """

    def upsert(
        self,
        row: PrivacySettingsRow,
        *,
        conn: Any | None = None,
    ) -> PrivacySettingsRow:
        """Insert or update the scope's row, returning the saved value."""

    def delete_for_scope(
        self,
        *,
        org_id: str,
        user_id: str | None,
        conn: Any | None = None,
    ) -> bool:
        """Drop the scope's row. Returns True if a row was removed.

        Used to revert a user override back to the workspace default
        without leaving a sentinel row behind.
        """


@dataclass
class InMemoryPrivacySettingsStore:
    """Dict-backed adapter for tests + dev. Mirrors postgres semantics.

    Keyed on ``(org_id, scope_key)`` where ``scope_key`` is the user_id
    for user overrides and ``"__org__"`` for the workspace default —
    same coalescence the unique index uses.
    """

    rows: dict[tuple[str, str], PrivacySettingsRow] = field(default_factory=dict)

    @contextmanager
    def transaction(self) -> Iterator[None]:
        yield None

    def get_for_scope(
        self,
        *,
        org_id: str,
        user_id: str | None,
    ) -> PrivacySettingsRow | None:
        scope = user_id or _ORG_SCOPE
        return self.rows.get((org_id, scope))

    def upsert(
        self,
        row: PrivacySettingsRow,
        *,
        conn: Any | None = None,
    ) -> PrivacySettingsRow:
        del conn
        scope = row.user_id or _ORG_SCOPE
        saved = row.model_copy(update={"updated_at": _now()})
        self.rows[(row.org_id, scope)] = saved
        return saved

    def delete_for_scope(
        self,
        *,
        org_id: str,
        user_id: str | None,
        conn: Any | None = None,
    ) -> bool:
        del conn
        scope = user_id or _ORG_SCOPE
        return self.rows.pop((org_id, scope), None) is not None


_ORG_SCOPE = "__org__"


__all__ = [
    "DataResidencyRegion",
    "InMemoryPrivacySettingsStore",
    "PrivacySettingsRow",
    "PrivacySettingsStore",
]
