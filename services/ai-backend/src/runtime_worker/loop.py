"""Async worker loop for durable runtime commands."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta, timezone
import logging
from typing import Protocol
from uuid import uuid4

from opentelemetry import trace as otel_trace

from agent_runtime.api.ports import (
    EventStorePort,
    PersistencePort,
    RuntimeQueuePort,
)
from agent_runtime.capabilities.operations.context import (
    OperationGatewayStartupGuard,
)
from agent_runtime.capabilities.operations.conformance import OperationConformanceGate
from agent_runtime.capabilities.desktop.workspace_attestation import (
    DesktopWorkspaceAttestationRegistry,
)
from agent_runtime.execution.contracts import RuntimeErrorCode
from agent_runtime.capabilities.operations.contracts import OperationGatewayMode
from agent_runtime.execution.errors import AgentRuntimeError
from agent_runtime.effects.claims import EffectClaimStore
from agent_runtime.observability.http_logging import LoggingConfigurator
from agent_runtime.observability.queue_propagation import QueueTracePropagator
from agent_runtime.persistence.constants import Values as PersistenceValues
from agent_runtime.persistence.records import RuntimeWorkerClaim, RuntimeWorkerResult
from agent_runtime.settings import RuntimeSettings
from agent_runtime.rollout import (
    RolloutStartupReadiness,
    RolloutStartupValidator,
)
from agent_runtime.rollout_admission import E2RolloutAdmission
from runtime_api.schemas import (
    RuntimeApprovalResolvedCommand,
    RuntimeArtifactEventCommand,
    RuntimeCancelCommand,
    RuntimeEffectCommitCommand,
    RuntimeEffectReconcileCommand,
    RuntimeRunCommand,
    RuntimeStageCommitCommand,
)
from runtime_worker.handlers.approval import RuntimeApprovalHandler
from runtime_worker.handlers.artifact_event import RuntimeArtifactEventHandler
from runtime_worker.handlers.cancel import RuntimeCancelHandler
from runtime_worker.handlers.stage_commit import RuntimeStageCommitHandler
from runtime_worker.handlers.effect_commit import RuntimeEffectCommitHandler
from runtime_worker.handlers.effect_reconcile import RuntimeEffectReconcileHandler
from runtime_worker.e2_rollout_admission import (
    E2RolloutEffectCommitHandler,
    EventStoreEffectStageExecutorResolver,
)
from runtime_worker.audit import WorkerAuditEmitter
from runtime_worker.mcp_operation_storage import RuntimeMcpEffectCoordinatorFactory
from runtime_worker.dependencies import DefaultRuntimeDependenciesFactory
from agent_runtime.persistence.ports import (
    CitationStorePort,
    ConversationToolOrdinalStorePort,
    DraftStorePort,
)
from runtime_adapters.in_memory.citation_store import InMemoryCitationStore
from runtime_adapters.in_memory.conversation_tool_ordinal_store import (
    InMemoryConversationToolOrdinalStore,
)
from runtime_worker.handlers.run import RuntimeRunHandler
from agent_runtime.surfaces_v2.ledger_models import EffectExecutorKind


_ROLLOUT_LOGGER = LoggingConfigurator.get_logger("runtime_worker.rollout")


class EffectCommitHandlerPort(Protocol):
    """Worker-only injection seam for A5's later effect-commit handler.

    This transport slice deliberately has no default implementation.  Reaching the
    queue cannot construct an executor or perform an external mutation by itself.
    """

    async def handle(self, command: RuntimeEffectCommitCommand) -> None:
        """Consume one validated effect-commit command."""


class EffectReconcileHandlerPort(Protocol):
    """Worker-only injection seam for A5's later reconciliation handler."""

    async def handle(self, command: RuntimeEffectReconcileCommand) -> None:
        """Consume one validated effect-reconciliation command."""


