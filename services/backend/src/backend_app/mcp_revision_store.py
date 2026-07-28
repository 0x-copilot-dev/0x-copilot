"""Concrete persistence adapters for body-free MCP descriptor revisions.

The registry remains the source of truth.  This module deliberately does not
discover remote MCP descriptors: callers may only publish a *complete*
descriptor observation. Registry mutations emit invalidations in the same
transaction, allowing a runtime to evict stale local material without
receiving an endpoint, token, or descriptor body from this feed.
"""

from __future__ import annotations

import secrets
from collections import deque
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any
from uuid import uuid4

from backend_app.contracts import (
    McpDescriptorRevision,
    McpDescriptorRevisionFeed,
    McpDescriptorRevisionNotice,
    McpRevisionReason,
)
from backend_app.store import PostgresMcpStore


class RevisionCursorExpired(ValueError):
    """The requested opaque feed cursor was retained no longer."""


def _scope_hash(
    *, org_id: str, user_id: str, server_id: str, credential_subject: str | None = None
) -> str:
    # One-way credential-subject/scope binding.  Do not use this value to
    # authenticate a caller; it is only an opaque identity in the feed.
    subject = credential_subject or f"profile:{org_id}:{user_id}"
    return sha256(
        f"{org_id}\x00{user_id}\x00{server_id}\x00{subject}".encode()
    ).hexdigest()


def _revision(
    *,
    descriptor_digest: str,
    subject_scope_hash: str,
    generations: tuple[int, int, int, int],
) -> str:
    """Digest a complete descriptor against the credential/profile scope."""

    return sha256(
        f"{descriptor_digest}|{subject_scope_hash}|{generations}".encode()
    ).hexdigest()


