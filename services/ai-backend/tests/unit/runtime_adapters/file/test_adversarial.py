"""Adversarial corpus for the file store (AC2 "Adversarial tests").

Each hostile input must be **rejected or neutralized, not honored**:

* path-traversal / absolute / NUL / Unicode-confusable conversation, task, and
  org ids never escape the store root — they are hashed to opaque hex keys;
* symlink / TOCTOU swaps on a content-addressed blob path are caught by the
  verify-on-read digest check, never returning attacker-substituted bytes;
* oversized and malformed payloads never corrupt or truncate neighbouring
  committed history (hard 1 MiB rejection is deferred per AC2 rule 4, so the
  invariant pinned here is *no collateral corruption*, which is the load-bearing
  one); and
* a token/key-shaped secret parked in a tool payload, an event payload, or
  conversation metadata never reaches the FTS index or its catalog columns —
  only redacted user/assistant text and titles are searchable.
"""

from __future__ import annotations

import hashlib
import sqlite3

import pytest
from pydantic import ValidationError

from agent_runtime.api.conversation_coordinator import ConversationCoordinator
from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.api.run_coordinator import RunCoordinator
from agent_runtime.execution.contracts import StreamEventSource
from agent_runtime.execution.models import ModelConfigResolver
from agent_runtime.settings import RuntimeSettings
from runtime_adapters.file._paths import FileStoreLayout
from runtime_adapters.file.object_store import FileObjectStore, ObjectStoreError
from runtime_adapters.file.runtime_api_store import FileRuntimeApiStore
from runtime_api.schemas import (
    CreateConversationRequest,
    CreateRunRequest,
    MessageRecord,
    MessageRole,
    RuntimeApiEventType,
    RuntimeEventDraft,
)

_ORG = "org_adv"
_USER = "user_adv"


class _RunSeedMixin:
    """Seed a conversation + real run through the coordinators (portable)."""

    @staticmethod
    def _settings() -> RuntimeSettings:
        return RuntimeSettings.load(
            environ={
                "OPENAI_API_KEY": "sk-test",
                "RUNTIME_DEFAULT_PROVIDER": "openai",
                "RUNTIME_DEFAULT_MODEL": "gpt-5.4-mini",
            }
        )

    async def _seed_run(self, store):
        settings = self._settings()
        run_coordinator = RunCoordinator(
            persistence=store,
            queue=store,
            event_producer=RuntimeEventProducer(
                persistence=store, event_store=store, on_event_appended=None
            ),
            settings=settings,
            model_resolver=ModelConfigResolver(settings),
        )
        conv_coordinator = ConversationCoordinator(
            persistence=store, settings=settings, run_coordinator=run_coordinator
        )
        conversation = await conv_coordinator.create_conversation(
            CreateConversationRequest(
                org_id=_ORG, user_id=_USER, assistant_id="a", title="adv"
            )
        )
        run = await run_coordinator.create_run(
            CreateRunRequest(
                conversation_id=conversation.conversation_id,
                org_id=_ORG,
                user_id=_USER,
                user_input="hi",
                model={"provider": "openai", "model_name": "gpt-5.4-mini"},
            )
        )
        return conversation.conversation_id, run.run_id


_HOSTILE_IDS = (
    "../../../etc/passwd",
    "..\\..\\windows\\system32",
    "/etc/shadow",
    "C:\\Windows\\System32",
    "conv/../../escape",
    "a\x00b",  # embedded NUL
    "....//....//etc",
    "‮/reversed",  # unicode confusable / RTL override
    ".",
    "..",
)


