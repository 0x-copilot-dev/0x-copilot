"""Prove the three properties the admission gate claims, rather than assume them.

The module under test states that (1) no successful oversized result reaches the
model unbounded, (2) admission happens before the result becomes a
``ToolMessage``, and (3) the recorded admission fact is raw-free.  These tests
are written to *falsify* those claims, so the oversized cases run through the
zero-argument gate -- the configuration the module says is the whole point --
and the raw-free proof seeds a secret into the admitted result and asserts on
the serialized fact rather than on its field names.  A fact that was never
recorded would make an absence assertion pass vacuously, so every raw-free test
carries a positive control: the same secret is asserted *present* in the bytes
the store kept, and the fact is asserted to be fully populated.
"""

from __future__ import annotations

from hashlib import sha256
import json
from typing import ClassVar

import pytest
from pydantic import ValidationError

from agent_runtime.context.context_contracts import (
    ContextCandidateKind,
    ContextInclusionReason,
    ContextLossiness,
    ContextRepresentationMode,
)
from agent_runtime.context.memory.constants import Patterns
from agent_runtime.context.memory.contracts import ContextCompressionStrategy
from agent_runtime.context.tool_result_admission import (
    ToolResultAdmission,
    ToolResultAdmissionAdapter,
    ToolResultAdmissionFact,
)
from agent_runtime.context.tool_result_admission_gate import (
    InProcessOffloadWriter,
    ToolResultAdmissionBypassed,
    ToolResultAdmissionGate,
    ToolResultAdmissionLedger,
)


class StoreUnavailable(RuntimeError):
    """The typed failure a durable object store raises when it cannot write."""

    MESSAGE: ClassVar[str] = "object store unavailable"


class OffloadWriterMixin:
    """Writer doubles standing in for the durable store the gate may be given."""

    class RecordingOffloadWriter:
        """Durable-writer stand-in that keeps every byte it was handed."""

        PREFIX: ClassVar[str] = "/large_tool_results/"

        def __init__(self) -> None:
            self.contents: list[str] = []
            self._stored: dict[str, str] = {}

        def __call__(self, content: str) -> str:
            reference = self.PREFIX + sha256(content.encode("utf-8")).hexdigest()
            self.contents.append(content)
            self._stored[reference] = content
            return reference

        def read(self, reference: str) -> str | None:
            return self._stored.get(reference)

    class ExplodingOffloadWriter:
        """Durable-writer stand-in whose write always fails."""

        def __init__(self) -> None:
            self.attempts = 0

        def __call__(self, content: str) -> str:
            self.attempts += 1
            raise StoreUnavailable(StoreUnavailable.MESSAGE)

    def recording_writer(self) -> RecordingOffloadWriter:
        return self.RecordingOffloadWriter()

    def exploding_writer(self) -> ExplodingOffloadWriter:
        return self.ExplodingOffloadWriter()


class AdmissionPayloadMixin:
    """Payload builders, tool-name sets, and the seeded secret."""

    SECRET: ClassVar[str] = "SEEDED-SECRET-a1b2c3-must-never-be-recorded"
    FILLER: ClassVar[str] = "lorem ipsum dolor sit amet "
    FILLER_REPEATS: ClassVar[int] = 2_400
    SCOPE: ClassVar[str] = "run-7f3a"
    CALL_ID: ClassVar[str] = "call-0001"
    REGISTRY_TOOL: ClassVar[str] = "acme_search"
    # Tools Deep Agents injects after the caller's list is assembled, plus the
    # delegation tool a locally compiled subagent gets its own copy of.  None of
    # them passes through a caller-side decorator, which is why the gate must not
    # own a per-tool policy.
    FRAMEWORK_INJECTED_TOOLS: ClassVar[tuple[str, ...]] = (
        "write_todos",
        "ls",
        "read_file",
        "write_file",
        "edit_file",
        "execute",
        "task",
    )

    def oversized_text(
        self,
        *,
        secret_at_head: bool = False,
        secret_at_tail: bool = False,
    ) -> str:
        """Return content comfortably past the inline budget, optionally seeded."""

        head = self.SECRET if secret_at_head else ""
        tail = self.SECRET if secret_at_tail else ""
        return f"{head}{self.FILLER * self.FILLER_REPEATS}{tail}"

    def gate(self, **kwargs: object) -> ToolResultAdmissionGate:
        return ToolResultAdmissionGate(**kwargs)  # type: ignore[arg-type]

    def admit_oversized(
        self,
        gate: ToolResultAdmissionGate,
        *,
        content: str | None = None,
        tool_name: str | None = None,
        tool_call_id: str | None = None,
        scope: str | None = None,
    ) -> ToolResultAdmission:
        """Admit an oversized return and refuse to proceed if it stayed inline.

        The offload branch is the one every oversized-path assertion is about.
        Asserting the strategy here means a raised inline budget can never turn
        those assertions into statements about a small result.
        """

        admission = gate.admit_tool_return(
            self.oversized_text() if content is None else content,
            tool_name=tool_name or self.REGISTRY_TOOL,
            tool_call_id=tool_call_id or self.CALL_ID,
            scope=self.SCOPE if scope is None else scope,
        )
        assert admission.strategy is ContextCompressionStrategy.OFFLOAD, (
            "payload no longer exceeds the inline budget; the oversized-path "
            "assertions below would be vacuous"
        )
        return admission

    @staticmethod
    def fact_payloads(fact: ToolResultAdmissionFact) -> tuple[str, str]:
        """Return two independent serializations of the persisted fact.

        Two encodings because a field that survives one dump mode and not the
        other would otherwise go unnoticed.
        """

        return (
            fact.model_dump_json(),
            json.dumps(fact.model_dump(mode="json"), default=str),
        )

    def assert_fact_is_populated(self, fact: ToolResultAdmissionFact) -> None:
        """Fail loudly when the fact is too empty for an absence proof to mean anything."""

        assert fact.source_digest and len(fact.source_digest) == 64
        assert fact.model_content_digest and len(fact.model_content_digest) == 64
        assert fact.source_chars > 0
        assert fact.model_content_limit_chars > 0
        assert fact.candidate_kind is ContextCandidateKind.TOOL_OBSERVATION
        assert fact.admitted_at.tzinfo is not None
        for payload in self.fact_payloads(fact):
            assert len(payload) > 200, "fact serialized too small to prove anything"


