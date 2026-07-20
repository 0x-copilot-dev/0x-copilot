"""Account-merge re-key over the Postgres tenant tables (PRD §6.3/§6.4).

Executes the ai-backend half of the merge saga: every tenant-scoped row of
the absorbed ``(org_id, user_id)`` account is re-keyed to the survivor in one
transaction. The authoritative table list is derived from
``migrations/*.sql`` — do not add tables here without a migration.

Rules:

- Primary keys, run ids, and conversation ids are never rewritten. Only
  ``org_id`` and user-owner columns (``user_id``, ``owner_user_id``,
  ``requested_by_user_id``, ``decided_by_user_id``, ``created_by_user_id``,
  ``updated_by_user_id``, ``released_by_user_id``, ``forwarded_to_user_id``)
  move, and user columns move only where they equal the absorbed user.
- Encrypted columns (C7 envelope v1) bind their AAD to
  ``(table, column, org_id)`` — see ``agent_runtime.persistence.encryption``.
  Every encrypted column on a re-tenanted row is decrypted with the absorbed
  org's AAD and re-encrypted with the survivor's inside the same
  transaction. With the Null adapter (dev) rows are v0 plaintext and move
  as-is; any v1 row found while the adapter is Null is moved with a warning
  because it cannot be re-wrapped (and will fail AAD checks until it is).
- ``runtime_audit_log`` is never rewritten (append-only, per-org hash
  chain + DB immutability trigger from migration 0003). The caller appends
  a merge marker to the survivor's chain via ``write_audit_log``.
- Unique-key collisions resolve conservatively: survivor row wins, daily
  usage rollups SUM-merge, colliding idempotency keys are NULLed. Every
  resolution is recorded as a warning.

RLS context: the re-key runs on a pool connection acquired via the parent
store's ``_role_connection("worker")`` — the same cross-tenant path the SIEM
export uses — with **no** ``app.current_org_id`` GUC pinned, because the
statements must see both orgs. The 0008 tenant-isolation policies are
dormant until ``do_rls.sql`` is applied; once RLS is enforced for the app
role, this module must run under a role with BYPASSRLS (``enterprise_admin``,
the same trust level as the migration runner) or the UPDATEs will silently
match zero absorbed rows.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only
    import psycopg

    from runtime_adapters.postgres.runtime_api_store import PostgresRuntimeApiStore


class PostgresAccountMergeRekeyer:
    """Re-key one absorbed account's rows to the survivor account.

    Instantiate per merge with the parent :class:`PostgresRuntimeApiStore`
    (borrowing its pool and :class:`FieldCodec`, the same pattern the
    satellite stores use); :meth:`rekey` runs the whole re-key in one
    transaction and returns ``(tables, warnings)``.
    """

    #: ``table -> user-owner columns`` for tables where a plain
    #: two-statement rewrite (user columns, then org column) is safe —
    #: no unique key involves the tenancy columns and no column is
    #: encrypted. Derived from migrations 0001..0032.
    _SIMPLE_TABLES: tuple[tuple[str, tuple[str, ...]], ...] = (
        ("runtime_outbox_events", ()),
        ("runtime_async_tasks", ()),
        # runtime_subagent_results / runtime_tool_invocations /
        # runtime_context_payload_blobs are 0011 encryption targets — they
        # move via _rekey_encrypted_extras (v1 guard + re-wrap), not here.
        (
            "runtime_approval_requests",
            ("requested_by_user_id", "decided_by_user_id", "forwarded_to_user_id"),
        ),
        ("runtime_approval_batches", ()),
        ("runtime_context_payloads", ()),
        ("runtime_compression_events", ()),
        ("runtime_capability_snapshots", ()),
        (
            "runtime_legal_holds",
            ("user_id", "created_by_user_id", "released_by_user_id"),
        ),
        ("runtime_deletion_evidence", ("user_id",)),
        ("runtime_run_usage", ("user_id",)),
        ("runtime_model_call_usage", ()),
        ("conversation_shares", ("created_by_user_id",)),
        ("agent_conversation_tool_ordinals", ()),
        ("todo_extractions", ("owner_user_id",)),
    )

    #: Daily rollup tables: ``table -> (non-org PK columns, SUM columns,
    #: GREATEST columns)``. Colliding rows SUM-merge into the survivor row.
    _ROLLUP_TABLES: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = (
        (
            "runtime_usage_daily_user",
            ("user_id", "day", "model_provider", "model_name"),
            (
                "runs_count",
                "input_tokens",
                "output_tokens",
                "cached_input_tokens",
                "total_tokens",
            ),
        ),
        (
            "runtime_usage_daily_org",
            ("day", "model_provider", "model_name"),
            (
                "runs_count",
                "input_tokens",
                "output_tokens",
                "cached_input_tokens",
                "total_tokens",
            ),
        ),
        (
            "runtime_usage_daily_connector",
            ("day", "connector_slug"),
            (
                "runs_count",
                "input_tokens",
                "output_tokens",
                "cached_input_tokens",
                "total_tokens",
            ),
        ),
        (
            "runtime_usage_daily_subagent",
            ("day", "subagent_slug", "model_provider", "model_name"),
            (
                "call_count",
                "input_tokens",
                "output_tokens",
                "cached_input_tokens",
                "cache_creation_input_tokens",
                "reasoning_tokens",
                "audio_input_tokens",
                "audio_output_tokens",
                "total_tokens",
            ),
        ),
        (
            "runtime_usage_daily_purpose",
            ("day", "purpose", "model_provider", "model_name"),
            (
                "call_count",
                "input_tokens",
                "output_tokens",
                "cached_input_tokens",
                "cache_creation_input_tokens",
                "reasoning_tokens",
                "audio_input_tokens",
                "audio_output_tokens",
                "total_tokens",
            ),
        ),
    )

    def __init__(self, store: PostgresRuntimeApiStore) -> None:
        # Borrows the parent store's pool + FieldCodec, mirroring the
        # satellite-store pattern (see ``PostgresDraftStore``).
        self._store = store
        self._codec = store._codec

    async def rekey(
        self,
        *,
        absorbed_org_id: str,
        absorbed_user_id: str,
        survivor_org_id: str,
        survivor_user_id: str,
    ) -> tuple[dict[str, int], list[str]]:
        """Run the full re-key in one transaction; return ``(tables, warnings)``.

        Idempotent: a re-run after completion matches zero absorbed rows and
        returns empty counts.
        """

        self._absorbed_org = absorbed_org_id
        self._absorbed_user = absorbed_user_id
        self._survivor_org = survivor_org_id
        self._survivor_user = survivor_user_id
        self._tables: dict[str, int] = {}
        self._warnings: list[str] = []

        async with self._store._role_connection("worker") as conn:
            async with conn.transaction():
                await self._warn_if_outbox_pending(conn)
                await self._rekey_conversations(conn)
                await self._rekey_runs(conn)
                for table, user_columns in self._SIMPLE_TABLES:
                    await self._rekey_simple(conn, table, user_columns)
                await self._rekey_encrypted_messages(conn)
                await self._rekey_encrypted_events(conn)
                await self._rekey_encrypted_citations(conn)
                await self._rekey_encrypted_extras(conn)
                await self._rekey_drafts(conn)
                await self._rekey_memory(conn)
                await self._rekey_share_recipients(conn)
                await self._rekey_checkpoints(conn)
                for table, key_columns, sum_columns in self._ROLLUP_TABLES:
                    await self._rekey_rollup(conn, table, key_columns, sum_columns)
                await self._rekey_usage_budgets(conn)
                await self._rekey_tool_budgets(conn)
                await self._rekey_retention_policies(conn)
                await self._rekey_workspace_defaults(conn)
        return self._tables, self._warnings

    # ----- helpers --------------------------------------------------------

    def _count(self, table: str, moved: int) -> None:
        """Accumulate a moved-row count, omitting zero-count tables."""

        if moved:
            self._tables[table] = self._tables.get(table, 0) + moved

    def _warn(self, message: str) -> None:
        self._warnings.append(message)

    async def _execute(
        self, conn: psycopg.AsyncConnection, sql: str, params: tuple
    ) -> int:
        """Execute one statement and return its rowcount."""

        cur = await conn.execute(sql, params)
        return cur.rowcount if cur.rowcount is not None and cur.rowcount > 0 else 0

    async def _rekey_simple(
        self,
        conn: psycopg.AsyncConnection,
        table: str,
        user_columns: tuple[str, ...],
    ) -> None:
        """Rewrite user-owner columns, then the org column, for one table.

        Table and column names come from the static specs above — never
        from request input — so f-string interpolation is not an injection
        surface here.
        """

        for column in user_columns:
            await conn.execute(
                f"UPDATE {table} SET {column} = %s WHERE org_id = %s AND {column} = %s",
                (self._survivor_user, self._absorbed_org, self._absorbed_user),
            )
        moved = await self._execute(
            conn,
            f"UPDATE {table} SET org_id = %s WHERE org_id = %s",
            (self._survivor_org, self._absorbed_org),
        )
        self._count(table, moved)

    async def _warn_if_outbox_pending(self, conn: psycopg.AsyncConnection) -> None:
        """FR-M6 quiesce is NOT implemented yet (deferred, PRD amendment §11):
        pending outbox work is re-keyed with a warning instead of drained."""

        cur = await conn.execute(
            """
            SELECT count(*) AS pending FROM runtime_outbox_events
             WHERE org_id = %s AND status IN ('pending', 'claimed', 'retry')
            """,
            (self._absorbed_org,),
        )
        row = await cur.fetchone()
        pending = int(row["pending"]) if row else 0
        if pending:
            self._warn(
                f"queue_not_drained: {pending} pending outbox row(s) for the "
                "absorbed org were re-keyed WITHOUT a drain (FR-M6 quiesce is "
                "deferred) — verify their commands re-execute correctly"
            )

    # ----- conversations / runs (idempotency uniques + runtime context) ---

    async def _null_colliding_idempotency(
        self, conn: psycopg.AsyncConnection, table: str
    ) -> None:
        """NULL absorbed idempotency keys that would collide post-move.

        ``(org_id, user_id, idempotency_key)`` is unique on both
        ``agent_conversations`` and ``agent_runs``. Idempotency keys dedup
        retries within one account; dropping the key (never the row) is the
        conservative resolution.
        """

        nulled = await self._execute(
            conn,
            f"""
            UPDATE {table} AS a SET idempotency_key = NULL
             WHERE a.org_id = %(absorbed_org)s
               AND a.idempotency_key IS NOT NULL
               AND EXISTS (
                    SELECT 1 FROM {table} AS s
                     WHERE s.org_id = %(survivor_org)s
                       AND s.user_id = %(survivor_user)s
                       AND s.idempotency_key = a.idempotency_key
               )
            """,
            {
                "absorbed_org": self._absorbed_org,
                "survivor_org": self._survivor_org,
                "survivor_user": self._survivor_user,
            },
        )
        if nulled:
            self._warn(
                f"{table}: cleared {nulled} colliding idempotency key(s) "
                "(rows kept; survivor keys win)"
            )

    async def _rekey_conversations(self, conn: psycopg.AsyncConnection) -> None:
        await self._null_colliding_idempotency(conn, "agent_conversations")
        await conn.execute(
            "UPDATE agent_conversations SET user_id = %s WHERE org_id = %s AND user_id = %s",
            (self._survivor_user, self._absorbed_org, self._absorbed_user),
        )
        moved = await self._execute(
            conn,
            "UPDATE agent_conversations SET org_id = %s WHERE org_id = %s",
            (self._survivor_org, self._absorbed_org),
        )
        self._count("agent_conversations", moved)

    async def _rekey_runs(self, conn: psycopg.AsyncConnection) -> None:
        """Re-key runs, including the persisted ``runtime_context_json`` identity.

        The run's frozen runtime context snapshot embeds ``org_id`` /
        ``user_id``; leaving them stale would break scoped re-reads and
        cross-turn context assembly after the merge.
        """

        await self._null_colliding_idempotency(conn, "agent_runs")
        await conn.execute(
            """
            UPDATE agent_runs
               SET runtime_context_json = jsonb_set(
                       runtime_context_json, '{user_id}', to_jsonb(%s::text), false)
             WHERE org_id = %s AND runtime_context_json->>'user_id' = %s
            """,
            (self._survivor_user, self._absorbed_org, self._absorbed_user),
        )
        await conn.execute(
            """
            UPDATE agent_runs
               SET runtime_context_json = jsonb_set(
                       runtime_context_json, '{org_id}', to_jsonb(%s::text), false)
             WHERE org_id = %s AND runtime_context_json->>'org_id' = %s
            """,
            (self._survivor_org, self._absorbed_org, self._absorbed_org),
        )
        await conn.execute(
            "UPDATE agent_runs SET user_id = %s WHERE org_id = %s AND user_id = %s",
            (self._survivor_user, self._absorbed_org, self._absorbed_user),
        )
        moved = await self._execute(
            conn,
            "UPDATE agent_runs SET org_id = %s WHERE org_id = %s",
            (self._survivor_org, self._absorbed_org),
        )
        self._count("agent_runs", moved)

    # ----- encrypted-column tables ----------------------------------------

    def _rewrap_text(
        self, stored: str | None, *, table: str, column: str
    ) -> str | None:
        """Decrypt a v1 text envelope with the absorbed AAD, re-encrypt with the survivor's.

        Non-envelope values (retention placeholders, legacy plaintext on a
        v1 row) pass through unchanged — they carry no AAD binding.
        """

        if stored is None or not stored.startswith("v1:"):
            return stored
        plaintext = self._codec.decrypt_text(
            stored,
            encryption_version=1,
            table=table,
            column=column,
            org_id=self._absorbed_org,
        )
        return self._codec.encrypt_text(
            plaintext, table=table, column=column, org_id=self._survivor_org
        )

    def _rewrap_jsonb(self, stored: Any, *, table: str, column: str) -> Any:
        """Re-wrap a ``{"$enc": ...}`` JSONB envelope; pass through anything else."""

        if not (
            isinstance(stored, dict)
            and len(stored) == 1
            and isinstance(stored.get("$enc"), str)
        ):
            return stored
        plaintext = self._codec.decrypt_jsonb(
            stored,
            encryption_version=1,
            table=table,
            column=column,
            org_id=self._absorbed_org,
        )
        return self._codec.encrypt_jsonb(
            plaintext, table=table, column=column, org_id=self._survivor_org
        )

    async def _warn_unrewrappable_v1_rows(
        self, conn: psycopg.AsyncConnection, table: str
    ) -> None:
        """With the Null adapter, v1 rows move without re-wrap — flag them."""

        cur = await conn.execute(
            f"SELECT count(*) AS n FROM {table} WHERE org_id = %s AND encryption_version = 1",
            (self._absorbed_org,),
        )
        row = await cur.fetchone()
        stuck = int(row["n"]) if row else 0
        if stuck:
            self._warn(
                f"{table}: {stuck} envelope-v1 row(s) moved without AAD "
                "re-wrap (field encryption disabled in this process); they "
                "will not decrypt under the survivor org until re-wrapped"
            )

    async def _rekey_encrypted_messages(self, conn: psycopg.AsyncConnection) -> None:
        """Move ``agent_messages`` re-wrapping content/metadata envelopes."""

        table = "agent_messages"
        if not self._codec.is_envelope_v1:
            await self._warn_unrewrappable_v1_rows(conn, table)
        else:
            cur = await conn.execute(
                """
                SELECT id, content_text, content_json, metadata_json
                  FROM agent_messages
                 WHERE org_id = %s AND encryption_version = 1
                """,
                (self._absorbed_org,),
            )
            for row in await cur.fetchall():
                await conn.execute(
                    """
                    UPDATE agent_messages
                       SET content_text = %s, content_json = %s, metadata_json = %s
                     WHERE id = %s
                    """,
                    (
                        self._rewrap_text(
                            row["content_text"], table=table, column="content_text"
                        ),
                        self._jsonb(
                            self._rewrap_jsonb(
                                row["content_json"], table=table, column="content_json"
                            )
                        ),
                        self._jsonb(
                            self._rewrap_jsonb(
                                row["metadata_json"],
                                table=table,
                                column="metadata_json",
                            )
                        ),
                        row["id"],
                    ),
                )
        moved = await self._execute(
            conn,
            "UPDATE agent_messages SET org_id = %s WHERE org_id = %s",
            (self._survivor_org, self._absorbed_org),
        )
        self._count(table, moved)

    async def _rekey_encrypted_extras(self, conn: psycopg.AsyncConnection) -> None:
        """Move the remaining 0011 encryption-target tables with a v1 guard.

        ``runtime_subagent_results.response_text`` (text envelope) and
        ``runtime_tool_invocations.args_json_redacted`` /
        ``result_summary_json_redacted`` (``$enc`` jsonb) re-wrap like the
        message columns. ``runtime_context_payload_blobs.encrypted_blob`` has
        NO live writer defining its byte format yet — v1 rows there always
        census-warn (operator re-wrap) rather than guessing an encoding.
        """

        table = "runtime_subagent_results"
        if not self._codec.is_envelope_v1:
            await self._warn_unrewrappable_v1_rows(conn, table)
        else:
            cur = await conn.execute(
                """
                SELECT id, response_text
                  FROM runtime_subagent_results
                 WHERE org_id = %s AND encryption_version = 1
                """,
                (self._absorbed_org,),
            )
            for row in await cur.fetchall():
                await conn.execute(
                    "UPDATE runtime_subagent_results SET response_text = %s "
                    "WHERE id = %s",
                    (
                        self._rewrap_text(
                            row["response_text"],
                            table=table,
                            column="response_text",
                        ),
                        row["id"],
                    ),
                )
        moved = await self._execute(
            conn,
            "UPDATE runtime_subagent_results SET org_id = %s WHERE org_id = %s",
            (self._survivor_org, self._absorbed_org),
        )
        self._count(table, moved)

        table = "runtime_tool_invocations"
        if not self._codec.is_envelope_v1:
            await self._warn_unrewrappable_v1_rows(conn, table)
        else:
            cur = await conn.execute(
                """
                SELECT id, args_json_redacted, result_summary_json_redacted
                  FROM runtime_tool_invocations
                 WHERE org_id = %s AND encryption_version = 1
                """,
                (self._absorbed_org,),
            )
            for row in await cur.fetchall():
                await conn.execute(
                    """
                    UPDATE runtime_tool_invocations
                       SET args_json_redacted = %s,
                           result_summary_json_redacted = %s
                     WHERE id = %s
                    """,
                    (
                        self._jsonb(
                            self._rewrap_jsonb(
                                row["args_json_redacted"],
                                table=table,
                                column="args_json_redacted",
                            )
                        ),
                        self._jsonb(
                            self._rewrap_jsonb(
                                row["result_summary_json_redacted"],
                                table=table,
                                column="result_summary_json_redacted",
                            )
                        ),
                        row["id"],
                    ),
                )
        moved = await self._execute(
            conn,
            "UPDATE runtime_tool_invocations SET org_id = %s WHERE org_id = %s",
            (self._survivor_org, self._absorbed_org),
        )
        self._count(table, moved)

        # Blob store: no writer defines the byte format yet — never guess.
        table = "runtime_context_payload_blobs"
        await self._warn_unrewrappable_v1_rows(conn, table)
        moved = await self._execute(
            conn,
            "UPDATE runtime_context_payload_blobs SET org_id = %s WHERE org_id = %s",
            (self._survivor_org, self._absorbed_org),
        )
        self._count(table, moved)

    async def _rekey_encrypted_events(self, conn: psycopg.AsyncConnection) -> None:
        """Move ``runtime_events`` re-wrapping payload/metadata envelopes.

        Row ids and ``sequence_no`` are untouched — replay ordering and SSE
        resume cursors survive the merge byte-identically.
        """

        table = "runtime_events"
        if not self._codec.is_envelope_v1:
            await self._warn_unrewrappable_v1_rows(conn, table)
        else:
            cur = await conn.execute(
                """
                SELECT id, payload_json_redacted, metadata_json_redacted
                  FROM runtime_events
                 WHERE org_id = %s AND encryption_version = 1
                """,
                (self._absorbed_org,),
            )
            for row in await cur.fetchall():
                await conn.execute(
                    """
                    UPDATE runtime_events
                       SET payload_json_redacted = %s, metadata_json_redacted = %s
                     WHERE id = %s
                    """,
                    (
                        self._jsonb(
                            self._rewrap_jsonb(
                                row["payload_json_redacted"],
                                table=table,
                                column="payload_json_redacted",
                            )
                        ),
                        self._jsonb(
                            self._rewrap_jsonb(
                                row["metadata_json_redacted"],
                                table=table,
                                column="metadata_json_redacted",
                            )
                        ),
                        row["id"],
                    ),
                )
        moved = await self._execute(
            conn,
            "UPDATE runtime_events SET org_id = %s WHERE org_id = %s",
            (self._survivor_org, self._absorbed_org),
        )
        self._count(table, moved)

    async def _rekey_encrypted_citations(self, conn: psycopg.AsyncConnection) -> None:
        """Move ``runtime_citations`` re-wrapping title/snippet envelopes."""

        table = "runtime_citations"
        if not self._codec.is_envelope_v1:
            await self._warn_unrewrappable_v1_rows(conn, table)
        else:
            cur = await conn.execute(
                """
                SELECT run_id, citation_id, title, snippet
                  FROM runtime_citations
                 WHERE org_id = %s AND encryption_version = 1
                """,
                (self._absorbed_org,),
            )
            for row in await cur.fetchall():
                await conn.execute(
                    """
                    UPDATE runtime_citations SET title = %s, snippet = %s
                     WHERE run_id = %s AND citation_id = %s
                    """,
                    (
                        self._rewrap_text(row["title"], table=table, column="title"),
                        self._rewrap_text(
                            row["snippet"], table=table, column="snippet"
                        ),
                        row["run_id"],
                        row["citation_id"],
                    ),
                )
        moved = await self._execute(
            conn,
            "UPDATE runtime_citations SET org_id = %s WHERE org_id = %s",
            (self._survivor_org, self._absorbed_org),
        )
        self._count(table, moved)

    async def _rekey_drafts(self, conn: psycopg.AsyncConnection) -> None:
        """Move ``runtime_drafts`` (BYTEA-encoded envelopes; draft-id unique).

        The draft store encodes envelope strings as UTF-8 bytes into BYTEA
        columns (``target_metadata`` holds JSON of the envelope dict) — the
        re-wrap mirrors that encoding exactly.
        """

        table = "runtime_drafts"
        deleted = await self._execute(
            conn,
            """
            DELETE FROM runtime_drafts AS a
             WHERE a.org_id = %(absorbed_org)s
               AND EXISTS (
                    SELECT 1 FROM runtime_drafts AS s
                     WHERE s.org_id = %(survivor_org)s
                       AND s.draft_id = a.draft_id
                       AND s.version = a.version
               )
            """,
            {
                "absorbed_org": self._absorbed_org,
                "survivor_org": self._survivor_org,
            },
        )
        if deleted:
            self._warn(
                f"runtime_drafts: deleted {deleted} absorbed draft version(s) "
                "colliding on (draft_id, version) — survivor rows win"
            )
        if not self._codec.is_envelope_v1:
            await self._warn_unrewrappable_v1_rows(conn, table)
        else:
            cur = await conn.execute(
                """
                SELECT id, title, content_text, target_metadata
                  FROM runtime_drafts
                 WHERE org_id = %s AND encryption_version = 1
                """,
                (self._absorbed_org,),
            )
            for row in await cur.fetchall():
                title = self._rewrap_bytea_text(
                    row["title"], table=table, column="title"
                )
                content = self._rewrap_bytea_text(
                    row["content_text"], table=table, column="content_text"
                )
                metadata = self._rewrap_bytea_jsonb(
                    row["target_metadata"], table=table, column="target_metadata"
                )
                await conn.execute(
                    """
                    UPDATE runtime_drafts
                       SET title = %s, content_text = %s, target_metadata = %s
                     WHERE id = %s
                    """,
                    (title, content, metadata, row["id"]),
                )
        await conn.execute(
            "UPDATE runtime_drafts SET user_id = %s WHERE org_id = %s AND user_id = %s",
            (self._survivor_user, self._absorbed_org, self._absorbed_user),
        )
        moved = await self._execute(
            conn,
            "UPDATE runtime_drafts SET org_id = %s WHERE org_id = %s",
            (self._survivor_org, self._absorbed_org),
        )
        self._count(table, moved)

    def _rewrap_bytea_text(self, stored: object, *, table: str, column: str) -> bytes:
        """Re-wrap a BYTEA column holding a UTF-8 envelope string."""

        raw = bytes(stored) if stored is not None else b""
        rewrapped = self._rewrap_text(raw.decode("utf-8"), table=table, column=column)
        return (rewrapped or "").encode("utf-8")

    def _rewrap_bytea_jsonb(
        self, stored: object, *, table: str, column: str
    ) -> bytes | None:
        """Re-wrap a BYTEA column holding JSON of an envelope dict."""

        if stored is None:
            return None
        decoded = json.loads(bytes(stored).decode("utf-8"))
        rewrapped = self._rewrap_jsonb(decoded, table=table, column=column)
        return json.dumps(rewrapped).encode("utf-8")

    @staticmethod
    def _jsonb(value: Any):
        """Adapt a Python value for a JSONB parameter."""

        from psycopg.types.json import Jsonb

        return Jsonb(value)

    # ----- structurally colliding tables ----------------------------------

    async def _rekey_memory(self, conn: psycopg.AsyncConnection) -> None:
        """Move memory scopes + items honoring the scope/namespace uniques.

        ``runtime_memory_scopes`` is unique on ``(org_id, scope_type,
        namespace_hash)``. When the survivor already has the same scope, the
        absorbed scope's items re-point to the survivor scope (dropping any
        item whose active ``(scope_id, path)`` slot is already taken —
        survivor wins) and the absorbed scope row is deleted.
        """

        cur = await conn.execute(
            """
            SELECT a.id AS absorbed_scope_id, s.id AS survivor_scope_id
              FROM runtime_memory_scopes AS a
              JOIN runtime_memory_scopes AS s
                ON s.org_id = %(survivor_org)s
               AND s.scope_type = a.scope_type
               AND s.namespace_hash = a.namespace_hash
             WHERE a.org_id = %(absorbed_org)s
            """,
            {
                "absorbed_org": self._absorbed_org,
                "survivor_org": self._survivor_org,
            },
        )
        for row in await cur.fetchall():
            dropped = await self._execute(
                conn,
                """
                DELETE FROM runtime_memory_items AS a
                 WHERE a.scope_id = %(absorbed_scope)s
                   AND a.deleted_at IS NULL
                   AND EXISTS (
                        SELECT 1 FROM runtime_memory_items AS s
                         WHERE s.scope_id = %(survivor_scope)s
                           AND s.path = a.path
                           AND s.deleted_at IS NULL
                   )
                """,
                {
                    "absorbed_scope": row["absorbed_scope_id"],
                    "survivor_scope": row["survivor_scope_id"],
                },
            )
            if dropped:
                self._warn(
                    f"runtime_memory_items: dropped {dropped} absorbed item(s) "
                    "colliding on (scope, path) — survivor items win"
                )
            await conn.execute(
                "UPDATE runtime_memory_items SET scope_id = %s WHERE scope_id = %s",
                (row["survivor_scope_id"], row["absorbed_scope_id"]),
            )
            await conn.execute(
                "DELETE FROM runtime_memory_scopes WHERE id = %s",
                (row["absorbed_scope_id"],),
            )
            self._warn(
                "runtime_memory_scopes: merged absorbed scope "
                f"{row['absorbed_scope_id']!r} into survivor scope "
                f"{row['survivor_scope_id']!r}"
            )
        await conn.execute(
            "UPDATE runtime_memory_scopes SET user_id = %s WHERE org_id = %s AND user_id = %s",
            (self._survivor_user, self._absorbed_org, self._absorbed_user),
        )
        moved_scopes = await self._execute(
            conn,
            "UPDATE runtime_memory_scopes SET org_id = %s WHERE org_id = %s",
            (self._survivor_org, self._absorbed_org),
        )
        self._count("runtime_memory_scopes", moved_scopes)
        # ``runtime_memory_items.content_summary`` is a 0011 encryption
        # target; no adapter writes v1 envelopes into it today, so a plain
        # move is correct. The v1 guard keeps us honest if that changes.
        if not self._codec.is_envelope_v1:
            await self._warn_unrewrappable_v1_rows(conn, "runtime_memory_items")
        else:
            cur = await conn.execute(
                """
                SELECT id, content_summary FROM runtime_memory_items
                 WHERE org_id = %s AND encryption_version = 1
                """,
                (self._absorbed_org,),
            )
            for row in await cur.fetchall():
                await conn.execute(
                    "UPDATE runtime_memory_items SET content_summary = %s WHERE id = %s",
                    (
                        self._rewrap_text(
                            row["content_summary"],
                            table="runtime_memory_items",
                            column="content_summary",
                        ),
                        row["id"],
                    ),
                )
        moved_items = await self._execute(
            conn,
            "UPDATE runtime_memory_items SET org_id = %s WHERE org_id = %s",
            (self._survivor_org, self._absorbed_org),
        )
        self._count("runtime_memory_items", moved_items)

    async def _rekey_share_recipients(self, conn: psycopg.AsyncConnection) -> None:
        """Rewrite recipient user ids on the merged account's shares.

        Scoped to shares in either merged org (``conversation_shares``
        itself is re-keyed by the simple pass). PK ``(share_id, user_id)``
        collisions — the survivor already a recipient — drop the absorbed
        row.
        """

        deleted = await self._execute(
            conn,
            """
            DELETE FROM conversation_share_recipients AS r
             WHERE r.user_id = %(absorbed_user)s
               AND EXISTS (
                    SELECT 1 FROM conversation_shares AS sh
                     WHERE sh.share_id = r.share_id
                       AND sh.org_id IN (%(absorbed_org)s, %(survivor_org)s)
               )
               AND EXISTS (
                    SELECT 1 FROM conversation_share_recipients AS r2
                     WHERE r2.share_id = r.share_id
                       AND r2.user_id = %(survivor_user)s
               )
            """,
            {
                "absorbed_user": self._absorbed_user,
                "survivor_user": self._survivor_user,
                "absorbed_org": self._absorbed_org,
                "survivor_org": self._survivor_org,
            },
        )
        if deleted:
            self._warn(
                f"conversation_share_recipients: dropped {deleted} absorbed "
                "recipient row(s) — survivor recipient wins"
            )
        moved = await self._execute(
            conn,
            """
            UPDATE conversation_share_recipients AS r SET user_id = %(survivor_user)s
             WHERE r.user_id = %(absorbed_user)s
               AND EXISTS (
                    SELECT 1 FROM conversation_shares AS sh
                     WHERE sh.share_id = r.share_id
                       AND sh.org_id IN (%(absorbed_org)s, %(survivor_org)s)
               )
            """,
            {
                "absorbed_user": self._absorbed_user,
                "survivor_user": self._survivor_user,
                "absorbed_org": self._absorbed_org,
                "survivor_org": self._survivor_org,
            },
        )
        self._count("conversation_share_recipients", moved)

    async def _rekey_checkpoints(self, conn: psycopg.AsyncConnection) -> None:
        """Move checkpoints; ``(org, thread, namespace, version)`` collisions drop."""

        deleted = await self._execute(
            conn,
            """
            DELETE FROM runtime_checkpoints AS a
             WHERE a.org_id = %(absorbed_org)s
               AND EXISTS (
                    SELECT 1 FROM runtime_checkpoints AS s
                     WHERE s.org_id = %(survivor_org)s
                       AND s.thread_id = a.thread_id
                       AND s.checkpoint_namespace = a.checkpoint_namespace
                       AND s.checkpoint_version = a.checkpoint_version
               )
            """,
            {
                "absorbed_org": self._absorbed_org,
                "survivor_org": self._survivor_org,
            },
        )
        if deleted:
            self._warn(
                f"runtime_checkpoints: dropped {deleted} absorbed checkpoint(s) "
                "colliding on (thread, namespace, version) — survivor wins"
            )
        moved = await self._execute(
            conn,
            "UPDATE runtime_checkpoints SET org_id = %s WHERE org_id = %s",
            (self._survivor_org, self._absorbed_org),
        )
        self._count("runtime_checkpoints", moved)

    async def _rekey_rollup(
        self,
        conn: psycopg.AsyncConnection,
        table: str,
        key_columns: tuple[str, ...],
        sum_columns: tuple[str, ...],
    ) -> None:
        """Move rollup rows; PK collisions SUM-merge into the survivor row.

        SUM-merge is trivially correct for additive counters; the same-key
        survivor row absorbs the counts, ``distinct_users`` takes the max
        (both accounts are one human post-merge), ``cost_micro_usd`` adds
        None-aware, and ``refreshed_at`` keeps the later stamp.
        """

        # ``user_id`` in the key means the row must land on the survivor
        # *user* as well as the survivor org.
        rekey_user = "user_id" in key_columns
        survivor_key_match = " AND ".join(
            f"s.{column} = a.{column}" for column in key_columns if column != "user_id"
        )
        if rekey_user:
            survivor_key_match += " AND s.user_id = %(survivor_user)s"
        set_sums = ", ".join(
            f"{column} = s.{column} + a.{column}" for column in sum_columns
        )
        set_extra = (
            ", cost_micro_usd = COALESCE(s.cost_micro_usd, 0) + COALESCE(a.cost_micro_usd, 0)"
            ", refreshed_at = GREATEST(s.refreshed_at, a.refreshed_at)"
        )
        # Only the org + connector rollups carry ``distinct_users`` (0007/0024).
        if table in {"runtime_usage_daily_org", "runtime_usage_daily_connector"}:
            set_extra += (
                ", distinct_users = GREATEST(s.distinct_users, a.distinct_users)"
            )
        absorbed_filter = "a.org_id = %(absorbed_org)s" + (
            " AND a.user_id = %(absorbed_user)s" if rekey_user else ""
        )
        params = {
            "absorbed_org": self._absorbed_org,
            "survivor_org": self._survivor_org,
            "absorbed_user": self._absorbed_user,
            "survivor_user": self._survivor_user,
        }
        merged = await self._execute(
            conn,
            f"""
            UPDATE {table} AS s
               SET {set_sums}{set_extra}
              FROM {table} AS a
             WHERE s.org_id = %(survivor_org)s
               AND {absorbed_filter}
               AND {survivor_key_match}
            """,
            params,
        )
        if merged:
            await conn.execute(
                f"""
                DELETE FROM {table} AS a
                 WHERE {absorbed_filter}
                   AND EXISTS (
                        SELECT 1 FROM {table} AS s
                         WHERE s.org_id = %(survivor_org)s
                           AND {survivor_key_match}
                   )
                """,
                params,
            )
            self._warn(f"{table}: SUM-merged {merged} colliding rollup row(s)")
        set_clause = "org_id = %(survivor_org)s" + (
            ", user_id = %(survivor_user)s" if rekey_user else ""
        )
        moved = await self._execute(
            conn,
            f"UPDATE {table} AS a SET {set_clause} WHERE {absorbed_filter}",
            params,
        )
        self._count(table, moved + merged)

    async def _rekey_usage_budgets(self, conn: psycopg.AsyncConnection) -> None:
        """Move budgets; the survivor's ``(user-slot, scope, period)`` row wins.

        Deleting an absorbed budget cascades to its state and reservation
        rows (``ON DELETE CASCADE`` in migration 0009).
        """

        deleted = await self._execute(
            conn,
            """
            DELETE FROM usage_budgets AS a
             WHERE a.org_id = %(absorbed_org)s
               AND EXISTS (
                    SELECT 1 FROM usage_budgets AS s
                     WHERE s.org_id = %(survivor_org)s
                       AND COALESCE(s.user_id, '<org>') = COALESCE(
                            CASE WHEN a.user_id = %(absorbed_user)s
                                 THEN %(survivor_user)s ELSE a.user_id END,
                            '<org>')
                       AND s.scope = a.scope
                       AND s.period = a.period
               )
            """,
            {
                "absorbed_org": self._absorbed_org,
                "survivor_org": self._survivor_org,
                "absorbed_user": self._absorbed_user,
                "survivor_user": self._survivor_user,
            },
        )
        if deleted:
            self._warn(
                f"usage_budgets: dropped {deleted} absorbed budget(s) whose "
                "slot the survivor already occupies (state/reservations cascade)"
            )
        for column in ("user_id", "created_by_user_id"):
            await conn.execute(
                f"UPDATE usage_budgets SET {column} = %s WHERE org_id = %s AND {column} = %s",
                (self._survivor_user, self._absorbed_org, self._absorbed_user),
            )
        moved = await self._execute(
            conn,
            "UPDATE usage_budgets SET org_id = %s WHERE org_id = %s",
            (self._survivor_org, self._absorbed_org),
        )
        self._count("usage_budgets", moved)

    async def _rekey_tool_budgets(self, conn: psycopg.AsyncConnection) -> None:
        """Move per-tool budgets; the survivor's ``tool_name`` slot wins."""

        deleted = await self._execute(
            conn,
            """
            DELETE FROM runtime_tool_budgets AS a
             WHERE a.org_id = %(absorbed_org)s
               AND EXISTS (
                    SELECT 1 FROM runtime_tool_budgets AS s
                     WHERE s.org_id = %(survivor_org)s
                       AND s.tool_name = a.tool_name
               )
            """,
            {
                "absorbed_org": self._absorbed_org,
                "survivor_org": self._survivor_org,
            },
        )
        if deleted:
            self._warn(
                f"runtime_tool_budgets: dropped {deleted} absorbed tool "
                "budget(s) — survivor slot wins"
            )
        moved = await self._execute(
            conn,
            "UPDATE runtime_tool_budgets SET org_id = %s WHERE org_id = %s",
            (self._survivor_org, self._absorbed_org),
        )
        self._count("runtime_tool_budgets", moved)

    async def _rekey_retention_policies(self, conn: psycopg.AsyncConnection) -> None:
        """Move retention policies; survivor's ``(scope, resource, kind)`` wins."""

        deleted = await self._execute(
            conn,
            """
            DELETE FROM retention_policies AS a
             WHERE a.org_id = %(absorbed_org)s
               AND EXISTS (
                    SELECT 1 FROM retention_policies AS s
                     WHERE s.org_id = %(survivor_org)s
                       AND s.scope = a.scope
                       AND COALESCE(s.resource_id, '') = COALESCE(a.resource_id, '')
                       AND s.kind = a.kind
               )
            """,
            {
                "absorbed_org": self._absorbed_org,
                "survivor_org": self._survivor_org,
            },
        )
        if deleted:
            self._warn(
                f"retention_policies: dropped {deleted} absorbed policy(ies) "
                "— survivor policies win"
            )
        await conn.execute(
            """
            UPDATE retention_policies SET created_by_user_id = %s
             WHERE org_id = %s AND created_by_user_id = %s
            """,
            (self._survivor_user, self._absorbed_org, self._absorbed_user),
        )
        moved = await self._execute(
            conn,
            "UPDATE retention_policies SET org_id = %s WHERE org_id = %s",
            (self._survivor_org, self._absorbed_org),
        )
        self._count("retention_policies", moved)

    async def _rekey_workspace_defaults(self, conn: psycopg.AsyncConnection) -> None:
        """Move the single per-org defaults row; the survivor's row wins."""

        deleted = await self._execute(
            conn,
            """
            DELETE FROM workspace_defaults
             WHERE org_id = %(absorbed_org)s
               AND EXISTS (
                    SELECT 1 FROM workspace_defaults WHERE org_id = %(survivor_org)s
               )
            """,
            {
                "absorbed_org": self._absorbed_org,
                "survivor_org": self._survivor_org,
            },
        )
        if deleted:
            self._warn(
                "workspace_defaults: dropped the absorbed row — survivor "
                "workspace defaults win"
            )
            return
        await conn.execute(
            """
            UPDATE workspace_defaults SET updated_by_user_id = %s
             WHERE org_id = %s AND updated_by_user_id = %s
            """,
            (self._survivor_user, self._absorbed_org, self._absorbed_user),
        )
        moved = await self._execute(
            conn,
            "UPDATE workspace_defaults SET org_id = %s WHERE org_id = %s",
            (self._survivor_org, self._absorbed_org),
        )
        self._count("workspace_defaults", moved)


__all__ = ("PostgresAccountMergeRekeyer",)
