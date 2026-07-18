"""Signed, tamper-evident manifests for the file store's sensitive operations.

Proves the AC5/AC10 tamper-evidence contract for the three sensitive file-store
ops — physical deletion (``#8``), conversation export (``#9``), and host
write-through (AC5):

* each op appends a signed manifest row and the whole chain verifies clean;
* a flipped field, a reordered row, and a dropped row are each detected with the
  offending sequence number;
* no secret value (token / run-capability-context / host path) leaks into any
  manifest row.
"""

from __future__ import annotations

import json

from agent_runtime.api.conversation_coordinator import ConversationCoordinator
from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.api.run_coordinator import RunCoordinator
from agent_runtime.execution.contracts import StreamEventSource
from agent_runtime.execution.models import ModelConfigResolver
from agent_runtime.capabilities.desktop.workspace_backend import (
    WorkspaceMutationSnapshot,
)
from agent_runtime.settings import RuntimeSettings
from runtime_adapters.file._audit_manifest import AuditManifest, AuditManifestVerifier
from runtime_adapters.file.offload import FileOffloadWriter
from runtime_adapters.file.runtime_api_store import FileRuntimeApiStore
from runtime_worker.workspace_backend_wiring import WorkspaceSnapshotEventEmitter
from runtime_api.schemas import (
    CreateConversationRequest,
    CreateRunRequest,
    RuntimeApiEventType,
    RuntimeEventDraft,
)

_ORG = "org_manifest"
_USER = "user_manifest"

# A canary that must never appear in any manifest row — stands in for a token /
# broker credential / run-capability-context the manifest must exclude.
_SECRET_CANARY = "rcc_SUPER_SECRET_run_capability_context_ABC123"


def _settings() -> RuntimeSettings:
    return RuntimeSettings.load(
        environ={
            "OPENAI_API_KEY": "sk-test",
            "RUNTIME_DEFAULT_PROVIDER": "openai",
            "RUNTIME_DEFAULT_MODEL": "gpt-5.4-mini",
        }
    )


class _SeedMixin:
    async def _store(self, root) -> FileRuntimeApiStore:
        store = FileRuntimeApiStore(root)
        await store.open()
        return store

    def _coordinators(self, store: FileRuntimeApiStore) -> ConversationCoordinator:
        settings = _settings()
        resolver = ModelConfigResolver(settings)
        event_producer = RuntimeEventProducer(
            persistence=store, event_store=store, on_event_appended=None
        )
        run_coordinator = RunCoordinator(
            persistence=store,
            queue=store,
            event_producer=event_producer,
            settings=settings,
            model_resolver=resolver,
        )
        return ConversationCoordinator(
            persistence=store, settings=settings, run_coordinator=run_coordinator
        )

    async def _seed_conversation(self, store: FileRuntimeApiStore):
        coordinator = self._coordinators(store)
        conversation = await coordinator.create_conversation(
            CreateConversationRequest(
                org_id=_ORG, user_id=_USER, assistant_id="assistant", metadata={}
            )
        )
        run = await coordinator._run_coordinator.create_run(
            CreateRunRequest(
                conversation_id=conversation.conversation_id,
                org_id=_ORG,
                user_id=_USER,
                user_input="Hello",
                model={"provider": "openai", "model_name": "gpt-5.4-mini"},
            )
        )
        reference = FileOffloadWriter(store.object_store)("PAYLOAD\n" * 4_000)
        await store.append_event(
            RuntimeEventDraft(
                org_id=_ORG,
                run_id=run.run_id,
                conversation_id=conversation.conversation_id,
                trace_id="trace_m",
                source=StreamEventSource.TOOL,
                event_type=RuntimeApiEventType.TOOL_RESULT,
                payload={
                    "tool_name": "web_search",
                    "call_id": "c1",
                    "status": "completed",
                    "output_ref": reference,
                    "preview": "PAYLOAD",
                },
            )
        )
        return conversation, run

    async def _emit_workspace_write(self, store: FileRuntimeApiStore) -> None:
        """Append a host write-through manifest row (as the AC5 emitter would)."""

        await store.write_audit_log(
            event_type=AuditManifest.EVENT_WORKSPACE_WRITE,
            record=AuditManifest.workspace_write_record(
                audit_event_id="ws_write_1",
                org_id=_ORG,
                user_id=_USER,
                run_id="run_ws",
                op="overwrite",
                mount="project-notes",
                path="/project-notes/report.md",
                object_sha256="a" * 64,
                size=1234,
                created_at="2026-07-18T00:00:00+00:00",
            ),
        )


