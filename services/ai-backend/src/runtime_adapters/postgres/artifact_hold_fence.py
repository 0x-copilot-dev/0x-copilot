"""Transaction fences shared by legal-hold writers and artifact deletion.

The legal-hold table is owned by the retention subsystem, so artifact code may
not rely on a best-effort callback after a deletion commits.  Both sides take
the same transaction-scoped PostgreSQL advisory fences; the migration's hold
trigger takes the identical SQL locks for every direct table writer.
"""

from __future__ import annotations

from collections.abc import Iterable


def hold_fence_tokens(
    *,
    org_id: str,
    user_id: str | None = None,
    conversation_id: str | None = None,
) -> tuple[str, ...]:
    """Return the closed, deterministic lock set for a lifecycle scope."""

    tokens = {f"artifact-hold:org:{org_id}"}
    if user_id is not None:
        tokens.add(f"artifact-hold:user:{org_id}:{user_id}")
    if conversation_id is not None:
        tokens.add(f"artifact-hold:conversation:{org_id}:{conversation_id}")
    return tuple(sorted(tokens))


async def acquire_artifact_hold_fences(
    conn,
    *,
    org_id: str,
    user_id: str | None = None,
    conversation_id: str | None = None,
) -> None:
    """Serialize lifecycle changes with a direct legal-hold INSERT/UPDATE."""

    for token in hold_fence_tokens(
        org_id=org_id,
        user_id=user_id,
        conversation_id=conversation_id,
    ):
        await conn.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
            (token,),
        )


def active_hold_predicate(
    *,
    artifact_alias: str,
) -> str:
    """SQL expression proving no active legal hold covers one artifact row."""

    return f"""
        NOT EXISTS (
            SELECT 1
              FROM runtime_legal_holds h
             WHERE h.org_id = {artifact_alias}.org_id
               AND h.released_at IS NULL
               AND (
                    (h.scope = 'org' AND h.resource_id = {artifact_alias}.org_id)
                    OR (h.scope = 'user' AND h.user_id = {artifact_alias}.user_id)
                    OR (
                        h.scope = 'conversation'
                        AND h.resource_id = {artifact_alias}.conversation_id
                    )
               )
        )
    """


async def has_active_hold_for_scope(
    conn,
    *,
    org_id: str,
    user_id: str | None = None,
    conversation_id: str | None = None,
) -> bool:
    """Recheck legal-hold ownership *after* acquiring the fence set.

    A user-wide erasure is blocked by a hold on any of the user's
    conversations; an org-wide lifecycle action is blocked by any active hold
    in the tenant.  This is intentionally stricter than the old preflight
    lookup because a hold must always win over destructive artifact work.
    """

    if conversation_id is not None:
        cursor = await conn.execute(
            """
            SELECT EXISTS (
                SELECT 1
                  FROM runtime_legal_holds h
                 WHERE h.org_id = %s
                   AND h.released_at IS NULL
                   AND (
                        (h.scope = 'org' AND h.resource_id = %s)
                        OR (h.scope = 'user' AND h.user_id = %s)
                        OR (h.scope = 'conversation' AND h.resource_id = %s)
                   )
            ) AS held
            """,
            (org_id, org_id, user_id, conversation_id),
        )
    elif user_id is not None:
        cursor = await conn.execute(
            """
            SELECT EXISTS (
                SELECT 1
                  FROM runtime_legal_holds h
                 WHERE h.org_id = %s
                   AND h.released_at IS NULL
                   AND (
                        (h.scope = 'org' AND h.resource_id = %s)
                        OR (h.scope = 'user' AND h.user_id = %s)
                        OR (
                            h.scope = 'conversation'
                            AND EXISTS (
                                SELECT 1
                                  FROM agent_conversations c
                                 WHERE c.org_id = %s
                                   AND c.user_id = %s
                                   AND c.id = h.resource_id
                            )
                        )
                   )
            ) AS held
            """,
            (org_id, org_id, user_id, org_id, user_id),
        )
    else:
        cursor = await conn.execute(
            """
            SELECT EXISTS (
                SELECT 1
                  FROM runtime_legal_holds h
                 WHERE h.org_id = %s AND h.released_at IS NULL
            ) AS held
            """,
            (org_id,),
        )
    row = await cursor.fetchone()
    return bool(row and row["held"])


def hold_fence_tokens_for_rows(
    rows: Iterable[tuple[str, str | None, str | None]],
) -> tuple[str, ...]:
    """Expose deterministic lock planning for database-free interleaving tests."""

    tokens: set[str] = set()
    for org_id, user_id, conversation_id in rows:
        tokens.update(
            hold_fence_tokens(
                org_id=org_id,
                user_id=user_id,
                conversation_id=conversation_id,
            )
        )
    return tuple(sorted(tokens))


__all__ = (
    "acquire_artifact_hold_fences",
    "active_hold_predicate",
    "has_active_hold_for_scope",
    "hold_fence_tokens",
    "hold_fence_tokens_for_rows",
)
