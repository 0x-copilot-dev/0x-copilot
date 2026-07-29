"""F6.2 persisted batch plans: construction, durability, replay, and privacy.

Every test here answers one of the five lane questions: is the decision
deterministic, is it durable before a child could start, does replay reproduce
it exactly, is a duplicate append a conflict rather than a second plan, and can
a body reach the journal.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
import re

import pytest

from agent_runtime.api.conversation_coordinator import ConversationCoordinator
from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.api.run_control_store import EventJournalRunControlStore
from agent_runtime.api.run_coordinator import RunCoordinator
from agent_runtime.capabilities.concurrency import (
    BatchChildDisposition,
    BatchChildTransitionRecorder,
    BatchFailurePolicy,
    BatchJournalConflict,
    BatchJournalCorruption,
    BatchJournalScopeConflict,
    BatchJournalSnapshotConflict,
    BatchJournalWrite,
    BatchJournalRecordKind,
    BatchOperation,
    BatchPlanBoundRecord,
    BatchPlanner,
    BatchPlanRecorder,
    BatchPlanRequest,
    BatchRunBinding,
    BatchSegmentMode,
    BatchSegmentReason,
    ConcurrencyAllowance,
    ConcurrencyBounds,
    ConcurrencyKillSwitchGate,
    ConcurrencyKillSwitchReason,
    ConcurrencyKillSwitchScope,
    ConcurrencyMode,
    ConcurrencyPolicy,
    ConcurrencyPolicyResolver,
    ConcurrencyPolicyResolution,
    ConcurrencyScope,
    IdempotencyKind,
    OrderingRequirement,
    PlannedOperation,
    PolicySource,
    ProviderSessionConstraint,
    ResourceKeyDimension,
    ResourceKeyTemplate,
    SideEffectKind,
    validate_batch_journal_record,
)
from agent_runtime.capabilities.concurrency.batch_journal_store import (
    EventJournalBatchPlanStore,
)
from agent_runtime.control_plane import (
    BudgetEnvelope,
    FeatureMode,
    FeatureModeSet,
    RunControlSnapshot,
    RunControlSnapshotWrite,
    RunPolicyRevisions,
)
from agent_runtime.execution.contracts import StreamEventSource
from agent_runtime.execution.models import ModelConfigResolver
from agent_runtime.settings import RuntimeSettings
from agent_runtime.surfaces_v2.canonical_json import canonical_json_sha256
from runtime_adapters.file.runtime_api_store import FileRuntimeApiStore
from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from runtime_api.schemas import (
    CreateConversationRequest,
    CreateRunRequest,
    OperationBatchJournalPayload,
    RuntimeActivityKind,
    RuntimeApiEventType,
    RuntimeEventDraft,
    RuntimeEventPresentationProjector,
    RuntimeEventRedactionState,
    RuntimeEventVisibility,
)

_ORG = "org-f6-batch"
_USER = "user-f6-batch"
_SUBJECT = "a" * 64
_OTHER_SUBJECT = "b" * 64
_CONCURRENCY_REVISION = "concurrency-r1"
_CREATED_AT = datetime(2026, 7, 28, 6, 15, tzinfo=timezone.utc)
_SECRET_PROMPT = "private prompt that must not enter the F6 journal"
_CONNECTOR = "acme-crm"

# One live capability reference per fixture operation. The shape is F6.1's
# pattern-locked ``cap_<32 hex>``: a raw connector or tool name is structurally
# unable to become one.
_CAP_READ_A = "cap_" + "0" * 32
_CAP_READ_B = "cap_" + "1" * 32
_CAP_WRITE = "cap_" + "2" * 32
_CAP_UNDECLARED = "cap_" + "3" * 32


class BatchJournalFixtureMixin:
    """Fixtures, builders, and constants shared by every F6.2 test class."""

    @staticmethod
    def resource(seed: str) -> str:
        return f"hmac-sha256:{seed * 64}"

    @staticmethod
    def settings() -> RuntimeSettings:
        return RuntimeSettings.load(
            environ={
                "OPENAI_API_KEY": "sk-test",
                "RUNTIME_DEFAULT_PROVIDER": "openai",
                "RUNTIME_DEFAULT_MODEL": "gpt-5.4-mini",
            }
        )

    @classmethod
    async def new_run(cls, store):
        settings = cls.settings()
        run_coordinator = RunCoordinator(
            persistence=store,
            queue=store,
            event_producer=RuntimeEventProducer(
                persistence=store,
                event_store=store,
                on_event_appended=None,
            ),
            settings=settings,
            model_resolver=ModelConfigResolver(settings),
        )
        conversation_coordinator = ConversationCoordinator(
            persistence=store,
            settings=settings,
            run_coordinator=run_coordinator,
        )
        conversation = await conversation_coordinator.create_conversation(
            CreateConversationRequest(
                org_id=_ORG,
                user_id=_USER,
                assistant_id="assistant",
            )
        )
        run = await run_coordinator.create_run(
            CreateRunRequest(
                conversation_id=conversation.conversation_id,
                org_id=_ORG,
                user_id=_USER,
                user_input=_SECRET_PROMPT,
                model={"provider": "openai", "model_name": "gpt-5.4-mini"},
            )
        )
        return conversation, run

    @staticmethod
    def snapshot_for(run, conversation) -> RunControlSnapshot:
        budget = BudgetEnvelope.create(
            budget_envelope_id="budget-f6",
            revision="budget-r1",
            max_model_turns=8,
            max_tool_calls=16,
        )
        return RunControlSnapshot.create(
            run_id=run.run_id,
            conversation_id=conversation.conversation_id,
            subject_fingerprint=_SUBJECT,
            deployment_profile="single_user_desktop",
            harness_variant_ref="harness://stable/r1",
            task_policy_selection_ref="task-policy://unknown.general/r1",
            policy_revisions=RunPolicyRevisions(
                prompt="prompt-r1",
                capability="capability-r1",
                context="context-r1",
                tool_controller="tool-r1",
                concurrency=_CONCURRENCY_REVISION,
                dataflow="dataflow-r1",
                mcp_freshness="mcp-r1",
                delegation="delegation-r1",
                model_route="model-r1",
                workspace_edit="workspace-r1",
                answer_verification="answer-r1",
            ),
            feature_modes=FeatureModeSet(f6=FeatureMode.ENFORCE),
            budget_envelope_ref=budget.revision_ref,
            assignment_revision="assignment-r1",
            snapshot_id="snapshot-f6",
            created_at=_CREATED_AT,
        )

    @staticmethod
    def operation(
        operation_id: str,
        *,
        authorization_epoch: str = "auth_1",
        dependency_ids: tuple[str, ...] | None = (),
        resource_fingerprints: tuple[str, ...] | None = (),
    ) -> BatchOperation:
        return BatchOperation(
            operation_id=operation_id,
            authorization_epoch=authorization_epoch,
            dependency_ids=dependency_ids,
            resource_fingerprints=resource_fingerprints,
        )

    @staticmethod
    def read_policy(max_parallelism: int | None = None) -> ConcurrencyPolicy:
        return ConcurrencyPolicy(
            mode=ConcurrencyMode.PARALLEL_SAFE,
            side_effect=SideEffectKind.READ,
            idempotency=IdempotencyKind.NATURAL,
            resource_key_template=ResourceKeyTemplate.from_template(
                "{connector}/{object}"
            ),
            max_parallelism=max_parallelism,
            rate_limit_scope=ConcurrencyScope.CONNECTOR,
            ordering_requirement=OrderingRequirement.NONE,
            provider_session_constraint=(
                ProviderSessionConstraint.SESSION_PARALLEL_SAFE
            ),
            policy_source=PolicySource.PRODUCT_CATALOG,
        )

    @staticmethod
    def write_policy() -> ConcurrencyPolicy:
        return ConcurrencyPolicy(
            mode=ConcurrencyMode.SERIAL,
            side_effect=SideEffectKind.IRREVERSIBLE_WRITE,
            policy_source=PolicySource.PRODUCT_CATALOG,
        )

    @classmethod
    def parallel_request(cls, run, **overrides) -> BatchPlanRequest:
        """Two independent curated reads plus one write, in model order."""

        operations = (
            PlannedOperation.of(
                operation=cls.operation(
                    "op-read-a",
                    resource_fingerprints=(cls.resource("a"),),
                ),
                capability_ref=_CAP_READ_A,
                policy=cls.read_policy(),
            ),
            PlannedOperation.of(
                operation=cls.operation(
                    "op-read-b",
                    resource_fingerprints=(cls.resource("b"),),
                ),
                capability_ref=_CAP_READ_B,
                policy=cls.read_policy(),
            ),
            PlannedOperation.of(
                operation=cls.operation("op-write"),
                capability_ref=_CAP_WRITE,
                policy=cls.write_policy(),
            ),
        )
        payload: dict[str, object] = {
            "org_id": _ORG,
            "trace_id": run.trace_id,
            "subject_fingerprint": _SUBJECT,
            "run_id": run.run_id,
            "batch_id": "batch-turn-1",
            "turn_ordinal": 1,
            "operations": operations,
            "failure_policy": BatchFailurePolicy.STOP_NEW,
            "connector_id": _CONNECTOR,
        }
        payload.update(overrides)
        return BatchPlanRequest(**payload)

    @classmethod
    def undeclared_request(cls, run, **overrides) -> BatchPlanRequest:
        """Two operations whose descriptors declared nothing at all."""

        operations = (
            PlannedOperation.of(
                operation=cls.operation("op-unknown-a"),
                capability_ref=_CAP_UNDECLARED,
            ),
            PlannedOperation.of(
                operation=cls.operation("op-unknown-b"),
                capability_ref=_CAP_UNDECLARED,
            ),
        )
        payload: dict[str, object] = {
            "org_id": _ORG,
            "trace_id": run.trace_id,
            "subject_fingerprint": _SUBJECT,
            "run_id": run.run_id,
            "batch_id": "batch-unknown",
            "turn_ordinal": 1,
            "operations": operations,
        }
        payload.update(overrides)
        return BatchPlanRequest(**payload)

    @staticmethod
    def gate(max_parallelism: int = 4, *, source=None) -> ConcurrencyKillSwitchGate:
        return ConcurrencyKillSwitchGate(
            snapshot_allowance=ConcurrencyAllowance.enforcing(max_parallelism),
            source=source,
        )

    @classmethod
    def recorder(cls, store, snapshots, **gate_kwargs) -> BatchPlanRecorder:
        return BatchPlanRecorder(
            journal=EventJournalBatchPlanStore(events=store, snapshots=snapshots),
            gate=cls.gate(**gate_kwargs),
        )


@pytest.fixture(params=("in_memory", "file"))
async def seeded_store(request: pytest.FixtureRequest, tmp_path):
    """A live run with a bound control snapshot on each canonical adapter."""

    if request.param == "in_memory":
        store = InMemoryRuntimeApiStore()
    else:
        store = FileRuntimeApiStore(tmp_path / "runtime")
    await store.open()
    conversation, run = await BatchJournalFixtureMixin.new_run(store)
    controls = EventJournalRunControlStore(store)
    snapshot = await controls.get_or_create(
        RunControlSnapshotWrite(
            org_id=_ORG,
            trace_id=run.trace_id,
            snapshot=BatchJournalFixtureMixin.snapshot_for(run, conversation),
        )
    )
    try:
        yield store, conversation, run, controls, snapshot
    finally:
        await store.close()


class TestBatchPlanConstruction(BatchJournalFixtureMixin):
    """Construction is a pure, deterministic function of declared inputs."""

    def test_identical_inputs_produce_byte_identical_records(self) -> None:
        run = _StubRun()
        snapshot = _stub_snapshot()
        recorder = BatchPlanRecorder(journal=_NullJournal(), gate=self.gate())
        request = self.parallel_request(run)
        decision = self.gate().admit(connector_id=_CONNECTOR)

        first = recorder.build_record(
            request,
            snapshot=snapshot,
            decision=decision,
            created_at=_CREATED_AT,
        )
        second = recorder.build_record(
            request,
            snapshot=snapshot,
            decision=decision,
            created_at=_CREATED_AT + timedelta(seconds=90),
        )

        assert first.record_digest == second.record_digest
        assert first.plan_digest == second.plan_digest
        assert first.segments == second.segments
        assert first.model_dump(mode="json", exclude={"created_at"}) == (
            second.model_dump(mode="json", exclude={"created_at"})
        )
        # Observation time is deliberately outside the digest so two writers
        # that made the same decision converge instead of conflicting.
        assert first.created_at != second.created_at

    def test_independent_reads_overlap_and_a_write_never_joins_them(self) -> None:
        record = self._record(self.parallel_request(_StubRun()))

        modes = [segment.mode for segment in record.segments]
        assert modes == [BatchSegmentMode.PARALLEL, BatchSegmentMode.SERIAL]
        assert record.segments[0].operation_ids == ("op-read-a", "op-read-b")
        assert record.segments[0].reason is BatchSegmentReason.INDEPENDENT_READS
        assert record.segments[1].operation_ids == ("op-write",)
        assert record.segments[1].reason is BatchSegmentReason.POLICY_REQUIRES_SERIAL
        assert record.rebuild_plan().operation_ids == (
            "op-read-a",
            "op-read-b",
            "op-write",
        )

    def test_undeclared_metadata_yields_a_fully_serial_plan(self) -> None:
        record = self._record(self.undeclared_request(_StubRun()))

        assert all(
            segment.mode is BatchSegmentMode.SERIAL for segment in record.segments
        )
        assert all(len(segment.operation_ids) == 1 for segment in record.segments)
        assert {segment.reason for segment in record.segments} == {
            BatchSegmentReason.CONSERVATIVE_POLICY_DEFAULT
        }
        assert all(
            segment.allowance.effective_max_parallelism
            == ConcurrencyBounds.SERIAL_PARALLELISM
            for segment in record.segments
        )
        assert record.operations[0].policy == ConcurrencyPolicy()

    def test_a_connector_kill_switch_forces_serial_and_is_recorded(self) -> None:
        record = self._record(
            self.parallel_request(_StubRun()),
            source=_KillSwitchSource([f"connector:{_CONNECTOR}"]),
        )

        assert record.effective_allowance.is_serial
        assert record.snapshot_allowance.permits_parallel
        assert record.kill_switch_reason is (
            ConcurrencyKillSwitchReason.CONNECTOR_KILL_SWITCH
        )
        assert record.kill_switch_scope is ConcurrencyKillSwitchScope.CONNECTOR
        assert all(
            segment.mode is BatchSegmentMode.SERIAL for segment in record.segments
        )
        assert {segment.reason for segment in record.segments} == {
            BatchSegmentReason.BATCH_SERIAL_DEFAULT
        }

    def test_an_unreadable_kill_switch_source_forces_serial(self) -> None:
        record = self._record(
            self.parallel_request(_StubRun()),
            source=_ExplodingKillSwitchSource(),
        )

        assert record.effective_allowance.is_serial
        assert record.kill_switch_reason is (
            ConcurrencyKillSwitchReason.SWITCH_SOURCE_UNAVAILABLE
        )
        assert all(
            segment.mode is BatchSegmentMode.SERIAL for segment in record.segments
        )

    def test_a_narrower_kill_switch_never_widens_the_run_snapshot(self) -> None:
        # A run admitted serial stays serial even with every switch off.
        record = self._record(
            self.parallel_request(_StubRun()),
            max_parallelism=ConcurrencyBounds.SERIAL_PARALLELISM,
        )

        assert record.snapshot_allowance.is_serial
        assert record.effective_allowance.is_serial
        assert record.kill_switch_reason is (
            ConcurrencyKillSwitchReason.SNAPSHOT_ALREADY_SERIAL
        )
        assert all(
            segment.mode is BatchSegmentMode.SERIAL for segment in record.segments
        )

    def test_a_resolved_policy_binds_without_restating_its_digest(self) -> None:
        resolution = ConcurrencyPolicyResolver().resolve(
            capability_ref=_CAP_READ_A,
            declarations=(),
        )
        planned = PlannedOperation.resolved(
            operation=self.operation("op-resolved"),
            resolution=resolution,
        )

        assert planned.capability_ref == _CAP_READ_A
        assert planned.policy_digest == resolution.policy_digest
        assert planned.policy.policy_source is PolicySource.CONSERVATIVE_DEFAULT

    def test_a_mismatched_policy_digest_is_refused(self) -> None:
        with pytest.raises(ValueError, match="policy digest"):
            PlannedOperation(
                operation=self.operation("op-tampered"),
                capability_ref=_CAP_READ_A,
                policy=self.read_policy(),
                policy_digest=ConcurrencyPolicyResolution.digest_of(
                    ConcurrencyPolicy()
                ),
            )

    def test_a_raw_capability_name_cannot_become_a_capability_ref(self) -> None:
        with pytest.raises(ValueError):
            PlannedOperation.of(
                operation=self.operation("op-leaky"),
                capability_ref="acme-crm/search_contacts",
            )

    def _record(self, request: BatchPlanRequest, **gate_kwargs):
        gate = self.gate(**gate_kwargs)
        recorder = BatchPlanRecorder(journal=_NullJournal(), gate=gate)
        return recorder.build_record(
            request,
            snapshot=_stub_snapshot(),
            decision=gate.admit(connector_id=request.connector_id),
            created_at=_CREATED_AT,
        )


class TestBatchPlanRecordIntegrity(BatchJournalFixtureMixin):
    """The record refuses to exist unless it reproduces its own decision.

    Every forgery below is **resealed** — its ``record_digest`` is recomputed so
    the tamper-evidence seal passes. That models an attacker or a buggy writer
    who knows the digest algorithm, and it is what isolates the reproducibility
    check from the digest check. One test deliberately skips the reseal, to
    prove the seal covers the segmentation too.
    """

    def test_an_unsealed_edit_is_caught_by_the_record_digest(self) -> None:
        record = self._parallel_record()
        forged = record.model_dump(mode="json")
        forged["turn_ordinal"] = record.turn_ordinal + 1

        with pytest.raises(ValueError, match="digest does not match its body"):
            validate_batch_journal_record(forged)

    def test_an_unsealed_segment_edit_is_caught_by_the_record_digest(self) -> None:
        record = self._parallel_record()
        forged = record.model_dump(mode="json")
        forged["segments"] = list(reversed(forged["segments"]))

        with pytest.raises(ValueError, match="digest does not match its body"):
            validate_batch_journal_record(forged)

    def test_segments_that_are_not_the_planners_decision_are_refused(self) -> None:
        record = self._parallel_record()
        # Widen the plan by hand: pull the write into the read segment.
        forged = _resealed(
            record,
            segments=[
                {
                    "segment_index": 0,
                    "mode": "parallel",
                    "operation_ids": ["op-read-a", "op-read-b", "op-write"],
                    "reason": "independent_reads",
                    "allowance": {"mode": "enforce", "max_parallelism": 4},
                }
            ],
        )

        with pytest.raises(ValueError, match="planner's decision"):
            validate_batch_journal_record(forged)

    def test_a_resealed_reordered_segment_list_is_refused(self) -> None:
        record = self._parallel_record()
        forged = _resealed(
            record,
            segments=list(reversed(record.model_dump(mode="json")["segments"])),
        )

        with pytest.raises(ValueError, match="planner's decision"):
            validate_batch_journal_record(forged)

    def test_a_resealed_stale_plan_digest_is_refused(self) -> None:
        record = self._parallel_record()
        forged = _resealed(record, plan_digest="f" * 64)

        with pytest.raises(ValueError, match="plan digest"):
            validate_batch_journal_record(forged)

    def test_an_effective_allowance_wider_than_the_snapshot_is_refused(self) -> None:
        record = self._parallel_record()
        forged = _resealed(
            record,
            snapshot_allowance={"mode": "enforce", "max_parallelism": 2},
        )

        with pytest.raises(ValueError, match="raise the run ceiling"):
            validate_batch_journal_record(forged)

    def test_an_effective_mode_wider_than_the_snapshot_is_refused(self) -> None:
        record = self._parallel_record()
        forged = _resealed(
            record,
            snapshot_allowance={"mode": "shadow", "max_parallelism": 4},
        )

        with pytest.raises(ValueError, match="broaden the run snapshot"):
            validate_batch_journal_record(forged)

    def test_a_plan_must_use_its_batchs_stable_record_id(self) -> None:
        record = self._parallel_record()
        forged = _resealed(record, record_id="batch-plan:some-other-batch")

        with pytest.raises(ValueError, match="stable record id"):
            validate_batch_journal_record(forged)

    def test_a_valid_record_round_trips_through_json(self) -> None:
        record = self._parallel_record()

        assert validate_batch_journal_record(record.model_dump(mode="json")) == record

    def test_the_record_rebuilds_the_planner_input_and_output(self) -> None:
        record = self._parallel_record()

        replanned = BatchPlanner().plan(
            record.rebuild_batch(),
            record.rebuild_policies(),
        )
        assert replanned == record.rebuild_plan()
        assert record.rebuild_batch().allowance == record.effective_allowance
        assert set(record.rebuild_policies()) == {
            "op-read-a",
            "op-read-b",
            "op-write",
        }

    def _parallel_record(self) -> BatchPlanBoundRecord:
        gate = self.gate()
        return BatchPlanRecorder(journal=_NullJournal(), gate=gate).build_record(
            self.parallel_request(_StubRun()),
            snapshot=_stub_snapshot(),
            decision=gate.admit(connector_id=_CONNECTOR),
            created_at=_CREATED_AT,
        )


class TestBatchPlanDurability(BatchJournalFixtureMixin):
    """Durability, idempotency, and replay across both canonical adapters."""

    async def test_a_plan_is_durable_before_a_dispatch_handle_exists(
        self,
        seeded_store,
    ) -> None:
        store, _conversation, run, controls, _snapshot = seeded_store
        journal = EventJournalBatchPlanStore(events=store, snapshots=controls)
        observing = _AppendObservingStore(store)
        recorder = BatchPlanRecorder(
            journal=EventJournalBatchPlanStore(
                events=observing,
                snapshots=controls,
            ),
            gate=self.gate(),
        )

        durable = await recorder.record(
            self.parallel_request(run),
            snapshot=await self._snapshot(controls, run),
        )

        # The handle only exists after a completed canonical append, and the
        # append carried the whole plan — so no child can start before the
        # ordering is on disk.
        assert observing.appended_event_types == [
            RuntimeApiEventType.OPERATION_BATCH_JOURNAL
        ]
        assert durable.sequence_no > 0
        assert durable.plan.segments == durable.record.segments
        replayed = await journal.load_recovery_view(
            org_id=_ORG,
            run_id=run.run_id,
            subject_fingerprint=_SUBJECT,
        )
        assert replayed.plan_for("batch-turn-1") == durable

    async def test_replay_reconstructs_the_identical_plan(
        self,
        seeded_store,
    ) -> None:
        store, _conversation, run, controls, _snapshot = seeded_store
        recorder = self.recorder(store, controls)
        snapshot = await self._snapshot(controls, run)

        first = await recorder.record(self.parallel_request(run), snapshot=snapshot)
        second = await recorder.record(
            self.undeclared_request(run),
            snapshot=snapshot,
        )

        view = await EventJournalBatchPlanStore(
            events=store,
            snapshots=controls,
        ).load_recovery_view(
            org_id=_ORG,
            run_id=run.run_id,
            subject_fingerprint=_SUBJECT,
        )
        assert view.plans == (first, second)
        assert view.run_id == run.run_id
        assert view.plan_for("batch-turn-1").plan == first.plan
        assert view.plan_for("batch-unknown").plan == second.plan
        assert view.plan_for("never-planned") is None
        assert [item.sequence_no for item in view.records] == sorted(
            item.sequence_no for item in view.records
        )

    async def test_after_sequence_returns_only_later_plans(
        self,
        seeded_store,
    ) -> None:
        store, _conversation, run, controls, _snapshot = seeded_store
        recorder = self.recorder(store, controls)
        snapshot = await self._snapshot(controls, run)
        first = await recorder.record(self.parallel_request(run), snapshot=snapshot)
        second = await recorder.record(
            self.undeclared_request(run),
            snapshot=snapshot,
        )

        journal = EventJournalBatchPlanStore(events=store, snapshots=controls)
        later = await journal.load_recovery_view(
            org_id=_ORG,
            run_id=run.run_id,
            subject_fingerprint=_SUBJECT,
            after_sequence=first.sequence_no,
        )

        assert later.plans == (second,)
        with pytest.raises(ValueError, match="non-negative"):
            await journal.load_recovery_view(
                org_id=_ORG,
                run_id=run.run_id,
                subject_fingerprint=_SUBJECT,
                after_sequence=-1,
            )

    async def test_an_identical_retry_returns_the_first_durable_plan(
        self,
        seeded_store,
    ) -> None:
        store, _conversation, run, controls, _snapshot = seeded_store
        recorder = self.recorder(store, controls)
        snapshot = await self._snapshot(controls, run)
        request = self.parallel_request(run)

        first = await recorder.record(request, snapshot=snapshot)
        retried = await recorder.record(request, snapshot=snapshot)

        assert retried == first
        view = await EventJournalBatchPlanStore(
            events=store,
            snapshots=controls,
        ).load_recovery_view(
            org_id=_ORG,
            run_id=run.run_id,
            subject_fingerprint=_SUBJECT,
        )
        assert len(view.plans) == 1

    async def test_a_different_plan_under_one_batch_id_is_a_conflict(
        self,
        seeded_store,
    ) -> None:
        store, _conversation, run, controls, _snapshot = seeded_store
        recorder = self.recorder(store, controls)
        snapshot = await self._snapshot(controls, run)
        await recorder.record(self.parallel_request(run), snapshot=snapshot)

        # Same batch identity, a materially different ordering decision.
        rewritten = self.parallel_request(
            run,
            operations=(
                PlannedOperation.of(
                    operation=self.operation("op-read-a"),
                    capability_ref=_CAP_READ_A,
                ),
            ),
        )

        with pytest.raises(BatchJournalConflict) as excinfo:
            await recorder.record(rewritten, snapshot=snapshot)

        assert excinfo.value.run_id == run.run_id
        assert excinfo.value.record_id == "batch-plan:batch-turn-1"
        view = await EventJournalBatchPlanStore(
            events=store,
            snapshots=controls,
        ).load_recovery_view(
            org_id=_ORG,
            run_id=run.run_id,
            subject_fingerprint=_SUBJECT,
        )
        assert len(view.plans) == 1

    async def test_a_racing_writer_with_the_same_decision_converges(
        self,
        seeded_store,
    ) -> None:
        store, _conversation, run, controls, _snapshot = seeded_store
        snapshot = await self._snapshot(controls, run)
        gate = self.gate()
        first = await BatchPlanRecorder(
            journal=EventJournalBatchPlanStore(events=store, snapshots=controls),
            gate=gate,
        ).record(self.parallel_request(run), snapshot=snapshot)

        # This writer's prefix read happened before the winner's append, so the
        # duplicate is only detectable at the store's stable event identity.
        blind = _RaceBlindEventStore(store)
        racing = await BatchPlanRecorder(
            journal=EventJournalBatchPlanStore(events=blind, snapshots=controls),
            gate=gate,
        ).record(self.parallel_request(run), snapshot=snapshot)

        assert blind.hidden_reads == 1
        assert racing == first
        view = await EventJournalBatchPlanStore(
            events=store,
            snapshots=controls,
        ).load_recovery_view(
            org_id=_ORG,
            run_id=run.run_id,
            subject_fingerprint=_SUBJECT,
        )
        assert len(view.plans) == 1

    async def test_a_racing_writer_with_a_different_decision_conflicts(
        self,
        seeded_store,
    ) -> None:
        store, _conversation, run, controls, _snapshot = seeded_store
        snapshot = await self._snapshot(controls, run)
        gate = self.gate()
        recorder = BatchPlanRecorder(
            journal=EventJournalBatchPlanStore(events=store, snapshots=controls),
            gate=gate,
        )
        await recorder.record(self.parallel_request(run), snapshot=snapshot)

        divergent = self.parallel_request(
            run,
            operations=(
                PlannedOperation.of(
                    operation=self.operation("op-read-a"),
                    capability_ref=_CAP_READ_A,
                ),
            ),
        )
        blind = _RaceBlindEventStore(store)

        with pytest.raises(BatchJournalConflict) as excinfo:
            await BatchPlanRecorder(
                journal=EventJournalBatchPlanStore(events=blind, snapshots=controls),
                gate=gate,
            ).record(divergent, snapshot=snapshot)

        assert blind.hidden_reads == 1
        assert excinfo.value.record_id == "batch-plan:batch-turn-1"
        view = await EventJournalBatchPlanStore(
            events=store,
            snapshots=controls,
        ).load_recovery_view(
            org_id=_ORG,
            run_id=run.run_id,
            subject_fingerprint=_SUBJECT,
        )
        assert len(view.plans) == 1
        assert view.plans[0].plan.operation_ids == (
            "op-read-a",
            "op-read-b",
            "op-write",
        )

    async def test_scope_and_snapshot_binding_fail_closed(
        self,
        seeded_store,
    ) -> None:
        store, _conversation, run, controls, _snapshot = seeded_store
        journal = EventJournalBatchPlanStore(events=store, snapshots=controls)
        recorder = self.recorder(store, controls)
        snapshot = await self._snapshot(controls, run)
        await recorder.record(self.parallel_request(run), snapshot=snapshot)

        with pytest.raises(BatchJournalScopeConflict):
            await journal.load_recovery_view(
                org_id=_ORG,
                run_id=run.run_id,
                subject_fingerprint=_OTHER_SUBJECT,
            )

        gate = self.gate()
        mismatched = BatchPlanRecorder(
            journal=journal,
            gate=gate,
        ).build_record(
            self.undeclared_request(run),
            snapshot=snapshot.model_copy(update={"snapshot_id": "another-snapshot"}),
            decision=gate.admit(),
            created_at=_CREATED_AT,
        )
        with pytest.raises(BatchJournalSnapshotConflict):
            await journal.put_plan(
                BatchJournalWrite(
                    org_id=_ORG,
                    trace_id=run.trace_id,
                    subject_fingerprint=_SUBJECT,
                    record=mismatched,
                )
            )

    async def test_a_plan_bound_to_another_policy_revision_is_refused(
        self,
        seeded_store,
    ) -> None:
        store, _conversation, run, controls, _snapshot = seeded_store
        journal = EventJournalBatchPlanStore(events=store, snapshots=controls)
        snapshot = await self._snapshot(controls, run)
        gate = self.gate()
        stale = BatchPlanRecorder(journal=journal, gate=gate).build_record(
            self.parallel_request(run),
            snapshot=snapshot.model_copy(
                update={
                    "policy_revisions": snapshot.policy_revisions.model_copy(
                        update={"concurrency": "concurrency-r0"}
                    )
                }
            ),
            decision=gate.admit(connector_id=_CONNECTOR),
            created_at=_CREATED_AT,
        )

        with pytest.raises(BatchJournalSnapshotConflict):
            await journal.put_plan(
                BatchJournalWrite(
                    org_id=_ORG,
                    trace_id=run.trace_id,
                    subject_fingerprint=_SUBJECT,
                    record=stale,
                )
            )

    async def test_a_journal_without_a_snapshot_cannot_be_written_or_read(
        self,
        seeded_store,
    ) -> None:
        store, conversation, run, _controls, _snapshot = seeded_store
        empty = EventJournalRunControlStore(store)
        journal = EventJournalBatchPlanStore(
            events=store,
            snapshots=_EmptySnapshotStore(empty),
        )
        gate = self.gate()
        record = BatchPlanRecorder(journal=journal, gate=gate).build_record(
            self.undeclared_request(run),
            snapshot=self.snapshot_for(run, conversation),
            decision=gate.admit(),
            created_at=_CREATED_AT,
        )

        with pytest.raises(BatchJournalCorruption, match="control snapshot"):
            await journal.put_plan(
                BatchJournalWrite(
                    org_id=_ORG,
                    trace_id=run.trace_id,
                    subject_fingerprint=_SUBJECT,
                    record=record,
                )
            )
        with pytest.raises(BatchJournalCorruption, match="snapshot"):
            await journal.load_recovery_view(
                org_id=_ORG,
                run_id=run.run_id,
                subject_fingerprint=_SUBJECT,
            )

    async def test_replay_detects_a_wrong_stable_event_identity(
        self,
        seeded_store,
    ) -> None:
        store, conversation, run, controls, _snapshot = seeded_store
        journal = EventJournalBatchPlanStore(events=store, snapshots=controls)
        recorder = self.recorder(store, controls)
        snapshot = await self._snapshot(controls, run)
        gate = self.gate()
        smuggled = BatchPlanRecorder(journal=journal, gate=gate).build_record(
            self.undeclared_request(run),
            snapshot=snapshot,
            decision=gate.admit(),
            created_at=_CREATED_AT,
        )
        await recorder.record(self.parallel_request(run), snapshot=snapshot)
        await store.append_event(
            RuntimeEventDraft(
                org_id=_ORG,
                event_id="wrong-stable-event-id",
                created_at=smuggled.created_at,
                run_id=run.run_id,
                conversation_id=conversation.conversation_id,
                trace_id=run.trace_id,
                source=StreamEventSource.RUNTIME,
                event_type=RuntimeApiEventType.OPERATION_BATCH_JOURNAL,
                activity_kind=RuntimeActivityKind.EVENT,
                visibility=RuntimeEventVisibility.INTERNAL,
                redaction_state=RuntimeEventRedactionState.REDACTED,
                payload=OperationBatchJournalPayload(record=smuggled).model_dump(
                    mode="json"
                ),
            )
        )

        with pytest.raises(BatchJournalCorruption, match="stable identity"):
            await journal.load_recovery_view(
                org_id=_ORG,
                run_id=run.run_id,
                subject_fingerprint=_SUBJECT,
            )

    async def test_replay_detects_a_user_visible_projection(
        self,
        seeded_store,
    ) -> None:
        store, conversation, run, controls, _snapshot = seeded_store
        journal = EventJournalBatchPlanStore(events=store, snapshots=controls)
        gate = self.gate()
        record = BatchPlanRecorder(journal=journal, gate=gate).build_record(
            self.undeclared_request(run),
            snapshot=await self._snapshot(controls, run),
            decision=gate.admit(),
            created_at=_CREATED_AT,
        )
        identity = f"{record.run_id}:{record.record_id}"
        await store.append_event(
            RuntimeEventDraft(
                org_id=_ORG,
                event_id="operation_batch:"
                + hashlib.sha256(identity.encode("utf-8")).hexdigest(),
                created_at=record.created_at,
                run_id=run.run_id,
                conversation_id=conversation.conversation_id,
                trace_id=run.trace_id,
                source=StreamEventSource.RUNTIME,
                event_type=RuntimeApiEventType.OPERATION_BATCH_JOURNAL,
                activity_kind=RuntimeActivityKind.TOOL,
                visibility=RuntimeEventVisibility.INTERNAL,
                redaction_state=RuntimeEventRedactionState.REDACTED,
                payload=OperationBatchJournalPayload(record=record).model_dump(
                    mode="json"
                ),
            )
        )

        with pytest.raises(BatchJournalCorruption, match="activity projection"):
            await journal.load_recovery_view(
                org_id=_ORG,
                run_id=run.run_id,
                subject_fingerprint=_SUBJECT,
            )

    async def test_the_file_store_replays_identical_plans_after_a_restart(
        self,
        tmp_path,
    ) -> None:
        root = tmp_path / "runtime"
        first = FileRuntimeApiStore(root)
        await first.open()
        conversation, run = await self.new_run(first)
        first_controls = EventJournalRunControlStore(first)
        snapshot = await first_controls.get_or_create(
            RunControlSnapshotWrite(
                org_id=_ORG,
                trace_id=run.trace_id,
                snapshot=self.snapshot_for(run, conversation),
            )
        )
        recorder = self.recorder(first, first_controls)
        expected = (
            await recorder.record(self.parallel_request(run), snapshot=snapshot),
            await recorder.record(self.undeclared_request(run), snapshot=snapshot),
        )
        await first.close()

        reopened = FileRuntimeApiStore(root)
        await reopened.open()
        try:
            recovered = await EventJournalBatchPlanStore(
                events=reopened,
                snapshots=EventJournalRunControlStore(reopened),
            ).load_recovery_view(
                org_id=_ORG,
                run_id=run.run_id,
                subject_fingerprint=_SUBJECT,
            )
        finally:
            await reopened.close()

        assert recovered.plans == expected
        assert recovered.plans[0].plan == expected[0].plan
        assert recovered.plans[1].plan.segments == expected[1].plan.segments

    @staticmethod
    async def _snapshot(controls, run) -> RunControlSnapshot:
        return await controls.get(
            org_id=_ORG,
            run_id=run.run_id,
            subject_fingerprint=_SUBJECT,
        )


class TestBatchJournalAppendCost(BatchJournalFixtureMixin):
    """BUG-20: one run's F6 appends must not re-read the whole run each time.

    The defect was not a slow query, it was the wrong *shape*: every append
    re-read the run's entire event log — model deltas, tool events, and all —
    to rebuild a prefix it had already seen, twice over once the control
    snapshot's own full scan is counted.  Cost was therefore quadratic in
    appends, and F6 writes one plan record per turn plus two child records per
    child.

    These two tests pin the shape, not a timing: they count the *envelopes the
    store hands back*, which is the quantity the defect made grow.
    """

    _TURNS = 12

    async def test_one_append_does_not_re_read_the_whole_run(
        self,
        seeded_store,
    ) -> None:
        """The marginal cost of an append must not grow with the journal."""

        store, _conversation, run, controls, _snapshot = seeded_store
        counting = _ReadCountingEventStore(store)
        # The control store reads the same log, so it is wired through the
        # counter too: the snapshot scan is half of what an append costs.
        journal = EventJournalBatchPlanStore(
            events=counting,
            snapshots=EventJournalRunControlStore(counting),
        )
        snapshot = await self._snapshot(controls, run)
        plans = BatchPlanRecorder(journal=journal, gate=self.gate())
        children = BatchChildTransitionRecorder(
            journal=journal,
            binding=BatchRunBinding.of(
                org_id=_ORG,
                trace_id=run.trace_id,
                snapshot=snapshot,
            ),
        )

        costs: list[int] = []
        for turn in range(self._TURNS):
            before = counting.events_read
            await self._turn(plans, children, run, snapshot, turn)
            costs.append(counting.events_read - before)

        # The first turn legitimately reads the durable prefix it is appending
        # to.  Every later turn appends to a prefix it already holds, and this
        # run emits nothing but F6 events, so there is nothing new to read.
        assert costs[0] > 0
        assert costs[1:] == [0] * (self._TURNS - 1), (
            "an append re-read events it had already folded; "
            f"per-turn envelope reads were {costs}"
        )

    async def test_total_reads_stay_linear_as_appends_double(
        self,
        seeded_store,
    ) -> None:
        """Doubling the appends must not quadruple the events read."""

        store, _conversation, run, controls, _snapshot = seeded_store
        counting = _ReadCountingEventStore(store)
        # The control store reads the same log, so it is wired through the
        # counter too: the snapshot scan is half of what an append costs.
        journal = EventJournalBatchPlanStore(
            events=counting,
            snapshots=EventJournalRunControlStore(counting),
        )
        snapshot = await self._snapshot(controls, run)
        plans = BatchPlanRecorder(journal=journal, gate=self.gate())
        children = BatchChildTransitionRecorder(
            journal=journal,
            binding=BatchRunBinding.of(
                org_id=_ORG,
                trace_id=run.trace_id,
                snapshot=snapshot,
            ),
        )

        for turn in range(self._TURNS):
            await self._turn(plans, children, run, snapshot, turn)
        half = counting.events_read
        for turn in range(self._TURNS, self._TURNS * 2):
            await self._turn(plans, children, run, snapshot, turn)
        full = counting.events_read

        # Quadratic cost roughly quadruples here; linear cost at most doubles.
        # The bound is on the ratio rather than an absolute count so it stays
        # true whatever the fixture's own run-creation events happen to number.
        assert full <= half * 2, (
            f"doubling the appends more than doubled the events read: {half} -> {full}"
        )
        assert counting.reads == self._TURNS * 2 * 3 + 1, (
            f"an append issued an unexpected number of store reads: {counting.reads}"
        )

    @classmethod
    async def _turn(cls, plans, children, run, snapshot, turn: int) -> None:
        """Journal one whole turn: a plan, then one child's two lifecycle facts."""

        batch_id = f"batch-cost-{turn}"
        await plans.record(
            cls.parallel_request(run, batch_id=batch_id, turn_ordinal=turn + 1),
            snapshot=snapshot,
        )
        await children.record_dispatch_intent(
            batch_id=batch_id,
            operation_id="op-read-a",
        )
        await children.record_settled(
            batch_id=batch_id,
            operation_id="op-read-a",
            disposition=BatchChildDisposition.SUCCEEDED,
        )

    @staticmethod
    async def _snapshot(controls, run) -> RunControlSnapshot:
        return await controls.get(
            org_id=_ORG,
            run_id=run.run_id,
            subject_fingerprint=_SUBJECT,
        )


