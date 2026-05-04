"""SCIM-specific persistence (A7): tokens, external-id mappings, groups, members.

Mirrors :mod:`backend_app.identity.oidc_store` and
:mod:`backend_app.identity.saml_store` so the SCIM service composes the
same way OIDC + SAML do.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol

from backend_app.contracts import (
    ScimExternalIdRecord,
    ScimGroupMemberRecord,
    ScimGroupRecord,
    ScimTokenRecord,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


class ScimStore(Protocol):
    @contextmanager
    def transaction(self) -> Iterator[None]: ...  # pragma: no cover

    # Tokens ------------------------------------------------------------
    def create_token(
        self, record: ScimTokenRecord, *, conn: Any | None = None
    ) -> ScimTokenRecord: ...

    def get_token_by_hash(
        self, *, token_hash: str, conn: Any | None = None
    ) -> ScimTokenRecord | None: ...

    def list_tokens(
        self, *, org_id: str, provider_id: str
    ) -> tuple[ScimTokenRecord, ...]: ...

    def revoke_token(self, *, token_id: str, conn: Any | None = None) -> bool: ...

    def touch_token_last_used(
        self, *, token_id: str, conn: Any | None = None
    ) -> bool: ...

    # External-id mappings ---------------------------------------------
    def create_external_id(
        self, record: ScimExternalIdRecord, *, conn: Any | None = None
    ) -> ScimExternalIdRecord: ...

    def get_external_id(
        self, *, provider_id: str, external_id: str
    ) -> ScimExternalIdRecord | None: ...

    def list_external_ids_for_user(
        self, *, user_id: str
    ) -> tuple[ScimExternalIdRecord, ...]: ...

    # Groups ------------------------------------------------------------
    def create_group(
        self, record: ScimGroupRecord, *, conn: Any | None = None
    ) -> ScimGroupRecord: ...

    def update_group(
        self, record: ScimGroupRecord, *, conn: Any | None = None
    ) -> ScimGroupRecord: ...

    def get_group(self, *, org_id: str, group_id: str) -> ScimGroupRecord | None: ...

    def list_groups(self, *, org_id: str) -> tuple[ScimGroupRecord, ...]: ...

    def soft_delete_group(
        self, *, org_id: str, group_id: str, conn: Any | None = None
    ) -> bool: ...

    # Members -----------------------------------------------------------
    def add_member(
        self, record: ScimGroupMemberRecord, *, conn: Any | None = None
    ) -> ScimGroupMemberRecord: ...

    def remove_member(
        self,
        *,
        group_id: str,
        user_id: str,
        conn: Any | None = None,
    ) -> bool: ...

    def list_members(self, *, group_id: str) -> tuple[ScimGroupMemberRecord, ...]: ...

    def list_active_groups_for_user(
        self, *, user_id: str
    ) -> tuple[ScimGroupRecord, ...]: ...


# ---------------------------------------------------------------------------
# In-memory adapter
# ---------------------------------------------------------------------------


@dataclass
class InMemoryScimStore:
    tokens: dict[str, ScimTokenRecord] = field(default_factory=dict)
    external_ids: dict[str, ScimExternalIdRecord] = field(default_factory=dict)
    groups: dict[str, ScimGroupRecord] = field(default_factory=dict)
    memberships: dict[str, ScimGroupMemberRecord] = field(default_factory=dict)

    @contextmanager
    def transaction(self) -> Iterator[None]:
        yield None

    # Tokens ------------------------------------------------------------
    def create_token(
        self, record: ScimTokenRecord, *, conn: Any | None = None
    ) -> ScimTokenRecord:
        del conn
        for existing in self.tokens.values():
            if existing.token_hash == record.token_hash:
                raise ValueError("scim token hash collision")
        self.tokens[record.token_id] = record
        return record

    def get_token_by_hash(
        self, *, token_hash: str, conn: Any | None = None
    ) -> ScimTokenRecord | None:
        del conn
        for token in self.tokens.values():
            if token.token_hash == token_hash:
                return token
        return None

    def list_tokens(
        self, *, org_id: str, provider_id: str
    ) -> tuple[ScimTokenRecord, ...]:
        return tuple(
            sorted(
                (
                    token
                    for token in self.tokens.values()
                    if token.org_id == org_id and token.provider_id == provider_id
                ),
                key=lambda t: t.created_at,
            )
        )

    def revoke_token(self, *, token_id: str, conn: Any | None = None) -> bool:
        del conn
        existing = self.tokens.get(token_id)
        if existing is None or existing.revoked_at is not None:
            return False
        self.tokens[token_id] = existing.model_copy(update={"revoked_at": _now()})
        return True

    def touch_token_last_used(self, *, token_id: str, conn: Any | None = None) -> bool:
        del conn
        existing = self.tokens.get(token_id)
        if existing is None:
            return False
        self.tokens[token_id] = existing.model_copy(update={"last_used_at": _now()})
        return True

    # External-id mappings ---------------------------------------------
    def create_external_id(
        self, record: ScimExternalIdRecord, *, conn: Any | None = None
    ) -> ScimExternalIdRecord:
        del conn
        for existing in self.external_ids.values():
            if (
                existing.provider_id == record.provider_id
                and existing.external_id == record.external_id
            ):
                raise ValueError("external_id already mapped for this provider")
        self.external_ids[record.mapping_id] = record
        return record

    def get_external_id(
        self, *, provider_id: str, external_id: str
    ) -> ScimExternalIdRecord | None:
        for record in self.external_ids.values():
            if record.provider_id == provider_id and record.external_id == external_id:
                return record
        return None

    def list_external_ids_for_user(
        self, *, user_id: str
    ) -> tuple[ScimExternalIdRecord, ...]:
        return tuple(r for r in self.external_ids.values() if r.user_id == user_id)

    # Groups ------------------------------------------------------------
    def create_group(
        self, record: ScimGroupRecord, *, conn: Any | None = None
    ) -> ScimGroupRecord:
        del conn
        for existing in self.groups.values():
            if (
                existing.org_id == record.org_id
                and existing.display_name == record.display_name
                and existing.deleted_at is None
            ):
                raise ValueError("scim group display_name already taken in org")
        self.groups[record.group_id] = record
        return record

    def update_group(
        self, record: ScimGroupRecord, *, conn: Any | None = None
    ) -> ScimGroupRecord:
        del conn
        if record.group_id not in self.groups:
            raise ValueError("scim group not found")
        updated = record.model_copy(update={"updated_at": _now()})
        self.groups[record.group_id] = updated
        return updated

    def get_group(self, *, org_id: str, group_id: str) -> ScimGroupRecord | None:
        existing = self.groups.get(group_id)
        if existing is None or existing.org_id != org_id:
            return None
        if existing.deleted_at is not None:
            return None
        return existing

    def list_groups(self, *, org_id: str) -> tuple[ScimGroupRecord, ...]:
        return tuple(
            sorted(
                (
                    group
                    for group in self.groups.values()
                    if group.org_id == org_id and group.deleted_at is None
                ),
                key=lambda g: g.created_at,
            )
        )

    def soft_delete_group(
        self, *, org_id: str, group_id: str, conn: Any | None = None
    ) -> bool:
        del conn
        existing = self.groups.get(group_id)
        if (
            existing is None
            or existing.org_id != org_id
            or existing.deleted_at is not None
        ):
            return False
        self.groups[group_id] = existing.model_copy(
            update={"deleted_at": _now(), "updated_at": _now()}
        )
        return True

    # Members -----------------------------------------------------------
    def add_member(
        self, record: ScimGroupMemberRecord, *, conn: Any | None = None
    ) -> ScimGroupMemberRecord:
        del conn
        for existing in self.memberships.values():
            if (
                existing.group_id == record.group_id
                and existing.user_id == record.user_id
                and existing.removed_at is None
            ):
                # Idempotent — return the existing active row.
                return existing
        self.memberships[record.membership_id] = record
        return record

    def remove_member(
        self,
        *,
        group_id: str,
        user_id: str,
        conn: Any | None = None,
    ) -> bool:
        del conn
        for membership_id, existing in list(self.memberships.items()):
            if (
                existing.group_id == group_id
                and existing.user_id == user_id
                and existing.removed_at is None
            ):
                self.memberships[membership_id] = existing.model_copy(
                    update={"removed_at": _now()}
                )
                return True
        return False

    def list_members(self, *, group_id: str) -> tuple[ScimGroupMemberRecord, ...]:
        return tuple(
            m
            for m in self.memberships.values()
            if m.group_id == group_id and m.removed_at is None
        )

    def list_active_groups_for_user(
        self, *, user_id: str
    ) -> tuple[ScimGroupRecord, ...]:
        active_group_ids = {
            m.group_id
            for m in self.memberships.values()
            if m.user_id == user_id and m.removed_at is None
        }
        return tuple(
            self.groups[gid]
            for gid in active_group_ids
            if gid in self.groups and self.groups[gid].deleted_at is None
        )


# ---------------------------------------------------------------------------
# Postgres adapter
# ---------------------------------------------------------------------------


class PostgresScimStore:
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

    # Tokens ------------------------------------------------------------
    def create_token(
        self, record: ScimTokenRecord, *, conn: Any | None = None
    ) -> ScimTokenRecord:
        with self._cursor(conn) as cur:
            cur.execute(
                """
                INSERT INTO scim_tokens (
                    token_id, org_id, provider_id, token_hash, token_prefix,
                    created_by_user_id, created_at, expires_at,
                    revoked_at, last_used_at
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    record.token_id,
                    record.org_id,
                    record.provider_id,
                    record.token_hash,
                    record.token_prefix,
                    record.created_by_user_id,
                    record.created_at,
                    record.expires_at,
                    record.revoked_at,
                    record.last_used_at,
                ),
            )
        return record

    def get_token_by_hash(
        self, *, token_hash: str, conn: Any | None = None
    ) -> ScimTokenRecord | None:
        with self._cursor(conn) as cur:
            cur.execute(
                "SELECT * FROM scim_tokens WHERE token_hash = %s",
                (token_hash,),
            )
            row = cur.fetchone()
        return ScimTokenRecord.model_validate(row) if row else None

    def list_tokens(
        self, *, org_id: str, provider_id: str
    ) -> tuple[ScimTokenRecord, ...]:
        with self._cursor(None) as cur:
            cur.execute(
                """
                SELECT * FROM scim_tokens
                WHERE org_id = %s AND provider_id = %s
                ORDER BY created_at
                """,
                (org_id, provider_id),
            )
            rows = cur.fetchall()
        return tuple(ScimTokenRecord.model_validate(row) for row in rows)

    def revoke_token(self, *, token_id: str, conn: Any | None = None) -> bool:
        with self._cursor(conn) as cur:
            cur.execute(
                """
                UPDATE scim_tokens SET revoked_at = now()
                WHERE token_id = %s AND revoked_at IS NULL
                """,
                (token_id,),
            )
            return bool(cur.rowcount)

    def touch_token_last_used(self, *, token_id: str, conn: Any | None = None) -> bool:
        with self._cursor(conn) as cur:
            cur.execute(
                "UPDATE scim_tokens SET last_used_at = now() WHERE token_id = %s",
                (token_id,),
            )
            return bool(cur.rowcount)

    # External-id mappings ---------------------------------------------
    def create_external_id(
        self, record: ScimExternalIdRecord, *, conn: Any | None = None
    ) -> ScimExternalIdRecord:
        with self._cursor(conn) as cur:
            cur.execute(
                """
                INSERT INTO scim_external_ids (
                    mapping_id, org_id, user_id, group_id,
                    provider_id, external_id, created_at
                ) VALUES (%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    record.mapping_id,
                    record.org_id,
                    record.user_id,
                    record.group_id,
                    record.provider_id,
                    record.external_id,
                    record.created_at,
                ),
            )
        return record

    def get_external_id(
        self, *, provider_id: str, external_id: str
    ) -> ScimExternalIdRecord | None:
        with self._cursor(None) as cur:
            cur.execute(
                """
                SELECT * FROM scim_external_ids
                WHERE provider_id = %s AND external_id = %s
                """,
                (provider_id, external_id),
            )
            row = cur.fetchone()
        return ScimExternalIdRecord.model_validate(row) if row else None

    def list_external_ids_for_user(
        self, *, user_id: str
    ) -> tuple[ScimExternalIdRecord, ...]:
        with self._cursor(None) as cur:
            cur.execute(
                "SELECT * FROM scim_external_ids WHERE user_id = %s",
                (user_id,),
            )
            rows = cur.fetchall()
        return tuple(ScimExternalIdRecord.model_validate(row) for row in rows)

    # Groups ------------------------------------------------------------
    def create_group(
        self, record: ScimGroupRecord, *, conn: Any | None = None
    ) -> ScimGroupRecord:
        with self._cursor(conn) as cur:
            cur.execute(
                """
                INSERT INTO scim_groups (
                    group_id, org_id, display_name, external_id,
                    mapped_role_id, created_at, updated_at, deleted_at
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    record.group_id,
                    record.org_id,
                    record.display_name,
                    record.external_id,
                    record.mapped_role_id,
                    record.created_at,
                    record.updated_at,
                    record.deleted_at,
                ),
            )
        return record

    def update_group(
        self, record: ScimGroupRecord, *, conn: Any | None = None
    ) -> ScimGroupRecord:
        updated = record.model_copy(update={"updated_at": _now()})
        with self._cursor(conn) as cur:
            cur.execute(
                """
                UPDATE scim_groups
                SET display_name = %s,
                    external_id = %s,
                    mapped_role_id = %s,
                    updated_at = %s
                WHERE group_id = %s AND org_id = %s AND deleted_at IS NULL
                """,
                (
                    updated.display_name,
                    updated.external_id,
                    updated.mapped_role_id,
                    updated.updated_at,
                    updated.group_id,
                    updated.org_id,
                ),
            )
        return updated

    def get_group(self, *, org_id: str, group_id: str) -> ScimGroupRecord | None:
        with self._cursor(None) as cur:
            cur.execute(
                """
                SELECT * FROM scim_groups
                WHERE org_id = %s AND group_id = %s AND deleted_at IS NULL
                """,
                (org_id, group_id),
            )
            row = cur.fetchone()
        return ScimGroupRecord.model_validate(row) if row else None

    def list_groups(self, *, org_id: str) -> tuple[ScimGroupRecord, ...]:
        with self._cursor(None) as cur:
            cur.execute(
                """
                SELECT * FROM scim_groups
                WHERE org_id = %s AND deleted_at IS NULL
                ORDER BY created_at
                """,
                (org_id,),
            )
            rows = cur.fetchall()
        return tuple(ScimGroupRecord.model_validate(row) for row in rows)

    def soft_delete_group(
        self, *, org_id: str, group_id: str, conn: Any | None = None
    ) -> bool:
        with self._cursor(conn) as cur:
            cur.execute(
                """
                UPDATE scim_groups
                SET deleted_at = now(), updated_at = now()
                WHERE org_id = %s AND group_id = %s AND deleted_at IS NULL
                """,
                (org_id, group_id),
            )
            return bool(cur.rowcount)

    # Members -----------------------------------------------------------
    def add_member(
        self, record: ScimGroupMemberRecord, *, conn: Any | None = None
    ) -> ScimGroupMemberRecord:
        with self._cursor(conn) as cur:
            cur.execute(
                """
                INSERT INTO scim_group_members (
                    membership_id, org_id, group_id, user_id,
                    added_at, removed_at
                ) VALUES (%s,%s,%s,%s,%s,%s)
                ON CONFLICT (group_id, user_id) WHERE removed_at IS NULL
                DO NOTHING
                """,
                (
                    record.membership_id,
                    record.org_id,
                    record.group_id,
                    record.user_id,
                    record.added_at,
                    record.removed_at,
                ),
            )
        return record

    def remove_member(
        self,
        *,
        group_id: str,
        user_id: str,
        conn: Any | None = None,
    ) -> bool:
        with self._cursor(conn) as cur:
            cur.execute(
                """
                UPDATE scim_group_members
                SET removed_at = now()
                WHERE group_id = %s AND user_id = %s AND removed_at IS NULL
                """,
                (group_id, user_id),
            )
            return bool(cur.rowcount)

    def list_members(self, *, group_id: str) -> tuple[ScimGroupMemberRecord, ...]:
        with self._cursor(None) as cur:
            cur.execute(
                """
                SELECT * FROM scim_group_members
                WHERE group_id = %s AND removed_at IS NULL
                """,
                (group_id,),
            )
            rows = cur.fetchall()
        return tuple(ScimGroupMemberRecord.model_validate(row) for row in rows)

    def list_active_groups_for_user(
        self, *, user_id: str
    ) -> tuple[ScimGroupRecord, ...]:
        with self._cursor(None) as cur:
            cur.execute(
                """
                SELECT g.* FROM scim_groups g
                JOIN scim_group_members m ON m.group_id = g.group_id
                WHERE m.user_id = %s
                  AND m.removed_at IS NULL
                  AND g.deleted_at IS NULL
                ORDER BY g.created_at
                """,
                (user_id,),
            )
            rows = cur.fetchall()
        return tuple(ScimGroupRecord.model_validate(row) for row in rows)


__all__ = [
    "InMemoryScimStore",
    "PostgresScimStore",
    "ScimStore",
]
