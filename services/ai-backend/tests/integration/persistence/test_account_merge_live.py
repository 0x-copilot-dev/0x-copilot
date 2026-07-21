"""Live-Postgres NFR-4 gate for the account-merge re-keyer (account-linking PRD §8).

Skipped when ``MERGE_LIVE_TEST_DATABASE_URL`` is unset — the decrypt smoke on
migrated encrypted rows can only be observed against a real Postgres with the
ai-backend schema applied. The env var is deliberately separate from
``TEST_DATABASE_URL``: the postgres adapter unit suite truncates tables
aggressively between tests, and the two gates must never share a database by
accident. Point it at a disposable database, e.g.::

    MERGE_LIVE_TEST_DATABASE_URL=postgresql://postgres@127.0.0.1:55433/merge_runtime_test

What this file proves (each is a PRD §8 production-gate assertion):

1. Rows seeded through the store's own APIs with a REAL
   ``EnvelopeFieldEncryption`` codec land as genuine v1 AES-256-GCM
   envelopes (raw SQL shows ciphertext, not plaintext).
2. ``PostgresAccountMergeRekeyer.rekey`` moves the absorbed account to the
   survivor and the DECRYPTED plaintext round-trips exactly when read back
   through the store as the survivor org (NFR-4 decrypt smoke).
3. The AAD re-wrap really happened: the moved ciphertext refuses to decrypt
   under the absorbed org's AAD and succeeds under the survivor's.
4. A decoy account is untouched; ``runtime_events`` ids / sequence numbers
   survive byte-identically; ``runtime_audit_log`` keeps its original org;
   a second ``rekey`` is a no-op.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator, Iterator

import psycopg
import pytest
from psycopg.rows import dict_row

from agent_runtime.execution.contracts import AgentRuntimeContext, StreamEventSource
from agent_runtime.persistence.encryption import (
    CiphertextDecodeError,
    EnvelopeFieldEncryption,
    FieldCodec,
)
from runtime_adapters.postgres import PostgresRuntimeApiStore
from runtime_adapters.postgres.account_merge import PostgresAccountMergeRekeyer
from runtime_api.schemas import (
    CreateConversationRequest,
    CreateRunRequest,
    MessageRecord,
    MessageRole,
    RuntimeApiEventType,
    RuntimeEventDraft,
    RuntimeRequestContext,
)


pytestmark = pytest.mark.skipif(
    not os.environ.get("MERGE_LIVE_TEST_DATABASE_URL"),
    reason=(
        "Set MERGE_LIVE_TEST_DATABASE_URL to a disposable Postgres database "
        "to exercise the account-merge live re-key gate (PRD §8 / NFR-4)."
    ),
)


class _FakeKms:
    """Stand-in for AWS KMS that wraps DEKs with a static XOR key.

    Copied from ``tests/unit/agent_runtime/persistence/test_field_encryption.py``
    — it makes :class:`EnvelopeFieldEncryption` produce REAL AES-256-GCM
    envelopes with a locally derivable KEK, so the AAD binding under test is
    the production code path, not a stub.
    """

    _STATIC_WRAP_KEY = bytes.fromhex("a5" * 32)

    def __init__(self, *, fail_after: int | None = None) -> None:
        self.calls = 0
        self._fail_after = fail_after

    def _maybe_fail(self) -> None:
        if self._fail_after is not None and self.calls >= self._fail_after:
            raise RuntimeError("KMS unavailable (fake)")
        self.calls += 1

    def wrap_data_key(self, plaintext_dek: bytes) -> tuple[bytes, str]:
        self._maybe_fail()
        wrapped = bytes(a ^ b for a, b in zip(plaintext_dek, self._STATIC_WRAP_KEY))
        return wrapped, "alias/test-cmk"

    def unwrap_data_key(self, wrapped_dek: bytes, *, key_id: str | None) -> bytes:
        self._maybe_fail()
        return bytes(a ^ b for a, b in zip(wrapped_dek, self._STATIC_WRAP_KEY))


# Tables truncated around each test — same list (and idiom) as the postgres
# adapter conftest so re-runs against the disposable database stay stable.
_RUNTIME_TABLES = (
    "runtime_deletion_evidence",
    "runtime_legal_holds",
    "runtime_audit_log",
    "runtime_capability_snapshots",
    "runtime_compression_events",
    "runtime_context_payloads",
    "runtime_memory_items",
    "runtime_memory_scopes",
    "runtime_tool_invocations",
    "runtime_approval_requests",
    "runtime_subagent_results",
    "runtime_async_tasks",
    "runtime_consumer_cursors",
    "runtime_outbox_events",
    "runtime_events",
    "agent_runs",
    "agent_messages",
    "agent_conversations",
)


def _truncate_runtime_tables(database_url: str) -> None:
    statement = (
        "TRUNCATE TABLE " + ", ".join(_RUNTIME_TABLES) + " RESTART IDENTITY CASCADE"
    )
    with psycopg.connect(database_url, autocommit=True) as conn:
        # First-ever run against a fresh database: tables appear when the
        # store fixture migrates immediately afterward.
        try:
            conn.execute(statement)
        except psycopg.errors.UndefinedTable:
            pass


@pytest.fixture
def database_url() -> str:
    return os.environ["MERGE_LIVE_TEST_DATABASE_URL"]


@pytest.fixture(autouse=True)
def _clean_tables(database_url: str) -> Iterator[None]:
    """Truncate seeded tables before AND after each test (clean re-runs)."""

    _truncate_runtime_tables(database_url)
    yield
    _truncate_runtime_tables(database_url)


@pytest.fixture
def encryption() -> EnvelopeFieldEncryption:
    return EnvelopeFieldEncryption(kms_client=_FakeKms())


@pytest.fixture
def codec(encryption: EnvelopeFieldEncryption) -> FieldCodec:
    """Direct-codec handle for the raw-SQL AAD reality checks."""

    return FieldCodec(encryption)


@pytest.fixture
async def store(
    database_url: str, encryption: EnvelopeFieldEncryption
) -> AsyncIterator[PostgresRuntimeApiStore]:
    """Async store with a REAL envelope-v1 codec injected via the constructor."""

    s = PostgresRuntimeApiStore(
        database_url,
        pool_min_size=2,
        pool_max_size=10,
        pool_acquire_timeout_seconds=10.0,
        field_encryption=encryption,
    )
    await s.open()
    try:
        await s.migrate()
        yield s
    finally:
        await s.close()


@pytest.fixture
def raw(database_url: str) -> Iterator[psycopg.Connection]:
    """Autocommit dict-row connection for raw-SQL pre/post-condition checks."""

    with psycopg.connect(database_url, autocommit=True, row_factory=dict_row) as conn:
        yield conn


def _runtime_context(*, org_id: str, user_id: str, suffix: str) -> AgentRuntimeContext:
    return AgentRuntimeContext(
        user_id=user_id,
        org_id=org_id,
        roles=("Admin",),
        model_profile={
            "provider": "openai",
            "model_name": "gpt-5.4-mini",
            "max_input_tokens": 128000,
            "timeout_seconds": 30,
            "temperature": 0,
            "supports_streaming": True,
        },
        run_id=f"run_{suffix}",
        trace_id=f"trace_{suffix}",
    )


async def _seed_account(
    store: PostgresRuntimeApiStore, *, org_id: str, user_id: str, marker: str
) -> dict[str, object]:
    """Seed one account through the store's own APIs (genuine v1 envelopes).

    One conversation, a user + assistant message with non-trivial content,
    one run with several runtime_events, and one audit row. Returns the
    plaintext fixtures the assertions round-trip against.
    """

    suffix = uuid.uuid4().hex
    user_plaintext = f"user says: café Δ ünïcode marker={marker}"
    assistant_plaintext = f"assistant answers: 42 × π marker={marker}"

    conversation = await store.create_conversation(
        CreateConversationRequest(
            org_id=org_id, user_id=user_id, assistant_id=f"assistant_{suffix}"
        )
    )
    # The validator forbids client-supplied runtime_context; attach the
    # server-owned context via model_copy the way the service layer does.
    client_request = CreateRunRequest(
        conversation_id=conversation.conversation_id,
        org_id=org_id,
        user_id=user_id,
        user_input=user_plaintext,
        model={"provider": "openai", "model_name": "gpt-5.4-mini"},
        request_context=RuntimeRequestContext(
            roles=("Admin",), permission_scopes=("Search:Read",)
        ),
    )
    request = client_request.model_copy(
        update={
            "runtime_context": _runtime_context(
                org_id=org_id, user_id=user_id, suffix=suffix
            )
        }
    )
    run, user_message, _created = await store.create_run_with_user_message(
        request=request, conversation=conversation
    )

    assistant_metadata = {"marker": marker, "note": "non-trivial métadata"}
    assistant_content = ({"type": "output_text", "text": assistant_plaintext},)
    assistant_message = await store.append_message(
        MessageRecord(
            conversation_id=conversation.conversation_id,
            org_id=org_id,
            run_id=run.run_id,
            role=MessageRole.ASSISTANT,
            content_text=assistant_plaintext,
            content=assistant_content,
            metadata=assistant_metadata,
            trace_id=f"trace_{suffix}",
        )
    )

    event_payloads = []
    for i, event_type in enumerate(
        (
            RuntimeApiEventType.RUN_STARTED,
            RuntimeApiEventType.PROGRESS,
            RuntimeApiEventType.RUN_COMPLETED,
        )
    ):
        payload = {"marker": marker, "i": i, "text": f"event body {i} for {marker}"}
        envelope = await store.append_event(
            RuntimeEventDraft(
                run_id=run.run_id,
                conversation_id=conversation.conversation_id,
                org_id=org_id,
                trace_id=f"trace_{suffix}",
                source=StreamEventSource.RUNTIME,
                event_type=event_type,
                payload=payload,
                metadata={"marker": marker},
            )
        )
        event_payloads.append((envelope.sequence_no, payload))

    await store.write_audit_log(
        event_type="account_merge_live_test_seed",
        record={
            "org_id": org_id,
            "user_id": user_id,
            "actor_type": "system",
            "resource_type": "test_seed",
            "resource_id": marker,
            "outcome": "success",
            "metadata": {"marker": marker},
        },
    )

    return {
        "conversation_id": conversation.conversation_id,
        "run_id": run.run_id,
        "user_message_id": user_message.message_id,
        "assistant_message_id": assistant_message.message_id,
        "user_plaintext": user_plaintext,
        "assistant_plaintext": assistant_plaintext,
        "assistant_content": assistant_content,
        "assistant_metadata": assistant_metadata,
        "event_payloads": event_payloads,
    }


def _event_rows(raw: psycopg.Connection, org_id: str) -> dict[str, dict[str, object]]:
    cur = raw.execute(
        """
        SELECT id, run_id, conversation_id, sequence_no
          FROM runtime_events
         WHERE org_id = %s
         ORDER BY sequence_no
        """,
        (org_id,),
    )
    return {row["id"]: row for row in cur.fetchall()}


class TestAccountMergeLiveRekey:
    """PRD §8 production gate: live re-key + NFR-4 decrypt smoke."""

    async def test_rekey_moves_rewraps_and_is_idempotent(
        self,
        store: PostgresRuntimeApiStore,
        codec: FieldCodec,
        raw: psycopg.Connection,
    ) -> None:
        suffix = uuid.uuid4().hex[:12]
        absorbed_org, absorbed_user = (
            f"org_absorbed_{suffix}",
            f"user_absorbed_{suffix}",
        )
        survivor_org, survivor_user = (
            f"org_survivor_{suffix}",
            f"user_survivor_{suffix}",
        )
        decoy_org, decoy_user = f"org_decoy_{suffix}", f"user_decoy_{suffix}"

        absorbed = await _seed_account(
            store, org_id=absorbed_org, user_id=absorbed_user, marker="absorbed"
        )
        survivor = await _seed_account(
            store, org_id=survivor_org, user_id=survivor_user, marker="survivor"
        )
        decoy = await _seed_account(
            store, org_id=decoy_org, user_id=decoy_user, marker="decoy"
        )

        # ---- Pre-condition: absorbed rows are REAL v1 ciphertext ----------
        cur = raw.execute(
            "SELECT id, content_text, content_json, encryption_version "
            "FROM agent_messages WHERE org_id = %s",
            (absorbed_org,),
        )
        message_rows = cur.fetchall()
        assert len(message_rows) == 2
        for row in message_rows:
            assert row["encryption_version"] == 1
            assert row["content_text"].startswith("v1:")
            assert row["content_text"] != absorbed["user_plaintext"]
            assert row["content_text"] != absorbed["assistant_plaintext"]
            assert set(row["content_json"]) == {"$enc"}
        cur = raw.execute(
            "SELECT payload_json_redacted, encryption_version "
            "FROM runtime_events WHERE org_id = %s",
            (absorbed_org,),
        )
        event_rows = cur.fetchall()
        assert len(event_rows) == 3
        for row in event_rows:
            assert row["encryption_version"] == 1
            assert set(row["payload_json_redacted"]) == {"$enc"}

        events_before = _event_rows(raw, absorbed_org)
        assert len(events_before) == 3
        decoy_events_before = _event_rows(raw, decoy_org)

        # ---- Run the re-key ----------------------------------------------
        rekeyer = PostgresAccountMergeRekeyer(store)
        tables, warnings = await rekeyer.rekey(
            absorbed_org_id=absorbed_org,
            absorbed_user_id=absorbed_user,
            survivor_org_id=survivor_org,
            survivor_user_id=survivor_user,
        )
        assert warnings == []
        assert tables["agent_conversations"] == 1
        assert tables["agent_messages"] == 2
        assert tables["agent_runs"] == 1
        assert tables["runtime_events"] == 3
        cur = raw.execute(
            "SELECT count(*) AS n FROM agent_messages WHERE org_id = %s",
            (absorbed_org,),
        )
        assert cur.fetchone()["n"] == 0

        # ---- (a) NFR-4 decrypt smoke: read back THROUGH the store --------
        conversation = await store.get_conversation(
            org_id=survivor_org,
            user_id=survivor_user,
            conversation_id=absorbed["conversation_id"],
        )
        assert conversation is not None

        messages = await store.list_messages(
            org_id=survivor_org,
            conversation_id=absorbed["conversation_id"],
            limit=50,
        )
        by_role = {message.role: message for message in messages}
        assert by_role[MessageRole.USER].content_text == absorbed["user_plaintext"]
        assistant = by_role[MessageRole.ASSISTANT]
        assert assistant.content_text == absorbed["assistant_plaintext"]
        assert assistant.content == absorbed["assistant_content"]
        assert assistant.metadata == absorbed["assistant_metadata"]

        events = await store.list_events_after(
            org_id=survivor_org, run_id=absorbed["run_id"], after_sequence=0
        )
        assert [(event.sequence_no, event.payload) for event in events] == absorbed[
            "event_payloads"
        ]
        assert all(event.metadata == {"marker": "absorbed"} for event in events)

        run = await store.get_run(org_id=survivor_org, run_id=absorbed["run_id"])
        assert run is not None
        assert run.user_id == survivor_user
        assert run.runtime_context.org_id == survivor_org
        assert run.runtime_context.user_id == survivor_user

        # ---- (b) AAD reality check: the re-wrap actually happened --------
        cur = raw.execute(
            "SELECT content_text FROM agent_messages WHERE id = %s",
            (absorbed["assistant_message_id"],),
        )
        moved = cur.fetchone()
        cur = raw.execute(
            "SELECT payload_json_redacted FROM runtime_events "
            "WHERE run_id = %s AND sequence_no = 1",
            (absorbed["run_id"],),
        )
        moved["payload_json_redacted"] = cur.fetchone()["payload_json_redacted"]
        assert moved["content_text"].startswith("v1:")
        with pytest.raises(CiphertextDecodeError):
            codec.decrypt_text(
                moved["content_text"],
                encryption_version=1,
                table="agent_messages",
                column="content_text",
                org_id=absorbed_org,
            )
        assert (
            codec.decrypt_text(
                moved["content_text"],
                encryption_version=1,
                table="agent_messages",
                column="content_text",
                org_id=survivor_org,
            )
            == absorbed["assistant_plaintext"]
        )
        with pytest.raises(CiphertextDecodeError):
            codec.decrypt_jsonb(
                moved["payload_json_redacted"],
                encryption_version=1,
                table="runtime_events",
                column="payload_json_redacted",
                org_id=absorbed_org,
            )
        assert (
            codec.decrypt_jsonb(
                moved["payload_json_redacted"],
                encryption_version=1,
                table="runtime_events",
                column="payload_json_redacted",
                org_id=survivor_org,
            )
            == absorbed["event_payloads"][0][1]
        )

        # ---- (c) Decoy untouched -----------------------------------------
        for table, id_column, row_id in (
            ("agent_conversations", "id", decoy["conversation_id"]),
            ("agent_messages", "id", decoy["assistant_message_id"]),
            ("agent_runs", "id", decoy["run_id"]),
        ):
            cur = raw.execute(
                f"SELECT org_id FROM {table} WHERE {id_column} = %s", (row_id,)
            )
            assert cur.fetchone()["org_id"] == decoy_org
        assert _event_rows(raw, decoy_org) == decoy_events_before
        decoy_messages = await store.list_messages(
            org_id=decoy_org, conversation_id=decoy["conversation_id"], limit=50
        )
        decoy_by_role = {message.role: message for message in decoy_messages}
        assert decoy_by_role[MessageRole.USER].content_text == decoy["user_plaintext"]
        assert (
            decoy_by_role[MessageRole.ASSISTANT].content_text
            == decoy["assistant_plaintext"]
        )

        # ---- (d) Event ids / sequence_no / run / conversation unchanged --
        events_after = _event_rows(raw, survivor_org)
        moved_after = {
            event_id: row
            for event_id, row in events_after.items()
            if event_id in events_before
        }
        assert moved_after == events_before
        # Survivor's own 3 events plus the absorbed 3 all live under the org.
        assert len(events_after) == 6

        # ---- (e) runtime_audit_log never rewritten -----------------------
        cur = raw.execute(
            "SELECT org_id FROM runtime_audit_log WHERE action = %s ORDER BY org_id",
            ("account_merge_live_test_seed",),
        )
        audit_orgs = [row["org_id"] for row in cur.fetchall()]
        assert audit_orgs == sorted([absorbed_org, survivor_org, decoy_org])
        assert absorbed_org in audit_orgs  # the absorbed chain kept its org

        # ---- (f) Idempotency: second rekey is a no-op --------------------
        tables_again, warnings_again = await rekeyer.rekey(
            absorbed_org_id=absorbed_org,
            absorbed_user_id=absorbed_user,
            survivor_org_id=survivor_org,
            survivor_user_id=survivor_user,
        )
        assert tables_again == {}
        assert warnings_again == []
        assert _event_rows(raw, survivor_org) == events_after
        messages_again = await store.list_messages(
            org_id=survivor_org,
            conversation_id=absorbed["conversation_id"],
            limit=50,
        )
        assert {
            message.message_id: message.content_text for message in messages_again
        } == {message.message_id: message.content_text for message in messages}

        # Survivor's own pre-existing rows also still round-trip.
        survivor_messages = await store.list_messages(
            org_id=survivor_org,
            conversation_id=survivor["conversation_id"],
            limit=50,
        )
        survivor_by_role = {message.role: message for message in survivor_messages}
        assert (
            survivor_by_role[MessageRole.USER].content_text
            == survivor["user_plaintext"]
        )