class TestBatchJournalPrivacy(BatchJournalFixtureMixin):
    """No prompt, argument, result, connector name, or host path can enter."""

    async def test_persisted_events_are_internal_redacted_and_body_free(
        self,
        seeded_store,
    ) -> None:
        store, _conversation, run, controls, _snapshot = seeded_store
        recorder = self.recorder(store, controls)
        snapshot = await controls.get(
            org_id=_ORG,
            run_id=run.run_id,
            subject_fingerprint=_SUBJECT,
        )
        await recorder.record(self.parallel_request(run), snapshot=snapshot)

        events = await store.list_events_after(
            org_id=_ORG,
            run_id=run.run_id,
            after_sequence=0,
        )
        journal_events = tuple(
            event
            for event in events
            if event.event_type is RuntimeApiEventType.OPERATION_BATCH_JOURNAL
        )
        assert len(journal_events) == 1
        assert all(
            event.visibility is RuntimeEventVisibility.INTERNAL
            for event in journal_events
        )
        assert all(
            event.redaction_state is RuntimeEventRedactionState.REDACTED
            for event in journal_events
        )
        assert all(
            event.activity_kind is RuntimeActivityKind.EVENT for event in journal_events
        )
        assert all(
            RuntimeEventPresentationProjector.payload_for_event(
                event_type=event.event_type,
                payload=event.payload,
            )
            == event.payload
            for event in journal_events
        )

        serialized = "".join(event.model_dump_json() for event in journal_events)
        for forbidden in (
            _SECRET_PROMPT,
            "private prompt",
            _CONNECTOR,
            "search_contacts",
            "raw_arguments",
            "raw_result",
            "credential",
            "https://",
            "/Users/",
        ):
            assert forbidden not in serialized

    def test_the_projector_drops_extra_private_fields(self) -> None:
        payload = {
            "record": {
                "schema_version": 1,
                "record_kind": "plan_bound",
                "record_id": "batch-plan:batch-1",
                "record_digest": "0" * 64,
                "run_id": "run-1",
                "snapshot_id": "snapshot-1",
                "created_at": _CREATED_AT.isoformat(),
                "raw_arguments": {"secret": "must not survive"},
            }
        }

        assert (
            RuntimeEventPresentationProjector.payload_for_event(
                event_type=RuntimeApiEventType.OPERATION_BATCH_JOURNAL,
                payload=payload,
            )
            == {}
        )

    def test_a_resource_fingerprint_must_be_a_keyed_digest(self) -> None:
        with pytest.raises(ValueError, match="keyed HMAC-SHA256"):
            self.operation(
                "op-leaky",
                resource_fingerprints=("crm://accounts/acme-holdings",),
            )

    def test_every_persisted_field_is_an_identity_or_closed_value(self) -> None:
        gate = self.gate()
        record = BatchPlanRecorder(journal=_NullJournal(), gate=gate).build_record(
            self.parallel_request(_StubRun()),
            snapshot=_stub_snapshot(),
            decision=gate.admit(connector_id=_CONNECTOR),
            created_at=_CREATED_AT,
        )

        for value in _leaf_strings(record.model_dump(mode="json")):
            assert _is_body_free(value), value


