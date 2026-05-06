"""User profile + preferences sidecar (PR 4.1 — Settings → "You" group).

Two per-user sidecars to ``users``:

* ``user_profiles`` — queryable presentation columns (title, timezone,
  locale, working_hours, avatar_url) the admin members directory and the
  working-hours-aware notification senders care about.
* ``user_preferences`` — opinion-only JSONB blob (theme/accent/density/
  reduce-motion, shortcut overrides, notification matrix). Validated at
  the route layer; the store treats it as opaque JSON.

Why a sidecar store and not extending ``users``: the identity table is
SCIM-reconciled and identity-critical. Presentation knobs evolve faster
than identity columns and shouldn't churn the hot table. Both rows
cascade on user delete (PK FK ``users.user_id``).

Both adapters mirror the existing identity-store / mfa-store pattern:
in-memory for tests + dev, Postgres for production. Methods accept an
optional ``conn`` so the route layer can wrap the row write + audit
append in one transaction (matches the SCIM / MCP / skill stores).
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------


class _MeContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, validate_assignment=True)


class UserProfileRecord(_MeContract):
    """Per-user profile sidecar — queryable columns only.

    Validation (timezone IANA-set membership, locale BCP-47 shape,
    working-hours start<end, days range) lives in the route layer's
    request shape — the store treats every value as opaque text/JSON.
    """

    user_id: str
    org_id: str
    title: str | None = None
    timezone: str | None = None
    locale: str | None = None
    working_hours: dict[str, Any] | None = None
    avatar_url: str | None = None
    # PR 8.2 — short free-text bio surfaced in the Settings → Profile card
    # and in the workspace member directory. NULL means "no bio set".
    bio: str | None = None
    updated_at: datetime = Field(default_factory=_now)


class UserPreferencesRecord(_MeContract):
    """Per-user opinion blob — appearance / shortcuts / notifications.

    The shape is enforced at the route layer so validation lives next to
    the wire schema rather than the storage abstraction. Adding a new
    top-level key (e.g. ``composer``) is one Pydantic field on the route
    and zero lines here.
    """

    user_id: str
    org_id: str
    preferences: dict[str, Any] = Field(default_factory=dict)
    updated_at: datetime = Field(default_factory=_now)


# ---------------------------------------------------------------------------
# Store contract
# ---------------------------------------------------------------------------


class MeStore(Protocol):
    """Adapter contract — every adapter implements both halves."""

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Yield a transactional scope. In-memory adapter is a no-op."""
        ...  # pragma: no cover

    # Profile -----------------------------------------------------------
    def get_profile(self, *, org_id: str, user_id: str) -> UserProfileRecord | None: ...

    def upsert_profile(
        self, record: UserProfileRecord, *, conn: Any | None = None
    ) -> UserProfileRecord: ...

    # Preferences -------------------------------------------------------
    def get_preferences(
        self, *, org_id: str, user_id: str
    ) -> UserPreferencesRecord | None: ...

    def upsert_preferences(
        self, record: UserPreferencesRecord, *, conn: Any | None = None
    ) -> UserPreferencesRecord: ...


# ---------------------------------------------------------------------------
# In-memory adapter
# ---------------------------------------------------------------------------


@dataclass
class InMemoryMeStore:
    """Dict-backed adapter for tests and dev. Mirrors Postgres semantics."""

    profiles: dict[tuple[str, str], UserProfileRecord] = field(default_factory=dict)
    preferences: dict[tuple[str, str], UserPreferencesRecord] = field(
        default_factory=dict
    )

    @contextmanager
    def transaction(self) -> Iterator[None]:
        yield None

    # Profile -----------------------------------------------------------
    def get_profile(self, *, org_id: str, user_id: str) -> UserProfileRecord | None:
        return self.profiles.get((org_id, user_id))

    def upsert_profile(
        self, record: UserProfileRecord, *, conn: Any | None = None
    ) -> UserProfileRecord:
        del conn
        updated = record.model_copy(update={"updated_at": _now()})
        self.profiles[(record.org_id, record.user_id)] = updated
        return updated

    # Preferences -------------------------------------------------------
    def get_preferences(
        self, *, org_id: str, user_id: str
    ) -> UserPreferencesRecord | None:
        return self.preferences.get((org_id, user_id))

    def upsert_preferences(
        self, record: UserPreferencesRecord, *, conn: Any | None = None
    ) -> UserPreferencesRecord:
        del conn
        updated = record.model_copy(update={"updated_at": _now()})
        self.preferences[(record.org_id, record.user_id)] = updated
        return updated


