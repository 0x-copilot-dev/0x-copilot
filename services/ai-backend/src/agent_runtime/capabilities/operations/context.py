"""Per-run Operation Gateway binding and canonical request construction."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TypeVar
from uuid import uuid4

from pydantic import BaseModel, Field

from agent_runtime.capabilities.operations.contracts import (
    ArtifactIntent,
    ArtifactServicePort,
    OperationArgumentResolver,
    OperationArgumentStore,
    OperationDisposition,
    OperationEventEmitter,
    OperationGatewayMode,
    OperationMetricsPort,
    OperationRequest,
)
from agent_runtime.capabilities.operations.errors import (
    OperationContextUnboundError,
    OperationEnforcementNotReadyError,
    OperationIdempotencyConflictError,
)
from agent_runtime.capabilities.operations.descriptors import (
    OperationDescriptorRegistry,
)
from agent_runtime.capabilities.operations.observability import (
    FailSoftOperationEventEmitter,
    FailSoftOperationMetrics,
    OperationGatewayMetrics,
)
from agent_runtime.capabilities.tools.permissions import ToolUsePolicySnapshot
from agent_runtime.execution.contracts import RuntimeContract
from agent_runtime.surfaces_v2.canonical_json import (
    canonical_json_bytes,
    sha256_hex,
)
from agent_runtime.surfaces_v2.ledger_ids import OperationArgsRefCodec, OperationIdCodec
from agent_runtime.surfaces_v2.ledger_models import (
    EffectClass,
    LedgerEventType,
    Producer,
)

_T = TypeVar("_T")


class VerifiedOperationIdentity(RuntimeContract):
    """Identity copied from the persisted run, never model arguments."""

    org_id: str = Field(min_length=1, max_length=255)
    user_id: str = Field(min_length=1, max_length=255)
    conversation_id: str = Field(min_length=1, max_length=255)
    run_id: str = Field(min_length=1, max_length=255)


class NullOperationEventEmitter:
    async def emit(
        self,
        event_type: object,
        payload: Mapping[str, object],
        summary: str | None = None,
    ) -> None:
        del event_type, payload, summary


@dataclass(frozen=True)
class OperationEventEmitterAdapter:
    """Adapt the existing string-valued Work Ledger append closure."""

    emit_fn: Callable[
        [str, Mapping[str, object], str | None],
        Awaitable[None],
    ]

    async def emit(
        self,
        event_type: LedgerEventType,
        payload: Mapping[str, object],
        summary: str | None = None,
    ) -> None:
        await self.emit_fn(event_type.value, payload, summary)


@dataclass
class OperationInvocationRegistry:
    """Per-run async idempotency coalescer keyed by operation id."""

    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _entries: dict[str, tuple[str, asyncio.Future[OperationDisposition]]] = field(
        default_factory=dict
    )

    async def invoke_once(
        self,
        *,
        operation_id: str,
        args_digest: str,
        invoke: Callable[[], Awaitable[OperationDisposition]],
    ) -> OperationDisposition:
        owner = False
        async with self._lock:
            existing = self._entries.get(operation_id)
            if existing is not None:
                stored_digest, future = existing
                if stored_digest != args_digest:
                    raise OperationIdempotencyConflictError
            else:
                future = asyncio.get_running_loop().create_future()
                self._entries[operation_id] = (args_digest, future)
                owner = True
        if not owner:
            return await asyncio.shield(future)
        try:
            result = await invoke()
        except BaseException as exc:
            if not future.done():
                future.set_exception(exc)
                # Consume the owner-side future exception if no waiter exists.
                future.exception()
            raise
        if not future.done():
            future.set_result(result)
        return result


@dataclass
class BoundOperationContext:
    """Immutable authority plus run-local caches."""

    identity: VerifiedOperationIdentity
    policy_snapshot: ToolUsePolicySnapshot
    ledger_emitter: OperationEventEmitter = field(
        default_factory=NullOperationEventEmitter
    )
    artifact_service: ArtifactServicePort | None = None
    mode: OperationGatewayMode = OperationGatewayMode.OFF
    metrics: OperationMetricsPort = field(default_factory=OperationGatewayMetrics)
    arguments: OperationArgumentResolver = field(default_factory=OperationArgumentStore)
    canonical_arguments_durable: bool = False
    invocations: OperationInvocationRegistry = field(
        default_factory=OperationInvocationRegistry
    )

    def __post_init__(self) -> None:
        self.ledger_emitter = FailSoftOperationEventEmitter.wrap(self.ledger_emitter)
        self.metrics = FailSoftOperationMetrics.wrap(self.metrics)


_CONTEXT: ContextVar[BoundOperationContext | None] = ContextVar(
    "operation_gateway_context", default=None
)
_OPERATION_STACK: ContextVar[tuple[str, ...]] = ContextVar(
    "operation_gateway_stack", default=()
)
_PRODUCER: ContextVar[Producer] = ContextVar(
    "operation_gateway_producer", default=Producer.MODEL
)


class OperationContext:
    """ContextVar facade following the runtime's existing bind/unbind pattern."""

    @classmethod
    def bind_for_run(
        cls,
        *,
        identity: VerifiedOperationIdentity,
        policy_snapshot: ToolUsePolicySnapshot,
        ledger_emitter: OperationEventEmitter | None,
        artifact_service: ArtifactServicePort | None,
        mode: OperationGatewayMode,
        metrics: OperationMetricsPort | None = None,
        arguments: OperationArgumentResolver | None = None,
        canonical_arguments_durable: bool = False,
    ) -> Token[BoundOperationContext | None]:
        return _CONTEXT.set(
            BoundOperationContext(
                identity=identity,
                policy_snapshot=policy_snapshot,
                ledger_emitter=(
                    ledger_emitter
                    if ledger_emitter is not None
                    else NullOperationEventEmitter()
                ),
                artifact_service=artifact_service,
                mode=mode,
                metrics=metrics if metrics is not None else OperationGatewayMetrics(),
                arguments=(
                    arguments if arguments is not None else OperationArgumentStore()
                ),
                canonical_arguments_durable=canonical_arguments_durable,
            )
        )

    @classmethod
    def unbind(cls, token: Token[BoundOperationContext | None]) -> None:
        _CONTEXT.reset(token)

    @classmethod
    def active(cls) -> BoundOperationContext | None:
        return _CONTEXT.get()

    @classmethod
    def require(cls) -> BoundOperationContext:
        active = cls.active()
        if active is None:
            raise OperationContextUnboundError
        return active

    @classmethod
    def parent_operation_id(cls) -> str | None:
        stack = _OPERATION_STACK.get()
        return stack[-1] if stack else None

    @classmethod
    def current_producer(cls) -> Producer:
        return _PRODUCER.get()

    @classmethod
    @contextmanager
    def operation_scope(cls, operation_id: str):
        stack = _OPERATION_STACK.get()
        token = _OPERATION_STACK.set((*stack, operation_id))
        try:
            yield
        finally:
            _OPERATION_STACK.reset(token)

    @classmethod
    @contextmanager
    def producer_scope(cls, producer: Producer):
        token = _PRODUCER.set(producer)
        try:
            yield
        finally:
            _PRODUCER.reset(token)