class InMemoryMcpRevisionStore:
    """In-memory revision-store adapter used by tests and local development."""

    def __init__(
        self,
        *,
        retain_max: int = 10_000,
    ) -> None:
        if retain_max < 1:
            raise ValueError("retain_max must be positive")
        self._retain_max = retain_max
        self._views: dict[tuple[str, str, str], McpDescriptorRevision] = {}
        self._generations: dict[tuple[str, str, str], tuple[int, int, int, int]] = {}
        self._notices: deque[tuple[str, str, McpDescriptorRevisionNotice]] = deque()
        self._idempotency: dict[
            tuple[str, str, str, str],
            tuple[tuple[str, int, int, str, str], McpDescriptorRevision],
        ] = {}
        self._idempotency_order: deque[tuple[str, str, str, str]] = deque()
        self._sequence = 0

    @staticmethod
    def _key(org_id: str, user_id: str, server_id: str) -> tuple[str, str, str]:
        return (org_id, user_id, server_id)

    def get_current(
        self, *, org_id: str, user_id: str, server_id: str
    ) -> McpDescriptorRevision | None:
        return self._views.get(self._key(org_id, user_id, server_id))

    def feed(
        self,
        *,
        org_id: str,
        user_id: str,
        after_cursor: str | None,
        limit: int,
    ) -> McpDescriptorRevisionFeed:
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        rows = [
            notice
            for org, user, notice in self._notices
            if org == org_id and user == user_id
        ]
        start = 0
        if after_cursor is not None:
            indexes = [
                index
                for index, notice in enumerate(rows)
                if notice.cursor == after_cursor
            ]
            if not indexes:
                # A cursor from another profile is indistinguishable from an
                # expired cursor; this avoids cross-profile existence leaks.
                raise RevisionCursorExpired("revision feed cursor expired")
            start = indexes[0] + 1
        page = tuple(rows[start : start + limit])
        return McpDescriptorRevisionFeed(
            notices=page, next_cursor=page[-1].cursor if page else after_cursor
        )

    def invalidate(
        self,
        *,
        org_id: str,
        user_id: str,
        server_id: str,
        reason: McpRevisionReason,
        credential_subject: str | None = None,
        conn: Any | None = None,
    ) -> None:
        """Append a body-free invalidation; it never fabricates a descriptor view."""
        key = self._key(org_id, user_id, server_id)
        view = self._views.pop(key, None)
        current = self._generations.get(key, (0, 0, 0, 0))
        self._generations[key] = self._advance_generation(current, reason)
        self._append_memory_notice(
            org_id=org_id,
            user_id=user_id,
            server_id=server_id,
            old_revision=view.revision if view else None,
            new_revision=None,
            reason=reason,
            credential_subject=credential_subject,
        )

    def publish_complete_descriptor_view(
        self,
        *,
        org_id: str,
        user_id: str,
        server_id: str,
        descriptor_digest: str,
        tool_count: int,
        resource_count: int,
        source: str,
        idempotency_key: str,
        credential_subject: str | None = None,
        conn: Any | None = None,
    ) -> McpDescriptorRevision:
        """Atomically replace the current body-free view and append a notice.

        ``descriptor_digest`` must be a digest of a *complete* paginated
        observation.  This authority has no remote-session dependency, so a
        partial cycle cannot accidentally publish an empty replacement.
        """
        if not descriptor_digest or tool_count < 0 or resource_count < 0 or not source:
            raise ValueError(
                "a complete descriptor digest, counts, and source are required"
            )
        if not idempotency_key:
            raise ValueError("idempotency_key is required")
        key = self._key(org_id, user_id, server_id)
        idem_key = (*key, idempotency_key)
        requested_scope = _scope_hash(
            org_id=org_id,
            user_id=user_id,
            server_id=server_id,
            credential_subject=credential_subject,
        )
        existing = self._idempotency.get(idem_key)
        if existing is not None:
            request_fingerprint = (
                descriptor_digest,
                tool_count,
                resource_count,
                source,
                requested_scope,
            )
            if existing[0] != request_fingerprint:
                raise ValueError(
                    "idempotency_key conflicts with a previous descriptor publication"
                )
            return existing[1]
        old = self._views.get(key)
        generations = self._generations.get(
            key,
            (
                old.config_generation,
                old.auth_generation,
                old.transport_generation,
                old.tool_filter_generation,
            )
            if old
            else (0, 0, 0, 0),
        )
        now = datetime.now(timezone.utc)
        subject_scope_hash = requested_scope
        view = McpDescriptorRevision(
            server_id=server_id,
            profile_id=user_id,
            subject_scope_hash=subject_scope_hash,
            revision=_revision(
                descriptor_digest=descriptor_digest,
                subject_scope_hash=subject_scope_hash,
                generations=generations,
            ),
            config_generation=generations[0],
            auth_generation=generations[1],
            transport_generation=generations[2],
            tool_filter_generation=generations[3],
            tool_count=tool_count,
            resource_count=resource_count,
            descriptor_digest=descriptor_digest,
            observed_at=now,
            source=source,
        )
        self._views[key] = view
        self._idempotency[idem_key] = (
            (descriptor_digest, tool_count, resource_count, source, requested_scope),
            view,
        )
        self._idempotency_order.append(idem_key)
        while len(self._idempotency_order) > self._retain_max:
            self._idempotency.pop(self._idempotency_order.popleft(), None)
        self._append_memory_notice(
            org_id=org_id,
            user_id=user_id,
            server_id=server_id,
            old_revision=old.revision if old else None,
            new_revision=view.revision,
            reason=McpRevisionReason.DESCRIPTOR_OBSERVED,
            credential_subject=credential_subject,
            occurred_at=now,
        )
        return view

    @staticmethod
    def _advance_generation(
        current: tuple[int, int, int, int], reason: McpRevisionReason
    ) -> tuple[int, int, int, int]:
        config, auth, transport, tool_filter = current
        if reason is McpRevisionReason.AUTH_CHANGED:
            auth += 1
        elif reason is McpRevisionReason.TRANSPORT_CHANGED:
            transport += 1
        elif reason is McpRevisionReason.TOOL_FILTER_CHANGED:
            tool_filter += 1
        else:
            config += 1
        return (config, auth, transport, tool_filter)

    def _append_memory_notice(
        self,
        *,
        org_id: str,
        user_id: str,
        server_id: str,
        old_revision: str | None,
        new_revision: str | None,
        reason: McpRevisionReason,
        credential_subject: str | None = None,
        occurred_at: datetime | None = None,
    ) -> None:
        self._sequence += 1
        notice = McpDescriptorRevisionNotice(
            cursor=secrets.token_urlsafe(24),
            notice_id=uuid4().hex,
            sequence_no=self._sequence,
            server_id=server_id,
            profile_id=user_id,
            subject_scope_hash=_scope_hash(
                org_id=org_id,
                user_id=user_id,
                server_id=server_id,
                credential_subject=credential_subject,
            ),
            old_revision=old_revision,
            new_revision=new_revision,
            reason=reason,
            occurred_at=occurred_at or datetime.now(timezone.utc),
        )
        self._notices.append((org_id, user_id, notice))
        while len(self._notices) > self._retain_max:
            self._notices.popleft()


