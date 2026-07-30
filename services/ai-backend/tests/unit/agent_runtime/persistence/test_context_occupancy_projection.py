"""The seam between the measured snapshot and the durable occupancy row.

``RuntimeContextOccupancyRecord`` deliberately does **not** import
:mod:`agent_runtime.observability.context_occupancy` — ``persistence.records``
is a leaf layer, and reaching up into the lane that owns prompts, budgets and
the token counter would put an import cycle one edit away (see the record's own
docstring on why ``RuntimeContextGraphScope`` is a hand-copied enum). The cost
of that correct layering is that **nothing in the type system connects the two
contracts**: a field added to :class:`ContextOccupancySnapshot` would be dropped
on the floor at the write, silently, and the loss would only surface as a column
of nulls in a read API months later.

This file is that connection, expressed the way §4.2 says a contract should be
expressed — as a gate rather than a comment. Two sweeps in opposite directions
pin the correspondence (every measured field reaches a column; every column that
is not run identity comes from a measurement), and a real
:class:`SnapshotBuilder` product is projected end-to-end so the mapping is proven
against actual measured numbers rather than against a hand-written dict.

The projection itself is intentionally *not* production code here. It belongs to
the capture seam that owns both layers (PRD-05), and putting it in
``persistence.records`` is the one shortcut that would break the layering the
record was written to protect.
"""

from __future__ import annotations

import inspect
from collections.abc import Mapping, Sequence
from typing import Final

import pytest

from agent_runtime.observability.context_occupancy import (
    ContextOccupancySnapshot,
    ContextSegment,
    GraphScope,
    SnapshotBuilder,
)
from agent_runtime.observability.context_origin import (
    UNDECLARED_CONTEXT_LABEL,
    ContextLifecycle,
    ContextOrigin,
    ContextSegmentClass,
)
from agent_runtime.observability.context_token_counter import (
    ContextTokenCounter,
    DigestTokenCache,
)
from agent_runtime.persistence.records import (
    RuntimeContextGraphScope,
    RuntimeContextOccupancyRecord,
)
from agent_runtime.prompts.assembly import PromptCacheEligibility


class LengthCounter:
    """A ``TokenCounterPort`` whose count varies deterministically with the text.

    ``len(content) // 4`` is the repo's documented char heuristic, so the numbers
    in these assertions are exact literals while still depending on the input — a
    constant-answer fake would let a projection that dropped a segment pass.
    """

    def count(self, *, model: str, messages: Sequence[Mapping[str, str]]) -> int:
        content = "".join(str(message.get("content", "")) for message in messages)
        return len(content) // 4