class TestPathTraversalNeutralized:
    """Untrusted logical ids are hashed to opaque keys; no path escapes root."""

    def test_safe_key_is_opaque_hex_and_collision_distinct(self) -> None:
        seen: dict[str, str] = {}
        for hostile in _HOSTILE_IDS:
            key = FileStoreLayout.safe_key(hostile)
            # 64 lowercase hex chars: no separators, dots, or traversal syntax.
            assert len(key) == 64
            assert all(c in "0123456789abcdef" for c in key)
            assert "/" not in key and "\\" not in key and "." not in key
            # Distinct inputs never collapse to the same path segment.
            assert key not in seen, f"{hostile!r} collided with {seen.get(key)!r}"
            seen[key] = hostile

    @pytest.mark.parametrize("hostile", _HOSTILE_IDS)
    def test_event_draft_rejects_hostile_ids_at_the_boundary(self, hostile) -> None:
        # Defense in depth layer 1: the schema validator refuses ids carrying
        # anything outside ``[A-Za-z0-9][A-Za-z0-9._:-]*`` — so ``../``, absolute
        # paths, NUL, and confusables never even reach the path layer.
        with pytest.raises(ValidationError):
            RuntimeEventDraft(
                org_id=hostile,
                run_id="run_ok",
                conversation_id="conv_ok",
                trace_id="t",
                source=StreamEventSource.MAIN_AGENT,
                event_type=RuntimeApiEventType.MODEL_DELTA,
            )
        with pytest.raises(ValidationError):
            RuntimeEventDraft(
                org_id="org_ok",
                run_id="run_ok",
                conversation_id=hostile,
                trace_id="t",
                source=StreamEventSource.MAIN_AGENT,
                event_type=RuntimeApiEventType.MODEL_DELTA,
            )

    def test_layout_neutralizes_hostile_ids_if_they_reach_it(self, tmp_path) -> None:
        # Defense in depth layer 2: even if a hostile id bypassed the schema, the
        # path layer hashes it, so the derived path never escapes the root.
        root = FileStoreLayout(tmp_path / "store")
        root.ensure_scaffold()
        resolved_root = root.root
        for hostile in _HOSTILE_IDS:
            for path in (
                root.events_path(hostile, hostile),
                root.subagent_path(hostile, hostile, hostile),
                root.workspace_dir(hostile),
                root.object_path(FileStoreLayout.safe_key(hostile)),
            ):
                assert resolved_root in path.resolve().parents


class TestObjectSymlinkAndToctou:
    """Verify-on-read defeats symlink/TOCTOU swaps on the blob path."""

    def _store(self, tmp_path) -> FileObjectStore:
        layout = FileStoreLayout(tmp_path / "store")
        layout.ensure_scaffold()
        return FileObjectStore(layout)

    def test_symlinked_blob_path_fails_integrity_check(self, tmp_path) -> None:
        store = self._store(tmp_path)
        layout = FileStoreLayout(tmp_path / "store")
        data = b"trusted large tool result"
        ref = store.put(data)
        path = layout.object_path(ref.sha256)

        # Attacker swaps the committed blob for a symlink to a secret file whose
        # bytes differ from the digest (a TOCTOU between validation and read).
        secret = tmp_path / "outside_secret"
        secret.write_bytes(b"attacker controlled contents")
        path.unlink()
        path.symlink_to(secret)

        # The read hashes the resolved bytes and refuses: no substituted bytes
        # are ever returned under the trusted digest.
        with pytest.raises(ObjectStoreError):
            store.get(ref)

    def test_toctou_rename_leaves_get_failing_closed(self, tmp_path) -> None:
        store = self._store(tmp_path)
        layout = FileStoreLayout(tmp_path / "store")
        data = b"content to be raced"
        digest = hashlib.sha256(data).hexdigest()
        target = layout.object_path(digest)
        FileStoreLayout.ensure_dir(target.parent)
        # Only an in-flight .tmp exists (put interrupted before rename).
        target.with_name(target.name + ".tmp").write_bytes(data)
        assert store.write_in_flight(digest) is True
        with pytest.raises(ObjectStoreError):
            store.get(digest)
        assert store.delete(digest) is False  # refuses to race the writer