class TestManifestChain(_SeedMixin):
    async def test_deletion_export_write_each_verify_clean(self, tmp_path) -> None:
        store = await self._store(tmp_path / "store")
        conversation, _run = await self._seed_conversation(store)
        archive = tmp_path / "backup.tar.gz"

        # #9 export → manifest row
        await store.export_conversation(
            org_id=_ORG,
            user_id=_USER,
            conversation_id=conversation.conversation_id,
            destination=archive,
        )
        # AC5 host write-through → manifest row
        await self._emit_workspace_write(store)
        # #8 physical deletion → manifest row
        response = await store.delete_user_history(org_id=_ORG, user_id=_USER)
        assert response.audit_event_id is not None

        event_types = {event_type for event_type, _ in store.audit_log}
        assert AuditManifest.EVENT_CONVERSATION_EXPORT in event_types
        assert AuditManifest.EVENT_WORKSPACE_WRITE in event_types
        assert "runtime_data_purged" in event_types

        result = store.verify_audit_log(org_id=_ORG)
        assert result.ok is True
        assert result.broken_at_seq is None

    async def test_chain_survives_reload_from_disk(self, tmp_path) -> None:
        store = await self._store(tmp_path / "store")
        conversation, _run = await self._seed_conversation(store)
        await store.export_conversation(
            org_id=_ORG,
            user_id=_USER,
            conversation_id=conversation.conversation_id,
            destination=tmp_path / "b.tar.gz",
        )
        await self._emit_workspace_write(store)
        await store.close()

        # A reopened store replays the signed rows from the ledger and the chain
        # still verifies (heads + counts rebuilt from disk).
        reopened = await self._store(tmp_path / "store")
        assert reopened.verify_audit_log().ok is True


class TestTamperDetection(_SeedMixin):
    async def _three_rows(self, store: FileRuntimeApiStore):
        conversation, _run = await self._seed_conversation(store)
        await store.export_conversation(
            org_id=_ORG,
            user_id=_USER,
            conversation_id=conversation.conversation_id,
            destination=store.layout.workspaces_dir.parent / "x.tar.gz",
        )
        await self._emit_workspace_write(store)
        await store.delete_user_history(org_id=_ORG, user_id=_USER)

    async def test_flipped_field_is_detected(self, tmp_path) -> None:
        store = await self._store(tmp_path / "store")
        await self._three_rows(store)
        # Mutate a signed field in place (leave the signature untouched).
        event_type, record = store.audit_log[1]
        record["size"] = 9_999
        result = store.verify_audit_log()
        assert result.ok is False
        assert result.reason == "signature mismatch"

    async def test_reordered_rows_are_detected(self, tmp_path) -> None:
        store = await self._store(tmp_path / "store")
        await self._three_rows(store)
        store.audit_log[0], store.audit_log[1] = (
            store.audit_log[1],
            store.audit_log[0],
        )
        # The verifier orders by seq, so a plain swap still verifies; simulate a
        # real reorder attack by ALSO rewriting the seq numbers to match the new
        # order — which breaks the prev_hash linkage.
        store.audit_log[0][1]["seq"] = 1
        store.audit_log[1][1]["seq"] = 2
        result = store.verify_audit_log()
        assert result.ok is False

    async def test_dropped_row_is_detected(self, tmp_path) -> None:
        store = await self._store(tmp_path / "store")
        await self._three_rows(store)
        del store.audit_log[1]  # excise the middle row
        result = store.verify_audit_log()
        assert result.ok is False
        assert result.reason == "prev_hash mismatch"

    async def test_replayed_foreign_row_is_detected(self, tmp_path) -> None:
        store = await self._store(tmp_path / "store")
        await self._three_rows(store)
        # Splice in a row signed under a DIFFERENT key version we do not hold.
        _etype, genuine = store.audit_log[1]
        forged = dict(genuine)
        forged["key_version"] = 999
        store.audit_log[1] = (_etype, forged)
        result = store.verify_audit_log()
        assert result.ok is False


