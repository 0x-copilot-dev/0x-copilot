"""Unit tests for the Context Occupancy Ledger records (PRD-04, design §4.5).

The module under test is contracts plus arithmetic, so these tests are about the
arithmetic being *honest* rather than about it being computed at all. Four
properties carry the design and each has its own class below:

1. **No fabrication (§3.3).** ``estimated_input_tokens`` is the plain sum of the
   segments. Segments are never scaled toward the provider's authoritative
   total, and the disagreement is promoted into ``unattributed_delta`` — signed,
   so an over-count reads as negative rather than as a suspicious zero.
2. **Two residuals, two meanings (§4.4).** ``undeclared_tokens`` (a contract bug)
   and ``unattributed_delta`` (tokenizer/wire drift) never absorb one another.
3. **Honest absence.** ``free_tokens`` is ``None`` when the model is missing from
   the pricing catalog, and negative when a request genuinely overflows the
   window. Neither case is rounded into a plausible-looking zero.
4. **No content leakage (§6.5).** ``detail`` is a bounded, single-line
   identifier, and the typed failure carries field paths only — occupancy is
   exposed over an HTTP read API.

Counting is faked throughout so segment token totals are exact literals; the
real fallback chain has its own suite in ``test_context_token_counter.py``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Final

import pytest
from pydantic import ValidationError

from agent_runtime.observability.context_occupancy import (
    ContextOccupancyError,
    ContextOccupancySnapshot,
    ContextSegment,
    GraphScope,
    SnapshotBuilder,
)
from agent_runtime.observability.context_origin import (
    MAX_LABEL_LENGTH as MAX_CONTEXT_LABEL_LENGTH,
)
from agent_runtime.observability.context_origin import (
    MAX_NAME_LENGTH,
    MAX_OWNER_LENGTH,
    UNDECLARED_CONTEXT_LABEL,
    ContextLifecycle,
    ContextOrigin,
    ContextSegmentClass,
)
from agent_runtime.observability.context_token_counter import (
    ContextTokenCounter,
    DigestTokenCache,
    TokenCounterSource,
)
from agent_runtime.prompts.assembly import PromptCacheEligibility


class LengthCounter:
    """A ``TokenCounterPort`` whose count is a deterministic function of the text.

    ``len(content) // 4`` matches the repo's documented heuristic, which keeps the
    expected numbers in these tests readable while still varying with the input —
    a constant-answer fake would let a builder that ignored a segment pass.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def count(self, *, model: str, messages: Sequence[Mapping[str, str]]) -> int:
        content = "".join(str(message.get("content", "")) for message in messages)
        self.calls.append((model, content))
        return len(content) // 4


