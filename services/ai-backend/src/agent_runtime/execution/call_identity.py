"""Stable model/tool call identity and run-local operation allocation."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Iterator
from uuid import UUID

from pydantic import Field, PositiveInt

from agent_runtime.control_plane.context import RunControlContext
from agent_runtime.execution.contracts import RuntimeContract
from agent_runtime.surfaces_v2.canonical_json import canonical_json_sha256
from agent_runtime.surfaces_v2.ledger_ids import OperationIdCodec


class RuntimeModelCallIdentity(RuntimeContract):
    """Checkpoint-derived identity for one graph model turn.

    The identity is independent of prompt and response bodies. A replay or
    process restart reconstructs the same value from the durable model-turn
    reducer and the Deep Agents execution scope, while parallel local
    subagents remain disjoint through their supervisor task-call id.
    """

    run_id: str = Field(min_length=1, max_length=160)
    snapshot_id: str = Field(min_length=1, max_length=160)
    execution_scope: str = Field(min_length=1, max_length=320)
    model_turn: PositiveInt
    model_call_id: str = Field(min_length=1, max_length=160)

    @classmethod
    def from_current(
        cls,
        *,
        execution_scope: str,
        model_turn: int,
    ) -> "RuntimeModelCallIdentity | None":
        """Build the stable identity for the active verified run."""

        binding = RunControlContext.current()
        if binding is None:
            return None
        if model_turn < 1:
            raise ValueError("model_turn must be positive")
        snapshot = binding.snapshot
        digest = canonical_json_sha256(
            {
                "execution_scope": execution_scope,
                "model_turn": model_turn,
                "run_id": snapshot.run_id,
                "snapshot_id": snapshot.snapshot_id,
            }
        )
        return cls(
            run_id=snapshot.run_id,
            snapshot_id=snapshot.snapshot_id,
            execution_scope=execution_scope,
            model_turn=model_turn,
            model_call_id=f"model-call:{digest}",
        )


class RuntimeToolCallIdentity(RuntimeContract):
    """Content-free identity for one model-visible tool call.

    The identity is reconstructed from checkpointed model-turn state and the
    provider tool-call id. It deliberately excludes tool arguments and result
    bytes, so it is safe to use as control-plane lineage.
    """

    run_id: str = Field(min_length=1, max_length=160)
    snapshot_id: str = Field(min_length=1, max_length=160)
    execution_scope: str = Field(min_length=1, max_length=320)
    model_turn: PositiveInt
    model_tool_call_id: str = Field(min_length=1, max_length=256)
    operation_id: str = Field(min_length=1, max_length=255)
    control_call_id: str = Field(min_length=1, max_length=160)

    @classmethod
    def from_current(
        cls,
        *,
        execution_scope: str = "supervisor",
        model_turn: int,
        model_tool_call_id: str,
    ) -> "RuntimeToolCallIdentity | None":
        """Build the stable identity for the active verified run.

        ``None`` preserves feature-off and isolated unit-test compatibility.
        Production workers bind :class:`RunControlContext` before graph entry.
        """

        binding = RunControlContext.current()
        if binding is None:
            return None
        snapshot = binding.snapshot
        payload = {
            "execution_scope": execution_scope,
            "model_tool_call_id": model_tool_call_id,
            "model_turn": model_turn,
            "run_id": snapshot.run_id,
            "snapshot_id": snapshot.snapshot_id,
        }
        digest = canonical_json_sha256(payload)
        return cls(
            run_id=snapshot.run_id,
            snapshot_id=snapshot.snapshot_id,
            execution_scope=execution_scope,
            model_turn=model_turn,
            model_tool_call_id=model_tool_call_id,
            operation_id=OperationIdCodec.format(cls._uuid4_from_digest(digest)),
            control_call_id=f"runtime-control:{digest}",
        )

    def derived_operation_id(self, ordinal: int) -> str:
        """Return a stable id for the ``ordinal``-th inner gateway operation."""

        if ordinal < 1:
            raise ValueError("operation ordinal must be positive")
        if ordinal == 1:
            return self.operation_id
        digest = canonical_json_sha256(
            {
                "control_call_id": self.control_call_id,
                "operation_ordinal": ordinal,
            }
        )
        return OperationIdCodec.format(self._uuid4_from_digest(digest))

    @staticmethod
    def _uuid4_from_digest(digest: str) -> UUID:
        raw = bytearray.fromhex(digest[:32])
        # Preserve deterministic bytes while satisfying the work-ledger UUID4
        # contract: version 4 in byte 6, RFC-4122 variant in byte 8.
        raw[6] = (raw[6] & 0x0F) | 0x40
        raw[8] = (raw[8] & 0x3F) | 0x80
        return UUID(bytes=bytes(raw))


@dataclass
class _RuntimeCallBinding:
    identity: RuntimeToolCallIdentity
    next_ordinal: int = 1

    def allocate_operation_id(self) -> str:
        operation_id = self.identity.derived_operation_id(self.next_ordinal)
        self.next_ordinal += 1
        return operation_id


_CURRENT_CALL: ContextVar[_RuntimeCallBinding | None] = ContextVar(
    "agent_runtime_tool_call_identity",
    default=None,
)


class RuntimeCallContext:
    """Read-only identity plus deterministic inner-operation allocation."""

    @classmethod
    @contextmanager
    def bind(
        cls,
        identity: RuntimeToolCallIdentity | None,
    ) -> Iterator[RuntimeToolCallIdentity | None]:
        """Bind one model-visible call around policy, budget, and execution."""

        if identity is None:
            yield None
            return
        token = _CURRENT_CALL.set(_RuntimeCallBinding(identity=identity))
        try:
            yield identity
        finally:
            _CURRENT_CALL.reset(token)

    @classmethod
    def current(cls) -> RuntimeToolCallIdentity | None:
        binding = _CURRENT_CALL.get()
        return binding.identity if binding is not None else None

    @classmethod
    def next_operation_id(cls) -> str | None:
        """Allocate the next stable operation id for the active tool call."""

        binding = _CURRENT_CALL.get()
        return None if binding is None else binding.allocate_operation_id()


__all__ = [
    "RuntimeCallContext",
    "RuntimeModelCallIdentity",
    "RuntimeToolCallIdentity",
]