class _PostgresMcpRevisionPersistence:
    """Postgres-only persistence implementation; never mixed into memory."""

    def __init__(self, *, store: PostgresMcpStore, retain_max: int) -> None:
        if retain_max < 1:
            raise ValueError("retain_max must be positive")
        self._store = store
        self._retain_max = retain_max

    @staticmethod
    def _take_scope_lock(
        cur: Any, *, org_id: str, user_id: str, server_id: str
    ) -> None:
        """Serialize one descriptor scope for invalidate/publish ordering."""

        cur.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
            (f"mcp-revision:{org_id}:{user_id}:{server_id}",),
        )

    @contextmanager
    def _pg_connection(self, *, conn: Any | None, org_id: str) -> Iterator[Any]:
        if conn is not None:
            yield conn
            return
        if not isinstance(self._store, PostgresMcpStore):
            raise TypeError("Postgres revision persistence is not configured")
        with self._store.transaction(org_id=org_id) as inherited:
            yield inherited

    def _pg_get_current(
        self, *, org_id: str, user_id: str, server_id: str
    ) -> McpDescriptorRevision | None:
        with (
            self._pg_connection(conn=None, org_id=org_id) as conn,
            conn.cursor() as cur,
        ):
            cur.execute(
                """SELECT * FROM mcp_descriptor_revisions
                    WHERE org_id=%(org_id)s AND user_id=%(user_id)s AND server_id=%(server_id)s""",
                {"org_id": org_id, "user_id": user_id, "server_id": server_id},
            )
            row = cur.fetchone()
        return self._view_from_row(row) if row else None

    def _pg_feed(
        self, *, org_id: str, user_id: str, after_cursor: str | None, limit: int
    ) -> McpDescriptorRevisionFeed:
        with (
            self._pg_connection(conn=None, org_id=org_id) as conn,
            conn.cursor() as cur,
        ):
            sequence = 0
            if after_cursor is not None:
                cur.execute(
                    """SELECT sequence_no FROM mcp_descriptor_revision_notices
                        WHERE org_id=%(org_id)s AND user_id=%(user_id)s AND cursor=%(cursor)s""",
                    {"org_id": org_id, "user_id": user_id, "cursor": after_cursor},
                )
                found = cur.fetchone()
                if found is None:
                    raise RevisionCursorExpired("revision feed cursor expired")
                sequence = int(found["sequence_no"])
            cur.execute(
                """SELECT * FROM mcp_descriptor_revision_notices
                    WHERE org_id=%(org_id)s AND user_id=%(user_id)s AND sequence_no > %(sequence)s
                    ORDER BY sequence_no ASC LIMIT %(limit)s""",
                {
                    "org_id": org_id,
                    "user_id": user_id,
                    "sequence": sequence,
                    "limit": limit,
                },
            )
            notices = tuple(self._notice_from_row(row) for row in cur.fetchall())
        return McpDescriptorRevisionFeed(
            notices=notices, next_cursor=notices[-1].cursor if notices else after_cursor
        )

    def _pg_invalidate(
        self,
        *,
        org_id: str,
        user_id: str,
        server_id: str,
        reason: McpRevisionReason,
        credential_subject: str | None,
        conn: Any | None,
    ) -> None:
        with (
            self._pg_connection(conn=conn, org_id=org_id) as connection,
            connection.cursor() as cur,
        ):
            self._take_scope_lock(
                cur, org_id=org_id, user_id=user_id, server_id=server_id
            )
            cur.execute(
                "SELECT revision FROM mcp_descriptor_revisions WHERE org_id=%s AND user_id=%s AND server_id=%s FOR UPDATE",
                (org_id, user_id, server_id),
            )
            old = cur.fetchone()
            cur.execute(
                """INSERT INTO mcp_descriptor_revision_notices
                    (notice_id, cursor, org_id, user_id, server_id, profile_id, subject_scope_hash,
                     old_revision, new_revision, reason, occurred_at)
                    VALUES (%(notice_id)s, %(cursor)s, %(org_id)s, %(user_id)s, %(server_id)s,
                     %(profile_id)s, %(scope)s, %(old_revision)s, NULL, %(reason)s, now())""",
                {
                    "notice_id": uuid4().hex,
                    "cursor": secrets.token_urlsafe(24),
                    "org_id": org_id,
                    "user_id": user_id,
                    "server_id": server_id,
                    "profile_id": user_id,
                    "scope": _scope_hash(
                        org_id=org_id,
                        user_id=user_id,
                        server_id=server_id,
                        credential_subject=credential_subject,
                    ),
                    "old_revision": old["revision"] if old else None,
                    "reason": reason.value,
                },
            )
            column = {
                McpRevisionReason.AUTH_CHANGED: "auth_generation",
                McpRevisionReason.TRANSPORT_CHANGED: "transport_generation",
                McpRevisionReason.TOOL_FILTER_CHANGED: "tool_filter_generation",
            }.get(reason, "config_generation")
            cur.execute(
                f"INSERT INTO mcp_descriptor_revision_generations (org_id,user_id,server_id,{column}) "
                f"VALUES (%s,%s,%s,1) ON CONFLICT (org_id,user_id,server_id) DO UPDATE SET {column}=mcp_descriptor_revision_generations.{column}+1",
                (org_id, user_id, server_id),
            )
            cur.execute(
                "DELETE FROM mcp_descriptor_revisions WHERE org_id=%s AND user_id=%s AND server_id=%s",
                (org_id, user_id, server_id),
            )
            cur.execute(
                "DELETE FROM mcp_descriptor_revision_notices WHERE sequence_no IN ("
                "SELECT sequence_no FROM mcp_descriptor_revision_notices "
                "WHERE org_id=%s AND user_id=%s ORDER BY sequence_no DESC OFFSET %s)",
                (org_id, user_id, self._retain_max),
            )

    def _pg_publish(self, **kwargs: Any) -> McpDescriptorRevision:
        org_id, user_id, server_id = (
            kwargs["org_id"],
            kwargs["user_id"],
            kwargs["server_id"],
        )
        requested_scope = _scope_hash(
            org_id=org_id,
            user_id=user_id,
            server_id=server_id,
            credential_subject=kwargs.get("credential_subject"),
        )
        with (
            self._pg_connection(conn=kwargs.get("conn"), org_id=org_id) as connection,
            connection.cursor() as cur,
        ):
            self._take_scope_lock(
                cur, org_id=org_id, user_id=user_id, server_id=server_id
            )
            cur.execute(
                """SELECT * FROM mcp_descriptor_revision_idempotency
                    WHERE org_id=%(org_id)s AND user_id=%(user_id)s AND server_id=%(server_id)s AND idempotency_key=%(idempotency_key)s""",
                kwargs,
            )
            existing = cur.fetchone()
            if existing:
                if (
                    str(existing["descriptor_digest"])
                    != str(kwargs["descriptor_digest"])
                    or int(existing["tool_count"]) != int(kwargs["tool_count"])
                    or int(existing["resource_count"]) != int(kwargs["resource_count"])
                    or str(existing["source"]) != str(kwargs["source"])
                    or str(existing["subject_scope_hash"]) != requested_scope
                ):
                    raise ValueError(
                        "idempotency_key conflicts with a previous descriptor publication"
                    )
                return self._view_from_row(existing)
            cur.execute(
                "SELECT * FROM mcp_descriptor_revisions WHERE org_id=%(org_id)s "
                "AND user_id=%(user_id)s AND server_id=%(server_id)s FOR UPDATE",
                kwargs,
            )
            old = cur.fetchone()
            cur.execute(
                "SELECT * FROM mcp_descriptor_revision_generations WHERE org_id=%(org_id)s "
                "AND user_id=%(user_id)s AND server_id=%(server_id)s FOR UPDATE",
                kwargs,
            )
            generation = cur.fetchone()
            gens = (
                int(generation["config_generation"]) if generation else 0,
                int(generation["auth_generation"]) if generation else 0,
                int(generation["transport_generation"]) if generation else 0,
                int(generation["tool_filter_generation"]) if generation else 0,
            )
            params = {
                **kwargs,
                "profile_id": user_id,
                "scope": requested_scope,
                "config": gens[0],
                "auth": gens[1],
                "transport": gens[2],
                "tool_filter": gens[3],
            }
            params["revision"] = _revision(
                descriptor_digest=kwargs["descriptor_digest"],
                subject_scope_hash=params["scope"],
                generations=gens,
            )
            cur.execute(
                """INSERT INTO mcp_descriptor_revisions (org_id,user_id,server_id,profile_id,subject_scope_hash,revision,config_generation,auth_generation,transport_generation,tool_filter_generation,tool_count,resource_count,descriptor_digest,observed_at,source)
                    VALUES (%(org_id)s,%(user_id)s,%(server_id)s,%(profile_id)s,%(scope)s,%(revision)s,%(config)s,%(auth)s,%(transport)s,%(tool_filter)s,%(tool_count)s,%(resource_count)s,%(descriptor_digest)s,now(),%(source)s)
                    ON CONFLICT (org_id,user_id,server_id) DO UPDATE SET profile_id=EXCLUDED.profile_id,subject_scope_hash=EXCLUDED.subject_scope_hash,revision=EXCLUDED.revision,config_generation=EXCLUDED.config_generation,auth_generation=EXCLUDED.auth_generation,transport_generation=EXCLUDED.transport_generation,tool_filter_generation=EXCLUDED.tool_filter_generation,tool_count=EXCLUDED.tool_count,resource_count=EXCLUDED.resource_count,descriptor_digest=EXCLUDED.descriptor_digest,observed_at=EXCLUDED.observed_at,source=EXCLUDED.source RETURNING *""",
                params,
            )
            row = cur.fetchone()
            cur.execute(
                """INSERT INTO mcp_descriptor_revision_idempotency (org_id,user_id,server_id,idempotency_key,revision,profile_id,subject_scope_hash,config_generation,auth_generation,transport_generation,tool_filter_generation,tool_count,resource_count,descriptor_digest,observed_at,source)
                    VALUES (%(org_id)s,%(user_id)s,%(server_id)s,%(idempotency_key)s,%(revision)s,%(profile_id)s,%(scope)s,%(config)s,%(auth)s,%(transport)s,%(tool_filter)s,%(tool_count)s,%(resource_count)s,%(descriptor_digest)s,%(observed_at)s,%(source)s)""",
                {**params, **row},
            )
            cur.execute(
                """INSERT INTO mcp_descriptor_revision_notices (notice_id,cursor,org_id,user_id,server_id,profile_id,subject_scope_hash,old_revision,new_revision,reason,occurred_at)
                    VALUES (%(notice_id)s,%(cursor)s,%(org_id)s,%(user_id)s,%(server_id)s,%(profile_id)s,%(scope)s,%(old)s,%(revision)s,%(reason)s,now())""",
                {
                    **params,
                    "notice_id": uuid4().hex,
                    "cursor": secrets.token_urlsafe(24),
                    "old": old["revision"] if old else None,
                    "reason": McpRevisionReason.DESCRIPTOR_OBSERVED.value,
                },
            )
            # Bounded retention intentionally makes a cursor older than
            # the retained window expire; consumers recover by exact
            # checks, never by replaying effects.
            cur.execute(
                "DELETE FROM mcp_descriptor_revision_notices WHERE sequence_no IN ("
                "SELECT sequence_no FROM mcp_descriptor_revision_notices "
                "WHERE org_id=%s AND user_id=%s ORDER BY sequence_no DESC OFFSET %s)",
                (org_id, user_id, self._retain_max),
            )
            cur.execute(
                "DELETE FROM mcp_descriptor_revision_idempotency WHERE sequence_no IN ("
                "SELECT sequence_no FROM mcp_descriptor_revision_idempotency "
                "WHERE org_id=%s AND user_id=%s ORDER BY sequence_no DESC OFFSET %s)",
                (org_id, user_id, self._retain_max),
            )
        return self._view_from_row(row)

    @staticmethod
    def _view_from_row(row: Any) -> McpDescriptorRevision:
        return McpDescriptorRevision(**dict(row))

    @staticmethod
    def _notice_from_row(row: Any) -> McpDescriptorRevisionNotice:
        return McpDescriptorRevisionNotice(**dict(row))