class SnapshotProjectionMixin:
    """Builds a measured snapshot and projects it onto the durable row.

    The projection body lives here, in one place, for the same reason
    :meth:`RuntimeContextOccupancyRecord.from_measurement` exists at all: the
    keyword set maps field-for-field, so a wiring mistake is a missing keyword
    rather than a transposed number, and every test below exercises the same
    single mapping.
    """

    MODEL: Final[str] = "gpt-5.4-mini"
    ORG_ID: Final[str] = "org-a"
    RUN_ID: Final[str] = "run-a"
    CONVERSATION_ID: Final[str] = "conversation-a"

    # Fields a snapshot carries that are deliberately absent from the write
    # keyword set. Each is here for a stated reason, and the reason is what the
    # gate below protects: a new field arriving in this set by accident is
    # exactly the silent data loss this file exists to catch.
    DERIVED_ON_THE_ROW: Final[frozenset[str]] = frozenset(
        {
            # Pinned Literal[1] on both contracts; a caller that could pass it
            # could also disagree with the column.
            "schema_version",
            # Computed by ``from_measurement`` from the two counts that were
            # actually measured, so no call site can invent or transpose it.
            "unattributed_delta",
            # A derived property on the record, not a column: storing it would
            # let the stored value drift from the window it was derived against.
            "free_tokens",
        }
    )

    # Columns that describe *where* the measurement happened rather than *what*
    # was measured. The snapshot has no opinion about these — the capture seam
    # supplies them from the run it is already inside.
    RUN_IDENTITY_COLUMNS: Final[frozenset[str]] = frozenset(
        {"id", "org_id", "run_id", "conversation_id", "created_at"}
    )

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

    def counter(self) -> ContextTokenCounter:
        """A counter with a private cache so digest memoization cannot leak."""

        return ContextTokenCounter(
            tokenizer=LengthCounter(),
            heuristic=LengthCounter(),
            cache=DigestTokenCache(),
        )

    def snapshot(
        self,
        *,
        graph_scope: GraphScope = GraphScope.ROOT,
        provider_input_tokens: int | None = 900,
        context_window_tokens: int | None = 200_000,
        attempt_ordinal: int = 1,
        assembly_record_id: str | None = "prompt_assembled:" + "a" * 16,
        include_undeclared: bool = True,
    ) -> ContextOccupancySnapshot:
        """A snapshot built the way the capture seam will build one."""

        counter = self.counter()
        segments = [
            ContextSegment.measure(
                "t" * 2_600,
                counter=counter,
                model=self.MODEL,
                origin=self.TOOL_ORIGIN,
                detail="publish_artifact",
                digest="tool-schema-revision-1",
            ),
            ContextSegment.measure(
                "s" * 400,
                counter=counter,
                model=self.MODEL,
                origin=self.SYSTEM_ORIGIN,
                detail="mcp_server_cards",
            ),
        ]
        if include_undeclared:
            segments.append(
                ContextSegment.measure_undeclared(
                    "u" * 120,
                    counter=counter,
                    model=self.MODEL,
                    segment_class=ContextSegmentClass.MESSAGES,
                    lifecycle=ContextLifecycle.PER_TURN,
                    detail="messages[3..4]",
                )
            )
        return SnapshotBuilder().build(
            model_call_id="call-0001",
            graph_scope=graph_scope,
            provider="openai",
            model_family="gpt-5.4",
            segments=segments,
            assembly_record_id=assembly_record_id,
            attempt_ordinal=attempt_ordinal,
            context_window_tokens=context_window_tokens,
            provider_input_tokens=provider_input_tokens,
            # Mirrors ``ContextOccupancyRecorder._cache_subsets``: the two cache
            # figures are subsets *of* the provider total, so a call the provider
            # never reported usage for carries neither. Hard-coding them here
            # regardless of the total built a snapshot the capture seam cannot
            # produce (``usage is None`` yields a ``None`` total AND zero
            # subsets), which is the shape that let the durability boundary skip
            # checking them.
            cached_input_tokens=0 if provider_input_tokens is None else 300,
            cache_creation_input_tokens=0 if provider_input_tokens is None else 100,
        )

    def project(
        self, snapshot: ContextOccupancySnapshot
    ) -> RuntimeContextOccupancyRecord:
        """Project a measured snapshot onto the durable row, field for field."""

        return RuntimeContextOccupancyRecord.from_measurement(
            org_id=self.ORG_ID,
            run_id=self.RUN_ID,
            conversation_id=self.CONVERSATION_ID,
            model_call_id=snapshot.model_call_id,
            attempt_ordinal=snapshot.attempt_ordinal,
            assembly_record_id=snapshot.assembly_record_id,
            graph_scope=snapshot.graph_scope,
            provider=snapshot.provider,
            model_family=snapshot.model_family,
            context_window_tokens=snapshot.context_window_tokens,
            estimated_input_tokens=snapshot.estimated_input_tokens,
            provider_input_tokens=snapshot.provider_input_tokens,
            cached_input_tokens=snapshot.cached_input_tokens,
            cache_creation_input_tokens=snapshot.cache_creation_input_tokens,
            undeclared_tokens=snapshot.undeclared_tokens,
            segments=tuple(
                segment.model_dump(mode="json") for segment in snapshot.segments
            ),
        )

    def write_keywords(self) -> frozenset[str]:
        """The keyword set of the record's single supported construction path."""

        parameters = inspect.signature(
            RuntimeContextOccupancyRecord.from_measurement
        ).parameters
        return frozenset(parameters) - {"cls", "record_id", "created_at"}