class RuntimeWorker:
    """Claim and process queued runtime commands with bounded concurrency."""

    def __init__(
        self,
        *,
        persistence: PersistencePort,
        event_store: EventStorePort,
        queue: RuntimeQueuePort,
        settings: RuntimeSettings | None = None,
        worker_id: str | None = None,
        lock_seconds: int = 60,
        retry_delay_seconds: float = 1,
        run_handler: RuntimeRunHandler | None = None,
        cancel_handler: RuntimeCancelHandler | None = None,
        approval_handler: RuntimeApprovalHandler | None = None,
        artifact_event_handler: RuntimeArtifactEventHandler | None = None,
        stage_commit_handler: RuntimeStageCommitHandler | None = None,
        effect_commit_handler: EffectCommitHandlerPort | None = None,
        effect_reconcile_handler: EffectReconcileHandlerPort | None = None,
        effect_claim_store: EffectClaimStore | None = None,
        on_event_appended: Callable[[str], None] | None = None,
        draft_store: "DraftStorePort | None" = None,
        conversation_tool_ordinal_store: (
            "ConversationToolOrdinalStorePort | None"
        ) = None,
        citation_store: "CitationStorePort | None" = None,
        mcp_discovery_cache: object | None = None,
        user_policies_resolver: object | None = None,
        artifact_service: object | None = None,
        artifact_blob_store: object | None = None,
        artifact_reference_store: object | None = None,
        workspace_host_sessions: object | None = None,
        workspace_overlay_store: object | None = None,
        workspace_attestation_registry: DesktopWorkspaceAttestationRegistry
        | None = None,
        sandbox_provider_overrides: Mapping[object, object] | None = None,
        capability_env: Mapping[str, str] | None = None,
    ) -> None:
        self.persistence: PersistencePort = persistence
        self.event_store: EventStorePort = event_store
        self.queue: RuntimeQueuePort = queue
        self.settings = settings or RuntimeSettings.load()
        d1_ready = bool(
            self.settings.execution.surfaces_v2
            and callable(getattr(queue, "enqueue_effect_commit", None))
            and callable(getattr(artifact_blob_store, "put_stream", None))
            and callable(getattr(artifact_reference_store, "acquire", None))
            and callable(getattr(artifact_reference_store, "list_edges", None))
        )
        OperationGatewayStartupGuard.validate(
            mode=(
                self.settings.execution.operation_gateway_mode
                if self.settings.execution.surfaces_v2
                else OperationGatewayMode.OFF
            ),
            stage_dependency_ready=d1_ready,
            # D1 queues only body-free commands. The sole executor remains the
            # worker-owned McpEffectExecutor wired by the effect-commit lane;
            # this readiness check prevents model execution from starting until
            # its material dependencies are present too.
            executor_dependency_ready=d1_ready,
            durable_arguments_ready=d1_ready,
        )
        self.worker_id = worker_id or f"runtime-worker-{uuid4().hex[:8]}"
        self.lock_seconds = lock_seconds
        self.retry_delay_seconds = retry_delay_seconds
        # Prefer an injected citation store: the composed RuntimePorts wire the
        # backend-correct DURABLE store (the Postgres pool, or the file store's
        # FileCitationStore). Fall back to the historical resolution when none is
        # injected, so existing direct-construction call sites and tests keep
        # their exact behavior (Postgres persistence satisfies CitationStorePort;
        # everything else gets an in-memory sibling).
        if citation_store is None:
            citation_store = (
                self.persistence
                if isinstance(self.persistence, CitationStorePort)
                else InMemoryCitationStore()
            )
        # Defaults to an in-memory adapter for dev/tests; production injects a Postgres
        # adapter that shares the main connection pool.
        self.conversation_tool_ordinal_store: ConversationToolOrdinalStorePort = (
            conversation_tool_ordinal_store or InMemoryConversationToolOrdinalStore()
        )
        # Process-wide MCP discovery cache (when wired). Forwarded into the
        # default run / approval handler dependencies factories so every
        # ``McpLoader`` built for a run in this process shares one cache.
        self.mcp_discovery_cache = mcp_discovery_cache
        self.artifact_service = artifact_service
        self._sandbox_provider_overrides = sandbox_provider_overrides
        self._capability_env = capability_env
        if workspace_host_sessions is None and (
            callable(getattr(artifact_blob_store, "put_stream", None))
            and callable(getattr(artifact_reference_store, "acquire", None))
        ):
            # Desktop C3 is opt-in and fails closed: a missing/invalid private
            # broker credential leaves this ``None``. The registry contains
            # only opaque host-session references, never a renderer token or a
            # raw C2 commit permit.
            from runtime_worker.workspace_effect_storage import (  # noqa: PLC0415
                desktop_workspace_host_sessions_from_env,
            )

            workspace_host_sessions = desktop_workspace_host_sessions_from_env(
                blobs=artifact_blob_store,  # type: ignore[arg-type]
                references=artifact_reference_store,  # type: ignore[arg-type]
            )
        self.workspace_host_sessions = workspace_host_sessions
        self.workspace_overlay_store = (
            workspace_overlay_store
            if workspace_overlay_store is not None
            else self._default_workspace_overlay_store()
        )
        # The registry is a proof reader only. It has neither filesystem nor
        # broker authority, and absent/malformed bootstrap material stays
        # unattested rather than fabricating a desktop-safe default.
        self.workspace_attestation_registry = (
            workspace_attestation_registry
            or DesktopWorkspaceAttestationRegistry.from_environment()
        )
        self._validate_e2_rollout_startup(
            d1_ready=d1_ready,
            artifact_blob_store=artifact_blob_store,
            artifact_reference_store=artifact_reference_store,
        )
        self.run_handler = run_handler or RuntimeRunHandler(
            persistence=self.persistence,
            event_store=self.event_store,
            settings=self.settings,
            on_event_appended=on_event_appended,
            citation_store=citation_store,
            draft_store=draft_store,
            conversation_tool_ordinal_store=self.conversation_tool_ordinal_store,
            mcp_discovery_cache=mcp_discovery_cache,
            user_policies_resolver=user_policies_resolver,  # type: ignore[arg-type]
            # PRD-D3 — lets the per-run bulk-staging tool enqueue an allow-always
            # auto-apply through the same durable queue the API uses.
            queue=self.queue,
            artifact_service=artifact_service,
            artifact_blob_store=artifact_blob_store,
            artifact_reference_store=artifact_reference_store,
            workspace_host_sessions=workspace_host_sessions,
            workspace_overlay_store=self.workspace_overlay_store,
            sandbox_provider_overrides=sandbox_provider_overrides,
            capability_env=capability_env,
        )
        self.cancel_handler = cancel_handler or RuntimeCancelHandler(
            persistence=self.persistence,
            event_store=self.event_store,
        )
        self.approval_handler = approval_handler or RuntimeApprovalHandler(
            persistence=self.persistence,
            event_store=self.event_store,
            settings=self.settings,
            on_event_appended=on_event_appended,
            draft_store=draft_store,
            conversation_tool_ordinal_store=self.conversation_tool_ordinal_store,
            mcp_discovery_cache=mcp_discovery_cache,
            user_policies_resolver=user_policies_resolver,  # type: ignore[arg-type]
            artifact_service=artifact_service,
        )
        self.artifact_event_handler = (
            artifact_event_handler
            or RuntimeArtifactEventHandler(
                persistence=self.persistence,
                event_store=self.event_store,
            )
        )
        # PRD-D2 — the SOLE producer of ``write.applied``. Default construction
        # mirrors ``approval_handler``: it builds its own durable claim ledger
        # (off the store backend) + per-run MCP connector. Tests inject a handler
        # with a fake engine for determinism.
        self.stage_commit_handler = stage_commit_handler or RuntimeStageCommitHandler(
            persistence=self.persistence,
            event_store=self.event_store,
            draft_store=draft_store,
            settings=self.settings,
            on_event_appended=on_event_appended,
            mcp_discovery_cache=mcp_discovery_cache,
        )
        self.effect_commit_handler = effect_commit_handler
        self.effect_reconcile_handler = effect_reconcile_handler
        # D12's planner and optional execution bridge receive this exact claim
        # source from the worker entrypoint.  That matters for the in-memory
        # adapter; file/Postgres would read the same durable data either way.
        self.effect_claim_store: EffectClaimStore | None = effect_claim_store
        self.repair_reconcile_supported_executors: frozenset[EffectExecutorKind] = (
            frozenset()
        )
        if d1_ready and self.effect_commit_handler is None:
            claims = self.effect_claim_store or self._effect_claim_store()
            self.effect_claim_store = claims
            browser_bridge = self._browser_effect_bridge()
            factory = RuntimeMcpEffectCoordinatorFactory(
                event_producer=self.run_handler.event_producer,
                claims=claims,
                blobs=artifact_blob_store,  # type: ignore[arg-type]
                references=artifact_reference_store,  # type: ignore[arg-type]
                dependencies_factory=DefaultRuntimeDependenciesFactory(
                    self.settings,
                    mcp_discovery_cache=mcp_discovery_cache,  # type: ignore[arg-type]
                ),
                timeout_seconds=self.settings.default_timeout_seconds,
                workspace_sessions=workspace_host_sessions,  # type: ignore[arg-type]
                workspace_overlay_store=self.workspace_overlay_store,  # type: ignore[arg-type]
                browser_bridge=browser_bridge,
                artifact_service=artifact_service,  # type: ignore[arg-type]
            )
            self.effect_commit_handler = RuntimeEffectCommitHandler(
                persistence=self.persistence,
                coordinator_factory=factory,
            )
            supported: set[EffectExecutorKind] = set()
            if (
                workspace_host_sessions is not None
                and self.workspace_overlay_store is not None
            ):
                supported.add(EffectExecutorKind.WORKSPACE)
            if browser_bridge is not None:
                supported.add(EffectExecutorKind.BROWSER)
            self.repair_reconcile_supported_executors = frozenset(supported)
            self.effect_reconcile_handler = RuntimeEffectReconcileHandler(
                persistence=self.persistence,
                claims=claims,
                coordinator_factory=factory,
                audit_emitter=WorkerAuditEmitter(persistence=self.persistence),
            )
        # The only worker dispatch reference is wrapped even when a test or
        # host injects the downstream handler. The wrapper validates the
        # persisted run + stage and applies E2 cohort/kill-switch admission
        # before the A5 claim/prepare/apply protocol is reachable.
        if self.effect_commit_handler is not None:
            self.effect_commit_handler = E2RolloutEffectCommitHandler(
                delegate=self.effect_commit_handler,
                persistence=self.persistence,
                executor_resolver=EventStoreEffectStageExecutorResolver(
                    event_store=self.event_store
                ),
                admission=E2RolloutAdmission(
                    resolution=self.settings.execution.rollout,
                    cohorts=self.settings.execution.rollout_cohorts,
                    kill_switches=self.settings.execution.rollout_kill_switches,
                ),
            )
        self._semaphore = asyncio.Semaphore(self.settings.execution.max_parallel_runs)
        self.logger = logging.getLogger("runtime_worker")
        self._sandbox_recovery_reaper = self._build_sandbox_recovery_reaper()

    def _validate_e2_rollout_startup(
        self,
        *,
        d1_ready: bool,
        artifact_blob_store: object | None,
        artifact_reference_store: object | None,
    ) -> None:
        """Apply E2 D1/D8/D9 startup gates before worker handlers exist.

        This is the only process that owns effect executors, so the API logs
        the resolved configuration while the worker decides whether an explicit
        new ``enforce`` request is admissible.  Workspace commit additionally
        requires a current, Ed25519-verified C2 statement from Electron main;
        the opaque host-session broker alone never counts as that proof.
        Existing C3 compatibility mode retains its established guard.
        """

        resolution = self.settings.execution.rollout
        explicit_producer_enforce = any(
            RolloutStartupValidator.is_producer(capability)
            for capability in resolution.provenance.explicit_enforced
        )
        descriptor_catalog_ready = False
        if explicit_producer_enforce:
            try:
                OperationConformanceGate.validate_current()
            except Exception:
                # The public startup error below intentionally names neither a
                # descriptor's source path nor a provider-specific detail.
                descriptor_catalog_ready = False
            else:
                descriptor_catalog_ready = True

        readiness = RolloutStartupReadiness(
            descriptor_catalog_ready=descriptor_catalog_ready,
            executor_registry_ready=d1_ready,
            artifact_repository_ready=bool(
                self.settings.execution.artifact_effects_v2
                and artifact_blob_store is not None
                and artifact_reference_store is not None
                and self.artifact_service is not None
            ),
            operation_gateway_ready=d1_ready,
            effect_stager_ready=d1_ready,
            effect_commit_ready=d1_ready,
            # D1 intentionally adds no v2.1 presentation/adapter consumer.
            # An explicit enforce request must wait for that consumer rather
            # than being accepted as a no-op or silently widening a v2 flag.
            presentation_v2_1_ready=False,
            workspace_overlay_ready=bool(
                d1_ready and self.workspace_overlay_store is not None
            ),
            workspace_commit_ready=bool(
                d1_ready
                and self.workspace_overlay_store is not None
                and self.workspace_host_sessions is not None
            ),
            # This is cryptographic launch evidence from Electron main, not a
            # broker URL/token or renderer-supplied capability assertion.
            workspace_c2_native_attested=(
                self.workspace_attestation_registry.workspace_commit_attested()
            ),
            mcp_gateway_ready=d1_ready,
            sandbox_adapter_ready=False,
            browser_adapter_ready=False,
        )
        RolloutStartupValidator.validate_startup(
            resolution,
            readiness=readiness,
        )
        _ROLLOUT_LOGGER.info(
            "e2_rollout_resolved",
            metadata=resolution.safe_diagnostics(process="worker"),
        )

    def _default_workspace_overlay_store(self) -> object:
        """Select the C1 overlay adapter that shares the configured runtime store."""

        if (
            self.settings.store.backend == "file"
            and self.settings.store.file_store_root
        ):
            from runtime_adapters.file.workspace_overlay_store import (
                FileWorkspaceOverlayStore,
            )

            return FileWorkspaceOverlayStore(root=self.settings.store.file_store_root)
        if self.settings.store.backend == "in_memory":
            from runtime_adapters.in_memory.workspace_overlay_store import (
                InMemoryWorkspaceOverlayStore,
            )

            return InMemoryWorkspaceOverlayStore()
        if self.settings.store.backend == "postgres" and hasattr(
            self.persistence, "_role_connection"
        ):
            from runtime_adapters.postgres.workspace_overlay_store import (
                PostgresWorkspaceOverlayStore,
            )

            return PostgresWorkspaceOverlayStore(store=self.persistence)
        # Keep an unrecognised/incompletely composed backend fail-closed. The
        # C1 writer cannot fall through to a host filesystem mutation.
        return None

    def _effect_claim_store(self) -> object:
        """Select the A5 durable claim store for D1's exact MCP executor."""

        if (
            self.settings.store.backend == "file"
            and self.settings.store.file_store_root
        ):
            from runtime_adapters.file.effect_claim_store import FileEffectClaimStore

            return FileEffectClaimStore(root=self.settings.store.file_store_root)
        if self.settings.store.backend == "postgres" and hasattr(
            self.persistence, "_role_connection"
        ):
            from runtime_adapters.postgres.effect_claim_store import (
                PostgresEffectClaimStore,
            )

            return PostgresEffectClaimStore(store=self.persistence)
        from runtime_adapters.in_memory.effect_claim_store import (
            InMemoryEffectClaimStore,
        )

        return InMemoryEffectClaimStore()

    @staticmethod
    def _browser_effect_bridge() -> object | None:
        """Compose the closed Electron effect client only in desktop mode."""

        import os

        from agent_runtime.capabilities.browser.constants import (
            BrowserEnv,
            BrowserServer,
        )
        from agent_runtime.capabilities.browser.desktop_effect_bridge import (
            DesktopBrowserEffectBridge,
        )

        env = os.environ
        broker_url = env.get(BrowserEnv.BROKER_URL)
        broker_token = env.get(BrowserEnv.BROKER_TOKEN)
        service_identity = env.get(BrowserEnv.SERVICE_IDENTITY)
        broker_audience = env.get(BrowserEnv.BROKER_AUDIENCE)
        if (
            not BrowserEnv.is_enabled(env.get(BrowserEnv.FLAG))
            or env.get("ENTERPRISE_DEPLOYMENT_PROFILE")
            != BrowserServer.REQUIRED_DEPLOYMENT_PROFILE
            or not broker_url
            or not broker_token
        ):
            return None
        return DesktopBrowserEffectBridge(
            broker_url=broker_url,
            broker_token=broker_token,
            service_identity=service_identity or None,
            broker_audience=broker_audience or None,
        )

    async def run_once(self) -> bool:
        """Claim and process one command, returning whether work was found."""

        await self._reap_sandbox_cleanup()
        claim = await self._claim_next()
        if claim is None:
            return False
        async with self._semaphore:
            await self._handle_claim(claim)
        return True

    async def run_until_idle(self) -> int:
        """Process commands until the queue has no immediately claimable work."""

        processed = 0
        while True:
            await self._reap_sandbox_cleanup()
            claims = await self._claim_batch()
            if not claims:
                return processed
            await asyncio.gather(
                *(self._handle_claim_with_limit(claim) for claim in claims)
            )
            processed += len(claims)

    def _build_sandbox_recovery_reaper(self) -> object | None:
        """Construct only the strict file-native D3 reaper, otherwise omit it."""

        from runtime_worker.sandbox_composition import (  # noqa: PLC0415
            FileSandboxRecoveryReaper,
        )

        return FileSandboxRecoveryReaper.compose(
            file_store=self.event_store,
            env=self._capability_env,
            provider_overrides=self._sandbox_provider_overrides,  # type: ignore[arg-type]
        )

    async def _reap_sandbox_cleanup(self) -> None:
        reaper = self._sandbox_recovery_reaper
        if reaper is None:
            return
        try:
            await reaper.run_once()  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001 - duty remains durable for next loop
            self.logger.exception("sandbox cleanup reaper pass failed")

    async def _claim_next(self) -> RuntimeWorkerClaim | None:
        """Attempt to claim one command from the queue; returns ``None`` when the queue is empty."""
        return await self.queue.claim_next(
            worker_id=self.worker_id,
            lock_expires_at=datetime.now(timezone.utc)
            + timedelta(seconds=self.lock_seconds),
        )

    async def _claim_batch(self) -> tuple[RuntimeWorkerClaim, ...]:
        """Claim up to ``max_parallel_runs`` commands in one pass."""
        claims: list[RuntimeWorkerClaim] = []
        for _ in range(self.settings.execution.max_parallel_runs):
            claim = await self._claim_next()
            if claim is None:
                break
            claims.append(claim)
        return tuple(claims)

    async def _handle_claim_with_limit(self, claim: RuntimeWorkerClaim) -> None:
        """Acquire the concurrency semaphore then dispatch the claim."""
        async with self._semaphore:
            await self._handle_claim(claim)

    async def run_forever(self, *, poll_interval_seconds: float = 1.0) -> None:
        """Continuously process queue claims."""

        while True:
            did_work = await self.run_once()
            if not did_work:
                await asyncio.sleep(poll_interval_seconds)

    async def _handle_claim(self, claim: RuntimeWorkerClaim) -> None:
        """Dispatch the claim and mark it complete, retry, or dead-letter on error."""
        try:
            await self._dispatch(claim)
        except AgentRuntimeError as exc:
            self.logger.exception(
                "runtime worker command failed command_id=%s command_type=%s run_id=%s",
                claim.command_id,
                claim.command_type,
                claim.run_id,
            )
            await self._mark_failure(claim=claim, error=exc)
            return
        except Exception:
            self.logger.exception(
                "runtime worker command crashed command_id=%s command_type=%s run_id=%s",
                claim.command_id,
                claim.command_type,
                claim.run_id,
            )
            safe_error = AgentRuntimeError(
                RuntimeErrorCode.EXTERNAL_SERVICE_ERROR,
                "Runtime worker command failed safely.",
                retryable=True,
            )
            await self._mark_failure(claim=claim, error=safe_error)
            return
        await self.queue.mark_complete(
            result=RuntimeWorkerResult(command_id=claim.command_id, succeeded=True)
        )

    # Re-parent handler spans under the API's trace tree so one trace_id covers
    # ingress → enqueue → handler. When trace_propagation is absent (legacy
    # claim or sweeper), the span begins a fresh trace.
    _DISPATCH_SPAN_NAMES: dict[str, str] = {
        PersistenceValues.EventType.RUN_REQUESTED: "runtime_worker.run",
        PersistenceValues.EventType.RUN_CANCEL_REQUESTED: "runtime_worker.cancel",
        PersistenceValues.EventType.APPROVAL_RESOLVED: "runtime_worker.approval_resolved",
        PersistenceValues.EventType.STAGE_COMMIT_REQUESTED: "runtime_worker.stage_commit",
        PersistenceValues.EventType.EFFECT_COMMIT_REQUESTED: "runtime_worker.effect_commit",
        PersistenceValues.EventType.EFFECT_RECONCILE_REQUESTED: (
            "runtime_worker.effect_reconcile"
        ),
        PersistenceValues.EventType.ARTIFACT_EVENT_PUBLISH_REQUESTED: (
            "runtime_worker.artifact_event"
        ),
    }

    async def _dispatch(self, claim: RuntimeWorkerClaim) -> None:
        """Route a claimed command to the appropriate handler under the extracted OTel trace context."""
        command_type = claim.command_type
        carrier = claim.payload.get("trace_propagation")
        parent_ctx = QueueTracePropagator.extract(carrier)
        span_name = self._DISPATCH_SPAN_NAMES.get(
            command_type, f"runtime_worker.{command_type}"
        )
        tracer = otel_trace.get_tracer("agent_runtime.runtime_worker")
        with tracer.start_as_current_span(span_name, context=parent_ctx):
            if command_type == PersistenceValues.EventType.RUN_REQUESTED:
                command = self._runtime_run_command(claim)
                await self.run_handler.handle(command)
                return
            if command_type == PersistenceValues.EventType.RUN_CANCEL_REQUESTED:
                command = self._runtime_cancel_command(claim)
                await self.cancel_handler.handle(command)
                return
            if command_type == PersistenceValues.EventType.APPROVAL_RESOLVED:
                command = self._runtime_approval_command(claim)
                await self.approval_handler.handle(command)
                return
            if command_type == PersistenceValues.EventType.STAGE_COMMIT_REQUESTED:
                command = self._runtime_stage_commit_command(claim)
                await self.stage_commit_handler.handle(command)
                return
            if command_type == PersistenceValues.EventType.EFFECT_COMMIT_REQUESTED:
                handler = self.effect_commit_handler
                if handler is None:
                    raise AgentRuntimeError(
                        RuntimeErrorCode.CONFIGURATION_ERROR,
                        "Effect-commit worker handler is not configured.",
                        retryable=False,
                    )
                command = self._runtime_effect_commit_command(claim)
                await handler.handle(command)
                return
            if command_type == PersistenceValues.EventType.EFFECT_RECONCILE_REQUESTED:
                handler = self.effect_reconcile_handler
                if handler is None:
                    raise AgentRuntimeError(
                        RuntimeErrorCode.CONFIGURATION_ERROR,
                        "Effect-reconcile worker handler is not configured.",
                        retryable=False,
                    )
                command = self._runtime_effect_reconcile_command(claim)
                await handler.handle(command)
                return
            if (
                command_type
                == PersistenceValues.EventType.ARTIFACT_EVENT_PUBLISH_REQUESTED
            ):
                command = self._runtime_artifact_event_command(claim)
                await self.artifact_event_handler.handle(command)
                return
            raise AgentRuntimeError(
                RuntimeErrorCode.VALIDATION_ERROR,
                f"Unsupported worker command type '{command_type}'.",
                retryable=False,
            )

    async def _mark_failure(
        self, *, claim: RuntimeWorkerClaim, error: AgentRuntimeError
    ) -> None:
        """Mark the claim as failed; routes to retry or dead-letter based on the error and attempt count."""
        result = RuntimeWorkerResult(
            command_id=claim.command_id,
            succeeded=False,
            safe_error=error.to_envelope(),
            retry_available_at=datetime.now(timezone.utc)
            + timedelta(seconds=self.retry_delay_seconds),
        )
        if error.retryable and claim.attempts <= self.settings.execution.max_retries:
            await self.queue.mark_retry(result=result)
            return
        await self.queue.mark_dead_letter(result=result)

    def _runtime_run_command(self, claim: RuntimeWorkerClaim) -> RuntimeRunCommand:
        """Deserialise the claim payload into a ``RuntimeRunCommand``."""
        payload = self._command_payload(claim)
        if payload:
            return RuntimeRunCommand.model_validate(payload)
        raise AgentRuntimeError(
            RuntimeErrorCode.VALIDATION_ERROR,
            "Run command payload is unavailable.",
            retryable=False,
        )

    def _runtime_cancel_command(
        self, claim: RuntimeWorkerClaim
    ) -> RuntimeCancelCommand:
        """Deserialise the claim payload into a ``RuntimeCancelCommand``."""
        payload = self._command_payload(claim)
        if payload:
            return RuntimeCancelCommand.model_validate(payload)
        raise AgentRuntimeError(
            RuntimeErrorCode.VALIDATION_ERROR,
            "Cancel command payload is unavailable.",
            retryable=False,
        )

    def _runtime_approval_command(
        self,
        claim: RuntimeWorkerClaim,
    ) -> RuntimeApprovalResolvedCommand:
        """Deserialise the claim payload into a ``RuntimeApprovalResolvedCommand``."""
        payload = self._command_payload(claim)
        if payload:
            return RuntimeApprovalResolvedCommand.model_validate(payload)
        raise AgentRuntimeError(
            RuntimeErrorCode.VALIDATION_ERROR,
            "Approval command payload is unavailable.",
            retryable=False,
        )

    def _runtime_stage_commit_command(
        self,
        claim: RuntimeWorkerClaim,
    ) -> RuntimeStageCommitCommand:
        """Deserialise the claim payload into a ``RuntimeStageCommitCommand`` (PRD-D2)."""
        payload = self._command_payload(claim)
        if payload:
            return RuntimeStageCommitCommand.model_validate(payload)
        raise AgentRuntimeError(
            RuntimeErrorCode.VALIDATION_ERROR,
            "Stage-commit command payload is unavailable.",
            retryable=False,
        )

    def _runtime_effect_commit_command(
        self,
        claim: RuntimeWorkerClaim,
    ) -> RuntimeEffectCommitCommand:
        """Deserialise a body-free A5 effect-commit command."""

        payload = self._command_payload(claim)
        if payload:
            return RuntimeEffectCommitCommand.model_validate(payload)
        raise AgentRuntimeError(
            RuntimeErrorCode.VALIDATION_ERROR,
            "Effect-commit command payload is unavailable.",
            retryable=False,
        )

    def _runtime_effect_reconcile_command(
        self,
        claim: RuntimeWorkerClaim,
    ) -> RuntimeEffectReconcileCommand:
        """Deserialise a body-free command and bind it to its durable queue scope."""

        payload = self._command_payload(claim)
        if payload:
            command = RuntimeEffectReconcileCommand.model_validate(payload)
            if command.org_id != claim.org_id or command.run_id != claim.run_id:
                raise AgentRuntimeError(
                    RuntimeErrorCode.VALIDATION_ERROR,
                    "Effect-reconcile command scope does not match its durable queue claim.",
                    retryable=False,
                )
            return command
        raise AgentRuntimeError(
            RuntimeErrorCode.VALIDATION_ERROR,
            "Effect-reconcile command payload is unavailable.",
            retryable=False,
        )

    def _runtime_artifact_event_command(
        self,
        claim: RuntimeWorkerClaim,
    ) -> RuntimeArtifactEventCommand:
        """Deserialise one canonical artifact-ledger publication command."""

        payload = self._command_payload(claim)
        if payload:
            return RuntimeArtifactEventCommand.model_validate(payload)
        raise AgentRuntimeError(
            RuntimeErrorCode.VALIDATION_ERROR,
            "Artifact event command payload is unavailable.",
            retryable=False,
        )

    @staticmethod
    def _command_payload(claim: RuntimeWorkerClaim) -> dict[str, object]:
        """Extract the command payload from the claim, stripping internal metadata keys."""
        payload: dict[str, object] = {}
        for key, value in claim.payload.items():
            if key == "command_type":
                continue
            if key == "approval_id" and value is None:
                continue
            payload[key] = value
        return payload
