"""Orchestration for one code-mode invocation.

:class:`InterpreterService` owns the drive loop: it resolves the model's
requested aliases against the run's already-authorized tools, stamps the
deployment limit profile, and steps the :class:`InterpreterPort`. Every external
function the interpreter requests is routed through the one shared
:class:`PolicyToolInvoker` (approval / budget / audit) and the interpreter is
resumed with the outcome. It also enforces the two ceilings the adapter cannot
see on its own — the external-call count and the total wall-time budget — and
emits the ``interpreter.*`` structured events.

The interpreter, the policy seam, snapshot bytes, and result bytes are separate
collaborators (single responsibility); this class only sequences them.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass

from agent_runtime.capabilities.interpreter.contracts import (
    ExternalFunctionCall,
    ExternalFunctionSpec,
    InterpreterCompleted,
    InterpreterError,
    InterpreterErrorCode,
    InterpreterFailed,
    InterpreterLimitKind,
    InterpreterLimitProfiles,
    InterpreterLimits,
    InterpreterRequest,
    InterpreterStep,
    RunCodeModeInput,
)
from agent_runtime.capabilities.interpreter.ports import (
    InterpreterEventSink,
    InterpreterPort,
    PolicyInvocationContext,
    PolicyToolInvocationOutcome,
    PolicyToolInvoker,
)
from agent_runtime.capabilities.interpreter.snapshot_store import (
    ContentAddressedBlobStore,
)
from agent_runtime.execution.contracts import JsonValue
from typing import Protocol, runtime_checkable
from uuid import uuid4


@runtime_checkable
class ExternalFunctionResolver(Protocol):
    """Resolves a model-declared alias to an authorized tool binding.

    Production wires this against the run's already scope-filtered tool / MCP
    cards, so an alias can only resolve to a tool the run may already call.
    """

    def resolve(self, alias: str) -> ExternalFunctionSpec | None: ...


class InterpreterEvents:
    """Structured event names (PRD "Observability and audit")."""

    STARTED = "interpreter.started"
    EXTERNAL_CALL_REQUESTED = "interpreter.external_call_requested"
    SUSPENDED_FOR_APPROVAL = "interpreter.suspended_for_approval"
    RESUMED = "interpreter.resumed"
    LIMIT_EXCEEDED = "interpreter.limit_exceeded"
    CANCELLED = "interpreter.cancelled"
    COMPLETED = "interpreter.completed"
    FAILED = "interpreter.failed"


@dataclass(frozen=True)
class InterpreterServiceConfig:
    """Static configuration for one service instance.

    ``limits_override`` lets a deployment (or a test) supply an explicit,
    already-clamped :class:`InterpreterLimits` instead of a named profile;
    deployment policy may only lower defaults, never raise them.
    """

    limit_profile_name: str = "desktop_v1"
    limits_override: InterpreterLimits | None = None

    def resolve_limits(self) -> InterpreterLimits:
        """Return the effective limits: an override if set, else the profile."""

        if self.limits_override is not None:
            return self.limits_override
        return InterpreterLimitProfiles.resolve(self.limit_profile_name)


class _NullEventSink:
    """Event sink that drops everything (used when observability is off)."""

    async def emit(self, *, name: str, payload: dict[str, JsonValue]) -> None:
        del name, payload


class InterpreterService:
    """Drives a single code-mode program to a terminal result."""

    def __init__(
        self,
        *,
        port: InterpreterPort,
        policy_invoker: PolicyToolInvoker,
        resolver: ExternalFunctionResolver,
        config: InterpreterServiceConfig | None = None,
        event_sink: InterpreterEventSink | None = None,
        result_store: ContentAddressedBlobStore | None = None,
    ) -> None:
        self._port = port
        self._policy = policy_invoker
        self._resolver = resolver
        self._config = config or InterpreterServiceConfig()
        self._events: InterpreterEventSink = event_sink or _NullEventSink()
        self._result_store = result_store

    async def run(
        self,
        model_input: RunCodeModeInput,
        *,
        run_id: str,
        org_id: str | None = None,
        user_id: str | None = None,
    ) -> InterpreterCompleted | InterpreterFailed:
        """Execute one code-mode program end to end.

        Never raises to the caller: every failure becomes a typed
        :class:`InterpreterFailed`. The model chooses ordinary tools or AC7 from
        the stable error code.
        """

        session_id = uuid4().hex
        limits = self._config.resolve_limits()
        try:
            specs = self._resolve_aliases(model_input.external_functions)
        except InterpreterError as exc:
            return await self._terminal_failure(session_id, run_id, exc.as_failed())

        request = InterpreterRequest(
            interpreter_session_id=session_id,
            run_id=run_id,
            code=model_input.code,
            inputs=dict(model_input.inputs),
            external_functions=specs,
            limits=limits,
        )
        spec_by_alias = {spec.alias: spec for spec in specs}

        await self._events.emit(
            name=InterpreterEvents.STARTED,
            payload={
                "run_id": run_id,
                "interpreter_session_id": session_id,
                "external_function_count": len(specs),
            },
        )
        started = time.monotonic()

        try:
            step = await self._port.start(request)
            external_calls = 0
            while isinstance(step, ExternalFunctionCall):
                self._enforce_total_time(started, limits)
                external_calls += 1
                self._enforce_external_ceiling(external_calls, limits)
                outcome = await self._route_external_call(
                    step,
                    run_id=run_id,
                    session_id=session_id,
                    org_id=org_id,
                    user_id=user_id,
                    spec_by_alias=spec_by_alias,
                )
                await self._events.emit(
                    name=InterpreterEvents.RESUMED,
                    payload={
                        "run_id": run_id,
                        "interpreter_session_id": session_id,
                        "invocation_index": step.invocation_index,
                        "outcome": outcome.status,
                    },
                )
                step = await self._port.resume(call=step, outcome=outcome)
        except InterpreterError as exc:
            await self._safe_cancel(session_id)
            return await self._terminal_failure(session_id, run_id, exc.as_failed())

        return await self._terminalise(session_id, run_id, step, limits)

    # -- external-call routing --------------------------------------------

    async def _route_external_call(
        self,
        call: ExternalFunctionCall,
        *,
        run_id: str,
        session_id: str,
        org_id: str | None,
        user_id: str | None,
        spec_by_alias: dict[str, ExternalFunctionSpec],
    ) -> PolicyToolInvocationOutcome:
        """Route one external call through the shared policy seam.

        This is the whole point of AC6: the interpreter never calls a tool
        itself. It suspends; the service asks the *same* policy invoker a direct
        tool call would use, and only ``allowed`` implies a side effect.
        """

        spec = spec_by_alias.get(call.alias)
        await self._events.emit(
            name=InterpreterEvents.EXTERNAL_CALL_REQUESTED,
            payload={
                "run_id": run_id,
                "interpreter_session_id": session_id,
                "invocation_index": call.invocation_index,
                "alias": call.alias,
                "tool_name": spec.tool_name if spec else None,
                "snapshot_bytes": call.snapshot.size,
            },
        )
        if spec is None:
            # The adapter already fails undeclared aliases closed; this guards
            # the resolver path too.
            return PolicyToolInvocationOutcome(
                status=PolicyToolInvocationOutcome.DENIED,
                invocation_id=uuid4().hex,
                error_code=InterpreterErrorCode.EXTERNAL_FUNCTION_UNKNOWN,
                safe_message="external function is not available",
            )
        await self._events.emit(
            name=InterpreterEvents.SUSPENDED_FOR_APPROVAL,
            payload={
                "run_id": run_id,
                "interpreter_session_id": session_id,
                "invocation_index": call.invocation_index,
                "tool_name": spec.tool_name,
            },
        )
        context = PolicyInvocationContext(
            run_id=run_id,
            interpreter_session_id=session_id,
            org_id=org_id,
            user_id=user_id,
            spec=spec,
        )
        return await self._policy.invoke(call=call, context=context)

    # -- terminalisation ---------------------------------------------------

    async def _terminalise(
        self,
        session_id: str,
        run_id: str,
        step: InterpreterStep,
        limits: InterpreterLimits,
    ) -> InterpreterCompleted | InterpreterFailed:
        """Emit the terminal event and offload an oversized result."""

        if isinstance(step, InterpreterFailed):
            return await self._terminal_failure(session_id, run_id, step)
        if isinstance(step, InterpreterCompleted):
            completed = self._offload_result(step, limits)
            await self._events.emit(
                name=InterpreterEvents.COMPLETED,
                payload={
                    "run_id": run_id,
                    "interpreter_session_id": session_id,
                    "external_calls": len(completed.external_invocation_ids),
                    "result_offloaded": completed.payload_ref is not None,
                },
            )
            return completed
        # Defensive: an ExternalFunctionCall must never reach here.
        return await self._terminal_failure(
            session_id,
            run_id,
            InterpreterError(
                InterpreterErrorCode.INTERPRETER_CRASH,
                "the interpreter did not reach a terminal state",
            ).as_failed(),
        )

    async def _terminal_failure(
        self, session_id: str, run_id: str, failed: InterpreterFailed
    ) -> InterpreterFailed:
        """Emit the appropriate failed/limit event for a terminal failure."""

        if failed.limit_kind is not None:
            await self._events.emit(
                name=InterpreterEvents.LIMIT_EXCEEDED,
                payload={
                    "run_id": run_id,
                    "interpreter_session_id": session_id,
                    "limit_kind": failed.limit_kind.value,
                },
            )
        await self._events.emit(
            name=InterpreterEvents.FAILED,
            payload={
                "run_id": run_id,
                "interpreter_session_id": session_id,
                "code": failed.code.value,
            },
        )
        return failed

    def _offload_result(
        self, completed: InterpreterCompleted, limits: InterpreterLimits
    ) -> InterpreterCompleted:
        """Offload a result larger than the inline ceiling to the blob store.

        Small results stay inline. A large result, when a store is available, is
        parked as content-addressed bytes and replaced by a bounded preview plus
        a reference; without a store it is bounded to a safe preview string.
        """

        encoded = json.dumps(completed.result).encode("utf-8")
        if len(encoded) <= limits.max_result_bytes:
            return completed
        preview = encoded[: limits.max_result_bytes].decode("utf-8", errors="ignore")
        if self._result_store is None:
            return completed.model_copy(update={"result": preview, "payload_ref": None})
        ref = self._result_store.put(encoded, media_type="application/json")
        from agent_runtime.capabilities.interpreter.contracts import SnapshotRef

        payload_ref = SnapshotRef(
            sha256=ref.sha256,
            size=ref.size,
            adapter="monty-result",
            abi_version="1",
            source_sha256="0" * 64,
            limit_profile_hash="result",
            invocation_index=0,
        )
        return completed.model_copy(
            update={"result": preview, "payload_ref": payload_ref}
        )

    # -- guards ------------------------------------------------------------

    @staticmethod
    def _enforce_external_ceiling(count: int, limits: InterpreterLimits) -> None:
        """Fail closed once the external-call ceiling is exceeded."""

        if count > limits.max_external_calls:
            raise InterpreterError(
                InterpreterErrorCode.RESOURCE_LIMIT_EXCEEDED,
                "the interpreted program exceeded the external-call limit",
                limit_kind=InterpreterLimitKind.EXTERNAL_CALLS,
            )

    @staticmethod
    def _enforce_total_time(started: float, limits: InterpreterLimits) -> None:
        """Fail closed once the total wall-time budget is exhausted.

        Measured with a monotonic host clock; interpreted code has no clock.
        """

        elapsed_ms = (time.monotonic() - started) * 1000.0
        if elapsed_ms > limits.total_timeout_ms:
            raise InterpreterError(
                InterpreterErrorCode.RESOURCE_LIMIT_EXCEEDED,
                "the interpreted program exceeded its total time budget",
                limit_kind=InterpreterLimitKind.WALL_TIME,
            )

    async def _safe_cancel(self, session_id: str) -> None:
        """Best-effort cancel; never masks the original failure."""

        try:
            await self._port.cancel(interpreter_session_id=session_id)
        except Exception:  # noqa: BLE001 - cancel is best-effort
            pass

    def _resolve_aliases(
        self, aliases: tuple[str, ...]
    ) -> tuple[ExternalFunctionSpec, ...]:
        """Resolve declared aliases; reject duplicates and unknowns closed."""

        seen: set[str] = set()
        resolved: list[ExternalFunctionSpec] = []
        for alias in aliases:
            if alias in seen:
                raise InterpreterError(
                    InterpreterErrorCode.EXTERNAL_FUNCTION_UNKNOWN,
                    "a requested external function was declared more than once",
                )
            seen.add(alias)
            spec = self._resolver.resolve(alias)
            if spec is None:
                raise InterpreterError(
                    InterpreterErrorCode.EXTERNAL_FUNCTION_UNKNOWN,
                    "a requested external function is not available",
                )
            resolved.append(spec)
        return tuple(resolved)


__all__ = (
    "ExternalFunctionResolver",
    "InterpreterEvents",
    "InterpreterService",
    "InterpreterServiceConfig",
)