class OccupancyFixtureMixin:
    """Declarations, counters, and snapshot construction shared by the tests."""

    MODEL: Final[str] = "gpt-5.4-mini"
    PROVIDER: Final[str] = "openai"
    MODEL_FAMILY: Final[str] = "gpt-5.4"
    CALL_ID: Final[str] = "call-0001"

    TOOL_ORIGIN: Final[ContextOrigin] = ContextOrigin(
        owner="agent_runtime.capabilities.backends",
        name="publish_artifact",
        segment_class=ContextSegmentClass.TOOLS,
        lifecycle=ContextLifecycle.RESIDENT,
        cache_eligibility=PromptCacheEligibility.STABLE_PREFIX,
    )
    SYSTEM_ORIGIN: Final[ContextOrigin] = ContextOrigin(
        owner="agent_runtime.capabilities.mcp",
        name="server_cards",
        segment_class=ContextSegmentClass.SYSTEM,
        lifecycle=ContextLifecycle.PER_TURN,
        cache_eligibility=PromptCacheEligibility.NEVER,
    )
    THIRD_PARTY_ORIGIN: Final[ContextOrigin] = ContextOrigin(
        owner="deepagents.middleware.subagents",
        name="task_tool_description",
        segment_class=ContextSegmentClass.TOOLS,
        lifecycle=ContextLifecycle.RESIDENT,
        third_party=True,
    )

    def counter(self) -> ContextTokenCounter:
        """A counter over an isolated cache — never the process-wide one."""

        return ContextTokenCounter(
            tokenizer=LengthCounter(),
            cache=DigestTokenCache(max_entries=8),
        )

    def segment(
        self,
        *,
        estimated_tokens: int,
        label: str = "agent_runtime.capabilities:tool",
        segment_class: ContextSegmentClass = ContextSegmentClass.TOOLS,
        lifecycle: ContextLifecycle = ContextLifecycle.RESIDENT,
        detail: str | None = None,
        item_count: int = 1,
        third_party: bool = False,
    ) -> ContextSegment:
        """A hand-built segment with an exact token count, for arithmetic tests."""

        return ContextSegment(
            segment_class=segment_class,
            label=label,
            lifecycle=lifecycle,
            third_party=third_party,
            detail=detail,
            byte_count=estimated_tokens * 4,
            estimated_tokens=estimated_tokens,
            item_count=item_count,
            counter_source=TokenCounterSource.TOKENIZER,
        )

    def undeclared_segment(self, *, estimated_tokens: int) -> ContextSegment:
        return self.segment(
            estimated_tokens=estimated_tokens,
            label=UNDECLARED_CONTEXT_LABEL,
        )

    def build(
        self,
        *,
        segments: Sequence[ContextSegment] = (),
        graph_scope: GraphScope = GraphScope.ROOT,
        **overrides: object,
    ) -> ContextOccupancySnapshot:
        return SnapshotBuilder().build(
            model_call_id=self.CALL_ID,
            graph_scope=graph_scope,
            provider=self.PROVIDER,
            model_family=self.MODEL_FAMILY,
            segments=segments,
            **overrides,  # type: ignore[arg-type]
        )


class TestGraphScope:
    def test_values_are_the_persisted_wire_strings(self) -> None:
        assert GraphScope.ROOT == "root"
        assert GraphScope.SUBAGENT == "subagent"

    def test_enum_is_closed_to_the_two_windows(self) -> None:
        assert {member.value for member in GraphScope} == {"root", "subagent"}


