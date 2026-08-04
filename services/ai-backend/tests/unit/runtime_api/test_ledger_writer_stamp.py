"""Every ledger row this build appends is signed — all 34 event types.

``LedgerWriter``'s docstring makes a load-bearing claim: "a row with no ``w`` was
written before any writer signed its work." A reader is entitled to key on that.

The first attempt at the stamp made that claim false. ``w`` was written by the
producers — ``WorkLedgerEmitter._sign`` (4 event types) and the workspace effect
gate (2) — and the other 28 live producers appended unsigned. Absence therefore
meant "historic **or** one of the 28", so the first reader keyed on ``w`` would
classify live rows as pre-stamp history: bit-for-bit the defect
``isLegacySurfaceCreated`` had, which answered "historic" for every surface the
live pipeline produced. A stamp only some producers apply is worse than no stamp,
because it looks complete in every backend test that drives a producer that signs.

The fix is one seam instead of 34 producers:
``RuntimeEventPresentationProjector.payload_for_event`` is the funnel every row
crosses on its way into the store, and it signs anything arriving unsigned. This
file is what makes that claim checkable — it walks the whole vocabulary rather
than the handful of types any one producer happens to write, so a 35th event type
added tomorrow is covered the day its sample lands in the contract corpus.

Samples come from the checked-in golden corpora rather than being hand-written
here, for the same reason the fold tests do: a hand-written payload is a payload
that agrees with the test, and the corpus is the one both languages already pin.
"""

from __future__ import annotations

import pytest

from copilot_service_contracts.work_ledger import (
    load_ledger_golden_events,
    load_ledger_golden_journeys,
)

from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.execution.contracts import (
    AgentRuntimeContext,
    JsonObject,
    StreamEventSource,
)
from agent_runtime.surfaces_v2.ledger_models import (
    CURRENT_LEDGER_WRITER,
    LedgerEventType,
    UnknownLedgerWriterError,
)
from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from runtime_api.schemas import (
    AgentRunStatus,
    RunRecord,
    RuntimeApiEventType,
    RuntimeEventPresentationProjector,
)

_ORG = "org_stamp"
_USER = "user_stamp"
_RUN = "run_stamp"
_CONV = "conv_stamp"
_WRITER = CURRENT_LEDGER_WRITER.value


def _payload_samples() -> dict[str, JsonObject]:
    """One valid payload per event type, taken from the contract corpora.

    Mirrors ``allPayloadSamples()`` in ``packages/api-types/src/ledgerV21.test.ts``
    — same two files, same first-wins order — so the two languages assert over
    the same rows.
    """

    samples: dict[str, JsonObject] = {}
    journeys = load_ledger_golden_journeys()["journeys"]
    assert isinstance(journeys, list)
    corpora: list[object] = [load_ledger_golden_events()["events"], *journeys]
    for corpus in corpora:
        events = corpus["events"] if isinstance(corpus, dict) else corpus
        assert isinstance(events, list)
        for event in events:
            assert isinstance(event, dict)
            event_type = event["event_type"]
            payload = event["payload"]
            assert isinstance(event_type, str)
            assert isinstance(payload, dict)
            samples.setdefault(event_type, dict(payload))
    return samples


_SAMPLES = _payload_samples()
_EVENT_TYPES = tuple(member.value for member in LedgerEventType)


def _unsigned(event_type: str) -> JsonObject:
    """The corpus sample with any stamp removed — what a producer hands over."""

    return {key: value for key, value in _SAMPLES[event_type].items() if key != "w"}


class TestTheCorpusCoversTheVocabulary:
    """Coverage of the corpus itself, so the round-trip below cannot go hollow.

    Without this, adding a 35th event type with no golden sample would make the
    parametrised test silently skip it while still reporting green — the same
    "green over a dead half" shape the producer gate exists to prevent.
    """

    def test_every_ledger_event_type_has_a_sample(self) -> None:
        assert set(_SAMPLES) == set(_EVENT_TYPES)

    def test_the_vocabulary_is_the_whole_34(self) -> None:
        # Pinned as a count as well as a set: the prose in ledger_models.py and
        # in this file both say "34 of 34", and a claim with a number in it
        # should fail when the number changes.
        assert len(_EVENT_TYPES) == 34


