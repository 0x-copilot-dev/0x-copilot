"""BUG-14b — the decision record can carry counts without changing its identity.

Widening an append-only record that is digested, mirrored into a durable event,
and read back with an equality check has three ways to break the journal, and
each one has a section here.

* The **digest** is the record's identity. Adding a measurement to it would
  re-key every ``decision_digest`` ever written and make a producer that
  measured nothing digest differently from the rows it replaces.
* The **mirror** is checked for equality on every read. A record and the payload
  it is carried by that disagree — including about a key one of them has learned
  to spell and the other has not — raise
  :class:`RunControlJournalCorruption` and take the run's whole decision
  lineage with them.
* The **bounds** are enforced at validation. A producer that can generate a
  value the record refuses turns a measurement into a dropped row.

Every section below is asserted against the real
:class:`EventJournalRunControlStore`, not against a re-implementation of it.
"""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib

import pytest

from agent_runtime.api.run_control_store import EventJournalRunControlStore
from agent_runtime.control_plane.contracts import (
    DECISION_COUNT_CEILINGS,
    DECISION_COUNT_FIELDS,
    RunControlDecision,
)
from agent_runtime.control_plane.feature_modes import AgentQualityFeature
from agent_runtime.control_plane.ports import RunControlJournalCorruption
from agent_runtime.execution.contracts import StreamEventSource
from runtime_api.schemas import (
    RuntimeActivityKind,
    RuntimeApiEventType,
    RuntimeEventEnvelope,
)
from runtime_api.schemas.events import QualityDecisionPayload

_DIGEST = "a" * 64
_RUN = "run_legacy"
_SNAPSHOT = "snap"
_DECISION = "f3.capability_search.1"
_WHEN = datetime(2026, 7, 29, tzinfo=UTC)

#: The digest this exact decision had on the commit *before* the numeric
#: extension reached ``RunControlDecision`` — measured there, then written out
#: here. A literal is the only form of this assertion that a change to
#: ``digest_payload`` cannot satisfy by also changing what it is compared
#: against, which is what makes it a real guard on "existing digests do not
#: move" rather than a restatement of whatever the code now computes.
_DIGEST_BEFORE_THE_EXTENSION = (
    "ef0e3e79a49b252912b7c2828085a32d775fd54b0a4ec7b3abb145d5bd8067fa"
)


def _decision(**counts: int | None) -> RunControlDecision:
    return RunControlDecision.create(
        decision_id=_DECISION,
        run_id=_RUN,
        snapshot_id=_SNAPSHOT,
        phase="capability_search",
        feature=AgentQualityFeature.F3_CAPABILITY_DISCOVERY,
        policy_revision="capability-r1",
        input_digest=_DIGEST,
        outcome_code="ok",
        created_at=_WHEN,
        **counts,
    )


def _envelope(payload: dict[str, object]) -> RuntimeEventEnvelope:
    """One event shaped exactly as the store's own writer shapes it."""

    identity = hashlib.sha256(f"{_RUN}:{_DECISION}".encode()).hexdigest()
    return RuntimeEventEnvelope(
        event_id=f"quality_decision:{identity}",
        run_id=_RUN,
        conversation_id="conv_bug14b",
        trace_id="trace_bug14b",
        source=StreamEventSource.RUNTIME,
        event_type=RuntimeApiEventType.QUALITY_DECISION,
        activity_kind=RuntimeActivityKind.EVENT,
        sequence_no=1,
        payload=payload,
    )


class TestTheDigestIsTheDecisionsIdentityNotItsMeasurement:
    """The exclusion with the widest blast radius, asserted four ways."""

    def test_a_measured_decision_digests_like_an_unmeasured_one(self) -> None:
        """The mutation check.

        Putting the four counters into ``digest_payload`` makes this fail. That
        is the whole point of the assertion: it is the cheapest possible tripwire
        on a change that would silently re-key every decision in every journal.
        """

        assert (
            _decision(
                candidate_count=4,
                selection_rank=2,
                result_tokens=180,
                model_turns=1,
            ).decision_digest
            == _decision().decision_digest
        )

    def test_the_unmeasured_digest_is_the_one_written_before_the_extension(
        self,
    ) -> None:
        """Not "unchanged by this change" — unchanged, full stop."""

        assert _decision().decision_digest == _DIGEST_BEFORE_THE_EXTENSION

    def test_no_counter_appears_in_the_digest_body(self) -> None:
        body = _decision(candidate_count=4, model_turns=1).digest_payload()

        assert not set(body) & set(DECISION_COUNT_FIELDS)

    def test_the_digest_still_binds_everything_that_is_identity(self) -> None:
        """The exclusion is narrow: only the counters left, nothing else."""

        body = _decision().digest_payload()

        assert set(body) == {
            "schema_version",
            "decision_id",
            "run_id",
            "snapshot_id",
            "phase",
            "feature",
            "policy_revision",
            "input_digest",
            "outcome_code",
            "record_ref",
            "parent_decision_refs",
        }

    def test_a_forged_digest_is_still_refused(self) -> None:
        """Excluding a field is not the same as relaxing the check."""

        with pytest.raises(ValueError, match="digest does not match"):
            _decision().model_copy(
                update={"phase": "capability_invoke"}
            ).model_validate(
                {
                    **_decision().model_dump(),
                    "phase": "capability_invoke",
                }
            )


