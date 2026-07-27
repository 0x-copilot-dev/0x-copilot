from __future__ import annotations

from datetime import datetime, timedelta, timezone

from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.api.run_control_store import EventJournalRunControlStore
from agent_runtime.api.run_termination import (
    RunTerminationCoordinator,
    TerminationReason,
)
from agent_runtime.execution.contracts import AgentRuntimeContext
from agent_runtime.harness_quality.evaluation_contracts import (
    EvaluationProjectionJob,
    EvaluationScope,
    ProjectionJobStatus,
    ProjectionPolicy,
)
from agent_runtime.harness_quality.projection import (
    EvaluationProjectionJobRunner,
    ProjectionJobLimits,
    TerminalEvaluationProjectionScheduler,
)
from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from runtime_adapters.in_memory.evaluation_repository import (
    InMemoryEvaluationRepository,
)
from runtime_api.schemas import AgentRunStatus, RunRecord
from runtime_worker.run_control import (
    RunControlPlaneBuilder,
    StableUserProfileHmac,
)


_NOW = datetime(2026, 7, 27, 12, tzinfo=timezone.utc)
_SCOPE = EvaluationScope(profile_id="local_profile")


def _run() -> RunRecord:
    return RunRecord(
        run_id="run_projection",
        org_id="runtime_org",
        user_id="local_user",
        conversation_id="conversation_projection",
        user_message_id="message_projection",
        model_provider="openai",
        model_name="gpt-test",
        trace_id="trace_projection",
        runtime_context=AgentRuntimeContext(
            org_id="runtime_org",
            user_id="local_user",
            roles=["employee"],
            model_profile={
                "provider": "openai",
                "model_name": "gpt-test",
                "max_input_tokens": 10_000,
                "timeout_seconds": 30,
                "temperature": 0,
                "supports_streaming": False,
            },
            run_id="run_projection",
            trace_id="trace_projection",
        ),
        created_at=_NOW,
    )


async def _terminal_run(
    *,
    policy: ProjectionPolicy,
    limits: ProjectionJobLimits = ProjectionJobLimits(),
) -> tuple[
    InMemoryRuntimeApiStore,
    InMemoryEvaluationRepository,
    EvaluationScope,
]:
    runtime = InMemoryRuntimeApiStore()
    run = _run()
    runtime.runs[run.run_id] = run
    repository = InMemoryEvaluationRepository()
    control_builder = RunControlPlaneBuilder(
        store=EventJournalRunControlStore(runtime),
        deployment_profile="single_user_desktop",
        subject_hmac=StableUserProfileHmac(b"projection-test-key-v1"),
        cutover_at=_NOW - timedelta(days=1),
    )
    await control_builder.ensure_snapshot(run=run, trace_id=run.trace_id)
    scheduler = TerminalEvaluationProjectionScheduler(
        repository=repository,
        scope=_SCOPE,
        policy=policy,
        is_development_run=True,
        limits=limits,
        clock=lambda: _NOW,
    )
    producer = RuntimeEventProducer(persistence=runtime, event_store=runtime)
    await RunTerminationCoordinator(
        event_producer=producer,
        terminal_observer=scheduler,
    ).terminate(
        run=run,
        terminal_status=AgentRunStatus.COMPLETED,
        reason=TerminationReason.NORMAL_COMPLETION,
    )
    return runtime, repository, _SCOPE


async def test_terminal_projection_is_consent_gated_and_idempotent() -> None:
    runtime, repository, scope = await _terminal_run(
        policy=ProjectionPolicy(
            revision="projection-policy-v1",
            enabled=True,
            user_consented=True,
            allow_development_runs=True,
        )
    )

    jobs = await repository.list_projection_jobs(scope)
    assert len(jobs) == 1
    assert jobs[0].source_org_id == "runtime_org"
    assert jobs[0].terminal_sequence_no == 2
    assert jobs[0].variant_id is None

    terminal_event = runtime.events_by_run["run_projection"][-1]
    scheduler = TerminalEvaluationProjectionScheduler(
        repository=repository,
        scope=scope,
        policy=ProjectionPolicy(
            revision="projection-policy-v1",
            enabled=True,
            user_consented=True,
            allow_development_runs=True,
        ),
        is_development_run=True,
        clock=lambda: _NOW,
    )
    await scheduler.observe_terminal_run(
        run=_run(),
        terminal_status=AgentRunStatus.COMPLETED,
        reason=TerminationReason.NORMAL_COMPLETION,
        terminal_event=terminal_event,
    )
    assert len(await repository.list_projection_jobs(scope)) == 1