class TestTheAppendFunnelSignsEveryRow:
    @pytest.mark.parametrize("event_type", _EVENT_TYPES)
    def test_an_unsigned_row_comes_back_signed(self, event_type: str) -> None:
        projected = RuntimeEventPresentationProjector.payload_for_event(
            event_type=RuntimeApiEventType(event_type),
            payload=_unsigned(event_type),
        )

        # Non-empty first: an allow-list that rejected the whole payload returns
        # ``{}``, which carries no stamp and would pass a bare ``w`` assertion by
        # never reaching it.
        assert projected, event_type
        assert projected["w"] == _WRITER, event_type

    @pytest.mark.parametrize("event_type", _EVENT_TYPES)
    def test_a_producer_signature_is_carried_not_re_signed(
        self, event_type: str
    ) -> None:
        # Two producers sign their own rows. The funnel must carry that stamp
        # rather than overwrite it, or a future generation's rows would be
        # relabelled as this one's on the way past.
        signed = {**_unsigned(event_type), "w": _WRITER}

        projected = RuntimeEventPresentationProjector.payload_for_event(
            event_type=RuntimeApiEventType(event_type),
            payload=signed,
        )

        assert projected["w"] == _WRITER, event_type

    @pytest.mark.parametrize("event_type", _EVENT_TYPES)
    def test_an_unreadable_stamp_refuses_the_append(self, event_type: str) -> None:
        forged = {**_unsigned(event_type), "w": "runtime.v9.7"}

        with pytest.raises(UnknownLedgerWriterError) as raised:
            RuntimeEventPresentationProjector.payload_for_event(
                event_type=RuntimeApiEventType(event_type),
                payload=forged,
            )

        assert str(raised.value) == "unknown ledger writer: 'runtime.v9.7'"


class TestTheStampSurvivesARealAppend:
    """The funnel is only the floor if the append path actually crosses it.

    Asserting on ``payload_for_event`` alone is asserting on a function. These
    drive ``RuntimeEventProducer`` and read the row back out of the store, which
    is the only way to see that the stamp is on the persisted envelope rather
    than on a dict the test built.
    """

    @staticmethod
    def _run() -> RunRecord:
        return RunRecord(
            run_id=_RUN,
            conversation_id=_CONV,
            org_id=_ORG,
            user_id=_USER,
            user_message_id="msg_stamp",
            trace_id="trace_stamp",
            model_provider="openai",
            model_name="gpt-4o-mini",
            status=AgentRunStatus.RUNNING,
            runtime_context=AgentRuntimeContext(
                user_id=_USER,
                org_id=_ORG,
                roles=["employee"],
                run_id=_RUN,
                trace_id="trace_stamp",
                model_profile={
                    "provider": "openai",
                    "model_name": "gpt-4o-mini",
                    "max_input_tokens": 4096,
                    "timeout_seconds": 30,
                    "temperature": 0,
                },
            ),
        )

    def _producer(self) -> tuple[InMemoryRuntimeApiStore, RuntimeEventProducer]:
        store = InMemoryRuntimeApiStore()
        run = self._run()
        store.runs[_RUN] = run
        store.events_by_run.setdefault(_RUN, [])
        return store, RuntimeEventProducer(persistence=store, event_store=store)

    async def test_a_single_append_persists_a_signed_row(self) -> None:
        store, producer = self._producer()
        event_type = LedgerEventType.SURFACE_CREATED.value

        envelope = await producer.append_api_event(
            run=store.runs[_RUN],
            source=StreamEventSource.SYSTEM,
            event_type=RuntimeApiEventType(event_type),
            payload=_unsigned(event_type),
        )

        assert envelope.payload["w"] == _WRITER
        stored = await store.list_events_after(
            org_id=_ORG, run_id=_RUN, after_sequence=0
        )
        assert [event.payload["w"] for event in stored] == [_WRITER]

    async def test_a_batch_append_persists_signed_rows(self) -> None:
        # The batch path builds its own drafts; it projects through the same
        # funnel, and this is the assertion that says so.
        store, producer = self._producer()
        event_type = LedgerEventType.OPERATION_REQUESTED.value

        await producer.append_api_events_batch(
            run=store.runs[_RUN],
            source=StreamEventSource.SYSTEM,
            event_type=RuntimeApiEventType(event_type),
            entries=[{"payload": _unsigned(event_type)}],
        )

        stored = await store.list_events_after(
            org_id=_ORG, run_id=_RUN, after_sequence=0
        )
        assert [event.payload["w"] for event in stored] == [_WRITER]

    async def test_a_forged_stamp_never_reaches_the_store(self) -> None:
        store, producer = self._producer()
        event_type = LedgerEventType.SURFACE_CREATED.value

        with pytest.raises(UnknownLedgerWriterError):
            await producer.append_api_event(
                run=store.runs[_RUN],
                source=StreamEventSource.SYSTEM,
                event_type=RuntimeApiEventType(event_type),
                payload={**_unsigned(event_type), "w": "runtime.v9.7"},
            )

        stored = await store.list_events_after(
            org_id=_ORG, run_id=_RUN, after_sequence=0
        )
        assert list(stored) == []