class TestContextSegmentMeasurement(OccupancyFixtureMixin):
    def test_every_declared_attribute_comes_from_the_origin(self) -> None:
        # The declaration is the single source of truth for how a number is
        # interpreted; the measurement site gets no second opinion (§4.1).
        segment = ContextSegment.measure(
            "a" * 40,
            counter=self.counter(),
            model=self.MODEL,
            origin=self.TOOL_ORIGIN,
            detail="publish_artifact",
        )

        assert segment.label == "agent_runtime.capabilities.backends:publish_artifact"
        assert segment.segment_class is ContextSegmentClass.TOOLS
        assert segment.lifecycle is ContextLifecycle.RESIDENT
        assert segment.cache_eligibility is PromptCacheEligibility.STABLE_PREFIX
        assert segment.third_party is False
        assert segment.is_undeclared is False

    def test_third_party_flag_rides_the_declaration(self) -> None:
        segment = ContextSegment.measure(
            "b" * 8,
            counter=self.counter(),
            model=self.MODEL,
            origin=self.THIRD_PARTY_ORIGIN,
        )

        assert segment.third_party is True

    def test_byte_count_is_utf8_bytes_not_characters(self) -> None:
        # Characters would undercount every non-ASCII segment, and badly
        # undercount base64 file content (audit item R).
        segment = ContextSegment.measure(
            "🙂",
            counter=self.counter(),
            model=self.MODEL,
            origin=self.SYSTEM_ORIGIN,
        )

        assert segment.byte_count == 4

    def test_token_count_and_source_come_from_the_counter(self) -> None:
        segment = ContextSegment.measure(
            "x" * 40,
            counter=self.counter(),
            model=self.MODEL,
            origin=self.SYSTEM_ORIGIN,
        )

        assert segment.estimated_tokens == 10
        assert segment.counter_source is TokenCounterSource.TOKENIZER

    def test_non_string_text_degrades_to_empty_instead_of_raising(self) -> None:
        segment = ContextSegment.measure(
            None,  # type: ignore[arg-type]
            counter=self.counter(),
            model=self.MODEL,
            origin=self.SYSTEM_ORIGIN,
        )

        assert (segment.byte_count, segment.estimated_tokens) == (0, 0)

    def test_digest_takes_the_memoized_path(self) -> None:
        tokenizer = LengthCounter()
        counter = ContextTokenCounter(
            tokenizer=tokenizer,
            cache=DigestTokenCache(max_entries=8),
        )

        for _ in range(3):
            ContextSegment.measure(
                "y" * 40,
                counter=counter,
                model=self.MODEL,
                origin=self.TOOL_ORIGIN,
                digest="tool-schema-rev-1",
            )

        assert len(tokenizer.calls) == 1

    def test_omitting_the_digest_recounts_every_time(self) -> None:
        tokenizer = LengthCounter()
        counter = ContextTokenCounter(
            tokenizer=tokenizer,
            cache=DigestTokenCache(max_entries=8),
        )

        for _ in range(3):
            ContextSegment.measure(
                "y" * 40,
                counter=counter,
                model=self.MODEL,
                origin=self.TOOL_ORIGIN,
            )

        assert len(tokenizer.calls) == 3

    def test_undeclared_measurement_uses_the_reserved_label(self) -> None:
        segment = ContextSegment.measure_undeclared(
            "z" * 40,
            counter=self.counter(),
            model=self.MODEL,
            segment_class=ContextSegmentClass.MESSAGES,
            lifecycle=ContextLifecycle.PER_TURN,
            detail="messages[12..37]",
        )

        assert segment.label == UNDECLARED_CONTEXT_LABEL
        assert segment.is_undeclared is True
        assert segment.segment_class is ContextSegmentClass.MESSAGES
        assert segment.third_party is False
        assert segment.cache_eligibility is None

    def test_item_count_rolls_dynamic_contributors_under_one_label(self) -> None:
        # Dynamic contributors declare per contributor, not per instance (§4.1).
        segment = ContextSegment.measure(
            "q" * 40,
            counter=self.counter(),
            model=self.MODEL,
            origin=self.SYSTEM_ORIGIN,
            item_count=7,
        )

        assert segment.item_count == 7


class TestContextSegmentDetailBounds(OccupancyFixtureMixin):
    def test_detail_at_the_bound_is_accepted(self) -> None:
        assert self.segment(estimated_tokens=1, detail="d" * 200).detail == "d" * 200

    def test_detail_over_the_bound_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            self.segment(estimated_tokens=1, detail="d" * 201)

    def test_documented_bound_is_two_hundred(self) -> None:
        assert ContextSegment.MAX_DETAIL_LENGTH == 200

    @pytest.mark.parametrize(
        "leaky_detail",
        [
            "user asked:\nplease summarise the attached deck",
            "tool result\ttruncated",
            "with a null \x00 byte",
        ],
    )
    def test_multiline_or_control_detail_is_rejected_as_content(
        self,
        leaky_detail: str,
    ) -> None:
        # Every legitimate detail is one printable line (tool name, fragment_id,
        # "messages[12..37]"); pasted content almost never is (§6.5).
        with pytest.raises(ValidationError):
            self.segment(estimated_tokens=1, detail=leaky_detail)

    def test_absent_detail_stays_absent(self) -> None:
        assert self.segment(estimated_tokens=1).detail is None