class TestOversizedResultsNeverReachTheModelUnbounded(
    AdmissionPayloadMixin,
    OffloadWriterMixin,
):
    """Property 1: a successful oversized result is always bounded."""

    def test_zero_argument_gate_bounds_an_oversized_result(self) -> None:
        """The no-store configuration must bound, not raise and not pass through.

        Regression: the in-process fallback emitted a ``scheme://`` reference,
        which the memory-path grammar behind ``ContextCompressionEvent`` rejects,
        so the zero-argument gate raised ``ValidationError`` on every oversized
        result -- bounding nothing at all in exactly the deployment this class
        exists to protect.
        """

        gate = self.gate()
        source = self.oversized_text()

        admission = self.admit_oversized(gate, content=source)

        assert len(admission.model_content) <= admission.model_content_limit_chars
        assert len(admission.model_content) < len(source)
        assert admission.output_ref is not None
        assert admission.preview is not None

    def test_fallback_reference_satisfies_the_memory_path_grammar(self) -> None:
        """The reference must be the shape the offload contract accepts."""

        gate = self.gate()

        admission = self.admit_oversized(gate)

        assert admission.output_ref is not None
        assert Patterns.MEMORY_PATH.fullmatch(admission.output_ref) is not None
        assert admission.output_ref.startswith(InProcessOffloadWriter.REFERENCE_PREFIX)

    def test_fallback_store_reads_back_the_exact_source_bytes(self) -> None:
        """Bounding the model's view must not lose the result itself."""

        gate = self.gate()
        source = self.oversized_text(secret_at_tail=True)

        admission = self.admit_oversized(gate, content=source)

        store = gate.fallback_store
        assert store is not None
        assert admission.output_ref is not None
        assert store.read(admission.output_ref) == source

    def test_durable_writer_bounds_the_same_result_the_same_way(self) -> None:
        """The decision must not depend on which store happens to be wired."""

        source = self.oversized_text()
        fallback_admission = self.admit_oversized(self.gate(), content=source)

        writer = self.recording_writer()
        durable_admission = self.admit_oversized(
            self.gate(offload_writer=writer),
            content=source,
        )

        assert durable_admission.strategy is fallback_admission.strategy
        assert durable_admission.source_digest == fallback_admission.source_digest
        assert len(durable_admission.model_content) <= (
            durable_admission.model_content_limit_chars
        )
        assert writer.contents == [source]

    def test_supplying_a_writer_disables_the_in_process_fallback(self) -> None:
        """A durable store must be used instead of, not alongside, the fallback."""

        gate = self.gate(offload_writer=self.recording_writer())

        assert gate.fallback_store is None

    @pytest.mark.parametrize("repeats", [2_400, 6_000, 20_000])
    def test_model_content_stays_within_its_limit_as_the_result_grows(
        self,
        repeats: int,
    ) -> None:
        """Growth in the source must not grow what the model is handed."""

        gate = self.gate()
        source = self.FILLER * repeats

        admission = self.admit_oversized(gate, content=source)

        assert len(admission.model_content) <= admission.model_content_limit_chars
        assert admission.fact is not None
        assert admission.fact.model_content_chars <= (
            admission.fact.model_content_limit_chars
        )
        assert admission.fact.source_chars == len(source)

    @pytest.mark.parametrize(
        "tool_name",
        AdmissionPayloadMixin.FRAMEWORK_INJECTED_TOOLS,
    )
    def test_framework_injected_tools_are_bounded_like_registry_tools(
        self,
        tool_name: str,
    ) -> None:
        """Coverage is the point: a tool nobody wrapped must still be bounded."""

        gate = self.gate()
        source = self.oversized_text()

        admission = self.admit_oversized(gate, content=source, tool_name=tool_name)

        assert len(admission.model_content) <= admission.model_content_limit_chars
        assert admission.output_ref is not None
        assert admission.fact is not None
        assert admission.fact.tool_name == tool_name

    def test_bounding_is_identical_across_every_tool_name(self) -> None:
        """No allowlist, no denylist: identical bytes get an identical decision."""

        source = self.oversized_text()
        names = (self.REGISTRY_TOOL, *self.FRAMEWORK_INJECTED_TOOLS)

        decisions = {
            name: self.admit_oversized(
                self.gate(),
                content=source,
                tool_name=name,
            )
            for name in names
        }

        shapes = {
            (
                admission.strategy,
                len(admission.model_content),
                admission.model_content_limit_chars,
                admission.source_digest,
                admission.output_ref,
            )
            for admission in decisions.values()
        }
        assert len(shapes) == 1

    def test_a_subagent_scope_is_bounded_like_the_parent_run(self) -> None:
        """A delegated subagent's own tool copies cross the same boundary."""

        gate = self.gate()
        source = self.oversized_text()

        parent = self.admit_oversized(gate, content=source, scope="run-parent")
        child = self.admit_oversized(
            gate,
            content=source,
            tool_name="task",
            scope="run-parent/subagent-1",
        )

        assert len(child.model_content) == len(parent.model_content)
        assert child.strategy is ContextCompressionStrategy.OFFLOAD

    def test_small_result_stays_inline_and_is_its_own_source(self) -> None:
        """Bounding must not offload what already fits."""

        gate = self.gate()

        admission = gate.admit_tool_return(
            {"rows": 3},
            tool_name=self.REGISTRY_TOOL,
            tool_call_id=self.CALL_ID,
            scope=self.SCOPE,
        )

        assert admission.strategy is ContextCompressionStrategy.INLINE
        assert admission.output_ref is None
        assert admission.preview is None
        assert admission.fact is not None
        assert admission.fact.source_digest == admission.fact.model_content_digest
        assert admission.fact.representation_mode is ContextRepresentationMode.FULL
        assert admission.fact.lossiness is ContextLossiness.NONE
        assert admission.fact.inclusion_reason is ContextInclusionReason.RECENCY_WINDOW

    def test_empty_result_is_admitted_without_a_reference(self) -> None:
        """A tool that returns nothing must still produce a recorded decision."""

        gate = self.gate()

        admission = gate.admit_tool_return(
            "",
            tool_name=self.REGISTRY_TOOL,
            tool_call_id=self.CALL_ID,
            scope=self.SCOPE,
        )

        assert admission.strategy is ContextCompressionStrategy.INLINE
        assert admission.model_content == ""
        assert admission.output_ref is None
        assert admission.fact is not None
        assert admission.fact.source_chars == 0

    def test_offloaded_fact_declares_the_reference_reading_it_back(self) -> None:
        """An offloaded fact must state the F5.1 reading its strategy produces."""

        gate = self.gate()

        admission = self.admit_oversized(gate)

        fact = admission.fact
        assert fact is not None
        assert fact.strategy is ContextCompressionStrategy.OFFLOAD
        assert fact.representation_mode is ContextRepresentationMode.REFERENCE
        assert fact.lossiness is ContextLossiness.ELIDED
        assert fact.inclusion_reason is ContextInclusionReason.RETRIEVABLE_REFERENCE
        assert fact.output_ref == admission.output_ref
        assert fact.source_digest != fact.model_content_digest

    def test_model_content_tells_the_model_the_preview_is_not_authoritative(
        self,
    ) -> None:
        """A truncated view the model believes is complete is its own hazard."""

        gate = self.gate()

        admission = self.admit_oversized(gate)

        assert "non-authoritative" in admission.model_content
        assert admission.output_ref is not None
        assert admission.output_ref in admission.model_content


