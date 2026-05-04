"""C7 phase 2 store-level projections (no live Postgres).

Pins the row-dict → record contract at the projection boundary
(``_message_record``, ``_event_envelope``). The projections are now
instance methods that consult ``self._codec`` to decrypt the encrypted
columns; an integration test against real Postgres is out of scope for
the unit tier, but the projection mapping is the surface a regression
would land on first.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

from psycopg_pool import AsyncConnectionPool

from agent_runtime.persistence.encryption import EnvelopeFieldEncryption
from runtime_adapters.postgres.runtime_api_store import (
    PostgresRuntimeApiStore,
    _Columns,
    _Tables,
)


class _FakeKms:
    _WRAP = bytes.fromhex("a5" * 32)

    def wrap_data_key(self, plaintext_dek: bytes) -> tuple[bytes, str]:
        return bytes(a ^ b for a, b in zip(plaintext_dek, self._WRAP)), "alias/test"

    def unwrap_data_key(self, wrapped_dek: bytes, *, key_id: str | None) -> bytes:
        return bytes(a ^ b for a, b in zip(wrapped_dek, self._WRAP))


def _store_with_envelope() -> PostgresRuntimeApiStore:
    """Construct the store with envelope encryption + a mock pool.

    The pool is never opened — projection methods don't touch it.
    """
    pool = MagicMock(spec=AsyncConnectionPool)
    return PostgresRuntimeApiStore(
        pool=pool,
        field_encryption=EnvelopeFieldEncryption(kms_client=_FakeKms()),
    )


class TestMessageProjection:
    def _row_template(self) -> dict[str, object]:
        return {
            _Columns.ID: "msg_1",
            _Columns.CONVERSATION_ID: "cnv_1",
            _Columns.ORG_ID: "org_a",
            _Columns.RUN_ID: None,
            _Columns.ROLE: "user",
            _Columns.CONTENT_FORMAT: "text",
            _Columns.ATTACHMENTS_JSON: [],
            _Columns.QUOTE_JSON: None,
            _Columns.PARENT_MESSAGE_ID: None,
            _Columns.SOURCE_MESSAGE_ID: None,
            _Columns.BRANCH_ID: None,
            _Columns.TOKEN_COUNT: 12,
            _Columns.TRACE_ID: "trc_1",
            _Columns.STATUS: "created",
            _Columns.CREATED_AT: datetime.now(timezone.utc),
            _Columns.EDITED_AT: None,
            _Columns.DELETED_AT: None,
        }

    def test_v1_row_decrypts_all_three_columns(self) -> None:
        store = _store_with_envelope()
        codec = store._codec
        org_id = "org_a"
        content_text = "hello world"
        content_json = [{"type": "text", "text": "hello world"}]
        metadata_json = {"source": "test", "v": 1}
        row = self._row_template()
        row[_Columns.ENCRYPTION_VERSION] = 1
        row[_Columns.CONTENT_TEXT] = codec.encrypt_text(
            content_text,
            table=_Tables.AGENT_MESSAGES,
            column=_Columns.CONTENT_TEXT,
            org_id=org_id,
        )
        row[_Columns.CONTENT_JSON] = codec.encrypt_jsonb(
            content_json,
            table=_Tables.AGENT_MESSAGES,
            column=_Columns.CONTENT_JSON,
            org_id=org_id,
        )
        row[_Columns.METADATA_JSON] = codec.encrypt_jsonb(
            metadata_json,
            table=_Tables.AGENT_MESSAGES,
            column=_Columns.METADATA_JSON,
            org_id=org_id,
        )

        record = store._message_record(row)
        assert record.content_text == content_text
        assert [dict(part) for part in record.content] == content_json
        assert record.metadata == metadata_json

    def test_v0_row_passes_through(self) -> None:
        store = _store_with_envelope()
        row = self._row_template()
        row[_Columns.ENCRYPTION_VERSION] = 0
        row[_Columns.CONTENT_TEXT] = "legacy text"
        row[_Columns.CONTENT_JSON] = [{"type": "text", "text": "legacy"}]
        row[_Columns.METADATA_JSON] = {"v": 0}

        record = store._message_record(row)
        assert record.content_text == "legacy text"
        assert record.metadata == {"v": 0}

    def test_missing_encryption_version_treated_as_v0(self) -> None:
        store = _store_with_envelope()
        row = self._row_template()
        # Pre-phase-1 rows pre-date the column; some test fixtures may
        # omit it. Defensive default = 0.
        row[_Columns.CONTENT_TEXT] = "no version"
        row[_Columns.CONTENT_JSON] = []
        row[_Columns.METADATA_JSON] = {}
        record = store._message_record(row)
        assert record.content_text == "no version"


class TestEventProjection:
    def _row_template(self) -> dict[str, object]:
        return {
            _Columns.ID: "evt_1",
            _Columns.RUN_ID: "run_1",
            _Columns.CONVERSATION_ID: "cnv_1",
            _Columns.ORG_ID: "org_a",
            _Columns.SEQUENCE_NO: 1,
            "event_protocol_version": 1,
            _Columns.SOURCE: "runtime",
            _Columns.EVENT_TYPE: "run_completed",
            _Columns.PARENT_EVENT_ID: None,
            _Columns.SPAN_ID: None,
            _Columns.PARENT_SPAN_ID: None,
            _Columns.PARENT_TASK_ID: None,
            _Columns.TASK_ID: None,
            _Columns.SUBAGENT_ID: None,
            _Columns.DISPLAY_TITLE: None,
            _Columns.SUMMARY: None,
            _Columns.STATUS: None,
            _Columns.TRACE_ID: "trc_1",
            _Columns.VISIBILITY: "user",
            _Columns.REDACTION_STATE: "redacted",
            _Columns.ACTIVITY_KIND: "run",
            # Force the projector to derive presentation from metadata —
            # exercises the path that uses the *decrypted* metadata, not
            # the stored envelope.
            _Columns.PRESENTATION_JSON: None,
            _Columns.CREATED_AT: datetime.now(timezone.utc),
        }

    # Payload keys here are deliberately neutral — the envelope schema's
    # ObservabilityRedactor would rewrite e.g. "tokens" to "[redacted]"
    # downstream, masking whether decryption worked. We pick "ms" /
    # "ok" / "trace_label" which the redactor leaves alone.
    def test_v1_row_decrypts_payload_and_metadata(self) -> None:
        store = _store_with_envelope()
        codec = store._codec
        org_id = "org_a"
        payload = {"ms": 1234, "ok": True}
        metadata = {"trace_label": "abc"}
        row = self._row_template()
        row[_Columns.ENCRYPTION_VERSION] = 1
        row[_Columns.PAYLOAD_JSON_REDACTED] = codec.encrypt_jsonb(
            payload,
            table=_Tables.RUNTIME_EVENTS,
            column=_Columns.PAYLOAD_JSON_REDACTED,
            org_id=org_id,
        )
        row[_Columns.METADATA_JSON_REDACTED] = codec.encrypt_jsonb(
            metadata,
            table=_Tables.RUNTIME_EVENTS,
            column=_Columns.METADATA_JSON_REDACTED,
            org_id=org_id,
        )
        envelope = store._event_envelope(row)
        assert envelope.payload == payload
        assert envelope.metadata == metadata

    def test_v0_row_passes_through(self) -> None:
        store = _store_with_envelope()
        row = self._row_template()
        row[_Columns.ENCRYPTION_VERSION] = 0
        row[_Columns.PAYLOAD_JSON_REDACTED] = {"legacy": True}
        row[_Columns.METADATA_JSON_REDACTED] = {"trace_label": "x"}
        envelope = store._event_envelope(row)
        assert envelope.payload == {"legacy": True}
        assert envelope.metadata == {"trace_label": "x"}
