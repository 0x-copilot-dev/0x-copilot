"""Canonical run-event adapter for body-free F2 prompt observations."""

from __future__ import annotations

import hashlib

from pydantic import ValidationError

from agent_runtime.api.ports import EventStorePort
from agent_runtime.control_plane.contracts import RunControlSnapshot
from agent_runtime.control_plane.ports import (
    RunControlJournalCorruption,
    RunControlScopeConflict,
    RunControlSnapshotStorePort,
)
from agent_runtime.execution.contracts import StreamEventSource
from agent_runtime.persistence.ports import RuntimeEventIdempotencyConflict
from agent_runtime.prompts.observation import (
    PromptAssembledRecord,
    PromptCacheObservedRecord,
    PromptObservationConflict,
    PromptObservationCorruption,
    PromptObservationRecord,
    PromptObservationScopeConflict,
    PromptObservationSnapshotConflict,
    PromptObservationStorePort,
    PromptObservationWrite,
    SequencedPromptObservationRecord,
)
from runtime_api.schemas import (
    PromptAssembledPayload,
    PromptCacheObservedPayload,
    RuntimeActivityKind,
    RuntimeApiEventType,
    RuntimeEventDraft,
    RuntimeEventEnvelope,
    RuntimeEventRedactionState,
    RuntimeEventVisibility,
)