class OperationRequestFactory:
    """Build A1 requests from trusted context and canonical JSON arguments."""

    @classmethod
    def create(
        cls,
        *,
        capability: str,
        op: str,
        arguments: Mapping[str, object],
        producer: Producer | None = None,
        artifact_intent: ArtifactIntent | None = None,
        effect_hint: EffectClass | None = None,
        operation_id: str | None = None,
        parent_operation_id: str | None = None,
    ) -> OperationRequest:
        context = OperationContext.require()
        normalized_capability = OperationDescriptorRegistry.normalize_capability(
            capability
        )
        normalized_op = OperationDescriptorRegistry.normalize(op)
        canonical_bytes = canonical_json_bytes(_json_compatible_arguments(arguments))
        digest = sha256_hex(canonical_bytes)
        identifier = operation_id or OperationIdCodec.format(uuid4())
        ref = OperationArgsRefCodec.format(identifier)
        context.arguments.put(
            ref=ref,
            digest=digest,
            canonical_bytes=canonical_bytes,
        )
        return OperationRequest(
            operation_id=identifier,
            run_id=context.identity.run_id,
            producer=producer or OperationContext.current_producer(),
            capability=normalized_capability,
            op=normalized_op,
            canonical_args_ref=ref,
            args_digest=digest,
            requested_at=datetime.now(timezone.utc).isoformat(),
            artifact_intent=artifact_intent,
            effect_hint=effect_hint,
            parent_operation_id=(
                parent_operation_id
                if parent_operation_id is not None
                else OperationContext.parent_operation_id()
            ),
        )


def _json_compatible_arguments(
    arguments: Mapping[str, object],
) -> dict[str, object]:
    """Normalize validated tool values into the A1 canonical-JSON domain.

    LangChain/Pydantic may hand a tool adapter tuples or nested contract
    instances even though the original wire arguments were JSON.  Normalize
    only those structural containers; arbitrary objects remain unsupported and
    cause the shadow probe to fail soft rather than inventing a serialization.
    """

    return {key: _json_compatible_value(value) for key, value in arguments.items()}


def _json_compatible_value(value: object) -> object:
    if isinstance(value, BaseModel):
        return _json_compatible_value(value.model_dump(mode="json"))
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            return value
        return {str(key): _json_compatible_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_compatible_value(item) for item in value]
    return value


class OperationGatewayStartupGuard:
    """Refuse authoritative mode until generic stage/executor wiring exists."""

    @classmethod
    def validate(
        cls,
        *,
        mode: OperationGatewayMode,
        stage_dependency_ready: bool = False,
        executor_dependency_ready: bool = False,
        durable_arguments_ready: bool = False,
    ) -> None:
        if mode is not OperationGatewayMode.ENFORCE:
            return
        if (
            not stage_dependency_ready
            or not executor_dependency_ready
            or not durable_arguments_ready
        ):
            raise OperationEnforcementNotReadyError


__all__ = (
    "BoundOperationContext",
    "NullOperationEventEmitter",
    "OperationContext",
    "OperationEventEmitterAdapter",
    "OperationGatewayStartupGuard",
    "OperationInvocationRegistry",
    "OperationRequestFactory",
    "VerifiedOperationIdentity",
)
