"""Production F4 domain/journal bridge tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from agent_runtime.capabilities.task_policy import (
    TaskFamily,
    ToolOperationOutcome,
    ToolUseDisposition,
    ToolUseIntent,
)
from agent_runtime.capabilities.task_policy_journal import (
    TaskPolicyAdmissionDisposition,
    TaskPolicyAdmissionRecordedRecord,
    TaskPolicyJournalRecord,
    TaskPolicyOutcomeRecordedRecord,
    TaskPolicyProfileSelectedRecord,
)
from agent_runtime.control_plane.contracts import BudgetEnvelope
from agent_runtime.control_plane.feature_modes import FeatureMode
from runtime_worker.run_control import VerifiedTaskPolicySignals
from runtime_worker.task_policy_runtime import (
    DefaultTaskPolicyRuntimeFactory,
    DeploymentTaskPolicyBundles,
    TaskPolicyFingerprinter,
    TaskPolicyRuntimeError,
)


def _signals(*, revision: str = "tool-r1") -> VerifiedTaskPolicySignals:
    envelope = BudgetEnvelope.create(
        budget_envelope_id="budget-r1",
        revision="budget-r1",
    )
    return VerifiedTaskPolicySignals(
        run_id="run-f4",
        conversation_id="conversation-f4",
        org_id="org-f4",
        user_id="user-f4",
        snapshot_id="snapshot-f4",
        task_policy_selection_ref="task-policy://unknown.general/tool-r1",
        task_policy_revision=revision,
        budget_envelope_ref=envelope.revision_ref,
        subject_fingerprint="a" * 64,
    )


class _Journal:
    def __init__(self) -> None:
        self.records: list[TaskPolicyJournalRecord] = []

    async def load(self) -> tuple[TaskPolicyJournalRecord, ...]:
        return tuple(self.records)

    async def append(self, record: object) -> TaskPolicyJournalRecord:
        assert isinstance(
            record,
            TaskPolicyProfileSelectedRecord
            | TaskPolicyAdmissionRecordedRecord
            | TaskPolicyOutcomeRecordedRecord,
        ) or hasattr(record, "record_digest")
        existing = next(
            (
                item
                for item in self.records
                if item.record_id == record.record_id  # type: ignore[union-attr]
            ),
            None,
        )
        if existing is not None:
            return existing
        self.records.append(record)  # type: ignore[arg-type]
        return record  # type: ignore[return-value]


async def _binding(
    journal: _Journal,
    *,
    mode: FeatureMode = FeatureMode.ENFORCE,
    envelope: BudgetEnvelope | None = None,
):
    factory = DefaultTaskPolicyRuntimeFactory(
        fingerprinter=TaskPolicyFingerprinter(root_key=b"k" * 32)
    )
    signals = _signals()
    if envelope is not None:
        signals = signals.model_copy(
            update={"budget_envelope_ref": envelope.revision_ref}
        )
    return await factory.prepare(
        signals=signals,
        mode=mode,
        budget_envelope=envelope,
        load_records=journal.load,
        append_record=journal.append,
    )


def _intent(binding, operation_id: str, arguments: dict[str, object]) -> ToolUseIntent:
    return ToolUseIntent(
        operation_id=operation_id,
        capability_id="connector.search",
        canonical_request_fingerprint=binding.fingerprinter.for_request(
            capability_id="connector.search",
            arguments=arguments,
        ),
    )


def _success(binding, intent: ToolUseIntent, value: str) -> ToolOperationOutcome:
    fingerprint = binding.fingerprinter.for_result(
        capability_id=intent.capability_id,
        result_metadata={"value": value},
    )
    return ToolOperationOutcome(
        operation_id=intent.operation_id,
        capability_id=intent.capability_id,
        succeeded=True,
        result_fingerprint=fingerprint,
        evidence_fingerprint=fingerprint,
        source_fingerprints=(fingerprint,),
    )


async def test_first_run_initializes_selection_plan_budget_and_replays() -> None:
    journal = _Journal()
    first = await _binding(journal)
    assert len(journal.records) == 3
    assert first.selection.task_family is TaskFamily.UNKNOWN
    assert first.progress().tool_calls_used == 0

    intent = _intent(first, "operation-1", {"query": "record"})
    assert (
        await first.controller.before_operation(intent)
    ).disposition is ToolUseDisposition.CONTINUE
    await first.controller.after_operation(_success(first, intent, "one"))

    resumed = await _binding(journal)
    assert resumed.progress().tool_calls_used == 1
    assert resumed.progress().completed_steps == 1
    assert len(journal.records) >= 8


async def test_partial_initialization_resumes_from_durable_prefix() -> None:
    journal = _Journal()
    await _binding(journal)
    journal.records = journal.records[:1]

    resumed = await _binding(journal)

    assert resumed.selection.task_family is TaskFamily.UNKNOWN
    assert len(journal.records) == 3


async def test_exact_duplicate_blocks_but_changed_cursor_is_admitted() -> None:
    journal = _Journal()
    binding = await _binding(journal)
    first = _intent(binding, "operation-1", {"query": "record", "cursor": "1"})
    await binding.controller.before_operation(first)
    await binding.controller.after_operation(_success(binding, first, "page-1"))

    duplicate = _intent(binding, "operation-2", {"query": "record", "cursor": "1"})
    duplicate_feedback = await binding.controller.before_operation(duplicate)
    assert duplicate_feedback.disposition is ToolUseDisposition.STOP
    assert duplicate_feedback.reason_code == "exact_duplicate"

    next_page = _intent(binding, "operation-3", {"query": "record", "cursor": "2"})
    assert (
        await binding.controller.before_operation(next_page)
    ).disposition is ToolUseDisposition.CONTINUE


async def test_shadow_persists_block_decision_but_accounts_dispatched_call() -> None:
    journal = _Journal()
    binding = await _binding(journal, mode=FeatureMode.SHADOW)
    first = _intent(binding, "operation-1", {"query": "record"})
    await binding.controller.before_operation(first)
    await binding.controller.after_operation(_success(binding, first, "same"))
    duplicate = _intent(binding, "operation-2", {"query": "record"})
    feedback = await binding.controller.before_operation(duplicate)
    assert feedback.disposition is ToolUseDisposition.STOP
    admission = next(
        item
        for item in journal.records
        if isinstance(item, TaskPolicyAdmissionRecordedRecord)
        and item.operation_id == duplicate.operation_id
    )
    assert admission.disposition is TaskPolicyAdmissionDisposition.SHADOW_ADMITTED
    await binding.controller.after_operation(_success(binding, duplicate, "same"))
    assert binding.progress().tool_calls_used == 2


async def test_retryable_failure_can_repeat_but_nonretryable_loop_stops() -> None:
    journal = _Journal()
    binding = await _binding(journal)
    first = _intent(binding, "operation-1", {"query": "record"})
    await binding.controller.before_operation(first)
    error_fp = binding.fingerprinter.for_error(
        capability_id=first.capability_id,
        request_fingerprint=first.canonical_request_fingerprint,
        error_class="Timeout",
        retryable=True,
    )
    await binding.controller.after_operation(
        ToolOperationOutcome(
            operation_id=first.operation_id,
            capability_id=first.capability_id,
            succeeded=False,
            error_class="Timeout",
            retryable=True,
            error_fingerprint=error_fp,
        )
    )
    retry = _intent(binding, "operation-2", {"query": "record"})
    assert (
        await binding.controller.before_operation(retry)
    ).disposition is ToolUseDisposition.CONTINUE

    nonretryable_fp = binding.fingerprinter.for_error(
        capability_id=retry.capability_id,
        request_fingerprint=retry.canonical_request_fingerprint,
        error_class="InvalidRequest",
        retryable=False,
    )
    await binding.controller.after_operation(
        ToolOperationOutcome(
            operation_id=retry.operation_id,
            capability_id=retry.capability_id,
            succeeded=False,
            error_class="InvalidRequest",
            error_fingerprint=nonretryable_fp,
        )
    )
    third = _intent(binding, "operation-3", {"query": "record"})
    feedback = await binding.controller.before_operation(third)
    assert feedback.disposition is ToolUseDisposition.STOP
    assert feedback.reason_code == "same_error_without_changed_input"


async def test_budget_and_deadline_exhaustion_are_deterministic() -> None:
    journal = _Journal()
    envelope = BudgetEnvelope.create(
        budget_envelope_id="budget-r1",
        revision="budget-r1",
        max_tool_calls=1,
        deadline_at=datetime.now(timezone.utc) + timedelta(minutes=5),
    )
    binding = await _binding(journal, envelope=envelope)
    first = _intent(binding, "operation-1", {"query": "one"})
    await binding.controller.before_operation(first)
    second = _intent(binding, "operation-2", {"query": "two"})
    feedback = await binding.controller.before_operation(second)
    assert feedback.disposition is ToolUseDisposition.STOP
    assert feedback.reason_code == "profile_total_tool_call_limit"


async def test_budget_envelope_mismatch_fails_before_journal_initialization() -> None:
    journal = _Journal()
    envelope = BudgetEnvelope.create(
        budget_envelope_id="other-budget",
        revision="other-budget",
        max_tool_calls=1,
    )

    with pytest.raises(TaskPolicyRuntimeError, match="does not match"):
        await DefaultTaskPolicyRuntimeFactory(
            fingerprinter=TaskPolicyFingerprinter(root_key=b"k" * 32)
        ).prepare(
            signals=_signals(),
            mode=FeatureMode.ENFORCE,
            budget_envelope=envelope,
            load_records=journal.load,
            append_record=journal.append,
        )

    assert journal.records == []


async def test_corrupt_or_profile_mismatched_replay_fails_closed() -> None:
    journal = _Journal()
    await _binding(journal)
    selected = next(
        item
        for item in journal.records
        if isinstance(item, TaskPolicyProfileSelectedRecord)
    )
    journal.records[0] = selected.model_copy(
        update={"profile_revision": "different-revision"}
    )
    with pytest.raises(TaskPolicyRuntimeError, match="corrupt durable record"):
        await _binding(journal)


def test_bundle_always_contains_conservative_unknown() -> None:
    bundle = DeploymentTaskPolicyBundles.for_revision("r1")
    unknown = next(
        profile
        for profile in bundle.profiles
        if profile.task_family is TaskFamily.UNKNOWN
    )
    assert unknown.enforce_exact_duplicates is True
    assert unknown.total_tool_call_limit == 6


async def test_factory_uses_conservative_unknown_without_verified_family_signal() -> (
    None
):
    binding = await _binding(_Journal())

    assert binding.selection.task_family is TaskFamily.UNKNOWN
    assert binding.selection.selection_reason == "conservative_default"


def test_production_fingerprint_secret_fails_closed() -> None:
    with pytest.raises(TaskPolicyRuntimeError, match="unavailable in production"):
        TaskPolicyFingerprinter.from_environment({"RUNTIME_ENVIRONMENT": "production"})
