"""Shared adapter-only rule for resolving a stable-event-id redelivery.

``EventStorePort.append_event`` promises that a retry of an event already
durably stored under the same producer-assigned ``event_id`` returns the stored
envelope, and that reusing that id for a *different* event fails closed. Which
of the two a redelivery is has to be decided identically by all three event
stores — otherwise one command succeeds against Postgres and crash-loops the
worker against the file store — so the decision lives here rather than being
restated at each ``append_event``.

The part a plain body comparison gets wrong is delivery annotations. A
:class:`~agent_runtime.api.ledger_seal.LedgerAmendment` stamps metadata
describing *this append attempt* — which lane published the fact, and where the
run's sequence cursor stood — onto an event whose identity is a digest of the
domain fact alone. Those keys therefore differ between an append and its own
redelivery while the event is unchanged:

* an artifact fact published inline while its run was open carries no
  annotation, and the outbox command for the same ``artevt_`` id declares
  ``late_causal_recovery`` when the queue bridge later replays it;
* ``effect.reconciled`` records ``ledger_amends`` from the run's
  ``latest_sequence_no``, which has moved on by the time a repair retries.

Whichever lane landed the event first told the truth about how it arrived, so
its stored annotation stands and the redelivery compares bodies with the
annotations removed from both sides.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TypeVar

from agent_runtime.api.ledger_seal import LedgerAmendment
from agent_runtime.persistence.ports import RuntimeEventIdempotencyConflict
from runtime_api.schemas import RuntimeEventDraft, RuntimeEventEnvelope

_EventT = TypeVar("_EventT", RuntimeEventDraft, RuntimeEventEnvelope)


class EventRedeliveryResolver:
    """Decide whether a re-appended draft is a replay of the stored event."""

    _METADATA = "metadata"

    @classmethod
    def resolve(
        cls,
        *,
        event: RuntimeEventDraft,
        existing: RuntimeEventEnvelope,
    ) -> RuntimeEventEnvelope:
        """Return the stored envelope for a redelivery, or fail closed.

        ``existing`` is returned unchanged, so the lane that actually landed the
        event keeps authorship of its delivery annotation.
        """

        if event.matches_envelope(existing) or cls._matches_undelivered(
            event=event, existing=existing
        ):
            return existing
        raise RuntimeEventIdempotencyConflict(
            run_id=event.run_id,
            event_id=existing.event_id,
        )

    @classmethod
    def _matches_undelivered(
        cls,
        *,
        event: RuntimeEventDraft,
        existing: RuntimeEventEnvelope,
    ) -> bool:
        """Compare the two bodies with delivery annotations removed."""

        return cls._stripped(event).matches_envelope(cls._stripped(existing))

    @classmethod
    def _stripped(cls, event: _EventT) -> _EventT:
        return event.model_copy(
            update={cls._METADATA: cls._without_annotations(event.metadata)}
        )

    @classmethod
    def _without_annotations(cls, metadata: Mapping[str, object]) -> dict[str, object]:
        return {
            key: value
            for key, value in metadata.items()
            if key not in LedgerAmendment.METADATA_KEYS
        }


__all__ = ("EventRedeliveryResolver",)
