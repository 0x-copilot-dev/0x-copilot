"""Substitution boundaries for AC6 code mode.

Three seams, each narrow (interface segregation, PRD "Why this is sane"):

* :class:`InterpreterPort` — an embedded interpreter the service drives with
  ``start`` / ``resume`` / ``cancel``. Monty is one adapter; a future QuickJS
  adapter would implement the same surface.
* :class:`PolicyToolInvoker` — the **single** product-owned seam every external
  function call is routed through. The ordinary direct-tool wrapper and this
  interpreter bridge both call it, so approval / budget / citation / audit
  semantics are shared by construction rather than duplicated (PRD "DRY").
* :class:`InterpreterSnapshotStore` and :class:`InterpreterEventSink` —
  persistence and observability seams, kept out of the interpreter's reach.

Nothing here imports Monty.
"""

from __future__ import annotations

from typing import ClassVar, Protocol, runtime_checkable

from pydantic import Field

from agent_runtime.capabilities.interpreter.contracts import (
    ExternalFunctionCall,
    ExternalFunctionSpec,
    InterpreterErrorCode,
    InterpreterRequest,
    InterpreterStep,
    SnapshotRef,
)
from agent_runtime.execution.contracts import JsonValue, RuntimeContract


class PolicyInvocationContext(RuntimeContract):
    """Trusted context handed to :class:`PolicyToolInvoker` for one external call.

    Everything here is derived from the verified run context, never from model
    input. The invoker re-reads the underlying tool's grant, connector state,
    policy, and budget at dispatch time using these ids.
    """

    run_id: str = Field(min_length=1)
    interpreter_session_id: str = Field(min_length=1)
    org_id: str | None = None
    user_id: str | None = None
    spec: ExternalFunctionSpec


class PolicyToolInvocationOutcome(RuntimeContract):
    """Result of routing one external call through the shared policy seam.

    ``status`` decides how the interpreter is resumed:

    * ``allowed``  -> resume the interpreter with ``return_value``;
    * ``rejected`` -> resume by surfacing a typed exception into interpreted
      code (a user rejected the approval; the tool did **not** run);
    * ``denied``   -> the alias/tool was not permitted (blocked policy, unknown
      alias, paused connector) — surfaced as a typed exception too;
    * ``error``    -> the underlying tool raised; surfaced as a typed exception.

    Only ``allowed`` implies a real side effect happened.
    """

    status: str
    invocation_id: str = Field(min_length=1)
    return_value: JsonValue = None
    error_code: InterpreterErrorCode | None = None
    safe_message: str | None = None

    # Class constants (not fields) for the four terminal statuses.
    ALLOWED: ClassVar[str] = "allowed"
    REJECTED: ClassVar[str] = "rejected"
    DENIED: ClassVar[str] = "denied"
    ERROR: ClassVar[str] = "error"


@runtime_checkable
class InterpreterPort(Protocol):
    """Embedded interpreter the service drives step by step.

    ``start`` runs until the program finishes, requests an external function, or
    fails. ``resume`` continues a suspended session with the policy outcome for
    the pending external call. Implementations hold live session state RAM-only
    and expose it only as an opaque snapshot via :class:`ExternalFunctionCall`.
    """

    async def start(self, request: InterpreterRequest) -> InterpreterStep: ...

    async def resume(
        self,
        *,
        call: ExternalFunctionCall,
        outcome: PolicyToolInvocationOutcome,
    ) -> InterpreterStep: ...

    async def cancel(self, *, interpreter_session_id: str) -> None: ...


@runtime_checkable
class PolicyToolInvoker(Protocol):
    """The one seam every external function invocation passes through.

    A production implementation runs the underlying tool through the same
    permission check, four-mode approval, budget guard, citation projection,
    payload offload, and audit path a direct tool call uses. The PRD flags that
    the direct-path four-mode engine is not yet wired; until it lands, AC6 ships
    pure-compute-only and this seam is exercised by fakes. The *shape* is fixed
    here so the real engine drops in without touching the interpreter bridge.
    """

    async def invoke(
        self,
        *,
        call: ExternalFunctionCall,
        context: PolicyInvocationContext,
    ) -> PolicyToolInvocationOutcome: ...


@runtime_checkable
class InterpreterSnapshotStore(Protocol):
    """Persists RAM-only interpreter snapshots as content-addressed bytes.

    ``put`` enforces the snapshot-size ceiling and returns a small
    :class:`SnapshotRef`; ``get`` verifies the digest and envelope binding
    before returning bytes so a corrupted or incompatible snapshot fails closed
    rather than being blind-loaded.
    """

    def put(
        self,
        data: bytes,
        *,
        adapter: str,
        abi_version: str,
        source_sha256: str,
        limit_profile_hash: str,
        invocation_index: int,
        max_snapshot_bytes: int,
    ) -> SnapshotRef: ...

    def get(self, ref: SnapshotRef) -> bytes: ...


@runtime_checkable
class InterpreterEventSink(Protocol):
    """Best-effort sink for the ``interpreter.*`` structured events.

    Kept as a narrow Protocol so the service does not depend on the concrete
    run-event producer; registration wires the real one. ``payload`` carries
    only redaction-safe fields (ids, counters, hashes, byte counts) — never
    source, inputs, callback arguments, or tool output.
    """

    async def emit(self, *, name: str, payload: dict[str, JsonValue]) -> None: ...


__all__ = (
    "InterpreterEventSink",
    "InterpreterPort",
    "InterpreterSnapshotStore",
    "PolicyInvocationContext",
    "PolicyToolInvocationOutcome",
    "PolicyToolInvoker",
)
