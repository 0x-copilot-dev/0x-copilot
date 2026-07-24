"""Durable material adapters for the D1 MCP operation gateway.

This module is deliberately worker-owned: model tools only receive the narrow
capability contracts while the composition root chooses the content-addressed
store and run event stream.  Argument bytes are retained as ``EFFECT``
references, so an approved operation can be reconstructed after a worker
restart without inventing a second database or passing raw arguments through a
queue command.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json

from agent_runtime.artifacts.ports import ArtifactBlobStorePort
from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.capabilities.mcp.operation_adapter import McpOperationStoredResult
from agent_runtime.capabilities.mcp.operation_adapter import (
    McpOperationArgumentMaterialResolver,
    McpTargetRefCodec,
)
from agent_runtime.capabilities.operations.contracts import OperationRequest
from agent_runtime.effects.coordinator import EffectCoordinator
from agent_runtime.effects.executor import EffectExecutionScope
from agent_runtime.effects.executor_registry import EffectExecutorRegistry
from agent_runtime.api.effect_ledger import RuntimeEffectLedger
from agent_runtime.surfaces_v2.canonical_json import canonical_json_bytes
from agent_runtime.surfaces_v2.ledger_models import EffectExecutorKind
from agent_runtime.execution.contracts import StreamEventSource
from agent_runtime.surfaces_v2.ledger_ids import OperationArgsRefCodec
from runtime_adapters.artifact_references import (
    ArtifactReferenceEdge,
    ArtifactReferenceKind,
    ArtifactReferenceRepositoryPort,
)
from runtime_api.schemas import RunRecord, RuntimeApiEventType
from runtime_worker.mcp_effect_executor import McpEffectExecutor
from agent_runtime.surfaces_v2.mcp_connector import McpStageCommitConnector

MAX_CANONICAL_ARGUMENT_BYTES = 1_048_576
MAX_MODEL_RESULT_BYTES = 65_536
MAX_MODEL_RESULT_PREVIEW_BYTES = 8_192


async def _one_chunk(body: bytes) -> AsyncIterator[bytes]:
    yield body


async def _read_bounded(stream: AsyncIterator[bytes], *, limit: int) -> bytes | None:
    body = bytearray()
    async for chunk in stream:
        if not isinstance(chunk, bytes) or len(body) + len(chunk) > limit:
            return None
        body.extend(chunk)
    return bytes(body)


@dataclass(frozen=True)
class RuntimeMcpOperationArgumentStore:
    """Persist and resolve digest-pinned canonical arguments for one run."""

    blobs: ArtifactBlobStorePort
    references: ArtifactReferenceRepositoryPort
    org_id: str
    user_id: str

    async def persist(self, *, ref: str, digest: str, canonical_bytes: bytes) -> None:
        OperationArgsRefCodec.parse(ref)
        if (
            len(canonical_bytes) > MAX_CANONICAL_ARGUMENT_BYTES
            or hashlib.sha256(canonical_bytes).hexdigest() != digest
        ):
            raise ValueError("canonical MCP arguments are invalid")
        stored = await self.blobs.put_stream(
            expected_digest=digest,
            chunks=_one_chunk(canonical_bytes),
            byte_limit=MAX_CANONICAL_ARGUMENT_BYTES,
        )
        if stored.blob_key != digest:
            raise ValueError("canonical MCP arguments changed during storage")
        edge_seed = f"{self.org_id}\0effect\0{ref}\0{digest}".encode()
        await self.references.acquire(
            ArtifactReferenceEdge(
                org_id=self.org_id,
                edge_id=f"effect-{hashlib.sha256(edge_seed).hexdigest()}",
                user_id=self.user_id,
                blob_key=digest,
                reference_kind=ArtifactReferenceKind.EFFECT,
                reference_id=ref,
                created_at=datetime.now(timezone.utc),
            )
        )

    async def resolve(self, *, ref: str, digest: str) -> bytes | None:
        OperationArgsRefCodec.parse(ref)
        edges = await self.references.list_edges(org_id=self.org_id)
        edge = next(
            (
                candidate
                for candidate in edges
                if candidate.reference_kind is ArtifactReferenceKind.EFFECT
                and candidate.reference_id == ref
                and candidate.blob_key == digest
                and candidate.released_at is None
            ),
            None,
        )
        if edge is None:
            return None
        body = await _read_bounded(
            await self.blobs.open_stream(edge.blob_key),
            limit=MAX_CANONICAL_ARGUMENT_BYTES,
        )
        if body is None or hashlib.sha256(body).hexdigest() != digest:
            return None
        return body

    async def resolve_reference(self, *, ref: str) -> bytes | None:
        """Open a retained argument ref for A5 digest revalidation."""

        OperationArgsRefCodec.parse(ref)
        edges = await self.references.list_edges(org_id=self.org_id)
        edge = next(
            (
                candidate
                for candidate in edges
                if candidate.reference_kind is ArtifactReferenceKind.EFFECT
                and candidate.reference_id == ref
                and candidate.released_at is None
            ),
            None,
        )
        if edge is None:
            return None
        body = await _read_bounded(
            await self.blobs.open_stream(edge.blob_key),
            limit=MAX_CANONICAL_ARGUMENT_BYTES,
        )
        if body is None or hashlib.sha256(body).hexdigest() != edge.blob_key:
            return None
        return body


@dataclass(frozen=True)
class RuntimeMcpOperationResultStore:
    """Persist one bounded model-facing MCP read result in the run event stream."""

    event_producer: RuntimeEventProducer
    run: RunRecord

    async def store_read_result(
        self,
        *,
        request: OperationRequest,
        output: Mapping[str, object],
    ) -> McpOperationStoredResult:
        encoded = json.dumps(
            {str(key): value for key, value in output.items()},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            default=str,
        ).encode("utf-8")
        if len(encoded) > MAX_MODEL_RESULT_BYTES:
            model_output: dict[str, object] = {
                "truncated": True,
                "byte_size": len(encoded),
                "preview": encoded[:MAX_MODEL_RESULT_PREVIEW_BYTES].decode(
                    "utf-8", errors="replace"
                ),
            }
        else:
            parsed = json.loads(encoded)
            if not isinstance(parsed, dict):
                raise ValueError("MCP result must be an object")
            model_output = parsed
        await self.event_producer.append_api_event(
            run=self.run,
            source=StreamEventSource.TOOL,
            event_type=RuntimeApiEventType.TOOL_RESULT,
            event_id=request.operation_id,
            payload={
                "tool_name": f"{request.capability}.{request.op}",
                "call_id": request.operation_id,
                "status": "completed",
                "output": model_output,
            },
        )
        return McpOperationStoredResult(
            result_ref=f"operation://{request.operation_id}/result",
            model_output=model_output,
        )


@dataclass(frozen=True)
class RuntimeMcpEffectCoordinatorFactory:
    """Build the sole D1 MCP executor behind A5's claim-before-apply protocol."""

    event_producer: RuntimeEventProducer
    claims: object
    blobs: ArtifactBlobStorePort
    references: ArtifactReferenceRepositoryPort
    dependencies_factory: object
    timeout_seconds: float

    def for_run(self, *, run: RunRecord) -> EffectCoordinator:
        owner_ref = f"principal://users/{run.user_id}"
        scope = EffectExecutionScope(
            org_id=run.org_id,
            user_id=run.user_id,
            conversation_id=run.conversation_id,
            run_id=run.run_id,
            owner_ref=owner_ref,
        )
        arguments = RuntimeMcpOperationArgumentStore(
            blobs=self.blobs,
            references=self.references,
            org_id=run.org_id,
            user_id=run.user_id,
        )
        return EffectCoordinator(
            ledger=RuntimeEffectLedger(
                event_producer=self.event_producer,
                run=run,
                owner_ref=owner_ref,
            ),
            claims=self.claims,  # type: ignore[arg-type]
            scopes=_StaticEffectScope(scope),
            references=_McpImmutableReferences(scope=scope, arguments=arguments),
            executors=EffectExecutorRegistry(
                {
                    EffectExecutorKind.MCP: lambda active_scope: McpEffectExecutor(
                        scope=active_scope,
                        connector=McpStageCommitConnector(
                            runtime_context=run.runtime_context,
                            dependencies_factory=self.dependencies_factory,  # type: ignore[arg-type]
                            timeout_seconds=self.timeout_seconds,
                        ),
                        material_resolver=McpOperationArgumentMaterialResolver(
                            arguments=arguments
                        ),
                        enabled=True,
                    )
                }
            ),
        )


@dataclass(frozen=True)
class _StaticEffectScope:
    scope: EffectExecutionScope

    async def resolve(self, *, run_id: str) -> EffectExecutionScope | None:
        return self.scope if run_id == self.scope.run_id else None


@dataclass(frozen=True)
class _McpImmutableReferences:
    scope: EffectExecutionScope
    arguments: RuntimeMcpOperationArgumentStore

    def open(
        self, *, scope: EffectExecutionScope, reference: str
    ) -> AsyncIterator[bytes]:
        async def _stream() -> AsyncIterator[bytes]:
            if scope != self.scope:
                return
            if reference.startswith("operation://"):
                body = await self.arguments.resolve_reference(ref=reference)
                if body is not None:
                    yield body
                return
            try:
                target = McpTargetRefCodec.parse(reference)
            except ValueError:
                return
            yield canonical_json_bytes(
                {"capability": target.capability, "op": target.op}
            )

        return _stream()


__all__ = [
    "RuntimeMcpOperationArgumentStore",
    "RuntimeMcpEffectCoordinatorFactory",
    "RuntimeMcpOperationResultStore",
]