class TestAdmissionPrecedesModelVisibility(
    AdmissionPayloadMixin,
    OffloadWriterMixin,
):
    """Property 2: content that was never admitted is refused at the boundary."""

    def test_unadmitted_call_identity_is_refused(self) -> None:
        """An unknown call must fail closed rather than verify vacuously."""

        gate = self.gate()

        with pytest.raises(ToolResultAdmissionBypassed) as excinfo:
            gate.verify_model_visible(
                "anything at all",
                tool_call_id="never-admitted",
                scope=self.SCOPE,
            )

        assert str(excinfo.value) == ToolResultAdmissionBypassed.Messages.NOT_ADMITTED

    def test_raw_oversized_output_is_refused_even_after_admission(self) -> None:
        """The bypass this guard exists for: admit, then hand the model the raw result."""

        gate = self.gate()
        source = self.oversized_text(secret_at_head=True)
        self.admit_oversized(gate, content=source)

        with pytest.raises(ToolResultAdmissionBypassed) as excinfo:
            gate.verify_model_visible(
                source,
                tool_call_id=self.CALL_ID,
                scope=self.SCOPE,
            )

        assert str(excinfo.value) == (
            ToolResultAdmissionBypassed.Messages.CONTENT_NOT_ADMITTED
        )

    def test_the_refusal_message_carries_none_of_the_refused_content(self) -> None:
        """A refusal that quotes the payload would reintroduce the leak."""

        gate = self.gate()
        source = self.oversized_text(secret_at_head=True)
        self.admit_oversized(gate, content=source)

        with pytest.raises(ToolResultAdmissionBypassed) as excinfo:
            gate.verify_model_visible(
                source,
                tool_call_id=self.CALL_ID,
                scope=self.SCOPE,
            )

        assert self.SECRET not in str(excinfo.value)
        assert self.SECRET not in repr(excinfo.value)

    def test_bypass_is_a_runtime_defect_not_a_model_facing_refusal(self) -> None:
        """It is never a condition a tool, connector, or model can cause."""

        assert issubclass(ToolResultAdmissionBypassed, RuntimeError)
        assert not issubclass(ToolResultAdmissionBypassed, ValueError)

    def test_admitted_model_content_verifies_and_returns_its_fact(self) -> None:
        """The value that does become a ToolMessage must pass."""

        gate = self.gate()
        admission = self.admit_oversized(gate)

        fact = gate.verify_model_visible(
            admission.model_content,
            tool_call_id=self.CALL_ID,
            scope=self.SCOPE,
        )

        assert fact is admission.fact

    def test_verification_does_not_consume_the_event_projection(self) -> None:
        """Verification runs on the way to the model and must not disturb the event path."""

        gate = self.gate()
        admission = self.admit_oversized(gate)

        gate.verify_model_visible(
            admission.model_content,
            tool_call_id=self.CALL_ID,
            scope=self.SCOPE,
        )
        projected = gate.consume_event_projection(
            tool_call_id=self.CALL_ID,
            scope=self.SCOPE,
        )

        assert projected is admission

    def test_content_admitted_for_another_run_is_refused(self) -> None:
        """Call identity is scoped, so one run cannot vouch for another's content."""

        gate = self.gate()
        admission = self.admit_oversized(gate, scope="run-a")

        with pytest.raises(ToolResultAdmissionBypassed) as excinfo:
            gate.verify_model_visible(
                admission.model_content,
                tool_call_id=self.CALL_ID,
                scope="run-b",
            )

        assert str(excinfo.value) == ToolResultAdmissionBypassed.Messages.NOT_ADMITTED

    def test_content_admitted_for_another_call_in_one_run_is_refused(self) -> None:
        """Two concurrent tools in one run must not vouch for each other."""

        gate = self.gate()
        first = gate.admit_tool_return(
            "result of the first call",
            tool_name=self.REGISTRY_TOOL,
            tool_call_id="call-a",
            scope=self.SCOPE,
        )
        gate.admit_tool_return(
            "result of the second call",
            tool_name=self.REGISTRY_TOOL,
            tool_call_id="call-b",
            scope=self.SCOPE,
        )

        with pytest.raises(ToolResultAdmissionBypassed) as excinfo:
            gate.verify_model_visible(
                first.model_content,
                tool_call_id="call-b",
                scope=self.SCOPE,
            )

        assert str(excinfo.value) == (
            ToolResultAdmissionBypassed.Messages.CONTENT_NOT_ADMITTED
        )

    def test_blank_and_missing_scopes_resolve_to_one_identity(self) -> None:
        """Scope normalization must not split one run into two."""

        gate = self.gate()
        admission = gate.admit_tool_return(
            "unscoped result",
            tool_name=self.REGISTRY_TOOL,
            tool_call_id=self.CALL_ID,
            scope=None,
        )

        fact = gate.verify_model_visible(
            admission.model_content,
            tool_call_id=self.CALL_ID,
            scope="   ",
        )

        assert fact is admission.fact

    def test_id_shaped_call_id_resolves_through_the_ledger(self) -> None:
        """The ledger key and the fact's own field must agree on one identity."""

        gate = self.gate()
        call_id = "call_ABC-123.xyz:1"
        admission = gate.admit_tool_return(
            "a result",
            tool_name=self.REGISTRY_TOOL,
            tool_call_id=call_id,
            scope=self.SCOPE,
        )

        fact = gate.verify_model_visible(
            admission.model_content,
            tool_call_id=call_id,
            scope=self.SCOPE,
        )

        assert fact is admission.fact
        assert fact.tool_call_id == call_id

    @pytest.mark.parametrize(
        "call_id",
        ["call with spaces", "call\twith\ttabs", "call/with/slashes", "call@host"],
    )
    def test_a_non_id_shaped_call_id_makes_admission_raise(self, call_id: str) -> None:
        """Pins observed behaviour that contradicts a claim the module makes.

        ``ToolResultAdmissionFact.bounded_identifier`` is documented as total --
        an identifier it cannot hold is digested "rather than rejected, because
        refusing to record the fact would leave the admission unrecorded".  The
        gate defeats that one layer up: it forwards the raw ``tool_call_id`` as
        the compression event's ``trace_id``, which enforces
        ``^[A-Za-z0-9][A-Za-z0-9._:-]*$``, so a call id outside that grammar
        raises before the totality can apply and the admission is never recorded.

        This is fail-closed, not a leak -- nothing reaches the model unbounded,
        the call simply fails -- so it is pinned rather than fixed here.  Real
        LangChain ids are ID-shaped; a connector-supplied one need not be.
        """

        gate = self.gate()

        with pytest.raises(ValidationError):
            gate.admit_tool_return(
                "a result",
                tool_name=self.REGISTRY_TOOL,
                tool_call_id=call_id,
                scope=self.SCOPE,
            )

        assert gate.fact_for(tool_call_id=call_id, scope=self.SCOPE) is None

    def test_bounded_identifier_itself_is_total(self) -> None:
        """The helper is total; only the gate's trace_id forwarding is not."""

        digested = ToolResultAdmissionFact.bounded_identifier("call with spaces")

        assert digested.startswith("sha256:")
        assert "call with spaces" not in digested

    def test_discarded_run_can_no_longer_vouch_for_its_content(self) -> None:
        """After a run ends or is cancelled, verification fails closed."""

        gate = self.gate()
        admission = self.admit_oversized(gate)

        gate.discard(scope=self.SCOPE)

        with pytest.raises(ToolResultAdmissionBypassed) as excinfo:
            gate.verify_model_visible(
                admission.model_content,
                tool_call_id=self.CALL_ID,
                scope=self.SCOPE,
            )
        assert str(excinfo.value) == ToolResultAdmissionBypassed.Messages.NOT_ADMITTED

    def test_consumed_projection_can_no_longer_be_verified(self) -> None:
        """Pins the ordering: verification belongs before the event path consumes."""

        gate = self.gate()
        admission = self.admit_oversized(gate)
        gate.consume_event_projection(tool_call_id=self.CALL_ID, scope=self.SCOPE)

        with pytest.raises(ToolResultAdmissionBypassed) as excinfo:
            gate.verify_model_visible(
                admission.model_content,
                tool_call_id=self.CALL_ID,
                scope=self.SCOPE,
            )
        assert str(excinfo.value) == ToolResultAdmissionBypassed.Messages.NOT_ADMITTED

    def test_retried_call_returns_the_earliest_fact_not_the_matching_one(self) -> None:
        """Pins observed behaviour, which is fail-safe but not exact.

        ``verify_model_visible`` matches the digest against *every* admission
        filed under one call identity, and then returns the earliest unconsumed
        fact rather than the fact describing the content it just verified.  For a
        retried call that produced two different results this returns a fact
        about the other admission.  It never admits unadmitted content -- the
        digest check is still total -- so this is a reporting imprecision, not a
        hole; it is pinned here so a future change to either half is deliberate.
        """

        gate = self.gate()
        first = gate.admit_tool_return(
            "first attempt",
            tool_name=self.REGISTRY_TOOL,
            tool_call_id=self.CALL_ID,
            scope=self.SCOPE,
        )
        second = gate.admit_tool_return(
            "second attempt",
            tool_name=self.REGISTRY_TOOL,
            tool_call_id=self.CALL_ID,
            scope=self.SCOPE,
        )

        fact = gate.verify_model_visible(
            second.model_content,
            tool_call_id=self.CALL_ID,
            scope=self.SCOPE,
        )

        assert fact is first.fact
        assert fact is not second.fact


