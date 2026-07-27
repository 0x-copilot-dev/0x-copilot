"""Worker-owned composition for consented terminal evaluation projection."""

from __future__ import annotations

from dataclasses import dataclass

from agent_runtime.api.ports import EventStorePort
from agent_runtime.harness_quality.evaluation_contracts import (
    EvaluationScope,
    ProjectionPolicy,
)
from agent_runtime.harness_quality.ports import EvaluationRepositoryPort
from agent_runtime.harness_quality.projection import (
    EvaluationProjectionJobRunner,
    ProjectionJobLimits,
    TerminalEvaluationProjectionScheduler,
)
from agent_runtime.settings import RuntimeEnvironment, RuntimeSettings


@dataclass(frozen=True)
class EvaluationProjectionComposition:
    observer: TerminalEvaluationProjectionScheduler
    runner: EvaluationProjectionJobRunner


def build_evaluation_projection(
    *,
    settings: RuntimeSettings,
    repository: EvaluationRepositoryPort | None,
    event_store: EventStorePort,
    worker_id: str,
) -> EvaluationProjectionComposition | None:
    """Build both sides of the durable projection queue, or a clean dark state."""

    configured = settings.evaluation
    is_development = settings.environment is not RuntimeEnvironment.PRODUCTION
    policy = ProjectionPolicy(
        revision=configured.policy_revision,
        enabled=configured.projection_enabled,
        user_consented=configured.user_consented,
        allow_development_runs=configured.allow_development_runs,
    )
    if not policy.permits(is_development_run=is_development):
        return None
    if repository is None:
        raise RuntimeError(
            "evaluation projection is permitted but no durable repository is composed"
        )
    scope = EvaluationScope(
        profile_id=configured.profile_id,
        project_id=configured.project_id,
    )
    limits = ProjectionJobLimits(
        max_events_per_run=configured.max_events_per_run,
        max_attempts=configured.max_projection_attempts,
        lease_seconds=configured.projection_lease_seconds,
        claim_batch_size=configured.projection_claim_batch,
    )
    return EvaluationProjectionComposition(
        observer=TerminalEvaluationProjectionScheduler(
            repository=repository,
            scope=scope,
            policy=policy,
            is_development_run=is_development,
            limits=limits,
        ),
        runner=EvaluationProjectionJobRunner(
            repository=repository,
            event_store=event_store,
            scope=scope,
            worker_id=worker_id,
            redaction_policy_revision=configured.redaction_revision,
            limits=limits,
        ),
    )


__all__ = (
    "EvaluationProjectionComposition",
    "build_evaluation_projection",
)
