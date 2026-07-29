"""BUG-14 — the decision row's bounded numeric extension, as a contract.

``quality.decision.v1`` grew four numbers so F1 can score selection recall and
end-to-end quality from real runtime events instead of from authored fixtures.
A durable event family is the wrong place to be casual, so this module pins the
four properties that keep the extension inside PRD 9.3's permission rather than
widening it:

* it is **bounded** — every field has a ceiling as well as a floor, so a
  runaway producer is rejected at validation rather than persisted;
* it is **closed** — four explicitly named fields, not a numeric map whose key
  set is whatever a producer felt like measuring;
* it is **body-free** — there is no field here a query, capability name,
  description, argument, or result can enter through. Proven by seeding a
  secret and trying to get it in, rather than by reviewing field names;
* it is **additive** — rows written before the extension still validate, and
  still normalize through the run-event envelope unchanged.
"""

from __future__ import annotations

from datetime import UTC, datetime
import json

from pydantic import ValidationError
import pytest

from agent_runtime.execution.contracts import StreamEventSource
from runtime_api.schemas import (
    RuntimeActivityKind,
    RuntimeApiEventType,
    RuntimeEventEnvelope,
)
from runtime_api.schemas.events import (
    QualityDecisionPayload,
    RuntimeEventPresentationProjector,
)


_DIGEST = "b" * 64
_NOW = datetime(2026, 7, 29, tzinfo=UTC)

#: The four members of the numeric extension, with the ceiling each declares.
_NUMERIC_FIELDS = (
    ("candidate_count", 64),
    ("selection_rank", 64),
    ("result_tokens", 1_000_000),
    ("model_turns", 1_000),
)

#: Seeded through every field the extension could plausibly leak through. The
#: point is to *try to get these in*, not to read the field list and conclude
#: they cannot be.
_SECRET_QUERY = "linear issues for PROJECT-ZEPHYR-CONFIDENTIAL"
_SECRET_NAME = "acme-internal-payroll-connector"
_SECRET_ARGUMENT = "TEAM-SECRET-DO-NOT-PERSIST"


def _payload(**overrides: object) -> QualityDecisionPayload:
    values: dict[str, object] = {
        "schema_version": 1,
        "decision_id": "f3.capability_search.1",
        "decision_digest": _DIGEST,
        "snapshot_id": "snapshot_bug14",
        "phase": "capability_search",
        "feature": "f3",
        "policy_revision": "capability-v1",
        "input_digest": _DIGEST,
        "outcome_code": "ok",
        "created_at": _NOW,
    }
    values.update(overrides)
    return QualityDecisionPayload(**values)  # type: ignore[arg-type]


class TestTheExtensionIsAdditive:
    """Older rows keep working, which is what makes this safe to deploy."""

    def test_a_row_without_any_numeric_validates(self) -> None:
        payload = _payload()

        assert payload.candidate_count is None
        assert payload.selection_rank is None
        assert payload.result_tokens is None
        assert payload.model_turns is None

    def test_a_row_without_any_numeric_normalizes(self) -> None:
        """Replay of a pre-extension row still normalizes rather than rejecting.

        ``payload_for_event`` re-validates and re-dumps the stored mapping and
        returns ``{}`` for anything malformed, so this is the path a persisted
        row actually takes on replay — and the assertion that matters is that a
        row missing all four keys is not treated as malformed.
        """

        stored = {
            "schema_version": 1,
            "decision_id": "f3.capability_search.1",
            "decision_digest": _DIGEST,
            "snapshot_id": "snapshot_bug14",
            "phase": "capability_search",
            "feature": "f3",
            "policy_revision": "capability-v1",
            "input_digest": _DIGEST,
            "outcome_code": "ok",
            "record_ref": None,
            "parent_decision_refs": [],
            "created_at": _NOW.isoformat(),
        }

        normalized = RuntimeEventPresentationProjector.payload_for_event(
            event_type=RuntimeApiEventType.QUALITY_DECISION,
            payload=stored,
        )

        assert normalized["outcome_code"] == "ok"
        for name, _ceiling in _NUMERIC_FIELDS:
            assert normalized[name] is None

    def test_a_row_with_numerics_survives_normalization(self) -> None:
        """The new half of the same path: the values are carried, not dropped."""

        normalized = RuntimeEventPresentationProjector.payload_for_event(
            event_type=RuntimeApiEventType.QUALITY_DECISION,
            payload=_payload(
                candidate_count=4,
                selection_rank=1,
                result_tokens=180,
                model_turns=1,
            ).model_dump(mode="json"),
        )

        assert normalized["candidate_count"] == 4
        assert normalized["selection_rank"] == 1
        assert normalized["result_tokens"] == 180
        assert normalized["model_turns"] == 1

    def test_an_out_of_range_numeric_is_rejected_on_replay(self) -> None:
        """The ceiling is enforced by the reader too, not only by the producer."""

        stored = _payload(candidate_count=10).model_dump(mode="json")
        stored["candidate_count"] = 10_000

        normalized = RuntimeEventPresentationProjector.payload_for_event(
            event_type=RuntimeApiEventType.QUALITY_DECISION,
            payload=stored,
        )

        assert normalized == {}

    def test_the_envelope_carries_the_numerics_verbatim(self) -> None:
        """What a projector downstream of the journal actually reads."""

        envelope = RuntimeEventEnvelope(
            run_id="run_bug14",
            conversation_id="conv_bug14",
            trace_id="trace_bug14",
            source=StreamEventSource.RUNTIME,
            event_type=RuntimeApiEventType.QUALITY_DECISION,
            activity_kind=RuntimeActivityKind.EVENT,
            sequence_no=1,
            payload=_payload(
                candidate_count=4,
                selection_rank=1,
                result_tokens=180,
                model_turns=1,
            ).model_dump(mode="json"),
        )

        assert envelope.payload["candidate_count"] == 4
        assert envelope.payload["selection_rank"] == 1

    def test_an_observed_zero_is_not_the_same_as_an_absent_field(self) -> None:
        """The distinction every ``maximum_`` bound of zero depends on."""

        assert _payload(candidate_count=0).candidate_count == 0
        assert _payload().candidate_count is None