class TestBatchJournalClientContract(BatchJournalFixtureMixin):
    """The published TypeScript contract must not drift from the Python one.

    ``test_api_type_contracts`` already pins the transport event-type tuple
    against the whole ``RuntimeApiEventType`` enum, so the new event value is
    covered there. These assertions pin the F6 record shape itself, which that
    file does not know about.
    """

    def test_typescript_record_fields_match_the_python_contract(self) -> None:
        source = self._api_types()

        assert self._interface_fields(source, "BatchPlanBoundRecord") == set(
            BatchPlanBoundRecord.model_fields
        )
        assert self._interface_fields(source, "PlannedOperation") == set(
            PlannedOperation.model_fields
        )
        assert self._interface_fields(source, "BatchOperation") == set(
            BatchOperation.model_fields
        )
        assert self._interface_fields(source, "ConcurrencyPolicy") == set(
            ConcurrencyPolicy.model_fields
        )
        assert self._interface_fields(source, "OperationBatchJournalPayload") == {
            "record"
        }

    def test_typescript_vocabularies_match_the_python_enums(self) -> None:
        source = self._api_types()

        for name, vocabulary in (
            ("BatchSegmentMode", BatchSegmentMode),
            ("BatchSegmentReason", BatchSegmentReason),
            ("BatchFailurePolicy", BatchFailurePolicy),
            ("ConcurrencyMode", ConcurrencyMode),
            ("ConcurrencySideEffectKind", SideEffectKind),
            ("ConcurrencyIdempotencyKind", IdempotencyKind),
            ("ConcurrencyScope", ConcurrencyScope),
            ("ConcurrencyOrderingRequirement", OrderingRequirement),
            (
                "ConcurrencyProviderSessionConstraint",
                ProviderSessionConstraint,
            ),
            ("ConcurrencyPolicySource", PolicySource),
            ("ConcurrencyResourceKeyDimension", ResourceKeyDimension),
            ("ConcurrencyKillSwitchReason", ConcurrencyKillSwitchReason),
            ("ConcurrencyKillSwitchScope", ConcurrencyKillSwitchScope),
        ):
            assert self._string_union(source, name) == {
                member.value for member in vocabulary
            }, name

    def test_the_record_kind_union_lags_python_only_where_intended(self) -> None:
        """The one place F6.6 knowingly leaves the published contract behind.

        ``child_transition`` is a backend record on an event family clients
        already receive, so the TypeScript union genuinely needs it — but
        ``packages/api-types`` is a cross-package change this lane may not make.
        Rather than drop the guard, it is narrowed to the exact, named delta:
        TypeScript may still never invent a kind the backend does not have, and
        any *other* new Python kind still fails here.

        The assertion is deliberately written to stay green once the pending
        api-types addition lands, so closing the gap does not require touching
        this test.
        """

        published = self._string_union(self._api_types(), "BatchJournalRecordKind")
        python_kinds = {member.value for member in BatchJournalRecordKind}
        pending = {BatchJournalRecordKind.CHILD_TRANSITION.value}

        assert published <= python_kinds
        assert python_kinds - published <= pending
        assert BatchJournalRecordKind.PLAN_BOUND.value in published

    @staticmethod
    def _api_types() -> str:
        repo_root = Path(__file__).resolve().parents[7]
        return (repo_root / "packages/api-types/src/index.ts").read_text()

    @staticmethod
    def _interface_fields(source: str, name: str) -> set[str]:
        match = re.search(
            rf"export interface {name}(?: extends \w+)?\s*\{{(?P<body>.*?)\n\}}",
            source,
            re.DOTALL,
        )
        assert match is not None, f"missing TypeScript interface {name}"
        fields = set(re.findall(r"^\s*(\w+)[?]?:", match.group("body"), re.MULTILINE))
        base = re.search(rf"export interface {name} extends (\w+)", source)
        if base is not None:
            fields |= TestBatchJournalClientContract._interface_fields(
                source,
                base.group(1),
            )
        return fields

    @staticmethod
    def _string_union(source: str, name: str) -> set[str]:
        match = re.search(
            rf"export type {name}\s*=\s*(?P<body>.*?);",
            source,
            re.DOTALL,
        )
        assert match is not None, f"missing TypeScript union {name}"
        return set(re.findall(r'"([^"]+)"', match.group("body")))