class TestFailedAdmissionNeverBecomesAToolMessage(
    AdmissionPayloadMixin,
    OffloadWriterMixin,
):
    """An oversized result that fails admission must not reach the model at all."""

    def test_store_failure_propagates_instead_of_returning_the_raw_result(
        self,
    ) -> None:
        """Degrading to "return it unbounded" is the failure mode being excluded."""

        writer = self.exploding_writer()
        gate = self.gate(offload_writer=writer)

        with pytest.raises(StoreUnavailable) as excinfo:
            gate.admit_tool_return(
                self.oversized_text(secret_at_head=True),
                tool_name=self.REGISTRY_TOOL,
                tool_call_id=self.CALL_ID,
                scope=self.SCOPE,
            )

        assert str(excinfo.value) == StoreUnavailable.MESSAGE
        assert writer.attempts == 1

    def test_failed_admission_records_no_fact(self) -> None:
        """A decision that did not complete must not look like one that did."""

        gate = self.gate(offload_writer=self.exploding_writer())

        with pytest.raises(StoreUnavailable):
            gate.admit_tool_return(
                self.oversized_text(),
                tool_name=self.REGISTRY_TOOL,
                tool_call_id=self.CALL_ID,
                scope=self.SCOPE,
            )

        assert gate.fact_for(tool_call_id=self.CALL_ID, scope=self.SCOPE) is None

    def test_failed_admission_leaves_the_raw_result_unverifiable(self) -> None:
        """The central consequence: nothing from that call can become a ToolMessage."""

        gate = self.gate(offload_writer=self.exploding_writer())
        source = self.oversized_text(secret_at_head=True)

        with pytest.raises(StoreUnavailable):
            gate.admit_tool_return(
                source,
                tool_name=self.REGISTRY_TOOL,
                tool_call_id=self.CALL_ID,
                scope=self.SCOPE,
            )

        with pytest.raises(ToolResultAdmissionBypassed) as excinfo:
            gate.verify_model_visible(
                source,
                tool_call_id=self.CALL_ID,
                scope=self.SCOPE,
            )
        assert str(excinfo.value) == ToolResultAdmissionBypassed.Messages.NOT_ADMITTED

    def test_failed_admission_leaves_no_event_projection(self) -> None:
        """The event path must not find a half-made decision to project."""

        gate = self.gate(offload_writer=self.exploding_writer())

        with pytest.raises(StoreUnavailable):
            gate.admit_tool_return(
                self.oversized_text(),
                tool_name=self.REGISTRY_TOOL,
                tool_call_id=self.CALL_ID,
                scope=self.SCOPE,
            )

        assert (
            gate.consume_event_projection(
                tool_call_id=self.CALL_ID,
                scope=self.SCOPE,
            )
            is None
        )

    def test_a_failing_store_does_not_silently_fall_back_to_the_in_process_one(
        self,
    ) -> None:
        """A configured store that fails must fail, not quietly become the fallback."""

        gate = self.gate(offload_writer=self.exploding_writer())

        assert gate.fallback_store is None
        with pytest.raises(StoreUnavailable):
            gate.admit_tool_return(
                self.oversized_text(),
                tool_name=self.REGISTRY_TOOL,
                tool_call_id=self.CALL_ID,
                scope=self.SCOPE,
            )

    def test_a_later_call_still_admits_after_one_call_failed(self) -> None:
        """One store failure must not wedge the boundary for the rest of the run."""

        gate = self.gate()
        failing = self.gate(offload_writer=self.exploding_writer())
        with pytest.raises(StoreUnavailable):
            failing.admit_tool_return(
                self.oversized_text(),
                tool_name=self.REGISTRY_TOOL,
                tool_call_id="call-failed",
                scope=self.SCOPE,
            )

        admission = self.admit_oversized(gate, tool_call_id="call-after")

        assert admission.fact is not None