class TestSnapshotAndRowStayInStep(SnapshotProjectionMixin):
    def test_every_measured_field_has_a_durable_home(self) -> None:
        # Forward sweep: a field added to the snapshot must either be written or
        # be classified as derived, and this assertion is what forces the
        # author to choose rather than to forget.
        unwritten = frozenset(ContextOccupancySnapshot.model_fields) - (
            self.write_keywords() | {"segments"}
        )

        assert unwritten == self.DERIVED_ON_THE_ROW

    def test_every_column_is_either_measured_or_run_identity(self) -> None:
        # Reverse sweep: a column with no measured source would be a field the
        # ledger can never populate — dead schema that reads as missing data.
        columns = frozenset(RuntimeContextOccupancyRecord.model_fields)
        measured = frozenset(ContextOccupancySnapshot.model_fields)

        assert columns - measured - self.RUN_IDENTITY_COLUMNS == {"segments_json"}
        # ``segments`` is the same measurement under the name the JSONB envelope
        # gives it, so the two contracts have no unmatched concept between them.
        assert "segments" in measured

    def test_the_scope_vocabularies_are_interchangeable(self) -> None:
        # The record's enum is a hand-copied duplicate (leaf-layer rule), so the
        # projection is only safe while the two agree on values *and* on the
        # coercion a domain member undergoes at the boundary.
        for scope in GraphScope:
            record = self.project(self.snapshot(graph_scope=scope))

            assert record.graph_scope is RuntimeContextGraphScope(scope.value)


class TestMeasuredSnapshotSurvivesTheProjection(SnapshotProjectionMixin):
    def test_no_count_changes_on_the_way_to_the_row(self) -> None:
        snapshot = self.snapshot()

        record = self.project(snapshot)

        assert record.estimated_input_tokens == snapshot.estimated_input_tokens
        assert record.provider_input_tokens == snapshot.provider_input_tokens
        assert record.cached_input_tokens == snapshot.cached_input_tokens
        assert (
            record.cache_creation_input_tokens == snapshot.cache_creation_input_tokens
        )
        assert record.context_window_tokens == snapshot.context_window_tokens
        assert record.attempt_ordinal == snapshot.attempt_ordinal
        assert record.assembly_record_id == snapshot.assembly_record_id
        # The residual is recomputed at the boundary rather than copied, so this
        # equality proves the two definitions match instead of proving one
        # assignment happened.
        assert record.unattributed_delta == snapshot.unattributed_delta

    def test_the_two_residuals_stay_apart_across_the_boundary(self) -> None:
        snapshot = self.snapshot()
        record = self.project(snapshot)

        # 120 chars // 4 — the undeclared segment, counted into the estimate and
        # *named* by undeclared_tokens rather than carved out of it (§4.4).
        assert snapshot.undeclared_tokens == 30
        assert record.undeclared_tokens == 30
        # 650 (tool schema) + 100 (mcp cards) + 30 (undeclared) — the undeclared
        # bytes are part of the total, not a deduction from it.
        assert record.estimated_input_tokens == 780
        # 900 provider − 780 estimated: drift, and it never absorbs the 30.
        assert record.unattributed_delta == 120
        assert record.unattributed_delta != record.undeclared_tokens

    def test_free_tokens_reads_the_same_from_either_contract(self) -> None:
        # The read API serves the row, not the snapshot. If these two disagreed,
        # the number an engineer sees would depend on which side answered.
        measured = self.snapshot()
        assert self.project(measured).free_tokens == measured.free_tokens

        unpriced = self.snapshot(context_window_tokens=None)
        assert self.project(unpriced).free_tokens is None
        assert unpriced.free_tokens is None

        unreported = self.snapshot(provider_input_tokens=None)
        assert self.project(unreported).free_tokens == unreported.free_tokens

    def test_every_segment_survives_with_its_declaration_intact(self) -> None:
        snapshot = self.snapshot()

        record = self.project(snapshot)

        assert record.segment_count == len(snapshot.segments)
        assert [segment["label"] for segment in record.segments] == [
            segment.label for segment in snapshot.segments
        ]
        assert [segment["estimated_tokens"] for segment in record.segments] == [
            segment.estimated_tokens for segment in snapshot.segments
        ]
        # lifecycle is what makes the report actionable (rent vs per-turn cost),
        # and counter_source is what tells a reader whether to trust the number.
        assert [segment["lifecycle"] for segment in record.segments] == [
            segment.lifecycle.value for segment in snapshot.segments
        ]
        assert [segment["counter_source"] for segment in record.segments] == [
            segment.counter_source.value for segment in snapshot.segments
        ]
        assert UNDECLARED_CONTEXT_LABEL in {
            segment["label"] for segment in record.segments
        }
        # Segment sums are the estimate: nothing was scaled toward the provider
        # total on the way into the row (§3.3).
        assert (
            sum(int(segment["estimated_tokens"]) for segment in record.segments)
            == record.estimated_input_tokens
        )

    def test_a_retry_projects_onto_a_second_identity(self) -> None:
        first = self.project(self.snapshot())
        retry = self.project(self.snapshot(attempt_ordinal=2))

        assert first.idempotency_key == ("call-0001", 1)
        assert retry.idempotency_key == ("call-0001", 2)