class _StubRun:
    """The transport identities a request needs, without a live store."""

    run_id = "run-f6-stub"
    trace_id = "trace-f6-stub"


def _stub_snapshot() -> RunControlSnapshot:
    class _StubConversation:
        conversation_id = "conversation-f6-stub"

    return BatchJournalFixtureMixin.snapshot_for(_StubRun(), _StubConversation())


class _NullJournal:
    """A store that refuses to persist, proving construction stands alone."""

    async def put_plan(self, write):  # pragma: no cover - never invoked
        raise AssertionError("construction tests must not persist")

    async def load_recovery_view(self, **kwargs):  # pragma: no cover
        raise AssertionError("construction tests must not read")


class _KillSwitchSource:
    """A trusted operator switch that names disabled targets."""

    def __init__(self, targets: list[str]) -> None:
        self._targets = targets

    def current_kill_switch_directives(self) -> object:
        return list(self._targets)


class _ExplodingKillSwitchSource:
    """A switch source that cannot be read, which must mean serial."""

    def current_kill_switch_directives(self) -> object:
        raise RuntimeError("switch source is down")


class _AppendObservingStore:
    """Records which event types reached the canonical store, in order."""

    def __init__(self, inner) -> None:
        self._inner = inner
        self.appended_event_types: list[RuntimeApiEventType] = []

    async def append_event(self, event):
        envelope = await self._inner.append_event(event)
        self.appended_event_types.append(envelope.event_type)
        return envelope

    async def list_events_after(self, **kwargs):
        return await self._inner.list_events_after(**kwargs)