class TestAdmissionFactIsRawFree(AdmissionPayloadMixin, OffloadWriterMixin):
    """Property 3, proved by seeding a secret rather than by reading field names."""

    def test_secret_in_an_offloaded_result_never_enters_the_fact(self) -> None:
        """The tail secret is in the stored bytes and in no part of the fact."""

        gate = self.gate()
        source = self.oversized_text(secret_at_tail=True)

        admission = self.admit_oversized(gate, content=source)

        fact = admission.fact
        assert fact is not None
        self.assert_fact_is_populated(fact)
        store = gate.fallback_store
        assert store is not None
        assert admission.output_ref is not None
        # Positive control: the secret really was admitted and really was kept.
        assert self.SECRET in (store.read(admission.output_ref) or "")
        for payload in self.fact_payloads(fact):
            assert self.SECRET not in payload

    def test_secret_the_model_can_see_in_the_preview_never_enters_the_fact(
        self,
    ) -> None:
        """The strongest form: the same secret is in the sibling admission object.

        Seeding at the head puts the secret inside the bounded preview the model
        is handed, so an absence assertion on the fact cannot pass merely because
        the secret went missing on the way in.
        """

        gate = self.gate()
        source = self.oversized_text(secret_at_head=True)

        admission = self.admit_oversized(gate, content=source)

        # Positive control: the model-visible half genuinely carries the secret.
        assert self.SECRET in admission.model_content
        assert self.SECRET in (admission.preview or "")
        fact = admission.fact
        assert fact is not None
        self.assert_fact_is_populated(fact)
        for payload in self.fact_payloads(fact):
            assert self.SECRET not in payload

    def test_secret_in_an_inline_result_never_enters_the_fact(self) -> None:
        """A small result is recorded as digests too, not as itself."""

        gate = self.gate()
        payload = {"token": self.SECRET, "rows": 1}

        admission = gate.admit_tool_return(
            payload,
            tool_name=self.REGISTRY_TOOL,
            tool_call_id=self.CALL_ID,
            scope=self.SCOPE,
        )

        # Positive control: inline means the model does see it.
        assert self.SECRET in admission.model_content
        fact = admission.fact
        assert fact is not None
        self.assert_fact_is_populated(fact)
        for encoded in self.fact_payloads(fact):
            assert self.SECRET not in encoded

    def test_a_secret_in_the_tool_name_is_bounded_out_of_the_fact(self) -> None:
        """The identifier fields are bounded tokens, not free text."""

        gate = self.gate()
        prose_name = f"tool that leaked {self.SECRET} while running"

        admission = gate.admit_tool_return(
            "a result",
            tool_name=prose_name,
            tool_call_id=self.CALL_ID,
            scope=self.SCOPE,
        )

        fact = admission.fact
        assert fact is not None
        assert fact.tool_name.startswith("sha256:")
        for payload in self.fact_payloads(fact):
            assert self.SECRET not in payload

    def test_every_recorded_fact_in_the_ledger_is_raw_free(self) -> None:
        """The proof has to hold for what was persisted, not just what was returned."""

        gate = self.gate()
        source = self.oversized_text(secret_at_head=True, secret_at_tail=True)
        self.admit_oversized(gate, content=source)

        recorded = gate.ledger.fact_for(scope=self.SCOPE, tool_call_id=self.CALL_ID)

        assert recorded is not None
        self.assert_fact_is_populated(recorded)
        for payload in self.fact_payloads(recorded):
            assert self.SECRET not in payload

    def test_the_fact_holds_only_digests_counts_and_closed_vocabulary(self) -> None:
        """Corroborates the seeded-secret proof without standing in for it."""

        gate = self.gate()

        admission = self.admit_oversized(gate)

        fact = admission.fact
        assert fact is not None
        dumped = fact.model_dump(mode="json")
        free_text = {
            key: value
            for key, value in dumped.items()
            if isinstance(value, str)
            and key not in {"source_digest", "model_content_digest"}
        }
        for key, value in free_text.items():
            assert len(value) <= ToolResultAdmissionFact.MAX_REF_CHARS, key
            assert not any(character.isspace() for character in value), key

    def test_digests_identify_the_content_without_retaining_it(self) -> None:
        """The recorded digests must actually be of the bytes they claim."""

        gate = self.gate()
        source = self.oversized_text(secret_at_head=True)

        admission = self.admit_oversized(gate, content=source)

        fact = admission.fact
        assert fact is not None
        assert fact.source_digest == sha256(source.encode("utf-8")).hexdigest()
        assert fact.model_content_digest == (
            sha256(admission.model_content.encode("utf-8")).hexdigest()
        )
        assert fact.source_chars == len(source)
        assert fact.model_content_chars == len(admission.model_content)