class TestContextSegmentLabelBound(OccupancyFixtureMixin):
    """The label bound must mirror ContextOrigin's, not guess at it (§6.4).

    Regression cover for a live fail-open violation: this contract bounded
    ``label`` at 240 while a valid ``ContextOrigin`` can spell 401, and
    :meth:`ContextSegment.measure` passes ``origin.label`` straight through. A
    long-but-legal declaration therefore raised ``ValidationError`` inside
    measurement — on the model-call path, where §6.4 says a measurement concern
    must never fail a run.
    """

    def test_the_bound_is_derived_from_the_origin_contract(self) -> None:
        # The assertion that actually prevents the regression: not "== 401",
        # which would drift the same way the literal did, but that the two
        # bounds are the SAME object of truth.
        assert ContextSegment.MAX_LABEL_LENGTH == MAX_CONTEXT_LABEL_LENGTH
        assert MAX_CONTEXT_LABEL_LENGTH == MAX_OWNER_LENGTH + 1 + MAX_NAME_LENGTH

    def test_the_widest_legal_declaration_measures_without_raising(self) -> None:
        widest = ContextOrigin(
            owner=("a" * (MAX_OWNER_LENGTH - 2)) + ".b",
            name="n" * MAX_NAME_LENGTH,
            segment_class=ContextSegmentClass.TOOLS,
            lifecycle=ContextLifecycle.RESIDENT,
        )
        assert len(widest.label) == MAX_CONTEXT_LABEL_LENGTH

        measured = ContextSegment.measure(
            "some tool schema text",
            counter=self.counter(),
            model=self.MODEL,
            origin=widest,
        )

        assert measured.label == widest.label


class TestSegmentOrdering(OccupancyFixtureMixin):
    def test_snapshot_orders_by_class_then_label_then_detail(self) -> None:
        scrambled = (
            self.segment(
                estimated_tokens=1,
                segment_class=ContextSegmentClass.TOOLS,
                label="owner:b",
            ),
            self.segment(
                estimated_tokens=1,
                segment_class=ContextSegmentClass.SYSTEM,
                label="owner:z",
            ),
            self.segment(
                estimated_tokens=1,
                segment_class=ContextSegmentClass.TOOLS,
                label="owner:a",
                detail="second",
            ),
            self.segment(
                estimated_tokens=1,
                segment_class=ContextSegmentClass.TOOLS,
                label="owner:a",
                detail="first",
            ),
            self.segment(
                estimated_tokens=1,
                segment_class=ContextSegmentClass.MESSAGES,
                label="owner:m",
            ),
        )

        snapshot = self.build(segments=scrambled)

        assert [
            (s.segment_class.value, s.label, s.detail) for s in snapshot.segments
        ] == [
            ("messages", "owner:m", None),
            ("system", "owner:z", None),
            ("tools", "owner:a", "first"),
            ("tools", "owner:a", "second"),
            ("tools", "owner:b", None),
        ]

    def test_absent_detail_sorts_before_any_present_detail(self) -> None:
        snapshot = self.build(
            segments=(
                self.segment(estimated_tokens=1, label="owner:a", detail="aaa"),
                self.segment(estimated_tokens=1, label="owner:a"),
            )
        )

        assert [segment.detail for segment in snapshot.segments] == [None, "aaa"]

    def test_direct_construction_canonicalizes_too(self) -> None:
        # A snapshot read back from JSONB must order identically to one the
        # builder produced, or golden-fixture diffs are noise.
        snapshot = ContextOccupancySnapshot(
            model_call_id=self.CALL_ID,
            graph_scope=GraphScope.ROOT,
            provider=self.PROVIDER,
            model_family=self.MODEL_FAMILY,
            segments=(
                self.segment(estimated_tokens=1, label="owner:z"),
                self.segment(estimated_tokens=1, label="owner:a"),
            ),
        )

        assert [segment.label for segment in snapshot.segments] == [
            "owner:a",
            "owner:z",
        ]

    def test_ordering_is_stable_for_fully_tied_segments(self) -> None:
        first = self.segment(estimated_tokens=3, label="owner:a", detail="same")
        second = self.segment(estimated_tokens=9, label="owner:a", detail="same")

        snapshot = self.build(segments=(first, second))

        assert [s.estimated_tokens for s in snapshot.segments] == [3, 9]

    def test_segments_are_a_tuple(self) -> None:
        assert isinstance(self.build().segments, tuple)