class _ReadCountingEventStore:
    """Counts the envelopes the journal asked the store to hand back.

    Envelopes, not calls: the BUG-20 defect issued a *constant* number of reads
    per append and made each one scan the whole run, so only the volume read
    tells the two shapes apart.
    """

    def __init__(self, inner) -> None:
        self._inner = inner
        self.reads = 0
        self.events_read = 0

    async def append_event(self, event):
        return await self._inner.append_event(event)

    async def list_events_after(self, **kwargs):
        events = await self._inner.list_events_after(**kwargs)
        self.reads += 1
        self.events_read += len(events)
        return events


class _RaceBlindEventStore:
    """Hides the durable F6 prefix from a writer's first read only.

    This is what losing a race actually looks like: the writer read the journal
    before the winner's append landed, so its own append is the first moment the
    duplicate can be detected. Appends and every later read go to the real
    store, so the conflict must come from the canonical stable event identity.
    """

    def __init__(self, inner) -> None:
        self._inner = inner
        self.hidden_reads = 0

    async def append_event(self, event):
        return await self._inner.append_event(event)

    async def list_events_after(self, **kwargs):
        events = await self._inner.list_events_after(**kwargs)
        if self.hidden_reads:
            return events
        self.hidden_reads += 1
        return tuple(
            event
            for event in events
            if event.event_type is not RuntimeApiEventType.OPERATION_BATCH_JOURNAL
        )


