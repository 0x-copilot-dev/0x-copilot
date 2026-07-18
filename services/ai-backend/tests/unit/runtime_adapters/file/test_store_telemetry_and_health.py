"""File-store structured logs/metrics + the "this chat needs repair" signal.

Covers three guarantees added alongside AC2's observability + repair UX:

* the file store emits counters/durations through the OTel metrics seam on its
  key operations — append/load, quota rejection, a forced **torn-index** discard
  + rebuild, and interior corruption;
* a corrupted conversation reports ``needs_repair`` with the correct reason code
  (reusing the repair module's diagnosis vocabulary) while healthy ones report
  clean; and
* no secret (payload byte, message text) ever reaches a log message, a log
  ``extra`` field, or a metric label — the fail-closed / rebuild / quota paths
  log ids-as-hashes, counts, sizes, durations, and reason codes only.
"""

from __future__ import annotations

import json
import logging

import pytest

from agent_runtime.api.conversation_coordinator import ConversationCoordinator
from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.api.run_coordinator import RunCoordinator
from agent_runtime.execution.contracts import StreamEventSource
from agent_runtime.execution.models import ModelConfigResolver
from agent_runtime.settings import RuntimeSettings
from runtime_adapters.file._health import FileStoreRepairReason
from runtime_adapters.file._jsonl import JsonlCorruptionError
from runtime_adapters.file._telemetry import FileStoreTelemetry
from runtime_adapters.file.runtime_api_store import FileRuntimeApiStore
from runtime_api.schemas import (
    CreateConversationRequest,
    CreateRunRequest,
    RuntimeApiEventType,
    RuntimeEventDraft,
)

_ORG = "org_obs"
_USER = "user_obs"
_CANARY = "SECRET-CANARY-do-not-log-4c1f9a"


def _settings() -> RuntimeSettings:
    return RuntimeSettings.load(
        environ={
            "OPENAI_API_KEY": "sk-test",
            "RUNTIME_DEFAULT_PROVIDER": "openai",
            "RUNTIME_DEFAULT_MODEL": "gpt-5.4-mini",
        }
    )


