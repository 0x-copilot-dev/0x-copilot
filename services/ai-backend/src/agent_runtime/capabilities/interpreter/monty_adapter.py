"""Pydantic Monty adapter for :class:`InterpreterPort`.

This is the **only** module that imports ``pydantic_monty`` (lazily, so the
package can be absent when the feature is off). It converts between Monty's
iterative ``start`` / ``FunctionSnapshot.resume`` API and the product's typed
contracts, and nothing else in the runtime learns Monty exists.

Design notes proven by an API spike against ``pydantic-monty==0.0.18``:

* ``Monty(code).start(...)`` returns either a ``MontyComplete`` (terminal) or a
  ``FunctionSnapshot`` — Monty yields to the host at *every* external function
  call. That per-call yield is exactly the interrupt seam the PRD says QuickJS
  PTC lacks, so approval/budget run **per external call**, not once per program.
* ``FunctionSnapshot.dump()`` / ``load_snapshot(bytes)`` round-trip the RAM-only
  state, so a suspended session survives a worker restart.
* ``resume({"return_value": ...})`` continues; ``resume({"exc_type": ...,
  "message": ...})`` surfaces a typed exception into interpreted code — how a
  denied/rejected external call is reported without pretending the tool ran.
* ``ResourceLimits`` enforces duration / memory / allocations / recursion
  pre-emptively; ``open`` / ``eval`` / ``exec`` / ``__import__`` are all denied
  with no host ``os`` supplied.
* ``start`` / ``resume`` are **synchronous** and CPU-bound, so every call is run
  via ``asyncio.to_thread`` to keep the worker event loop responsive.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from agent_runtime.capabilities.interpreter.contracts import (
    ExternalFunctionCall,
    InterpreterCompleted,
    InterpreterError,
    InterpreterErrorCode,
    InterpreterFailed,
    InterpreterLimitKind,
    InterpreterLimits,
    InterpreterRequest,
    InterpreterStep,
)
from agent_runtime.capabilities.interpreter.ports import (
    InterpreterSnapshotStore,
    PolicyToolInvocationOutcome,
)
from agent_runtime.capabilities.interpreter.snapshot_store import (
    ObjectStoreSnapshotStore,
)
from agent_runtime.execution.contracts import JsonValue


class _MontyErrorClassifier:
    """Maps a Monty exception to a stable, redaction-safe interpreter error.

    Only the exception *shape* (limit vs syntax vs host-access) is used — never
    the raw message, which can echo interpreted values.
    """

    _LIMIT_MARKERS = (
        ("time limit", InterpreterLimitKind.WALL_TIME),
        ("timeouterror", InterpreterLimitKind.WALL_TIME),
        ("memory limit", InterpreterLimitKind.HEAP_BYTES),
        ("memoryerror", InterpreterLimitKind.HEAP_BYTES),
        ("allocation", InterpreterLimitKind.ALLOCATIONS),
        ("recursion", InterpreterLimitKind.RECURSION_DEPTH),
    )

    _HOST_MARKERS = (
        "not implemented",
        "os function",
        "name '__import__'",
        "name 'eval'",
        "name 'exec'",
        "name 'open'",
        "name 'compile'",
    )

    @classmethod
    def classify(cls, exc: BaseException) -> InterpreterError:
        """Return the typed :class:`InterpreterError` for ``exc``."""

        text = str(exc).lower()
        type_name = type(exc).__name__
        if type_name == "MontySyntaxError":
            return InterpreterError(
                InterpreterErrorCode.INVALID_SOURCE,
                "the interpreted program failed to parse",
            )
        for marker, kind in cls._LIMIT_MARKERS:
            if marker in text:
                return InterpreterError(
                    InterpreterErrorCode.RESOURCE_LIMIT_EXCEEDED,
                    "the interpreted program exceeded a resource limit",
                    limit_kind=kind,
                )
        for marker in cls._HOST_MARKERS:
            if marker in text:
                return InterpreterError(
                    InterpreterErrorCode.UNSUPPORTED_LANGUAGE_FEATURE,
                    "the interpreted program used an unsupported or host feature",
                )
        # Residual bucket: an unhandled exception raised by interpreted code.
        # We do not surface its message (may contain interpreted values); the
        # model can retry with ordinary tools. This is intentionally coarse for
        # an experimental, gated feature.
        return InterpreterError(
            InterpreterErrorCode.UNSUPPORTED_LANGUAGE_FEATURE,
            "the interpreted program raised an unhandled error",
        )


@dataclass
class _Session:
    """RAM-only state for one suspended interpreter session.

    Holds the live Monty snapshot object (fast-path resume) plus everything
    needed to rebuild it from persisted bytes after a worker restart.
    """

    source_sha256: str
    limit_profile_hash: str
    monty_limits: Any
    output_collector: Any
    pending_snapshot: Any | None = None
    invocation_index: int = 0
    external_invocation_ids: list[str] = field(default_factory=list)


class MontyInterpreterPort:
    """``InterpreterPort`` backed by Pydantic Monty."""

    ADAPTER_NAME = "monty"

    #: Names Monty surfaces as external calls in iterative mode but that are
    #: really host-capability escape hatches. Calling any of these fails closed
    #: as an unsupported feature rather than being routed to a tool.
    _HOST_BUILTINS = frozenset(
        {
            "eval",
            "exec",
            "compile",
            "__import__",
            "open",
            "globals",
            "locals",
            "vars",
            "input",
            "breakpoint",
            "memoryview",
        }
    )

    def __init__(self, snapshot_store: InterpreterSnapshotStore) -> None:
        self._snapshot_store = snapshot_store
        self._sessions: dict[str, _Session] = {}
        self._monty: Any = None  # lazily imported pydantic_monty module

    # -- lifecycle ---------------------------------------------------------

    async def start(self, request: InterpreterRequest) -> InterpreterStep:
        """Compile and run ``request.code`` until it yields, finishes, or fails."""

        monty = self._require_monty()
        try:
            self._check_code_size(request)
        except InterpreterError as exc:
            return exc.as_failed()
        source_sha256 = self._sha256(request.code.encode("utf-8"))
        limit_profile_hash = self._limit_hash(request.limits)
        monty_limits = self._to_monty_limits(monty, request.limits)
        collector = monty.CollectString()

        # Input names are declared at construction; values are supplied to
        # start(). Each key becomes a global name in the interpreted program.
        input_names = list(request.inputs.keys()) or None
        try:
            interpreter = monty.Monty(request.code, inputs=input_names)
        except monty.MontyError as exc:  # syntax/compile
            return _MontyErrorClassifier.classify(exc).as_failed()

        session = _Session(
            source_sha256=source_sha256,
            limit_profile_hash=limit_profile_hash,
            monty_limits=monty_limits,
            output_collector=collector,
        )
        self._sessions[request.interpreter_session_id] = session

        step_fn = lambda: interpreter.start(  # noqa: E731 - thin thread target
            inputs=dict(request.inputs) or None,
            limits=monty_limits,
            print_callback=collector,
        )
        return await self._advance(request.interpreter_session_id, request, step_fn)

    async def resume(
        self,
        *,
        call: ExternalFunctionCall,
        outcome: PolicyToolInvocationOutcome,
    ) -> InterpreterStep:
        """Continue a suspended session with the policy outcome for its call."""

        monty = self._require_monty()
        session = self._sessions.get(call.interpreter_session_id)
        snapshot = self._recover_snapshot(monty, session, call)
        # Record the invocation id so the completed step lists what ran.
        if session is not None:
            session.external_invocation_ids.append(outcome.invocation_id)

        result_arg = self._resume_argument(outcome)
        step_fn = lambda: snapshot.resume(result_arg)  # noqa: E731
        request = self._request_for(call, session)
        return await self._advance(call.interpreter_session_id, request, step_fn)

    async def cancel(self, *, interpreter_session_id: str) -> None:
        """Drop RAM state for a session. Idempotent."""

        self._sessions.pop(interpreter_session_id, None)

    # -- internal drive ----------------------------------------------------

    async def _advance(
        self,
        session_id: str,
        request: InterpreterRequest | None,
        step_fn: Any,
    ) -> InterpreterStep:
        """Run one synchronous Monty segment in a thread and map its outcome.

        Always returns an :class:`InterpreterStep`; every failure — a Monty
        error, a typed policy/limit error from suspend/complete, or an
        unexpected host exception — is converted to a terminal failure so the
        port never raises to the service.
        """

        monty = self._monty
        try:
            step = await asyncio.to_thread(step_fn)
            if isinstance(step, monty.MontyComplete):
                return self._complete(session_id, step)
            if isinstance(step, monty.FunctionSnapshot):
                return self._suspend(session_id, request, step)
            raise InterpreterError(
                InterpreterErrorCode.INTERPRETER_CRASH,
                "the interpreter returned an unexpected step",
            )
        except monty.MontyError as exc:
            return self._fail(session_id, _MontyErrorClassifier.classify(exc))
        except InterpreterError as exc:
            return self._fail(session_id, exc)
        except Exception:  # noqa: BLE001 - defensive: never leak a host traceback
            return self._fail(
                session_id,
                InterpreterError(
                    InterpreterErrorCode.INTERPRETER_CRASH,
                    "the interpreter failed unexpectedly",
                ),
            )

    def _complete(self, session_id: str, step: Any) -> InterpreterCompleted:
        """Build a terminal completed step and drop RAM state."""

        session = self._sessions.pop(session_id, None)
        stdout = self._stdout_preview(session)
        try:
            result: JsonValue = json.loads(step.output_json())
        except Exception as exc:  # noqa: BLE001 - non-JSON result
            raise InterpreterError(
                InterpreterErrorCode.RESULT_INVALID,
                "the interpreter result was not JSON-serialisable",
            ) from exc
        ids = tuple(session.external_invocation_ids) if session else ()
        return InterpreterCompleted(
            result=result,
            stdout_preview=stdout,
            external_invocation_ids=ids,
        )

    def _suspend(
        self, session_id: str, request: InterpreterRequest | None, snapshot: Any
    ) -> ExternalFunctionCall:
        """Persist the snapshot and emit an external-call step for the policy seam.

        An undeclared alias fails closed with ``external_function_unknown`` — the
        model may only call names it declared on the request.
        """

        session = self._sessions[session_id]
        session.pending_snapshot = snapshot
        alias = str(snapshot.function_name)
        # An attempt to use a host OS function or a dangerous builtin (which
        # Monty surfaces as an "external" call in iterative mode) is a host-access
        # attempt, never an external tool. Fail closed as unsupported.
        if bool(getattr(snapshot, "is_os_function", False)) or (
            alias in self._HOST_BUILTINS
        ):
            raise InterpreterError(
                InterpreterErrorCode.UNSUPPORTED_LANGUAGE_FEATURE,
                "the interpreted program used an unsupported or host feature",
            )
        allowed = self._allowed_aliases(request)
        if allowed is not None and alias not in allowed:
            raise InterpreterError(
                InterpreterErrorCode.EXTERNAL_FUNCTION_UNKNOWN,
                "the interpreted program called an undeclared external function",
            )

        arguments = self._arguments(snapshot)
        blob = bytes(snapshot.dump())
        max_snapshot = (
            request.limits.max_snapshot_bytes if request is not None else len(blob) + 1
        )
        ref = self._snapshot_store.put(
            blob,
            adapter=self.ADAPTER_NAME,
            abi_version=self._abi_version(),
            source_sha256=session.source_sha256,
            limit_profile_hash=session.limit_profile_hash,
            invocation_index=session.invocation_index,
            max_snapshot_bytes=max_snapshot,
        )
        call = ExternalFunctionCall(
            interpreter_session_id=session_id,
            invocation_index=session.invocation_index,
            alias=alias,
            arguments=arguments,
            snapshot=ref,
            source_sha256=session.source_sha256,
        )
        session.invocation_index += 1
        return call

    def _fail(self, session_id: str, error: InterpreterError) -> InterpreterFailed:
        """Terminalise a session with a typed failure and drop RAM state."""

        session = self._sessions.pop(session_id, None)
        return error.as_failed(stdout_preview=self._stdout_preview(session))

    # -- recovery / conversion --------------------------------------------

    def _recover_snapshot(
        self, monty: Any, session: _Session | None, call: ExternalFunctionCall
    ) -> Any:
        """Return the live snapshot, or rebuild it from persisted bytes.

        The RAM fast-path avoids a store round-trip; the cold path (worker
        restart) verifies envelope compatibility before decoding — an
        incompatible snapshot fails closed, never blind-loaded.
        """

        if session is not None and session.pending_snapshot is not None:
            return session.pending_snapshot
        # Cold recovery.
        ObjectStoreSnapshotStore.ensure_compatible(
            call.snapshot,
            adapter=self.ADAPTER_NAME,
            abi_version=self._abi_version(),
            source_sha256=call.source_sha256,
            limit_profile_hash=(
                session.limit_profile_hash
                if session
                else call.snapshot.limit_profile_hash
            ),
        )
        blob = self._snapshot_store.get(call.snapshot)
        return monty.load_snapshot(blob)

    @staticmethod
    def _resume_argument(outcome: PolicyToolInvocationOutcome) -> dict[str, Any]:
        """Translate a policy outcome into a Monty ``resume`` argument.

        ``allowed`` returns the value to interpreted code; anything else raises a
        typed exception inside the interpreter so the program can branch without
        the side effect having happened.
        """

        if outcome.status == PolicyToolInvocationOutcome.ALLOWED:
            return {"return_value": outcome.return_value}
        message = outcome.safe_message or (
            outcome.error_code.value if outcome.error_code else "external call denied"
        )
        return {"exc_type": "RuntimeError", "message": message}

    @staticmethod
    def _arguments(snapshot: Any) -> dict[str, JsonValue]:
        """Extract JSON-only args from a suspension.

        Uses Monty's ``*_json`` accessors so no host object identity leaks. Keyword
        args map directly; positional args are carried under ``args``.
        """

        kwargs: dict[str, JsonValue] = json.loads(snapshot.kwargs_json())
        positional: list[JsonValue] = json.loads(snapshot.args_json())
        if positional:
            return {"args": positional, **kwargs}
        return kwargs

    def _request_for(
        self, call: ExternalFunctionCall, session: _Session | None
    ) -> InterpreterRequest | None:
        """No request is rebuilt on resume; alias validation happened at suspend."""

        del call, session
        return None

    @staticmethod
    def _allowed_aliases(request: InterpreterRequest | None) -> frozenset[str] | None:
        """Return the declared alias set, or ``None`` when unknown (resume path)."""

        if request is None:
            return None
        return frozenset(spec.alias for spec in request.external_functions)

    def _stdout_preview(self, session: _Session | None) -> str:
        """Bounded stdout capture for a session (empty when none)."""

        if session is None:
            return ""
        raw = getattr(session.output_collector, "output", "") or ""
        return raw[:8192]

    # -- limits / hashing / lazy import -----------------------------------

    @staticmethod
    def _check_code_size(request: InterpreterRequest) -> None:
        """Reject oversized source before compiling it."""

        if len(request.code.encode("utf-8")) > request.limits.max_code_bytes:
            raise InterpreterError(
                InterpreterErrorCode.RESOURCE_LIMIT_EXCEEDED,
                "the interpreted program source exceeded the size limit",
                limit_kind=InterpreterLimitKind.CODE_BYTES,
            )

    def _to_monty_limits(self, monty: Any, limits: InterpreterLimits) -> Any:
        """Map product limits onto Monty ``ResourceLimits`` (per-segment)."""

        return monty.ResourceLimits(
            max_duration_secs=limits.segment_timeout_ms / 1000.0,
            max_memory=limits.max_heap_bytes,
            max_allocations=limits.max_allocations,
            max_recursion_depth=limits.max_recursion_depth,
        )

    @staticmethod
    def _sha256(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    def _limit_hash(self, limits: InterpreterLimits) -> str:
        return self._sha256(limits.model_dump_json().encode("utf-8"))

    def _abi_version(self) -> str:
        monty = self._require_monty()
        return str(getattr(monty, "__version__", "unknown"))

    def _require_monty(self) -> Any:
        """Import ``pydantic_monty`` lazily; a missing package is a typed error."""

        if self._monty is not None:
            return self._monty
        try:
            import pydantic_monty  # noqa: PLC0415 - intentional lazy, gated import
        except ImportError as exc:  # pragma: no cover - exercised via registration
            raise InterpreterError(
                InterpreterErrorCode.INTERPRETER_UNAVAILABLE,
                "the code interpreter is not available",
            ) from exc
        self._monty = pydantic_monty
        return pydantic_monty

    @staticmethod
    def is_available() -> bool:
        """Return whether the Monty package can be imported."""

        try:
            import pydantic_monty  # noqa: F401, PLC0415
        except ImportError:
            return False
        return True


__all__ = ("MontyInterpreterPort",)