class _EmptySnapshotStore:
    """A run whose control snapshot was never bound."""

    def __init__(self, inner) -> None:
        self._inner = inner

    async def get(self, **kwargs):
        return None


def _resealed(record: BatchPlanBoundRecord, **overrides: object) -> dict[str, object]:
    """Return a forged record body whose ``record_digest`` is recomputed.

    Resealing defeats the tamper-evidence seal on purpose, so the assertion
    that follows is about the reproducibility rule and nothing else.
    """

    forged = record.model_dump(mode="json")
    forged.update(overrides)
    sealed = {
        key: value
        for key, value in forged.items()
        if key not in {"record_digest", "created_at"}
    }
    forged["record_digest"] = canonical_json_sha256(sealed)
    return forged


def _leaf_strings(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [
            item
            for key, nested in value.items()
            for item in ([key] if isinstance(key, str) else []) + _leaf_strings(nested)
        ]
    if isinstance(value, (list, tuple)):
        return [item for nested in value for item in _leaf_strings(nested)]
    return []


def _is_body_free(value: str) -> bool:
    """Return whether one persisted string can only be an identity or code.

    Bodies are free text. Every string this record may carry is a field name, a
    closed vocabulary member, an opaque identity, a digest, or a timestamp — all
    of which are drawn from a restricted alphabet with no whitespace, no scheme
    separators, and no filesystem paths.
    """

    forbidden = (" ", "\t", "\n", "://", "\\")
    return value != "" and not any(token in value for token in forbidden)