class TestTheRecordAndItsEventPayloadStayInAgreement:
    """The round trip the journal raises corruption over."""

    @pytest.mark.parametrize(
        "counts",
        [
            {},
            {"candidate_count": 0, "selection_rank": 0},
            {"candidate_count": 4, "selection_rank": 2, "result_tokens": 180},
            {"result_tokens": 180, "model_turns": 1},
            dict.fromkeys(DECISION_COUNT_FIELDS, 0),
        ],
        ids=["none", "observed-zeros", "partial", "cost-only", "all-zero"],
    )
    def test_every_observation_shape_survives_the_round_trip(
        self, counts: dict[str, int]
    ) -> None:
        decision = _decision(**counts)

        payload = EventJournalRunControlStore._decision_payload(decision)
        restored = EventJournalRunControlStore._decision_from_event(_envelope(payload))

        assert restored == decision
        assert restored.decision_digest == decision.decision_digest

    def test_an_observed_zero_and_an_unobserved_field_stay_different(self) -> None:
        """The distinction the ``unauthorized_probe`` ceilings rest on."""

        observed = EventJournalRunControlStore._decision_payload(
            _decision(candidate_count=0)
        )
        unobserved = EventJournalRunControlStore._decision_payload(_decision())

        assert observed["candidate_count"] == 0
        assert unobserved["candidate_count"] is None

    def test_a_row_written_before_the_extension_still_replays(self) -> None:
        """The keys did not exist when older rows were written.

        A stored payload that predates the extension carries none of the four
        keys. It has to read back as the same decision, with the same digest,
        rather than as a mirror mismatch — otherwise this change would make
        every existing journal unreadable.
        """

        decision = _decision()
        legacy = {
            key: value
            for key, value in EventJournalRunControlStore._decision_payload(
                decision
            ).items()
            if key not in DECISION_COUNT_FIELDS
        }
        assert not set(legacy) & set(DECISION_COUNT_FIELDS)

        restored = EventJournalRunControlStore._decision_from_event(_envelope(legacy))

        assert restored == decision
        assert restored.decision_digest == _DIGEST_BEFORE_THE_EXTENSION

    def test_tampering_with_an_identity_field_is_still_caught(self) -> None:
        """The digest is what guards the row, and it still guards it."""

        payload = EventJournalRunControlStore._decision_payload(_decision())
        tampered = {**payload, "outcome_code": "capability_not_found"}

        with pytest.raises(RunControlJournalCorruption):
            EventJournalRunControlStore._decision_from_event(_envelope(tampered))

    @pytest.mark.parametrize("name", DECISION_COUNT_FIELDS)
    def test_tampering_with_a_counter_is_not_caught_and_that_is_the_trade(
        self, name: str
    ) -> None:
        """The honest price of keeping measurements out of the identity digest.

        A counter is excluded from ``digest_payload``, so an edited counter
        re-digests to the same value and reads back clean. That is stated here
        as an assertion rather than left implicit, because it is the one thing
        the exclusion costs and it should be discovered by reading this test
        rather than by trusting a count nobody signed.

        It is the right trade for this record: the alternative re-keys every
        decision ever written, and these rows already carry no authority — they
        are a measurement of a decision whose identity is signed beside them.
        """

        payload = EventJournalRunControlStore._decision_payload(_decision())
        tampered = {**payload, name: 7}

        restored = EventJournalRunControlStore._decision_from_event(_envelope(tampered))

        assert getattr(restored, name) == 7
        assert restored.decision_digest == _DIGEST_BEFORE_THE_EXTENSION


class TestTheRecordAndThePayloadAgreeOnTheirBounds:
    """A producer that can outrun the record turns a measurement into a gap."""

    @pytest.mark.parametrize("name", DECISION_COUNT_FIELDS)
    def test_the_record_ceiling_matches_the_payload_ceiling(self, name: str) -> None:
        """Two contracts, one number, restated in two packages.

        ``agent_runtime`` must not import the presentation schema, so the
        ceilings are declared twice. This is the assertion that keeps the copies
        honest: widen one without the other and it fails.
        """

        (payload_bound,) = [
            item.le
            for item in QualityDecisionPayload.model_fields[name].metadata
            if getattr(item, "le", None) is not None
        ]

        assert DECISION_COUNT_CEILINGS[name] == payload_bound

    @pytest.mark.parametrize("name", DECISION_COUNT_FIELDS)
    def test_a_value_at_the_ceiling_is_accepted_end_to_end(self, name: str) -> None:
        decision = _decision(**{name: DECISION_COUNT_CEILINGS[name]})

        payload = EventJournalRunControlStore._decision_payload(decision)
        restored = EventJournalRunControlStore._decision_from_event(_envelope(payload))

        assert getattr(restored, name) == DECISION_COUNT_CEILINGS[name]

    @pytest.mark.parametrize("name", DECISION_COUNT_FIELDS)
    def test_a_value_past_the_ceiling_is_refused_rather_than_stored(
        self, name: str
    ) -> None:
        """The reason the F3 producer clamps instead of passing values through."""

        with pytest.raises(ValueError):
            _decision(**{name: DECISION_COUNT_CEILINGS[name] + 1})

    @pytest.mark.parametrize("name", DECISION_COUNT_FIELDS)
    def test_a_negative_count_is_refused(self, name: str) -> None:
        with pytest.raises(ValueError):
            _decision(**{name: -1})