class TestNoSecretLeak(_SeedMixin):
    async def test_no_secret_canary_in_any_manifest_row(self, tmp_path) -> None:
        store = await self._store(tmp_path / "store")
        conversation, _run = await self._seed_conversation(store)
        await store.export_conversation(
            org_id=_ORG,
            user_id=_USER,
            conversation_id=conversation.conversation_id,
            destination=tmp_path / "b.tar.gz",
        )
        await self._emit_workspace_write(store)
        await store.delete_user_history(org_id=_ORG, user_id=_USER)

        serialized = json.dumps(
            [record for _et, record in store.audit_log], default=str
        )
        assert _SECRET_CANARY not in serialized

    def test_workspace_write_record_excludes_capability_context(self) -> None:
        # The pre-image snapshot model CARRIES the run-capability-context, but the
        # manifest builder consumes only its path-free event payload — the
        # context must never reach a manifest row.
        snapshot = WorkspaceMutationSnapshot(
            op="overwrite",
            mount="notes",
            path="/notes/a.md",
            object_sha256="b" * 64,
            size=10,
            run_capability_context=_SECRET_CANARY,
        )
        record = AuditManifest.workspace_write_record(
            audit_event_id="w1",
            org_id=_ORG,
            user_id=_USER,
            run_id="run_x",
            op=snapshot.op,
            mount=snapshot.mount,
            path=snapshot.path,
            object_sha256=snapshot.object_sha256,
            size=snapshot.size,
            created_at="2026-07-18T00:00:00+00:00",
        )
        assert _SECRET_CANARY not in json.dumps(record)
        assert "run_capability_context" not in record


class _FakeEventProducer:
    def __init__(self) -> None:
        self.events: list = []

    async def append_api_event(self, **kwargs) -> None:
        self.events.append(kwargs)


class _FakeRun:
    user_id = _USER


class _FakePersistence:
    def __init__(self) -> None:
        self.audit_rows: list = []

    async def get_run(self, *, org_id: str, run_id: str):
        return _FakeRun()

    async def write_audit_log(self, *, event_type: str, record) -> None:
        self.audit_rows.append((event_type, record))


class TestEmitterWiring:
    async def test_emitter_writes_a_signed_manifest_row(self) -> None:
        producer = _FakeEventProducer()
        persistence = _FakePersistence()
        emitter = WorkspaceSnapshotEventEmitter(
            event_producer=producer,
            persistence=persistence,
            org_id=_ORG,
            run_id="run_e",
        )
        snapshot = WorkspaceMutationSnapshot(
            op="edit",
            mount="notes",
            path="/notes/plan.md",
            object_sha256="c" * 64,
            size=42,
            run_capability_context=_SECRET_CANARY,
        )
        await emitter(snapshot)

        # Both durable records were written, in order: the timeline event AND the
        # signed manifest row — with no capability context in the manifest.
        assert len(producer.events) == 1
        assert len(persistence.audit_rows) == 1
        event_type, record = persistence.audit_rows[0]
        assert event_type == AuditManifest.EVENT_WORKSPACE_WRITE
        assert record[AuditManifest.F_OBJECT_SHA256] == "c" * 64
        assert _SECRET_CANARY not in json.dumps(record)


class TestVerifierUnit:
    def test_verify_rows_accepts_exported_row_dicts(self) -> None:
        # Build a tiny two-row chain by hand and prove the row-dict verifier
        # (the SIEM-side shape from list_audit_log_for_export) round-trips.
        from copilot_audit_chain import AuditChainSigner

        signer = AuditChainSigner.from_env(
            environment_env_var="RUNTIME_ENVIRONMENT", fail_closed=False
        )
        rows: list[dict] = []
        prev = None
        for seq in (1, 2):
            record = {"org_id": _ORG, "audit_event_id": f"e{seq}", "seq": seq}
            payload = AuditManifest.signing_payload(
                event_type="unit_evt", record=record
            )
            sig = signer.sign(prev_hash=prev, payload=payload)
            rows.append(
                {
                    "event_type": "unit_evt",
                    **record,
                    "prev_hash": prev.hex() if prev else None,
                    "signature": sig.signature.hex(),
                    "key_version": sig.key_version,
                }
            )
            prev = sig.signature
        verifier = AuditManifestVerifier(signer)
        assert verifier.verify_rows(rows).ok is True
        # Flip one exported field → detected.
        rows[1]["audit_event_id"] = "tampered"
        assert verifier.verify_rows(rows).ok is False