class RecordingMetrics:
    """A drop-in for ``FileStoreMetrics`` that records every call.

    Mirrors the facade's method surface so ``FileStoreTelemetry`` drives it
    unchanged; each call appends ``(name, kwargs)`` to :attr:`calls`.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def record_op(self, *, op: str, outcome: str = "ok") -> None:
        self.calls.append(("op", {"op": op, "outcome": outcome}))

    def record_failure(self, *, op: str, reason: str) -> None:
        self.calls.append(("failure", {"op": op, "reason": reason}))

    def record_quota_rejection(self) -> None:
        self.calls.append(("quota_rejection", {}))

    def record_corruption(self, *, kind: str) -> None:
        self.calls.append(("corruption", {"kind": kind}))

    def record_index_rebuild(
        self, *, trigger: str, elapsed_seconds: float | None = None
    ) -> None:
        self.calls.append(("index_rebuild", {"trigger": trigger}))

    def record_objects_collected(self, *, count: int) -> None:
        self.calls.append(("objects_collected", {"count": count}))

    def record_committed_bytes(self, *, kind: str, size: int) -> None:
        self.calls.append(("committed_bytes", {"kind": kind, "size": size}))

    # ----- assertion helpers --------------------------------------------

    def names(self) -> list[str]:
        return [name for name, _ in self.calls]

    def kwargs_for(self, name: str) -> list[dict]:
        return [payload for call, payload in self.calls if call == name]


class StoreSeedMixin:
    """Builds a store + seeds conversations through the real coordinators."""

    def _fresh_store(self, tmp_path, *, max_bytes: int = 0) -> FileRuntimeApiStore:
        return FileRuntimeApiStore(tmp_path / "store", max_bytes=max_bytes)

    def _wire_recording_metrics(self, store: FileRuntimeApiStore) -> RecordingMetrics:
        metrics = RecordingMetrics()
        store._telemetry = FileStoreTelemetry(metrics=metrics)  # type: ignore[arg-type]
        return metrics

    async def _seed(self, store: FileRuntimeApiStore, *, user_input: str = "Hello"):
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
        conv_coordinator = ConversationCoordinator(
            persistence=store, settings=settings, run_coordinator=run_coordinator
        )
        conversation = await conv_coordinator.create_conversation(
            CreateConversationRequest(
                org_id=_ORG, user_id=_USER, assistant_id="assistant"
            )
        )
        run = await run_coordinator.create_run(
            CreateRunRequest(
                conversation_id=conversation.conversation_id,
                org_id=_ORG,
                user_id=_USER,
                user_input=user_input,
                model={"provider": "openai", "model_name": "gpt-5.4-mini"},
            )
        )
        for i in range(3):
            await store.append_event(
                RuntimeEventDraft(
                    org_id=_ORG,
                    run_id=run.run_id,
                    conversation_id=conversation.conversation_id,
                    trace_id="trace_obs",
                    source=StreamEventSource.MAIN_AGENT,
                    event_type=RuntimeApiEventType.MODEL_DELTA,
                    summary=f"chunk-{i}",
                )
            )
        return conversation, run


def _corrupt_interior_line(path) -> None:
    """Make an interior line malformed with valid data still after it."""

    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) >= 2, f"need >=2 lines to corrupt an interior of {path}"
    lines[0] = "{ this is not json"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _corrupt_index_db(store: FileRuntimeApiStore) -> None:
    """Overwrite the disposable SQLite catalog with non-database bytes."""

    db_path = store.layout.index_db_path
    db_path.write_bytes(b"this is definitely not a sqlite database header\n" * 4)
    for suffix in ("-wal", "-shm"):
        sidecar = db_path.with_name(db_path.name + suffix)
        sidecar.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


class TestFileStoreMetrics(StoreSeedMixin):
    async def test_open_and_append_record_ops_and_bytes(self, tmp_path) -> None:
        store = self._fresh_store(tmp_path)
        metrics = self._wire_recording_metrics(store)
        await store.open()
        await self._seed(store)
        await store.close()

        # An ordinary boot rebuilds the index with the OPEN trigger.
        assert {"trigger": "open"} in metrics.kwargs_for("index_rebuild")
        assert {"op": "open", "outcome": "ok"} in metrics.kwargs_for("op")
        # Every seeded record is counted with a positive byte size.
        byte_kinds = {c["kind"] for c in metrics.kwargs_for("committed_bytes")}
        assert {"conversation", "message", "run", "event"} <= byte_kinds
        assert all(c["size"] > 0 for c in metrics.kwargs_for("committed_bytes"))

    async def test_quota_rejection_increments_counter(self, tmp_path) -> None:
        store = self._fresh_store(tmp_path, max_bytes=2_000)
        metrics = self._wire_recording_metrics(store)
        await store.open()

        with pytest.raises(Exception):
            store.object_store.put(b"x" * 5_000)

        assert metrics.names().count("quota_rejection") == 1
        assert {"op": "append", "outcome": "error"} in metrics.kwargs_for("op")
        await store.close()

    async def test_forced_torn_index_triggers_discard_and_rebuild(
        self, tmp_path
    ) -> None:
        store = self._fresh_store(tmp_path)
        await store.open()
        await self._seed(store)
        await store.close()

        _corrupt_index_db(store)

        reopened = self._fresh_store(tmp_path)
        metrics = self._wire_recording_metrics(reopened)
        await reopened.open()  # torn catalog is discarded + rebuilt, never raises

        # Discard is surfaced as a catalog_open failure, and the rebuild counter
        # fires with the catalog_discard trigger (not the ordinary open trigger).
        assert {"op": "catalog_open", "reason": "db_corrupt_discarded"} in (
            metrics.kwargs_for("failure")
        )
        assert {"trigger": "catalog_discard"} in metrics.kwargs_for("index_rebuild")
        report = await reopened.store_health()
        assert report.catalog_rebuilt is True
        await reopened.close()

    async def test_interior_corruption_on_open_records_metric_and_flag(
        self, tmp_path
    ) -> None:
        store = self._fresh_store(tmp_path)
        await store.open()
        conversation, _run = await self._seed(store)
        await store.close()

        events_path = store.layout.events_path(_ORG, conversation.conversation_id)
        _corrupt_interior_line(events_path)

        reopened = self._fresh_store(tmp_path)
        metrics = self._wire_recording_metrics(reopened)
        # Fail-closed contract preserved: open() still raises on interior corruption.
        with pytest.raises(JsonlCorruptionError):
            await reopened.open()

        assert {"kind": "interior_corrupt"} in metrics.kwargs_for("corruption")
        # The at-fault conversation is flagged for repair on the live path.
        assert conversation.conversation_id in reopened.needs_repair_ids()


# ---------------------------------------------------------------------------
# Health / needs-repair
# ---------------------------------------------------------------------------


class TestConversationHealth(StoreSeedMixin):
    async def test_clean_store_reports_healthy(self, tmp_path) -> None:
        store = self._fresh_store(tmp_path)
        await store.open()
        conversation, _run = await self._seed(store)
        report = await store.store_health()
        assert report.healthy is True
        assert report.needs_repair_ids() == frozenset()

        health = await store.conversation_health(
            org_id=_ORG, conversation_id=conversation.conversation_id
        )
        assert health.needs_repair is False
        assert health.reason_codes == ()
        await store.close()

    async def test_corrupted_conversation_reports_needs_repair(self, tmp_path) -> None:
        store = self._fresh_store(tmp_path)
        await store.open()
        bad, _run_bad = await self._seed(store)
        await store.close()

        events_path = store.layout.events_path(_ORG, bad.conversation_id)
        _corrupt_interior_line(events_path)

        # store_health()/conversation_health() work directly off disk — no open().
        fresh = self._fresh_store(tmp_path)
        report = await fresh.store_health()
        assert report.healthy is False
        assert bad.conversation_id in report.needs_repair_ids()

        health = await fresh.conversation_health(
            org_id=_ORG, conversation_id=bad.conversation_id
        )
        assert health.needs_repair is True
        assert FileStoreRepairReason.INTERIOR_CORRUPTION in health.reason_codes

    async def test_healthy_sibling_stays_clean_when_another_is_corrupt(
        self, tmp_path
    ) -> None:
        store = self._fresh_store(tmp_path)
        await store.open()
        good, _run_good = await self._seed(store)
        bad, _run_bad = await self._seed(store)
        await store.close()

        _corrupt_interior_line(store.layout.events_path(_ORG, bad.conversation_id))

        fresh = self._fresh_store(tmp_path)
        good_health = await fresh.conversation_health(
            org_id=_ORG, conversation_id=good.conversation_id
        )
        bad_health = await fresh.conversation_health(
            org_id=_ORG, conversation_id=bad.conversation_id
        )
        assert good_health.needs_repair is False
        assert bad_health.needs_repair is True
        # Only the corrupt conversation is listed as unhealthy.
        assert (await fresh.store_health()).needs_repair_ids() == frozenset(
            {bad.conversation_id}
        )


# ---------------------------------------------------------------------------
# Secret redaction
# ---------------------------------------------------------------------------


class TestSecretRedaction(StoreSeedMixin):
    async def test_no_secret_in_logs_or_metric_labels(
        self, tmp_path, caplog: pytest.LogCaptureFixture
    ) -> None:
        store = self._fresh_store(tmp_path, max_bytes=10_000)
        metrics = self._wire_recording_metrics(store)
        with caplog.at_level(logging.DEBUG, logger="agent_runtime.file_store"):
            await store.open()
            # The canary lands in on-disk message content...
            conversation, _run = await self._seed(store, user_input=_CANARY)
            # ...then force a quota rejection carrying oversized bytes.
            with pytest.raises(Exception):
                store.object_store.put(_CANARY.encode() * 2_000)
            await store.close()

            # And force interior corruption + torn-index rebuild on reopen.
            _corrupt_interior_line(
                store.layout.events_path(_ORG, conversation.conversation_id)
            )
            reopened = self._fresh_store(tmp_path)
            self._wire_recording_metrics(reopened)
            with pytest.raises(JsonlCorruptionError):
                await reopened.open()

        # The canary is genuinely on disk (so the test is meaningful).
        messages_blob = store.layout.messages_path(
            _ORG, conversation.conversation_id
        ).read_text(encoding="utf-8")
        assert _CANARY in messages_blob

        # ...but never in any captured log record — message OR structured extra.
        for record in caplog.records:
            assert _CANARY not in record.getMessage()
            extra = getattr(record, "file_store", None)
            if extra is not None:
                assert _CANARY not in json.dumps(extra, default=str)
        # ...and never in any metric label/value.
        for _name, payload in metrics.calls:
            assert _CANARY not in json.dumps(payload, default=str)
