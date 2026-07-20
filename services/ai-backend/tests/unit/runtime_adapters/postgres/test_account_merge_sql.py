"""DB-free SQL guards for the Postgres account-merge re-keyer.

The real cross-tenant behavior (RLS context, encryption AAD re-wrap) is
gated on a live-Postgres run per the account-linking PRD §8; these tests
execute every statement-building path against a recording fake connection
and enforce the merge's structural invariants:

- ``runtime_audit_log`` is never the target of an UPDATE or DELETE (the
  chain is append-only; migration 0003 would reject it at the DB anyway);
- every mutated table is one that actually exists in ``migrations/*.sql``;
- both the Null-codec and envelope-v1 paths run to completion.
"""

from __future__ import annotations

import re
from contextlib import asynccontextmanager

from agent_runtime.persistence.encryption import (
    FieldCodec,
    NullFieldEncryption,
)
from runtime_adapters.postgres.account_merge import PostgresAccountMergeRekeyer

#: Every table the migrations define (0001..0032) that the merge may touch.
_MIGRATION_TABLES = {
    "agent_conversations",
    "agent_messages",
    "agent_runs",
    "runtime_events",
    "runtime_outbox_events",
    "runtime_async_tasks",
    "runtime_subagent_results",
    "runtime_tool_invocations",
    "runtime_approval_requests",
    "runtime_approval_batches",
    "runtime_memory_scopes",
    "runtime_memory_items",
    "runtime_context_payloads",
    "runtime_context_payload_blobs",
    "runtime_compression_events",
    "runtime_capability_snapshots",
    "runtime_legal_holds",
    "runtime_deletion_evidence",
    "runtime_checkpoints",
    "runtime_run_usage",
    "runtime_model_call_usage",
    "runtime_usage_daily_user",
    "runtime_usage_daily_org",
    "runtime_usage_daily_connector",
    "runtime_usage_daily_subagent",
    "runtime_usage_daily_purpose",
    "usage_budgets",
    "runtime_tool_budgets",
    "retention_policies",
    "workspace_defaults",
    "runtime_drafts",
    "runtime_citations",
    "conversation_shares",
    "conversation_share_recipients",
    "agent_conversation_tool_ordinals",
    "todo_extractions",
}

_MUTATION_TARGET = re.compile(r"^\s*(?:UPDATE|DELETE\s+FROM)\s+([a-z_]+)", re.I | re.M)


class _FakeCursor:
    """Cursor stub: zero rowcount, empty result sets, scalar-count rows."""

    rowcount = 0

    async def fetchone(self):
        # Both scalar-count reads (outbox pending, v1-row census) use one
        # of these keys; returning zeros keeps every branch quiet.
        return {"pending": 0, "n": 0}

    async def fetchall(self):
        return []


class _FakeConnection:
    """Records every executed statement for structural assertions."""

    def __init__(self) -> None:
        self.statements: list[str] = []

    async def execute(self, sql: str, params=None):
        del params
        self.statements.append(sql)
        return _FakeCursor()

    @asynccontextmanager
    async def transaction(self):
        yield


class _FakeStore:
    """Just enough of ``PostgresRuntimeApiStore`` for the re-keyer."""

    def __init__(self, codec: FieldCodec) -> None:
        self._codec = codec
        self.connection = _FakeConnection()

    @asynccontextmanager
    async def _role_connection(self, role: str):
        assert role == "worker"
        yield self.connection


class _EnvelopeV1Codec(FieldCodec):
    """Null-encryption codec that reports v1 so the re-wrap branches run."""

    @property
    def is_envelope_v1(self) -> bool:
        return True


class TestPostgresAccountMergeSql:
    async def _run(self, codec: FieldCodec) -> list[str]:
        store = _FakeStore(codec)
        rekeyer = PostgresAccountMergeRekeyer(store)  # type: ignore[arg-type]
        tables, warnings = await rekeyer.rekey(
            absorbed_org_id="org_absorbed",
            absorbed_user_id="user_absorbed",
            survivor_org_id="org_survivor",
            survivor_user_id="user_survivor",
        )
        # Zero-rowcount fakes mean an empty (idempotent-noop-shaped) result.
        assert tables == {}
        assert warnings == []
        return store.connection.statements

    async def test_never_mutates_the_audit_log(self) -> None:
        for codec in (
            FieldCodec(NullFieldEncryption()),
            _EnvelopeV1Codec(NullFieldEncryption()),
        ):
            for sql in await self._run(codec):
                for target in _MUTATION_TARGET.findall(sql):
                    assert target != "runtime_audit_log"

    async def test_only_migration_tables_are_mutated(self) -> None:
        statements = await self._run(FieldCodec(NullFieldEncryption()))
        targets = {
            target for sql in statements for target in _MUTATION_TARGET.findall(sql)
        }
        assert targets, "re-keyer must issue mutations"
        assert targets <= _MIGRATION_TABLES

    async def test_every_tenant_table_is_covered(self) -> None:
        """Each migration-defined tenant table appears in at least one mutation.

        Guards against a new tenant table silently escaping the merge: add
        the table to the re-keyer AND to ``_MIGRATION_TABLES`` here when a
        migration introduces one.
        """

        statements = await self._run(FieldCodec(NullFieldEncryption()))
        targets = {
            target for sql in statements for target in _MUTATION_TARGET.findall(sql)
        }
        # usage_budget_state / usage_budget_reservations follow their budget
        # via ON DELETE CASCADE and carry no org column; consumer cursors and
        # model_pricing carry no tenancy at all.
        assert _MIGRATION_TABLES - targets == set()