class TestDerivedTotals(OccupancyFixtureMixin):
    def test_estimated_is_the_plain_sum_of_segments(self) -> None:
        snapshot = self.build(
            segments=(
                self.segment(estimated_tokens=650, label="owner:publish_artifact"),
                self.segment(estimated_tokens=364, label="owner:revise_artifact"),
                self.segment(estimated_tokens=323, label="owner:stage_rowset_write"),
            )
        )

        assert snapshot.estimated_input_tokens == 1337

    def test_no_segments_is_zero_not_an_error(self) -> None:
        snapshot = self.build()

        assert snapshot.estimated_input_tokens == 0
        assert snapshot.segments == ()

    def test_segments_are_never_scaled_toward_the_provider_total(self) -> None:
        # §3.3: rescaling would manufacture precision we do not have.
        segments = (
            self.segment(estimated_tokens=100, label="owner:a"),
            self.segment(estimated_tokens=200, label="owner:b"),
        )

        snapshot = self.build(segments=segments, provider_input_tokens=3000)

        assert [s.estimated_tokens for s in snapshot.segments] == [100, 200]
        assert snapshot.estimated_input_tokens == 300

    def test_delta_is_positive_when_the_provider_billed_more(self) -> None:
        snapshot = self.build(
            segments=(self.segment(estimated_tokens=900),),
            provider_input_tokens=1000,
        )

        assert snapshot.unattributed_delta == 100

    def test_delta_is_negative_when_we_over_counted(self) -> None:
        # The per-message envelope bias documented on the counter shows up here
        # rather than being smeared across segments.
        snapshot = self.build(
            segments=(self.segment(estimated_tokens=1100),),
            provider_input_tokens=1000,
        )

        assert snapshot.unattributed_delta == -100

    def test_delta_is_zero_when_the_provider_reported_nothing(self) -> None:
        # A delta against an absent total is not a small residual, it is no
        # measurement at all — inventing one would put fiction in the honesty
        # field.
        snapshot = self.build(segments=(self.segment(estimated_tokens=900),))

        assert snapshot.provider_input_tokens is None
        assert snapshot.unattributed_delta == 0

    def test_a_reported_zero_is_a_real_total_not_an_absent_one(self) -> None:
        snapshot = self.build(
            segments=(self.segment(estimated_tokens=900),),
            provider_input_tokens=0,
        )

        assert snapshot.unattributed_delta == -900

    def test_cache_columns_are_carried_through_untouched(self) -> None:
        snapshot = self.build(
            segments=(self.segment(estimated_tokens=900),),
            provider_input_tokens=1000,
            cached_input_tokens=800,
            cache_creation_input_tokens=120,
        )

        assert snapshot.cached_input_tokens == 800
        assert snapshot.cache_creation_input_tokens == 120
        # §6.6: cache columns describe billing, never the occupancy arithmetic.
        assert snapshot.unattributed_delta == 100


class TestUndeclaredTokens(OccupancyFixtureMixin):
    def test_sums_only_segments_carrying_the_reserved_label(self) -> None:
        snapshot = self.build(
            segments=(
                self.segment(estimated_tokens=100, label="owner:declared"),
                self.undeclared_segment(estimated_tokens=30),
                self.undeclared_segment(estimated_tokens=12),
            )
        )

        assert snapshot.undeclared_tokens == 42

    def test_undeclared_bytes_still_count_as_occupancy(self) -> None:
        # The field names the offending subset; it does not carve it out of the
        # total, because those bytes really are in the window.
        snapshot = self.build(
            segments=(
                self.segment(estimated_tokens=100, label="owner:declared"),
                self.undeclared_segment(estimated_tokens=42),
            )
        )

        assert snapshot.estimated_input_tokens == 142

    def test_zero_when_every_contributor_declared(self) -> None:
        snapshot = self.build(
            segments=(self.segment(estimated_tokens=100, label="owner:declared"),)
        )

        assert snapshot.undeclared_tokens == 0

    def test_the_two_residuals_do_not_absorb_one_another(self) -> None:
        # §4.4: a contract bug and tokenizer drift are different problems with
        # different owners; collapsing them is the failure mode being avoided.
        snapshot = self.build(
            segments=(
                self.segment(estimated_tokens=100, label="owner:declared"),
                self.undeclared_segment(estimated_tokens=40),
            ),
            provider_input_tokens=150,
        )

        assert snapshot.undeclared_tokens == 40
        assert snapshot.unattributed_delta == 10

    def test_a_similar_looking_label_is_not_undeclared(self) -> None:
        snapshot = self.build(
            segments=(self.segment(estimated_tokens=40, label="owner:undeclared"),)
        )

        assert snapshot.undeclared_tokens == 0