class TestInProcessOffloadWriterIsItselfBounded(AdmissionPayloadMixin):
    """The fallback must not become the unbounded cache it exists to prevent."""

    def test_rejects_a_nonpositive_entry_ceiling(self) -> None:
        with pytest.raises(ValueError) as excinfo:
            InProcessOffloadWriter(max_entries=0)

        assert str(excinfo.value) == "in-process offload store needs at least one entry"

    def test_rejects_a_nonpositive_byte_ceiling(self) -> None:
        with pytest.raises(ValueError) as excinfo:
            InProcessOffloadWriter(max_total_chars=0)

        assert str(excinfo.value) == (
            "in-process offload store needs a positive byte ceiling"
        )

    def test_evicts_oldest_first_beyond_the_entry_ceiling(self) -> None:
        writer = InProcessOffloadWriter(max_entries=2)

        first = writer("first content")
        second = writer("second content")
        third = writer("third content")

        assert writer.read(first) is None
        assert writer.read(second) == "second content"
        assert writer.read(third) == "third content"

    def test_retained_chars_stay_within_the_byte_ceiling(self) -> None:
        writer = InProcessOffloadWriter(max_total_chars=100)

        for index in range(12):
            writer(f"{index:02d}" + "y" * 28)
            assert writer.stored_chars <= 100

    def test_an_entry_larger_than_the_ceiling_becomes_unresolvable_not_wrong(
        self,
    ) -> None:
        """Eviction may lose bytes; it must never return different ones."""

        writer = InProcessOffloadWriter(max_total_chars=10)

        reference = writer("x" * 200)

        assert writer.read(reference) is None
        assert writer.stored_chars == 0

    def test_identical_content_dedupes_to_one_reference_and_one_copy(self) -> None:
        writer = InProcessOffloadWriter()

        first = writer("repeated content")
        second = writer("repeated content")

        assert first == second
        assert writer.stored_chars == len("repeated content")

    def test_reference_is_content_addressed_and_holds_no_raw(self) -> None:
        writer = InProcessOffloadWriter()

        reference = writer(self.SECRET)

        assert self.SECRET not in reference
        assert reference == (
            InProcessOffloadWriter.REFERENCE_PREFIX
            + sha256(self.SECRET.encode("utf-8")).hexdigest()
        )
        assert Patterns.MEMORY_PATH.fullmatch(reference) is not None

    def test_unknown_reference_reads_none(self) -> None:
        writer = InProcessOffloadWriter()

        assert writer.read("/in_process_tool_results/deadbeef") is None