class TestOversizedAndMalformedPayloads(_RunSeedMixin):
    """Hostile payloads never corrupt or truncate surrounding committed lines."""

    async def _append(self, store, conv_id, run_id, summary, payload=None) -> None:
        await store.append_event(
            RuntimeEventDraft(
                org_id=_ORG,
                run_id=run_id,
                conversation_id=conv_id,
                trace_id="t",
                source=StreamEventSource.MAIN_AGENT,
                event_type=RuntimeApiEventType.MODEL_DELTA,
                summary=summary,
                payload=payload or {},
            )
        )

    async def test_oversized_line_does_not_corrupt_neighbours(self, tmp_path) -> None:
        root = tmp_path / "store"
        store = FileRuntimeApiStore(root)
        await store.open()
        conv_id, run_id = await self._seed_run(store)
        base = await store.get_latest_sequence(run_id=run_id)
        # A >1 MiB event between two normal ones. AC2 rule 4 (hard reject at
        # 1 MiB) is deferred; the invariant asserted here is that an oversized
        # line neither corrupts nor truncates the committed events around it.
        huge = "X" * (2 * 1024 * 1024)
        await self._append(store, conv_id, run_id, "before")
        await self._append(store, conv_id, run_id, "huge", payload={"blob": huge})
        await self._append(store, conv_id, run_id, "after")
        await store.close()

        reopened = FileRuntimeApiStore(root)
        await reopened.open()
        events = await reopened.list_events_after(
            org_id=_ORG, run_id=run_id, after_sequence=base
        )
        assert [e.summary for e in events] == ["before", "huge", "after"]
        # Contiguous with no gap punched by the oversized line.
        assert [e.sequence_no for e in events] == [base + 1, base + 2, base + 3]
        assert events[1].payload["blob"] == huge  # faithful round-trip
        await reopened.close()

    async def test_control_chars_and_nul_in_text_round_trip(self, tmp_path) -> None:
        root = tmp_path / "store"
        store = FileRuntimeApiStore(root)
        await store.open()
        conv_id, _run_id = await self._seed_run(store)
        nasty = "line1\nline2\ttab\x00nul\x1bescape\r\nend"
        await store.append_message(
            MessageRecord(
                conversation_id=conv_id,
                org_id=_ORG,
                role=MessageRole.USER,
                content_text=nasty,
            )
        )
        await store.close()

        reopened = FileRuntimeApiStore(root)
        await reopened.open()
        messages = await reopened.list_messages(
            org_id=_ORG, conversation_id=conv_id, limit=10
        )
        # The control/NUL/newline-laden text is JSON-escaped on write and
        # restored byte-identically on reopen — no split lines, no truncation.
        assert any(m.content_text == nasty for m in messages)
        await reopened.close()

    async def test_record_flood_keeps_sequence_contiguous(self, tmp_path) -> None:
        root = tmp_path / "store"
        store = FileRuntimeApiStore(root)
        await store.open()
        conv_id, run_id = await self._seed_run(store)
        base = await store.get_latest_sequence(run_id=run_id)
        for i in range(300):
            await self._append(store, conv_id, run_id, f"e{i}")
        await store.close()

        reopened = FileRuntimeApiStore(root)
        await reopened.open()
        events = await reopened.list_events_after(
            org_id=_ORG, run_id=run_id, after_sequence=0
        )
        # A record-count flood stays fully contiguous and gap-free across reopen.
        assert [e.sequence_no for e in events] == list(range(1, base + 300 + 1))
        assert [e.summary for e in events[base:]] == [f"e{i}" for i in range(300)]
        await reopened.close()


class TestSecretCanaryNeverIndexed:
    """A credential-shaped secret never reaches the FTS index or its columns."""

    _CANARY = "sk-live-CANARY0xDEADBEEF-do-not-index"

    def _fts_texts(self, store) -> list[str]:
        """All indexed FTS text rows, read straight from the catalog db."""

        conn = sqlite3.connect(str(store.layout.index_db_path))
        try:
            rows = conn.execute("SELECT text FROM conversation_fts").fetchall()
        except sqlite3.OperationalError:
            pytest.skip("SQLite build lacks FTS5; search-only capability disabled")
        finally:
            conn.close()
        return [r[0] for r in rows]

    async def test_secret_in_tool_event_and_metadata_is_not_searchable(
        self, tmp_path
    ) -> None:
        store = FileRuntimeApiStore(tmp_path / "store")
        await store.open()
        conv = await store.create_conversation(
            CreateConversationRequest(
                org_id=_ORG,
                user_id=_USER,
                assistant_id="a",
                title="quarterly planning",
                metadata={"api_key": self._CANARY},
            )
        )
        conv_id = conv.conversation_id
        # Control: a user message with a distinctive word IS indexed/searchable.
        await store.append_message(
            MessageRecord(
                conversation_id=conv_id,
                org_id=_ORG,
                role=MessageRole.USER,
                content_text="please summarize the widgetronomics report",
            )
        )
        # A TOOL message carrying the secret must NOT be indexed.
        await store.append_message(
            MessageRecord(
                conversation_id=conv_id,
                org_id=_ORG,
                role=MessageRole.TOOL,
                content_text=f"connector returned token {self._CANARY}",
            )
        )
        # An event payload carrying the secret must NOT be indexed.
        await store.append_event(
            RuntimeEventDraft(
                org_id=_ORG,
                run_id="run_secret",
                conversation_id=conv_id,
                trace_id="t",
                source=StreamEventSource.TOOL,
                event_type=RuntimeApiEventType.TOOL_CALL_COMPLETED,
                payload={"authorization": f"Bearer {self._CANARY}"},
            )
        )

        # The control word finds the conversation...
        control_hits = await store.search_conversations(
            org_id=_ORG, user_id=_USER, query="widgetronomics", limit=10
        )
        if control_hits:  # only meaningful when FTS5 is present
            assert any(h.conversation.conversation_id == conv_id for h in control_hits)

        # ...but the secret is nowhere searchable.
        secret_hits = await store.search_conversations(
            org_id=_ORG, user_id=_USER, query=self._CANARY, limit=10
        )
        assert secret_hits == ()

        # And the raw FTS text column never contains the canary anywhere.
        for text in self._fts_texts(store):
            assert self._CANARY not in text
        await store.close()