class PostgresMcpRevisionStore(_PostgresMcpRevisionPersistence):
    """Postgres revision-store adapter using the registry's existing pool."""

    def __init__(self, *, store: PostgresMcpStore, retain_max: int = 10_000) -> None:
        super().__init__(store=store, retain_max=retain_max)

    def get_current(
        self, *, org_id: str, user_id: str, server_id: str
    ) -> McpDescriptorRevision | None:
        return self._pg_get_current(org_id=org_id, user_id=user_id, server_id=server_id)

    def feed(
        self,
        *,
        org_id: str,
        user_id: str,
        after_cursor: str | None,
        limit: int,
    ) -> McpDescriptorRevisionFeed:
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        return self._pg_feed(
            org_id=org_id, user_id=user_id, after_cursor=after_cursor, limit=limit
        )

    def invalidate(
        self,
        *,
        org_id: str,
        user_id: str,
        server_id: str,
        reason: McpRevisionReason,
        credential_subject: str | None = None,
        conn: Any | None = None,
    ) -> None:
        self._pg_invalidate(
            org_id=org_id,
            user_id=user_id,
            server_id=server_id,
            reason=reason,
            credential_subject=credential_subject,
            conn=conn,
        )

    def publish_complete_descriptor_view(
        self,
        *,
        org_id: str,
        user_id: str,
        server_id: str,
        descriptor_digest: str,
        tool_count: int,
        resource_count: int,
        source: str,
        idempotency_key: str,
        credential_subject: str | None = None,
        conn: Any | None = None,
    ) -> McpDescriptorRevision:
        if not descriptor_digest or tool_count < 0 or resource_count < 0 or not source:
            raise ValueError(
                "a complete descriptor digest, counts, and source are required"
            )
        if not idempotency_key:
            raise ValueError("idempotency_key is required")
        return self._pg_publish(
            org_id=org_id,
            user_id=user_id,
            server_id=server_id,
            descriptor_digest=descriptor_digest,
            tool_count=tool_count,
            resource_count=resource_count,
            source=source,
            idempotency_key=idempotency_key,
            credential_subject=credential_subject,
            conn=conn,
        )