class TestLedgerRecordsOneDecisionOnce(AdmissionPayloadMixin, OffloadWriterMixin):
    """The single record both the model path and the event path read."""

    def test_event_path_gets_the_object_the_model_path_was_handed(self) -> None:
        """No second budgeting pass means no second, divergent decision."""

        gate = self.gate()

        admission = self.admit_oversized(gate)
        projected = gate.consume_event_projection(
            tool_call_id=self.CALL_ID,
            scope=self.SCOPE,
        )

        assert projected is admission
        assert projected.event_content == admission.preview

    def test_retried_call_projects_its_admissions_in_order(self) -> None:
        gate = self.gate()
        first = gate.admit_tool_return(
            "first attempt",
            tool_name=self.REGISTRY_TOOL,
            tool_call_id=self.CALL_ID,
            scope=self.SCOPE,
        )
        second = gate.admit_tool_return(
            "second attempt",
            tool_name=self.REGISTRY_TOOL,
            tool_call_id=self.CALL_ID,
            scope=self.SCOPE,
        )

        assert (
            gate.consume_event_projection(
                tool_call_id=self.CALL_ID,
                scope=self.SCOPE,
            )
            is first
        )
        assert (
            gate.consume_event_projection(
                tool_call_id=self.CALL_ID,
                scope=self.SCOPE,
            )
            is second
        )
        assert (
            gate.consume_event_projection(
                tool_call_id=self.CALL_ID,
                scope=self.SCOPE,
            )
            is None
        )

    def test_discard_drops_only_the_named_run(self) -> None:
        gate = self.gate()
        gate.admit_tool_return(
            "kept",
            tool_name=self.REGISTRY_TOOL,
            tool_call_id=self.CALL_ID,
            scope="run-keep",
        )
        gate.admit_tool_return(
            "dropped",
            tool_name=self.REGISTRY_TOOL,
            tool_call_id=self.CALL_ID,
            scope="run-drop",
        )

        gate.discard(scope="run-drop")

        assert gate.fact_for(tool_call_id=self.CALL_ID, scope="run-drop") is None
        assert gate.fact_for(tool_call_id=self.CALL_ID, scope="run-keep") is not None

    def test_admitted_digests_lookup_is_non_consuming(self) -> None:
        ledger = ToolResultAdmissionLedger()
        gate = self.gate(ledger=ledger)
        admission = self.admit_oversized(gate)

        digests = ledger.admitted_digests(scope=self.SCOPE, tool_call_id=self.CALL_ID)
        again = ledger.admitted_digests(scope=self.SCOPE, tool_call_id=self.CALL_ID)

        assert admission.fact is not None
        assert admission.fact.model_content_digest in digests
        assert digests == again
        assert (
            ledger.fact_for(scope=self.SCOPE, tool_call_id=self.CALL_ID)
            is admission.fact
        )

    def test_an_admission_without_a_fact_is_not_recorded(self) -> None:
        """Only an identified call has a decision to file."""

        ledger = ToolResultAdmissionLedger()
        adapter = ToolResultAdmissionAdapter(InProcessOffloadWriter())
        unidentified = adapter.admit("a result", trace_id="trace-1")

        ledger.record(unidentified, scope=self.SCOPE, tool_call_id=self.CALL_ID)

        assert unidentified.fact is None
        assert ledger.fact_for(scope=self.SCOPE, tool_call_id=self.CALL_ID) is None

    def test_an_injected_ledger_is_the_one_the_gate_records_into(self) -> None:
        """Shared wiring must not silently get a private ledger."""

        ledger = ToolResultAdmissionLedger()
        gate = self.gate(ledger=ledger)

        admission = self.admit_oversized(gate)

        assert gate.ledger is ledger
        assert (
            ledger.fact_for(scope=self.SCOPE, tool_call_id=self.CALL_ID)
            is admission.fact
        )