# ---------------------------------------------------------------------------
# Postgres adapter
# ---------------------------------------------------------------------------


class PostgresMeStore:
    """Postgres-backed adapter for ``user_profiles`` + ``user_preferences``."""

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

    # Profile -----------------------------------------------------------
    def get_profile(self, *, org_id: str, user_id: str) -> UserProfileRecord | None:
        with self._cursor(None) as cur:
            cur.execute(
                """
                SELECT user_id, org_id, title, timezone, locale,
                       working_hours, avatar_url, bio, updated_at
                FROM user_profiles
                WHERE org_id = %s AND user_id = %s
                """,
                (org_id, user_id),
            )
            row = cur.fetchone()
        if row is None:
            return None
        return UserProfileRecord.model_validate(
            {**dict(row), "working_hours": _coerce_json(row["working_hours"])}
        )

    def upsert_profile(
        self, record: UserProfileRecord, *, conn: Any | None = None
    ) -> UserProfileRecord:
        updated = record.model_copy(update={"updated_at": _now()})
        with self._cursor(conn) as cur:
            cur.execute(
                """
                INSERT INTO user_profiles (
                    user_id, org_id, title, timezone, locale,
                    working_hours, avatar_url, bio, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (user_id) DO UPDATE SET
                    title = EXCLUDED.title,
                    timezone = EXCLUDED.timezone,
                    locale = EXCLUDED.locale,
                    working_hours = EXCLUDED.working_hours,
                    avatar_url = EXCLUDED.avatar_url,
                    bio = EXCLUDED.bio,
                    updated_at = EXCLUDED.updated_at
                """,
                (
                    updated.user_id,
                    updated.org_id,
                    updated.title,
                    updated.timezone,
                    updated.locale,
                    json.dumps(updated.working_hours)
                    if updated.working_hours is not None
                    else None,
                    updated.avatar_url,
                    updated.bio,
                    updated.updated_at,
                ),
            )
        return updated

    # Preferences -------------------------------------------------------
    def get_preferences(
        self, *, org_id: str, user_id: str
    ) -> UserPreferencesRecord | None:
        with self._cursor(None) as cur:
            cur.execute(
                """
                SELECT user_id, org_id, preferences, updated_at
                FROM user_preferences
                WHERE org_id = %s AND user_id = %s
                """,
                (org_id, user_id),
            )
            row = cur.fetchone()
        if row is None:
            return None
        return UserPreferencesRecord.model_validate(
            {**dict(row), "preferences": _coerce_json(row["preferences"]) or {}}
        )

    def upsert_preferences(
        self, record: UserPreferencesRecord, *, conn: Any | None = None
    ) -> UserPreferencesRecord:
        updated = record.model_copy(update={"updated_at": _now()})
        with self._cursor(conn) as cur:
            cur.execute(
                """
                INSERT INTO user_preferences (
                    user_id, org_id, preferences, updated_at
                ) VALUES (%s, %s, %s, %s)
                ON CONFLICT (user_id) DO UPDATE SET
                    preferences = EXCLUDED.preferences,
                    updated_at = EXCLUDED.updated_at
                """,
                (
                    updated.user_id,
                    updated.org_id,
                    json.dumps(updated.preferences),
                    updated.updated_at,
                ),
            )
        return updated


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _coerce_json(value: Any) -> Any:
    """psycopg returns JSONB as native objects; tolerate strings + bytes too."""

    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, (bytes, bytearray)):
        return json.loads(bytes(value).decode("utf-8"))
    if isinstance(value, str):
        return json.loads(value)
    return value


__all__ = [
    "InMemoryMeStore",
    "MeStore",
    "PostgresMeStore",
    "UserPreferencesRecord",
    "UserProfileRecord",
]