class TestEveryLegalSnapshotIsPersistable(SnapshotProjectionMixin):
    def test_the_widest_legal_segment_still_fits_the_row(self) -> None:
        # The row's content-leakage bound (§6.5) is enforced structurally, which
        # means it could be tightened below what a legal segment can carry —
        # and the write path is fail-open, so the row would simply vanish. This
        # pins the two bounds in the only order that is safe.
        assert (
            RuntimeContextOccupancyRecord.Limits.MAX_SEGMENT_TEXT_CHARS
            >= ContextSegment.MAX_DETAIL_LENGTH
        )
        widest = ContextSegment(
            segment_class=ContextSegmentClass.TOOLS,
            label="o" * 200 + ":" + "n" * 39,
            lifecycle=ContextLifecycle.RESIDENT,
            detail="d" * ContextSegment.MAX_DETAIL_LENGTH,
            byte_count=1,
            estimated_tokens=1,
            counter_source="tokenizer",
        )
        snapshot = SnapshotBuilder().build(
            model_call_id="call-widest",
            graph_scope=GraphScope.ROOT,
            provider="openai",
            model_family="gpt-5.4",
            segments=(widest,),
        )

        record = self.project(snapshot)

        assert record.segments[0]["label"] == widest.label
        assert record.segments[0]["detail"] == widest.detail

    def test_an_empty_fail_open_snapshot_is_still_a_row(self) -> None:
        # §6.4: when measurement degrades to nothing, the honest record is a row
        # with no segments — not a dropped write, which would be indistinguishable
        # from a call that never happened.
        empty = SnapshotBuilder().build(
            model_call_id="call-empty",
            graph_scope=GraphScope.SUBAGENT,
            provider="openai",
            model_family="gpt-5.4",
        )

        record = self.project(empty)

        assert record.segments == ()
        assert record.estimated_input_tokens == 0
        assert record.graph_scope is RuntimeContextGraphScope.SUBAGENT

    def test_a_snapshot_beyond_the_row_bound_fails_at_the_write(self) -> None:
        """The record refuses; eliding is the *recorder's* job, one layer up.

        This asserts the durability bound only, reached by handing
        ``from_measurement`` an oversized decomposition directly. The bound is
        worth keeping — a runaway tool surface must not write an unbounded JSONB
        document — and this contract has no way to choose which segments matter,
        so refusing is the only honest answer it can give.

        It is explicitly **not** a claim that an over-long conversation loses its
        occupancy row. Left as one, it read that way, and the reading was wrong:
        ``ContextOccupancyRecorder.project`` bounds the decomposition to the
        largest ``MAX_SEGMENTS`` before it ever reaches this validator, keeping
        every rollup total exact. That is where the policy lives, and
        ``test_context_occupancy_recorder.py::TestLongConversationsStillProduceARow``
        is where it is pinned.
        """

        oversized = tuple(
            ContextSegment(
                segment_class=ContextSegmentClass.TOOLS,
                label=f"owner:tool-{index}",
                lifecycle=ContextLifecycle.RESIDENT,
                byte_count=0,
                estimated_tokens=0,
                counter_source="tokenizer",
            )
            for index in range(RuntimeContextOccupancyRecord.Limits.MAX_SEGMENTS + 1)
        )
        snapshot = SnapshotBuilder().build(
            model_call_id="call-oversized",
            graph_scope=GraphScope.ROOT,
            provider="openai",
            model_family="gpt-5.4",
            segments=oversized,
        )

        with pytest.raises(ValueError, match="segment count exceeds"):
            self.project(snapshot)