async def test_projection_is_not_scheduled_without_consent() -> None:
    _, repository, scope = await _terminal_run(
        policy=ProjectionPolicy(
            revision="projection-policy-v1",
            enabled=True,
            user_consented=False,
            allow_development_runs=True,
        )
    )

    assert await repository.list_projection_jobs(scope) == ()


async def test_projection_runner_persists_content_free_trajectory_and_job_cursor() -> (
    None
):
    runtime, repository, scope = await _terminal_run(
        policy=ProjectionPolicy(
            revision="projection-policy-v1",
            enabled=True,
            user_consented=True,
            allow_development_runs=True,
        )
    )
    runner = EvaluationProjectionJobRunner(
        repository=repository,
        event_store=runtime,
        scope=scope,
        worker_id="projection-worker",
        redaction_policy_revision="redaction-v1",
        clock=lambda: _NOW + timedelta(seconds=1),
    )

    assert await runner.run_once() is True
    job = (await repository.list_projection_jobs(scope))[0]
    assert job.status is ProjectionJobStatus.SUCCEEDED
    assert job.variant_id is not None
    assert job.next_sequence_no == job.terminal_sequence_no + 1
    assert job.trajectory_id is not None
    trajectory = await repository.get_trajectory_manifest(
        scope,
        trajectory_id=job.trajectory_id,
    )
    assert trajectory is not None
    assert trajectory.run_id == "run_projection"
    assert tuple(step.sequence_no for step in trajectory.ordered_steps) == (1, 2)
    assert trajectory.harness_revisions["harness_variant_ref"].startswith("harness://")


async def test_expired_projection_lease_is_resumed_with_a_bounded_attempt() -> None:
    runtime, repository, scope = await _terminal_run(
        policy=ProjectionPolicy(
            revision="projection-policy-v1",
            enabled=True,
            user_consented=True,
            allow_development_runs=True,
        )
    )
    pending = (await repository.list_projection_jobs(scope))[0]
    running_values = pending.model_dump(mode="python", exclude={"job_digest"})
    running_values.update(
        {
            "status": ProjectionJobStatus.RUNNING,
            "attempt_count": 1,
            "lease_owner_digest": "a" * 64,
            "lease_expires_at": _NOW + timedelta(seconds=1),
            "version": 1,
            "updated_at": _NOW + timedelta(seconds=1),
        }
    )
    running = EvaluationProjectionJob(
        **running_values,
        job_digest=EvaluationProjectionJob.digest_for(**running_values),
    )
    await repository.compare_and_set_projection_job(
        scope,
        expected_version=0,
        replacement=running,
    )
    runner = EvaluationProjectionJobRunner(
        repository=repository,
        event_store=runtime,
        scope=scope,
        worker_id="recovery-worker",
        redaction_policy_revision="redaction-v1",
        limits=ProjectionJobLimits(max_attempts=2),
        clock=lambda: _NOW + timedelta(seconds=2),
    )

    assert await runner.run_once() is True
    resumed = (await repository.list_projection_jobs(scope))[0]
    assert resumed.status is ProjectionJobStatus.SUCCEEDED
    assert resumed.attempt_count == 2


async def test_scheduler_refuses_runs_above_the_event_projection_bound() -> None:
    _, repository, scope = await _terminal_run(
        policy=ProjectionPolicy(
            revision="projection-policy-v1",
            enabled=True,
            user_consented=True,
            allow_development_runs=True,
        ),
        limits=ProjectionJobLimits(max_events_per_run=1),
    )

    jobs = await repository.list_projection_jobs(scope)
    assert len(jobs) == 1
    assert jobs[0].status is ProjectionJobStatus.SKIPPED
    assert jobs[0].failure_reason_code == "event_limit_exceeded"