class TestFreeTokens(OccupancyFixtureMixin):
    def test_none_when_the_model_is_absent_from_the_pricing_catalog(self) -> None:
        # None states "we do not know"; zero would assert a full window.
        snapshot = self.build(
            segments=(self.segment(estimated_tokens=900),),
            provider_input_tokens=1000,
        )

        assert snapshot.context_window_tokens is None
        assert snapshot.free_tokens is None

    def test_uses_the_provider_total_when_one_was_reported(self) -> None:
        snapshot = self.build(
            segments=(self.segment(estimated_tokens=900),),
            context_window_tokens=200_000,
            provider_input_tokens=1000,
        )

        assert snapshot.free_tokens == 199_000

    def test_falls_back_to_our_estimate_when_the_provider_is_silent(self) -> None:
        snapshot = self.build(
            segments=(self.segment(estimated_tokens=900),),
            context_window_tokens=200_000,
        )

        assert snapshot.free_tokens == 199_100

    def test_is_negative_when_the_request_overflows_the_window(self) -> None:
        # Clamping would hide a stale pricing row or an over-stuffed request.
        snapshot = self.build(
            segments=(self.segment(estimated_tokens=10),),
            context_window_tokens=1_000,
            provider_input_tokens=1_500,
        )

        assert snapshot.free_tokens == -500

    def test_zero_window_is_honoured_rather_than_treated_as_unknown(self) -> None:
        snapshot = self.build(
            segments=(self.segment(estimated_tokens=10),),
            context_window_tokens=0,
        )

        assert snapshot.free_tokens == -10


class TestScopeAndIdentity(OccupancyFixtureMixin):
    def test_subagent_free_space_is_computed_within_its_own_window(self) -> None:
        # §6.2: a child has its own window. Nothing here may consult the root.
        root = self.build(
            segments=(self.segment(estimated_tokens=50_000),),
            context_window_tokens=200_000,
            graph_scope=GraphScope.ROOT,
        )
        child = SnapshotBuilder().build(
            model_call_id="call-child",
            graph_scope=GraphScope.SUBAGENT,
            provider=self.PROVIDER,
            model_family=self.MODEL_FAMILY,
            segments=(self.segment(estimated_tokens=1_000),),
            context_window_tokens=200_000,
        )

        assert root.free_tokens == 150_000
        assert child.free_tokens == 199_000
        assert child.graph_scope is GraphScope.SUBAGENT

    def test_a_retry_is_a_second_snapshot_not_an_overwrite(self) -> None:
        # §6.3: identity is (model_call_id, attempt_ordinal).
        first = self.build(segments=(self.segment(estimated_tokens=100),))
        second = self.build(
            segments=(self.segment(estimated_tokens=140),),
            attempt_ordinal=2,
        )

        assert first.model_call_id == second.model_call_id
        assert (first.attempt_ordinal, second.attempt_ordinal) == (1, 2)
        assert first.estimated_input_tokens != second.estimated_input_tokens

    def test_assembly_record_id_links_to_the_assembled_prompt(self) -> None:
        snapshot = self.build(assembly_record_id="assembly-77")

        assert snapshot.assembly_record_id == "assembly-77"

    def test_assembly_record_id_is_optional(self) -> None:
        assert self.build().assembly_record_id is None

    def test_schema_version_is_pinned_to_one(self) -> None:
        snapshot = self.build()

        assert snapshot.schema_version == 1
        assert ContextOccupancySnapshot.SCHEMA_VERSION == 1

    def test_a_foreign_schema_version_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ContextOccupancySnapshot(
                schema_version=2,  # type: ignore[arg-type]
                model_call_id=self.CALL_ID,
                graph_scope=GraphScope.ROOT,
                provider=self.PROVIDER,
                model_family=self.MODEL_FAMILY,
            )


