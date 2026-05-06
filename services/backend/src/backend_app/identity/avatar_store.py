"""Avatar BYTEA store (PR 8.3).

A small port + two adapters (in-memory for tests / dev, Postgres for
production). The route layer only sees ``AvatarStore``; a future cloud
adapter (S3 / GCS) is a third subclass and the routes don't change.

Why Postgres for the production adapter: the avatar is small (≤ 200 KB
after FE resize), tenant-scoped, and inherits the existing RLS + audit
posture. Object storage adds a deploy dependency that's premature for
the first iteration of this surface.
"""

from __future__ import annotations

import hashlib
from contextlib import contextmanager
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict


def _now() -> datetime:
    return datetime.now(timezone.utc)


def compute_etag(content: bytes) -> str:
    """Stable cache key for the GET route's ETag + the FE cache-bust."""
    return hashlib.sha256(content).hexdigest()[:32]


class UserAvatarRecord(BaseModel):
    """One row in ``user_avatars``."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    user_id: str
    org_id: str
    content_type: str
    bytes_: bytes
    size_bytes: int
    etag: str
    updated_at: datetime


class AvatarStore(Protocol):
    """Adapter contract — every adapter implements all three methods."""

    def get(self, *, org_id: str, user_id: str) -> UserAvatarRecord | None: ...

    def upsert(
        self,
        *,
        org_id: str,
        user_id: str,
        content_type: str,
        content: bytes,
    ) -> UserAvatarRecord: ...

    def delete(self, *, org_id: str, user_id: str) -> bool: ...


@dataclass
class InMemoryAvatarStore:
    """Dict-backed adapter for tests + dev. Mirrors Postgres semantics."""

    rows: dict[tuple[str, str], UserAvatarRecord] = field(default_factory=dict)

    @contextmanager
    def transaction(self) -> Iterator[None]:
        yield None

    def get(self, *, org_id: str, user_id: str) -> UserAvatarRecord | None:
        return self.rows.get((org_id, user_id))

    def upsert(
        self,
        *,
        org_id: str,
        user_id: str,
        content_type: str,
        content: bytes,
    ) -> UserAvatarRecord:
        record = UserAvatarRecord(
            user_id=user_id,
            org_id=org_id,
            content_type=content_type,
            bytes_=content,
            size_bytes=len(content),
            etag=compute_etag(content),
            updated_at=_now(),
        )
        self.rows[(org_id, user_id)] = record
        return record

    def delete(self, *, org_id: str, user_id: str) -> bool:
        return self.rows.pop((org_id, user_id), None) is not None


class PostgresAvatarStore:
    """Postgres-backed adapter for ``user_avatars`` (migration 0028)."""

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    @contextmanager
    def _cursor(self, conn: Any | None) -> Iterator[Any]:
        if conn is not None:
            with conn.cursor() as cur:
                yield cur
            return
        with self._pool.connection() as owned:
            with owned.cursor() as cur:
                yield cur

    def get(self, *, org_id: str, user_id: str) -> UserAvatarRecord | None:
        with self._cursor(None) as cur:
            cur.execute(
                """
                SELECT user_id, org_id, content_type, bytes,
                       size_bytes, etag, updated_at
                FROM user_avatars
                WHERE org_id = %s AND user_id = %s
                """,
                (org_id, user_id),
            )
            row = cur.fetchone()
        if row is None:
            return None
        record = dict(row)
        # psycopg returns BYTEA as ``memoryview``; coerce to bytes so
        # the Pydantic ``bytes_`` field validates.
        record["bytes_"] = bytes(record.pop("bytes"))
        return UserAvatarRecord.model_validate(record)

    def upsert(
        self,
        *,
        org_id: str,
        user_id: str,
        content_type: str,
        content: bytes,
    ) -> UserAvatarRecord:
        etag = compute_etag(content)
        size = len(content)
        with self._cursor(None) as cur:
            cur.execute(
                """
                INSERT INTO user_avatars (
                    user_id, org_id, content_type, bytes,
                    size_bytes, etag, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, NOW())
                ON CONFLICT (user_id) DO UPDATE SET
                    content_type = EXCLUDED.content_type,
                    bytes = EXCLUDED.bytes,
                    size_bytes = EXCLUDED.size_bytes,
                    etag = EXCLUDED.etag,
                    updated_at = NOW()
                RETURNING updated_at
                """,
                (user_id, org_id, content_type, content, size, etag),
            )
            row = cur.fetchone()
        return UserAvatarRecord(
            user_id=user_id,
            org_id=org_id,
            content_type=content_type,
            bytes_=content,
            size_bytes=size,
            etag=etag,
            updated_at=row["updated_at"] if row else _now(),
        )

    def delete(self, *, org_id: str, user_id: str) -> bool:
        with self._cursor(None) as cur:
            cur.execute(
                "DELETE FROM user_avatars WHERE org_id = %s AND user_id = %s",
                (org_id, user_id),
            )
            return bool(cur.rowcount)


__all__ = [
    "AvatarStore",
    "InMemoryAvatarStore",
    "PostgresAvatarStore",
    "UserAvatarRecord",
    "compute_etag",
]
