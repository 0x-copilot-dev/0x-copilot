"""Production composition for signed release assignment and local controls."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import os

from agent_runtime.api.ports import EventStorePort
from agent_runtime.api.task_policy_store import EventJournalTaskPolicyStore
from agent_runtime.capabilities.task_policy_journal import (
    TaskPolicyJournalWrite,
    validate_task_policy_journal_record,
)
from agent_runtime.control_plane.ports import RunControlSnapshotStorePort
from agent_runtime.deployment.profile import DeploymentProfileLoader
from agent_runtime.harness_quality.evaluation_contracts import EvaluationScope
from agent_runtime.harness_quality.ports import EvaluationRepositoryPort
from agent_runtime.release.control import LocalReleaseControlService
from agent_runtime.release.local_control import (
    LocalReleaseControlPolicy,
    ReleaseControlProfile,
)
from agent_runtime.release.manifest import ReleaseManifestVerifier
from agent_runtime.settings import RuntimeEnvironment, RuntimeSettings
from runtime_worker.run_control import (
    BudgetEnvelopeLoader,
    RunControlPlaneBuilder,
    StableUserProfileHmac,
    TaskPolicyRecordAppender,
    TaskPolicyRecordLoader,
    TaskPolicyRuntimeFactoryPort,
)
from runtime_worker.run_control_release_bootstrap import (
    NoActiveRunControlRelease,
    bootstrap_run_control_release,
)
from runtime_worker.run_control_release_configuration import (
    RunControlReleaseDeploymentConfiguration,
    load_run_control_release_configuration,
)
from runtime_worker.task_policy_runtime import (
    DefaultTaskPolicyRuntimeFactory,
    TaskPolicyFingerprinter,
)


class RunControlReleaseCompositionError(RuntimeError):
    """Startup cannot safely bind the configured active release."""


async def build_run_control_plane_builder(
    *,
    settings: RuntimeSettings,
    repository: EvaluationRepositoryPort | None,
    store: RunControlSnapshotStorePort,
    event_store: EventStorePort | None = None,
    environment: Mapping[str, str] | None = None,
    task_policy_runtime_factory: TaskPolicyRuntimeFactoryPort | None = None,
    load_task_policy_records: TaskPolicyRecordLoader | None = None,
    append_task_policy_record: TaskPolicyRecordAppender | None = None,
    load_budget_envelope: BudgetEnvelopeLoader | None = None,
) -> RunControlPlaneBuilder:
    """Return the one Step 1 builder, using a verified active release if present."""

    env = dict(os.environ if environment is None else environment)
    deployment_profile = DeploymentProfileLoader.load(env).name
    subject_hmac = StableUserProfileHmac.from_environment(env)
    (
        task_policy_runtime_factory,
        load_task_policy_records,
        append_task_policy_record,
    ) = _task_policy_composition(
        store=store,
        event_store=event_store,
        environment=env,
        factory=task_policy_runtime_factory,
        load_records=load_task_policy_records,
        append_record=append_task_policy_record,
    )
    path = settings.evaluation.release_config_path
    if repository is None:
        if path is not None:
            raise RunControlReleaseCompositionError(
                "release configuration requires an evaluation repository"
            )
        return _safe_builder(
            store=store,
            deployment_profile=deployment_profile,
            subject_hmac=subject_hmac,
            task_policy_runtime_factory=task_policy_runtime_factory,
            load_task_policy_records=load_task_policy_records,
            append_task_policy_record=append_task_policy_record,
            load_budget_envelope=load_budget_envelope,
        )

    pointer = await repository.get_active_harness_manifest(_scope(settings))
    if path is None:
        if pointer is not None:
            raise RunControlReleaseCompositionError(
                "active harness release has no verification configuration"
            )
        return _safe_builder(
            store=store,
            deployment_profile=deployment_profile,
            subject_hmac=subject_hmac,
            task_policy_runtime_factory=task_policy_runtime_factory,
            load_task_policy_records=load_task_policy_records,
            append_task_policy_record=append_task_policy_record,
            load_budget_envelope=load_budget_envelope,
        )

    configuration = load_run_control_release_configuration(path)
    _validate_runtime_profile(settings=settings, configuration=configuration)
    result = await bootstrap_run_control_release(
        repository=repository,
        scope=_scope(settings),
        verification_keys=configuration.verification_key_map(),
        catalog=configuration.assignment_catalog(),
        store=store,
        deployment_profile=deployment_profile,
        release_profile=configuration.release_profile,
        subject_hmac=subject_hmac,
        development_override=configuration.development_override,
    )
    builder = (
        result.builder if isinstance(result, NoActiveRunControlRelease) else result
    )
    _install_task_policy_runtime(
        builder=builder,
        factory=task_policy_runtime_factory,
        load_records=load_task_policy_records,
        append_record=append_task_policy_record,
        load_budget_envelope=load_budget_envelope,
    )
    return builder


def build_local_release_control_service(
    *,
    settings: RuntimeSettings,
    repository: EvaluationRepositoryPort | None,
    environment: Mapping[str, str] | None = None,
) -> LocalReleaseControlService | None:
    """Compose a verifier/mutator only for explicit non-production loopback use."""

    if not settings.evaluation.local_release_control_enabled:
        return None
    if repository is None:
        raise RunControlReleaseCompositionError(
            "local release control requires an evaluation repository"
        )
    path = settings.evaluation.release_config_path
    if path is None:  # guarded by RuntimeSettings; retained as defense-in-depth
        raise RunControlReleaseCompositionError(
            "local release control requires release configuration"
        )
    configuration = load_run_control_release_configuration(path)
    _validate_runtime_profile(settings=settings, configuration=configuration)
    if configuration.release_profile is ReleaseControlProfile.PRODUCTION:
        raise RunControlReleaseCompositionError(
            "local release control requires development or dogfood profile"
        )
    env = os.environ if environment is None else environment
    if not env.get("ENTERPRISE_SERVICE_TOKEN", "").strip():
        raise RunControlReleaseCompositionError(
            "local release control requires ENTERPRISE_SERVICE_TOKEN"
        )
    return LocalReleaseControlService(
        repository=repository,
        verifier=ReleaseManifestVerifier(
            verification_keys=configuration.verification_key_map()
        ),
        policy=LocalReleaseControlPolicy(
            profile=configuration.release_profile,
            explicitly_enabled=True,
            bind_host="127.0.0.1",
        ),
        scope=_scope(settings),
        allowed_variant_digests={
            variant_ref: assignment.digest
            for variant_ref, assignment in configuration.assignment_catalog().items()
        },
    )


def _validate_runtime_profile(
    *,
    settings: RuntimeSettings,
    configuration: RunControlReleaseDeploymentConfiguration,
) -> None:
    if (
        settings.environment is RuntimeEnvironment.PRODUCTION
        and configuration.release_profile is not ReleaseControlProfile.PRODUCTION
    ):
        raise RunControlReleaseCompositionError(
            "production runtime requires a production release profile"
        )


def _scope(settings: RuntimeSettings) -> EvaluationScope:
    return EvaluationScope(
        profile_id=settings.evaluation.profile_id,
        project_id=settings.evaluation.project_id,
    )


def _safe_builder(
    *,
    store: RunControlSnapshotStorePort,
    deployment_profile: str,
    subject_hmac: StableUserProfileHmac,
    task_policy_runtime_factory: TaskPolicyRuntimeFactoryPort | None,
    load_task_policy_records: TaskPolicyRecordLoader | None,
    append_task_policy_record: TaskPolicyRecordAppender | None,
    load_budget_envelope: BudgetEnvelopeLoader | None,
) -> RunControlPlaneBuilder:
    builder = RunControlPlaneBuilder(
        store=store,
        deployment_profile=deployment_profile,
        subject_hmac=subject_hmac,
    )
    _install_task_policy_runtime(
        builder=builder,
        factory=task_policy_runtime_factory,
        load_records=load_task_policy_records,
        append_record=append_task_policy_record,
        load_budget_envelope=load_budget_envelope,
    )
    return builder


def _install_task_policy_runtime(
    *,
    builder: RunControlPlaneBuilder,
    factory: TaskPolicyRuntimeFactoryPort | None,
    load_records: TaskPolicyRecordLoader | None,
    append_record: TaskPolicyRecordAppender | None,
    load_budget_envelope: BudgetEnvelopeLoader | None,
) -> None:
    supplied = (
        factory is not None,
        load_records is not None,
        append_record is not None,
    )
    if not any(supplied):
        return
    if not all(supplied):
        raise RunControlReleaseCompositionError(
            "task-policy runtime requires factory and durable journal callbacks"
        )
    assert factory is not None
    assert load_records is not None
    assert append_record is not None
    builder.install_task_policy_runtime(
        factory=factory,
        load_records=load_records,
        append_record=append_record,
        load_budget_envelope=load_budget_envelope,
    )


def _task_policy_composition(
    *,
    store: RunControlSnapshotStorePort,
    event_store: EventStorePort | None,
    environment: Mapping[str, str],
    factory: TaskPolicyRuntimeFactoryPort | None,
    load_records: TaskPolicyRecordLoader | None,
    append_record: TaskPolicyRecordAppender | None,
) -> tuple[
    TaskPolicyRuntimeFactoryPort | None,
    TaskPolicyRecordLoader | None,
    TaskPolicyRecordAppender | None,
]:
    supplied = (
        factory is not None,
        load_records is not None,
        append_record is not None,
    )
    if any(supplied):
        if not all(supplied):
            raise RunControlReleaseCompositionError(
                "task-policy runtime requires factory and durable journal callbacks"
            )
        return factory, load_records, append_record
    if event_store is None:
        return None, None, None
    journal = EventJournalTaskPolicyStore(events=event_store, snapshots=store)
    runtime_factory = DefaultTaskPolicyRuntimeFactory(
        fingerprinter=TaskPolicyFingerprinter.from_environment(environment)
    )

    async def load(
        org_id: str,
        run_id: str,
        subject_fingerprint: str,
    ) -> Sequence[object]:
        return await journal.list_for_run(
            org_id=org_id,
            run_id=run_id,
            subject_fingerprint=subject_fingerprint,
        )

    async def append(
        org_id: str,
        run_id: str,
        subject_fingerprint: str,
        record: object,
    ) -> object:
        parsed = validate_task_policy_journal_record(record)
        if parsed.run_id != run_id:
            raise RunControlReleaseCompositionError(
                "task-policy callback run identity mismatch"
            )
        return await journal.append(
            TaskPolicyJournalWrite(
                org_id=org_id,
                trace_id=f"f4-{parsed.record_id}",
                subject_fingerprint=subject_fingerprint,
                record=parsed,
            )
        )

    return runtime_factory, load, append


def install_default_task_policy_runtime(
    *,
    builder: RunControlPlaneBuilder,
    store: RunControlSnapshotStorePort,
    event_store: EventStorePort,
    environment: Mapping[str, str] | None = None,
) -> RunControlPlaneBuilder:
    """Attach the canonical F4 journal composition to a direct worker builder."""

    factory, load, append = _task_policy_composition(
        store=store,
        event_store=event_store,
        environment=dict(os.environ if environment is None else environment),
        factory=None,
        load_records=None,
        append_record=None,
    )
    assert factory is not None and load is not None and append is not None
    builder.install_task_policy_runtime(
        factory=factory,
        load_records=load,
        append_record=append,
    )
    return builder


__all__ = (
    "RunControlReleaseCompositionError",
    "build_local_release_control_service",
    "build_run_control_plane_builder",
    "install_default_task_policy_runtime",
)