class TestTheExtensionIsBounded:
    """A durable row must not be able to hold an arbitrary integer."""

    @pytest.mark.parametrize(("name", "ceiling"), _NUMERIC_FIELDS)
    def test_the_ceiling_is_accepted(self, name: str, ceiling: int) -> None:
        assert getattr(_payload(**{name: ceiling}), name) == ceiling

    @pytest.mark.parametrize(("name", "ceiling"), _NUMERIC_FIELDS)
    def test_one_above_the_ceiling_is_refused(self, name: str, ceiling: int) -> None:
        with pytest.raises(ValidationError):
            _payload(**{name: ceiling + 1})

    @pytest.mark.parametrize(("name", "_ceiling"), _NUMERIC_FIELDS)
    def test_a_negative_value_is_refused(self, name: str, _ceiling: int) -> None:
        with pytest.raises(ValidationError):
            _payload(**{name: -1})

    def test_the_ceilings_are_distinct_rather_than_one_shared_bound(self) -> None:
        """Each quantity states its own bound, which is why they are named.

        A single shared ceiling would let a rank as large as a token count, and
        a numeric map could not express a per-quantity bound at all.
        """

        with pytest.raises(ValidationError):
            _payload(candidate_count=1_000)
        assert _payload(result_tokens=1_000).result_tokens == 1_000


class TestTheExtensionIsClosed:
    """Four named fields, and no room for a fifth a producer invents."""

    def test_an_unknown_numeric_key_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            _payload(scanned_count=12)

    def test_the_extension_admits_integers_only(self) -> None:
        """Structural: nothing here is typed loosely enough to hold a body."""

        for name, _ceiling in _NUMERIC_FIELDS:
            annotation = QualityDecisionPayload.model_fields[name].annotation
            assert annotation == (int | None), f"{name} is not a bounded count"


class TestTheExtensionIsBodyFree:
    """Try to smuggle a secret in; the contract must refuse it.

    This is deliberately an attempt rather than a review of field names: a
    reviewer can be wrong about what a field accepts, but a constructor that
    raises cannot be.
    """

    @pytest.mark.parametrize(("name", "_ceiling"), _NUMERIC_FIELDS)
    @pytest.mark.parametrize("secret", [_SECRET_QUERY, _SECRET_NAME, _SECRET_ARGUMENT])
    def test_a_secret_cannot_enter_through_a_numeric_field(
        self, name: str, _ceiling: int, secret: str
    ) -> None:
        with pytest.raises(ValidationError):
            _payload(**{name: secret})

    @pytest.mark.parametrize(("name", "_ceiling"), _NUMERIC_FIELDS)
    def test_a_numeric_field_will_not_take_a_container(
        self, name: str, _ceiling: int
    ) -> None:
        """The shape a "just make it a map" extension would have accepted."""

        with pytest.raises(ValidationError):
            _payload(**{name: {"candidates": [_SECRET_NAME]}})

    def test_a_fully_populated_row_serializes_without_any_seeded_secret(self) -> None:
        """Grep the real serialization rather than trusting the field list.

        The row is built at its most populated, then searched for every seeded
        secret and for the substrings a leak would most likely carry.
        """

        serialized = json.dumps(
            _payload(
                candidate_count=10,
                selection_rank=3,
                result_tokens=4_096,
                model_turns=2,
            ).model_dump(mode="json")
        )

        for forbidden in (
            _SECRET_QUERY,
            _SECRET_NAME,
            _SECRET_ARGUMENT,
            "PROJECT-ZEPHYR",
            "CONFIDENTIAL",
            "payroll",
            "arguments",
            "candidates",
            "https://",
            "/Users/",
        ):
            assert forbidden not in serialized, forbidden
