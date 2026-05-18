"""Storage adapter for the Phase 12 Settings module.

Two storage targets sit behind one ``SettingsStore`` Protocol:

* User namespaces ride inside the existing ``user_preferences`` JSONB
  blob (migration 0018). The store reads + writes a single top-level
  key inside ``user_preferences.preferences`` and deep-merges into the
  rest of the blob so other top-level keys (e.g. ``home.*``) are
  preserved untouched. This is the same merge shape
  ``backend_app.home.last_visit`` uses for ``home.last_visit_iso``.

* Tenant namespaces ride in the new ``tenant_settings`` table
  (migration 0033). One row per (tenant_id, namespace).

Adapters: ``InMemorySettingsStore`` (dict-backed for tests/dev) and
``PostgresSettingsStore`` (production). Same shape as
``backend_app.identity.me_store``.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Final, Protocol


# ---------------------------------------------------------------------------
# Constants — single source of truth for the namespaces this PR ships.
# ---------------------------------------------------------------------------


USER_NAMESPACES: Final[frozenset[str]] = frozenset({"notifications", "home"})
"""Allowed user-scoped namespaces. ``home`` is read-only via this store
(Phase 2 + P9-A2 own the writes); it's listed here so callers can fetch
it without the store rejecting the namespace as unknown."""

TENANT_NAMESPACES: Final[frozenset[str]] = frozenset(
    {"notifications", "security.webhooks"}
)
"""Allowed tenant-scoped namespaces."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NamespaceRecord:
    """Result of a get/patch operation — JSONB blob + audit metadata.

    ``updated_by_user_id`` is null for user namespaces (the owner is
    implicit in the row key); it's populated for tenant namespaces.
    """

    namespace: str
    settings: dict[str, Any]
    updated_at: datetime
    updated_by_user_id: str | None = None


# ---------------------------------------------------------------------------
# Store contract
# ---------------------------------------------------------------------------