class EventJournalPromptObservationStore(PromptObservationStorePort):
    """Persist and replay F2 facts through the existing scoped event store."""

    _EVENT_TYPES = frozenset(
        {
            RuntimeApiEventType.PROMPT_ASSEMBLED,
            RuntimeApiEventType.PROMPT_CACHE_OBSERVED,
        }
    )

    def __init__(
        self,
        *,
        events: EventStorePort,
        snapshots: RunControlSnapshotStorePort,
    ) -> None:
        self._events = events
        self._snapshots = snapshots

    async def append(
        self,
        write: PromptObservationWrite,
    ) -> SequencedPromptObservationRecord:
        record = write.record
        snapshot = await self._load_snapshot(
            org_id=write.org_id,
            run_id=record.run_id,
            subject_fingerprint=write.subject_fingerprint,
        )
        if snapshot is None:
            raise PromptObservationCorruption(
                run_id=record.run_id,
                reason="a prompt observation cannot precede the control snapshot",
            )
        self._validate_snapshot_binding(snapshot=snapshot, record=record)
        prefix = await self.list_for_run(
            org_id=write.org_id,
            run_id=record.run_id,
            subject_fingerprint=write.subject_fingerprint,
        )
        existing = next(
            (item for item in prefix if item.record.record_id == record.record_id),
            None,
        )
        if existing is not None:
            if existing.record.record_digest != record.record_digest:
                raise PromptObservationConflict(
                    run_id=record.run_id,
                    record_id=record.record_id,
                )
            return existing
        self._validate_next(
            run_id=record.run_id,
            records=prefix,
            candidate=record,
        )

        event_id = self._stable_event_id(record)
        draft = RuntimeEventDraft(
            org_id=write.org_id,
            event_id=event_id,
            created_at=record.created_at,
            run_id=record.run_id,
            conversation_id=snapshot.conversation_id,
            trace_id=write.trace_id,
            source=StreamEventSource.RUNTIME,
            event_type=self._event_type(record),
            activity_kind=RuntimeActivityKind.EVENT,
            visibility=RuntimeEventVisibility.INTERNAL,
            redaction_state=RuntimeEventRedactionState.REDACTED,
            payload=self._payload(record),
        )
        try:
            envelope = await self._events.append_event(draft)
        except RuntimeEventIdempotencyConflict:
            durable = await self._record_by_event_id(
                org_id=write.org_id,
                run_id=record.run_id,
                event_id=event_id,
                expected_conversation_id=snapshot.conversation_id,
            )
            if durable is None:
                raise PromptObservationCorruption(
                    run_id=record.run_id,
                    reason="stable F2 event conflicted but was not readable",
                ) from None
            if durable.record.record_digest != record.record_digest:
                raise PromptObservationConflict(
                    run_id=record.run_id,
                    record_id=record.record_id,
                ) from None
            return durable

        durable_record = self._record_from_event(
            envelope,
            expected_conversation_id=snapshot.conversation_id,
        )
        if durable_record.record_digest != record.record_digest:
            raise PromptObservationCorruption(
                run_id=record.run_id,
                reason="appended F2 record does not match its canonical digest",
            )
        return SequencedPromptObservationRecord(
            sequence_no=envelope.sequence_no,
            record=durable_record,
        )

    async def list_for_run(
        self,
        *,
        org_id: str,
        run_id: str,
        subject_fingerprint: str,
        after_sequence: int = 0,
    ) -> tuple[SequencedPromptObservationRecord, ...]:
        if after_sequence < 0:
            raise ValueError("after_sequence must be non-negative")
        snapshot = await self._load_snapshot(
            org_id=org_id,
            run_id=run_id,
            subject_fingerprint=subject_fingerprint,
        )
        if snapshot is None:
            return ()
        events = await self._events.list_events_after(
            org_id=org_id,
            run_id=run_id,
            after_sequence=0,
        )
        records = tuple(
            SequencedPromptObservationRecord(
                sequence_no=event.sequence_no,
                record=self._record_from_event(
                    event,
                    expected_conversation_id=snapshot.conversation_id,
                ),
            )
            for event in events
            if event.event_type in self._EVENT_TYPES
        )
        self._validate_replay(snapshot=snapshot, records=records)
        return tuple(item for item in records if item.sequence_no > after_sequence)

    async def _load_snapshot(
        self,
        *,
        org_id: str,
        run_id: str,
        subject_fingerprint: str,
    ) -> RunControlSnapshot | None:
        try:
            return await self._snapshots.get(
                org_id=org_id,
                run_id=run_id,
                subject_fingerprint=subject_fingerprint,
            )
        except RunControlScopeConflict as exc:
            raise PromptObservationScopeConflict(run_id=run_id) from exc
        except RunControlJournalCorruption as exc:
            raise PromptObservationCorruption(
                run_id=run_id,
                reason="the bound control snapshot is not replayable",
            ) from exc

    async def _record_by_event_id(
        self,
        *,
        org_id: str,
        run_id: str,
        event_id: str,
        expected_conversation_id: str,
    ) -> SequencedPromptObservationRecord | None:
        events = await self._events.list_events_after(
            org_id=org_id,
            run_id=run_id,
            after_sequence=0,
        )
        matches = tuple(
            SequencedPromptObservationRecord(
                sequence_no=event.sequence_no,
                record=self._record_from_event(
                    event,
                    expected_conversation_id=expected_conversation_id,
                ),
            )
            for event in events
            if event.event_type in self._EVENT_TYPES and event.event_id == event_id
        )
        if len(matches) > 1:
            raise PromptObservationCorruption(
                run_id=run_id,
                reason="duplicate stable F2 event identity",
            )
        return matches[0] if matches else None

    @classmethod
    def _record_from_event(
        cls,
        event: RuntimeEventEnvelope,
        *,
        expected_conversation_id: str,
    ) -> PromptObservationRecord:
        try:
            if event.event_type is RuntimeApiEventType.PROMPT_ASSEMBLED:
                record: PromptObservationRecord = PromptAssembledPayload.model_validate(
                    event.payload
                ).record
            elif event.event_type is RuntimeApiEventType.PROMPT_CACHE_OBSERVED:
                record = PromptCacheObservedPayload.model_validate(event.payload).record
            else:
                raise ValueError("not an F2 prompt observation")
        except (ValidationError, ValueError) as exc:
            raise PromptObservationCorruption(
                run_id=event.run_id,
                reason=f"malformed {event.event_type.value} payload",
            ) from exc
        if event.run_id != record.run_id:
            cls._corrupt(event, "F2 record run identity does not match its journal")
        if event.conversation_id != expected_conversation_id:
            cls._corrupt(
                event,
                "F2 event conversation identity does not match its run snapshot",
            )
        if event.event_id != cls._stable_event_id(record):
            cls._corrupt(event, "F2 event does not use its stable identity")
        if event.source is not StreamEventSource.RUNTIME:
            cls._corrupt(event, "F2 event has a non-runtime source")
        if event.activity_kind is not RuntimeActivityKind.EVENT:
            cls._corrupt(event, "F2 event has a user activity projection")
        if event.visibility is not RuntimeEventVisibility.INTERNAL:
            cls._corrupt(event, "F2 event is not internal")
        if event.redaction_state is not RuntimeEventRedactionState.REDACTED:
            cls._corrupt(event, "F2 event is not body-free/redacted")
        if event.created_at != record.created_at:
            cls._corrupt(event, "F2 event time does not match the canonical record")
        if event.metadata:
            cls._corrupt(event, "F2 event contains non-canonical metadata")
        if any(
            value is not None
            for value in (
                event.parent_event_id,
                event.span_id,
                event.parent_span_id,
                event.parent_task_id,
                event.task_id,
                event.subagent_id,
                event.display_title,
                event.summary,
                event.status,
                event.presentation,
            )
        ):
            cls._corrupt(event, "F2 event contains non-canonical presentation data")
        if event.payload != cls._payload(record):
            cls._corrupt(event, "F2 event payload does not match the canonical record")
        return record

    @classmethod
    def _validate_replay(
        cls,
        *,
        snapshot: RunControlSnapshot,
        records: tuple[SequencedPromptObservationRecord, ...],
    ) -> None:
        if any(
            left.sequence_no >= right.sequence_no
            for left, right in zip(records, records[1:], strict=False)
        ):
            raise PromptObservationCorruption(
                run_id=snapshot.run_id,
                reason="F2 event sequence is not strictly increasing",
            )
        seen_ids: set[str] = set()
        prefix: list[SequencedPromptObservationRecord] = []
        for item in records:
            record = item.record
            cls._validate_snapshot_binding(snapshot=snapshot, record=record)
            if record.record_id in seen_ids:
                raise PromptObservationCorruption(
                    run_id=snapshot.run_id,
                    reason="duplicate F2 record identity",
                )
            cls._validate_next(
                run_id=snapshot.run_id,
                records=tuple(prefix),
                candidate=record,
                replay=True,
            )
            seen_ids.add(record.record_id)
            prefix.append(item)

    @staticmethod
    def _validate_snapshot_binding(
        *,
        snapshot: RunControlSnapshot,
        record: PromptObservationRecord,
    ) -> None:
        if (
            record.run_id != snapshot.run_id
            or record.snapshot_id != snapshot.snapshot_id
            or record.snapshot_digest != snapshot.snapshot_digest
        ):
            raise PromptObservationSnapshotConflict(run_id=record.run_id)

    @classmethod
    def _validate_next(
        cls,
        *,
        run_id: str,
        records: tuple[SequencedPromptObservationRecord, ...],
        candidate: PromptObservationRecord,
        replay: bool = False,
    ) -> None:
        prior = tuple(item.record for item in records)
        assemblies = {
            record.model_call_id: record
            for record in prior
            if isinstance(record, PromptAssembledRecord)
        }
        caches = {
            record.model_call_id: record
            for record in prior
            if isinstance(record, PromptCacheObservedRecord)
        }
        if isinstance(candidate, PromptAssembledRecord):
            if candidate.model_call_id in assemblies:
                cls._invalid(
                    run_id=run_id,
                    record_id=candidate.record_id,
                    reason="model call has more than one assembly observation",
                    replay=replay,
                )
            return
        if candidate.model_call_id in caches:
            cls._invalid(
                run_id=run_id,
                record_id=candidate.record_id,
                reason="model call has more than one cache observation",
                replay=replay,
            )
        assembly = assemblies.get(candidate.model_call_id)
        if assembly is None or (
            candidate.assembly_record_id != assembly.record_id
            or candidate.assembly_record_digest != assembly.record_digest
            or candidate.plan_id != assembly.plan_id
            or candidate.plan_digest != assembly.plan_digest
            or candidate.provider != assembly.provider
            or candidate.model_family != assembly.model_family
            or candidate.cache_owner is not assembly.cache_owner
        ):
            cls._invalid(
                run_id=run_id,
                record_id=candidate.record_id,
                reason="cache observation does not bind its prior assembly",
                replay=replay,
            )

    @staticmethod
    def _invalid(
        *,
        run_id: str,
        record_id: str,
        reason: str,
        replay: bool,
    ) -> None:
        if replay:
            raise PromptObservationCorruption(run_id=run_id, reason=reason)
        raise PromptObservationConflict(run_id=run_id, record_id=record_id)

    @staticmethod
    def _event_type(record: PromptObservationRecord) -> RuntimeApiEventType:
        if isinstance(record, PromptAssembledRecord):
            return RuntimeApiEventType.PROMPT_ASSEMBLED
        return RuntimeApiEventType.PROMPT_CACHE_OBSERVED

    @staticmethod
    def _payload(record: PromptObservationRecord) -> dict[str, object]:
        if isinstance(record, PromptAssembledRecord):
            return PromptAssembledPayload(record=record).model_dump(mode="json")
        return PromptCacheObservedPayload(record=record).model_dump(mode="json")

    @staticmethod
    def _stable_event_id(record: PromptObservationRecord) -> str:
        digest = hashlib.sha256(record.record_id.encode("utf-8")).hexdigest()
        return f"prompt_observation:{digest}"

    @staticmethod
    def _corrupt(event: RuntimeEventEnvelope, reason: str) -> None:
        raise PromptObservationCorruption(run_id=event.run_id, reason=reason)


__all__ = ["EventJournalPromptObservationStore"]
