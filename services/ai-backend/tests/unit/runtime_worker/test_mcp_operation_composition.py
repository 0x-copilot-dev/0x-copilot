"""Keystones for the D1 production composition root."""

from __future__ import annotations

import hashlib

from agent_runtime.capabilities.operations.contracts import OperationGatewayMode
from agent_runtime.execution.contracts import AgentRuntimeContext, ModelConfig
from agent_runtime.settings import RuntimeSettings
from runtime_adapters.artifact_references import InMemoryArtifactReferenceStore
from runtime_adapters.in_memory.artifact_blob_store import InMemoryArtifactBlobStore
from runtime_adapters.in_memory.artifact_publication import (
    InMemoryArtifactPublicationCoordinator,
)
from runtime_adapters.in_memory.runtime_api_store import InMemoryRuntimeApiStore
from runtime_api.schemas import RunRecord
from runtime_worker.handlers.run import RuntimeRunHandler
from runtime_worker.loop import RuntimeWorker
from runtime_worker.mcp_operation_storage import (
    RuntimeMcpOperationArgumentStore,
    RuntimeMcpOperationResultStore,
)


def _settings() -> RuntimeSettings:
    return RuntimeSettings.load(
        environ={
            "SURFACES_V2": "true",
            "OPERATION_GATEWAY_MODE": OperationGatewayMode.ENFORCE.value,
        }
    )


def _run() -> RunRecord:
    context = AgentRuntimeContext(
        user_id="user_d1",
        org_id="org_d1",
        roles={"employee"},
        model_profile=ModelConfig(
            provider="openai",
            model_name="gpt-5.4-mini",
            max_input_tokens=4096,
            timeout_seconds=30,
            temperature=0,
        ),
        run_id="run_d1",
        trace_id="trace_d1",
    )
    return RunRecord(
        run_id="run_d1",
        conversation_id="conv_d1",
        org_id="org_d1",
        user_id="user_d1",
        user_message_id="msg_d1",
        trace_id="trace_d1",
        model_provider="openai",
        model_name="gpt-5.4-mini",
        runtime_context=context,
    )


async def test_complete_run_composition_binds_only_durable_gateway_services() -> None:
    store = InMemoryRuntimeApiStore()
    coordinator = InMemoryArtifactPublicationCoordinator()
    blobs = InMemoryArtifactBlobStore(coordinator)
    references = InMemoryArtifactReferenceStore(coordinator)
    handler = RuntimeRunHandler(
        persistence=store,
        event_store=store,
        settings=_settings(),
        queue=store,
        artifact_blob_store=blobs,
        artifact_reference_store=references,
    )

    services = handler._build_mcp_operation_gateway_services(_run())

    assert services is not None
    assert isinstance(services.argument_store, RuntimeMcpOperationArgumentStore)
    assert isinstance(services.result_store, RuntimeMcpOperationResultStore)
    assert services.stage_scope.run_id == "run_d1"
    assert services.stage_scope.owner_ref == "principal://users/user_d1"


def test_worker_composes_the_only_effect_commit_executor_when_d1_is_ready() -> None:
    store = InMemoryRuntimeApiStore()
    coordinator = InMemoryArtifactPublicationCoordinator()
    worker = RuntimeWorker(
        persistence=store,
        event_store=store,
        queue=store,
        settings=_settings(),
        artifact_blob_store=InMemoryArtifactBlobStore(coordinator),
        artifact_reference_store=InMemoryArtifactReferenceStore(coordinator),
    )

    assert worker.effect_commit_handler is not None
    assert worker.effect_reconcile_handler is not None


async def test_durable_argument_store_rejects_missing_or_changed_material() -> None:
    coordinator = InMemoryArtifactPublicationCoordinator()
    store = RuntimeMcpOperationArgumentStore(
        blobs=InMemoryArtifactBlobStore(coordinator),
        references=InMemoryArtifactReferenceStore(coordinator),
        org_id="org_d1",
        user_id="user_d1",
    )
    body = b'{"issue_id":"L-1"}'
    digest = hashlib.sha256(body).hexdigest()
    ref = "operation://op_00000000-0000-4000-8000-000000000001/args"

    await store.persist(ref=ref, digest=digest, canonical_bytes=body)

    assert await store.resolve(ref=ref, digest=digest) == body
    assert await store.resolve(ref=ref, digest="0" * 64) is None