class SettingsStore(Protocol):
    """Adapter contract. Production injects the Postgres adapter."""

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Yield a transactional scope. In-memory adapter is a no-op."""
        ...  # pragma: no cover

    # User-namespaced ---------------------------------------------------
    def get_user_namespace(
        self,
        *,
        org_id: str,
        user_id: str,
        namespace: str,
    ) -> NamespaceRecord | None: ...

    def patch_user_namespace(
        self,
        *,
        org_id: str,
        user_id: str,
        namespace: str,
        patch: dict[str, Any],
        conn: Any | None = None,
    ) -> NamespaceRecord: ...

    # Tenant-namespaced -------------------------------------------------
    def get_tenant_namespace(
        self,
        *,
        tenant_id: str,
        namespace: str,
    ) -> NamespaceRecord | None: ...

    def patch_tenant_namespace(
        self,
        *,
        tenant_id: str,
        namespace: str,
        patch: dict[str, Any],
        actor_user_id: str,
        conn: Any | None = None,
    ) -> NamespaceRecord: ...


# ---------------------------------------------------------------------------
# In-memory adapter
# ---------------------------------------------------------------------------


@dataclass
class InMemorySettingsStore:
    """Dict-backed adapter for tests and dev. Mirrors Postgres semantics."""

    # Mirrors the on-disk ``user_preferences.preferences`` JSONB blob.
    # Key: (org_id, user_id) -> {namespace_key: {...}, ...}. Stays a
    # single dict so a PATCH against ``notifications`` deep-merges
    # without clobbering ``home.*`` or future top-level keys.
    user_preferences: dict[tuple[str, str], dict[str, Any]] = field(
        default_factory=dict
    )
    _user_updated: dict[tuple[str, str], datetime] = field(default_factory=dict)

    tenant_settings: dict[tuple[str, str], NamespaceRecord] = field(
        default_factory=dict
    )

    @contextmanager
    def transaction(self) -> Iterator[None]:
        yield None

    # User-namespaced ---------------------------------------------------
    def get_user_namespace(
        self,
        *,
        org_id: str,
        user_id: str,
        namespace: str,
    ) -> NamespaceRecord | None:
        blob = self.user_preferences.get((org_id, user_id))
        if blob is None:
            return None
        value = blob.get(namespace)
        if not isinstance(value, dict):
            return None
        return NamespaceRecord(
            namespace=namespace,
            settings=dict(value),
            updated_at=self._user_updated.get((org_id, user_id), _now()),
        )

    def patch_user_namespace(
        self,
        *,
        org_id: str,
        user_id: str,
        namespace: str,
        patch: dict[str, Any],
        conn: Any | None = None,
    ) -> NamespaceRecord:
        del conn
        base = dict(self.user_preferences.get((org_id, user_id), {}) or {})
        existing = base.get(namespace)
        existing_dict = dict(existing) if isinstance(existing, dict) else {}
        merged_namespace = _deep_merge(existing_dict, patch)
        base[namespace] = merged_namespace
        self.user_preferences[(org_id, user_id)] = base
        updated_at = _now()
        self._user_updated[(org_id, user_id)] = updated_at
        return NamespaceRecord(
            namespace=namespace,
            settings=dict(merged_namespace),
            updated_at=updated_at,
        )

    # Tenant-namespaced -------------------------------------------------
    def get_tenant_namespace(
        self,
        *,
        tenant_id: str,
        namespace: str,
    ) -> NamespaceRecord | None:
        record = self.tenant_settings.get((tenant_id, namespace))
        if record is None:
            return None
        return NamespaceRecord(
            namespace=record.namespace,
            settings=dict(record.settings),
            updated_at=record.updated_at,
            updated_by_user_id=record.updated_by_user_id,
        )

    def patch_tenant_namespace(
        self,
        *,
        tenant_id: str,
        namespace: str,
        patch: dict[str, Any],
        actor_user_id: str,
        conn: Any | None = None,
    ) -> NamespaceRecord:
        del conn
        existing = self.tenant_settings.get((tenant_id, namespace))
        existing_dict = existing.settings if existing is not None else {}
        merged = _deep_merge(existing_dict, patch)
        record = NamespaceRecord(
            namespace=namespace,
            settings=merged,
            updated_at=_now(),
            updated_by_user_id=actor_user_id,
        )
        self.tenant_settings[(tenant_id, namespace)] = record
        return record


# ---------------------------------------------------------------------------
# Postgres adapter
# ---------------------------------------------------------------------------


class PostgresSettingsStore:
    """Postgres-backed adapter.

    User namespaces ride in ``user_preferences`` (migration 0018);
    tenant namespaces in ``tenant_settings`` (migration 0033).
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

    # User-namespaced ---------------------------------------------------
    def get_user_namespace(
        self,
        *,
        org_id: str,
        user_id: str,
        namespace: str,
    ) -> NamespaceRecord | None:
        with self._cursor(None) as cur:
            cur.execute(
                """
                SELECT preferences, updated_at
                FROM user_preferences
                WHERE org_id = %s AND user_id = %s
                """,
                (org_id, user_id),
            )
            row = cur.fetchone()
        if row is None:
            return None
        blob = _coerce_json(row["preferences"]) or {}
        value = blob.get(namespace) if isinstance(blob, dict) else None
        if not isinstance(value, dict):
            return None
        return NamespaceRecord(
            namespace=namespace,
            settings=dict(value),
            updated_at=row["updated_at"],
        )

    def patch_user_namespace(
        self,
        *,
        org_id: str,
        user_id: str,
        namespace: str,
        patch: dict[str, Any],
        conn: Any | None = None,
    ) -> NamespaceRecord:
        updated_at = _now()
        with self._cursor(conn) as cur:
            cur.execute(
                """
                SELECT preferences
                FROM user_preferences
                WHERE org_id = %s AND user_id = %s
                FOR UPDATE
                """,
                (org_id, user_id),
            )
            row = cur.fetchone()
            blob: dict[str, Any] = {}
            if row is not None:
                stored = _coerce_json(row["preferences"]) or {}
                if isinstance(stored, dict):
                    blob = dict(stored)
            existing = blob.get(namespace)
            existing_dict = dict(existing) if isinstance(existing, dict) else {}
            merged = _deep_merge(existing_dict, patch)
            blob[namespace] = merged
            cur.execute(
                """
                INSERT INTO user_preferences (
                    user_id, org_id, preferences, updated_at
                ) VALUES (%s, %s, %s, %s)
                ON CONFLICT (user_id) DO UPDATE SET
                    preferences = EXCLUDED.preferences,
                    updated_at = EXCLUDED.updated_at
                """,
                (user_id, org_id, json.dumps(blob), updated_at),
            )
        return NamespaceRecord(
            namespace=namespace,
            settings=merged,
            updated_at=updated_at,
        )

    # Tenant-namespaced -------------------------------------------------
    def get_tenant_namespace(
        self,
        *,
        tenant_id: str,
        namespace: str,
    ) -> NamespaceRecord | None:
        with self._cursor(None) as cur:
            cur.execute(
                """
                SELECT settings, updated_at, updated_by_user_id
                FROM tenant_settings
                WHERE tenant_id = %s AND namespace = %s
                """,
                (tenant_id, namespace),
            )
            row = cur.fetchone()
        if row is None:
            return None
        return NamespaceRecord(
            namespace=namespace,
            settings=_coerce_json(row["settings"]) or {},
            updated_at=row["updated_at"],
            updated_by_user_id=row["updated_by_user_id"],
        )

    def patch_tenant_namespace(
        self,
        *,
        tenant_id: str,
        namespace: str,
        patch: dict[str, Any],
        actor_user_id: str,
        conn: Any | None = None,
    ) -> NamespaceRecord:
        updated_at = _now()
        with self._cursor(conn) as cur:
            cur.execute(
                """
                SELECT settings
                FROM tenant_settings
                WHERE tenant_id = %s AND namespace = %s
                FOR UPDATE
                """,
                (tenant_id, namespace),
            )
            row = cur.fetchone()
            existing: dict[str, Any] = {}
            if row is not None:
                stored = _coerce_json(row["settings"]) or {}
                if isinstance(stored, dict):
                    existing = stored
            merged = _deep_merge(existing, patch)
            cur.execute(
                """
                INSERT INTO tenant_settings (
                    tenant_id, namespace, settings, updated_at, updated_by_user_id
                ) VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (tenant_id, namespace) DO UPDATE SET
                    settings = EXCLUDED.settings,
                    updated_at = EXCLUDED.updated_at,
                    updated_by_user_id = EXCLUDED.updated_by_user_id
                """,
                (
                    tenant_id,
                    namespace,
                    json.dumps(merged),
                    updated_at,
                    actor_user_id,
                ),
            )
        return NamespaceRecord(
            namespace=namespace,
            settings=merged,
            updated_at=updated_at,
            updated_by_user_id=actor_user_id,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """RFC 7396-flavoured merge — overlay wins; nested dicts recurse;
    non-dict on either side replaces wholesale.

    Same shape as ``backend_app.routes.me_preferences._deep_merge``.
    Reused here so the merge semantics across the two settings surfaces
    stay aligned.
    """

    if not isinstance(base, dict):
        return overlay  # type: ignore[unreachable]
    out: dict[str, Any] = dict(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def _coerce_json(value: Any) -> Any:
    """psycopg returns JSONB as native; tolerate strings + bytes too."""

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
    "InMemorySettingsStore",
    "NamespaceRecord",
    "PostgresSettingsStore",
    "SettingsStore",
    "TENANT_NAMESPACES",
    "USER_NAMESPACES",
]