class TestBuilderFailure(OccupancyFixtureMixin):
    def test_invalid_input_raises_the_typed_error(self) -> None:
        with pytest.raises(ContextOccupancyError) as caught:
            SnapshotBuilder().build(
                model_call_id=self.CALL_ID,
                graph_scope=GraphScope.ROOT,
                provider="",
                model_family=self.MODEL_FAMILY,
            )

        assert "provider" in caught.value.field_paths

    def test_a_non_positive_attempt_ordinal_is_rejected(self) -> None:
        with pytest.raises(ContextOccupancyError) as caught:
            self.build(attempt_ordinal=0)

        assert "attempt_ordinal" in caught.value.field_paths

    def test_the_error_message_carries_field_paths_not_values(self) -> None:
        # §6.5: pydantic embeds the offending input in its own message; the
        # wrapper must not, because occupancy is read over HTTP.
        leaky = "leak-me-" + "x" * 300

        with pytest.raises(ContextOccupancyError) as caught:
            SnapshotBuilder().build(
                model_call_id=leaky,
                graph_scope=GraphScope.ROOT,
                provider=self.PROVIDER,
                model_family=self.MODEL_FAMILY,
            )

        assert "leak-me" not in str(caught.value)
        assert caught.value.field_paths == ("model_call_id",)

    def test_the_typed_error_is_a_runtime_error(self) -> None:
        assert issubclass(ContextOccupancyError, RuntimeError)


class TestEndToEndMeasuredSnapshot(OccupancyFixtureMixin):
    """One assembled snapshot from measured segments, as PRD-05 will build it."""

    def test_measured_segments_reconcile_against_a_provider_total(self) -> None:
        counter = self.counter()
        segments = (
            ContextSegment.measure(
                "t" * 400,
                counter=counter,
                model=self.MODEL,
                origin=self.TOOL_ORIGIN,
                detail="publish_artifact",
                digest="tool-rev-1",
            ),
            ContextSegment.measure(
                "s" * 200,
                counter=counter,
                model=self.MODEL,
                origin=self.SYSTEM_ORIGIN,
                detail="mcp_cards",
            ),
            ContextSegment.measure_undeclared(
                "u" * 40,
                counter=counter,
                model=self.MODEL,
                segment_class=ContextSegmentClass.MESSAGES,
                lifecycle=ContextLifecycle.PER_TURN,
                detail="messages[3..3]",
            ),
        )

        snapshot = self.build(
            segments=segments,
            context_window_tokens=200_000,
            provider_input_tokens=200,
            cached_input_tokens=100,
        )

        assert snapshot.estimated_input_tokens == 160  # 100 + 50 + 10
        assert snapshot.undeclared_tokens == 10
        assert snapshot.unattributed_delta == 40
        assert snapshot.free_tokens == 199_800
        assert [segment.segment_class.value for segment in snapshot.segments] == [
            "messages",
            "system",
            "tools",
        ]

    def test_the_snapshot_round_trips_through_json(self) -> None:
        # PRD-08 stores segments as JSONB; a record that cannot survive the trip
        # is not persistable.
        original = self.build(
            segments=(
                ContextSegment.measure(
                    "t" * 400,
                    counter=self.counter(),
                    model=self.MODEL,
                    origin=self.TOOL_ORIGIN,
                    detail="publish_artifact",
                ),
            ),
            context_window_tokens=200_000,
            provider_input_tokens=120,
        )

        restored = ContextOccupancySnapshot.model_validate_json(
            original.model_dump_json()
        )

        assert restored == original
