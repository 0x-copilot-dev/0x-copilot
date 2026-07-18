"""The model-facing ``run_code_mode`` tool.

A thin LangChain ``StructuredTool`` over :class:`InterpreterService`. It exposes
only :class:`RunCodeModeInput` to the model — no limits, adapter, identity, or
approval state — and returns a small JSON envelope. Run identity comes from
trusted context via an injected provider, never from the model.

The tool is only ever built by :mod:`.registration` when every server-side gate
is satisfied; when the feature is off this module is never imported at runtime.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass

from langchain_core.tools import StructuredTool

from agent_runtime.capabilities.interpreter.contracts import (
    InterpreterCompleted,
    RunCodeModeInput,
)
from agent_runtime.capabilities.interpreter.service import InterpreterService


@dataclass(frozen=True)
class RunIdentity:
    """Trusted run identity resolved from context for one tool invocation."""

    run_id: str
    org_id: str | None = None
    user_id: str | None = None


#: Supplies the current run identity. Wired to the run context at registration;
#: the model never influences it.
RunIdentityProvider = Callable[[], RunIdentity]

TOOL_NAME = "run_code_mode"
TOOL_DESCRIPTION = (
    "Run a small program in a sandboxed Python subset for calculations, "
    "transformations, branching, and repeated calls to approved tools. No "
    "filesystem, network, or imports. Declare any approved tool aliases you "
    "need in `external_functions`; each call is still subject to normal "
    "approval and budget. Returns the program's JSON result."
)


class CodeModeToolFactory:
    """Builds the ``run_code_mode`` StructuredTool bound to a service + identity."""

    @classmethod
    def build(
        cls,
        *,
        service: InterpreterService,
        identity_provider: RunIdentityProvider,
    ) -> StructuredTool:
        """Return a ``run_code_mode`` StructuredTool."""

        async def _run_code_mode(
            code: str,
            inputs: dict | None = None,
            external_functions: tuple[str, ...] = (),
        ) -> str:
            identity = identity_provider()
            model_input = RunCodeModeInput(
                code=code,
                inputs=inputs or {},
                external_functions=tuple(external_functions),
            )
            outcome = await service.run(
                model_input,
                run_id=identity.run_id,
                org_id=identity.org_id,
                user_id=identity.user_id,
            )
            return cls._render(outcome)

        return StructuredTool.from_function(
            coroutine=_run_code_mode,
            name=TOOL_NAME,
            description=TOOL_DESCRIPTION,
            args_schema=RunCodeModeInput,
        )

    @staticmethod
    def _render(outcome: object) -> str:
        """Render a terminal outcome as a compact, model-safe JSON envelope."""

        if isinstance(outcome, InterpreterCompleted):
            envelope = {
                "status": "completed",
                "result": outcome.result,
                "stdout": outcome.stdout_preview,
                "external_calls": list(outcome.external_invocation_ids),
                "result_offloaded": outcome.payload_ref is not None,
            }
        else:  # InterpreterFailed
            envelope = {
                "status": "failed",
                "error_code": outcome.code.value,  # type: ignore[attr-defined]
                "message": outcome.safe_message,  # type: ignore[attr-defined]
                "limit_kind": (
                    outcome.limit_kind.value  # type: ignore[attr-defined]
                    if outcome.limit_kind  # type: ignore[attr-defined]
                    else None
                ),
                "retryable": outcome.retryable,  # type: ignore[attr-defined]
                "stdout": outcome.stdout_preview,  # type: ignore[attr-defined]
            }
        return json.dumps(envelope)


__all__ = (
    "CodeModeToolFactory",
    "RunIdentity",
    "RunIdentityProvider",
    "TOOL_DESCRIPTION",
    "TOOL_NAME",
)
