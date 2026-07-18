"""Per-run construction of the gated Wave-1 capability tools.

Two model-visible tools are built here, each OFF by default and each returning
``None`` unless its server-side gate holds. When both return ``None`` the runtime
is byte-identical to today:

* **Monty code mode** (``run_code_mode``) — gated by
  :class:`MontyCodeModeConfig` (``RUNTIME_ENABLE_MONTY`` + ``single_user_desktop``
  + ``RUNTIME_INTERPRETER_PROVIDER=monty`` + the ``pydantic_monty`` package). Wired
  in **pure-compute posture** (see :mod:`agent_runtime.capabilities.interpreter.pure_compute`):
  calculation / transformation only, with a resolver that authorizes no external
  tool, until the direct-path tool-policy engine lands. The snapshot + result
  stores are the desktop file object store.
* **Remote sandbox execute** (``run_in_sandbox``) — gated by
  ``RUNTIME_ENABLE_REMOTE_SANDBOX`` + a configured provider (via
  :func:`build_sandbox_backend`) **and** ``single_user_desktop``. A dedicated
  execute-only tool, NOT the deepagents composite default backend, so local
  filesystem / ``/memories/`` / ``/skills/`` stay untouched.

Kept in its own module (mirroring
:class:`runtime_worker.workspace_backend_wiring.WorkspaceBackendWorkerWiring`) so
the run path constructs these exactly once per run without leaking desktop-only
concerns into the handler. The capability packages are imported lazily so they
never load on non-desktop images.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping

from agent_runtime.execution.contracts import AgentRuntimeContext

logger = logging.getLogger(__name__)

_DESKTOP_PROFILE = "single_user_desktop"
_DEPLOYMENT_PROFILE_ENV = "ENTERPRISE_DEPLOYMENT_PROFILE"


class _ContextBudgetGuard:
    """Bridges an interpreter external call to the run's active tool budget.

    Reuses the *same* per-run :class:`~agent_runtime.capabilities.tool_budget_guard.
    ToolBudgetGuard` an ordinary tool call is charged against, so a bridged call
    is not a budget-free back door. Admits when no guard is bound (parity with
    ``ToolBudgetGuardedTool``'s ``guard is None`` path — non-desktop / eval runs
    install no guard) and denies on a hard :class:`ToolBudgetReject`.
    """

    async def charge(self, *, tool_name, arguments, context) -> bool:  # noqa: ANN001
        del context
        from agent_runtime.capabilities.tool_budget_guard import (  # noqa: PLC0415
            ToolBudgetGuard,
        )
        from agent_runtime.capabilities.tool_budget_middleware import (  # noqa: PLC0415
            ToolBudgetAdmit,
            ToolBudgetWarn,
        )

        guard = ToolBudgetGuard.active()
        if guard is None:
            return True
        estimated = _estimate_input_tokens(arguments)
        decision = guard.check_admit(
            tool_name=tool_name, estimated_input_tokens=estimated
        )
        if isinstance(decision, ToolBudgetWarn):
            await guard.emit_warning(decision=decision)
        return isinstance(decision, (ToolBudgetAdmit, ToolBudgetWarn))


def _estimate_input_tokens(arguments: Mapping[str, object]) -> int:
    """Cheap char-count estimate for the external call's arguments (1 tok ~= 4 chars)."""

    import json  # noqa: PLC0415

    try:
        payload = json.dumps(arguments, default=str)
    except (TypeError, ValueError):
        payload = repr(arguments)
    return min(len(payload) // 4, 100_000)


class CapabilityToolWiring:
    """Gate + builder for the per-run Monty and sandbox model tools.

    ``runtime_context`` supplies the trusted run identity threaded into each
    tool; ``file_store`` is the active file store (``None`` off the file
    backend) whose content-addressed object store backs Monty's snapshot/result
    stores; ``env`` defaults to ``os.environ`` and is injectable for tests.

    ``external_tools_by_name`` is the run's already scope-filtered, model-visible
    toolset keyed by tool name. When supplied (non-empty), Monty code mode is
    wired in **Option B** — interpreted code can make real external calls under
    budget + HITL approval, dispatched to these tools. When ``None`` / empty (the
    default), Monty stays **pure-compute** (Option A): the resolver authorizes
    nothing and no external tool is reachable.
    """

    def __init__(
        self,
        *,
        runtime_context: AgentRuntimeContext,
        file_store: object | None = None,
        env: Mapping[str, str] | None = None,
        external_tools_by_name: Mapping[str, object] | None = None,
    ) -> None:
        self._runtime_context = runtime_context
        self._file_store = file_store
        self._env = env
        self._external_tools_by_name = dict(external_tools_by_name or {})

    # -- Monty code mode ---------------------------------------------------

    def code_mode_tool(self) -> object | None:
        """Build the gated ``run_code_mode`` tool, or ``None`` when gated off.

        Returns ``None`` unless every Monty gate holds AND the file object store
        (snapshot backing) is available.

        Posture depends on whether an external toolset was supplied:

        * **Option B** (a non-empty ``external_tools_by_name``) — interpreted
          code can make real external calls. Each is routed through the
          production :class:`HitlPolicyToolInvoker`: budget, then HITL approval
          via the LangGraph interrupt seam, then dispatch to the authorized
          tool. Nothing bypasses the normal approval/budget path.
        * **Option A / pure-compute** (the default) — the resolver authorizes no
          external function, so interpreted code has no tool surface and the
          normal approval path is untouched.
        """

        from agent_runtime.capabilities.interpreter import (  # noqa: PLC0415
            MontyCodeModeConfig,
            build_code_mode_tool,
            build_monty_interpreter,
            build_snapshot_store,
        )

        config = MontyCodeModeConfig.from_env(self._env_dict())
        if not config.enabled:
            return None
        object_store = self._object_store()
        if object_store is None:
            # Gates are on but there is no durable snapshot store (non-file
            # backend). Fail soft — code mode stays absent rather than crash.
            logger.debug("code_mode.object_store_absent")
            return None

        port = build_monty_interpreter(
            config, snapshot_store=build_snapshot_store(object_store)
        )
        if port is None:
            return None
        policy_invoker, resolver = self._code_mode_policy()
        return build_code_mode_tool(
            port=port,
            policy_invoker=policy_invoker,
            resolver=resolver,
            identity_provider=self._code_mode_identity,
            config=config,
            result_store=object_store,
        )

    def _code_mode_policy(self) -> tuple[object, object]:
        """Select the invoker + resolver pair for code mode.

        Option B when a real toolset is available (real external calls under
        budget + HITL approval), else the fail-closed pure-compute pair.
        """

        if self._external_tools_by_name:
            from agent_runtime.capabilities.interpreter.policy_invoker import (  # noqa: PLC0415
                AuthorizedToolResolver,
                HitlPolicyToolInvoker,
                InterruptApprovalGate,
                LangChainToolDispatcher,
            )

            invoker = HitlPolicyToolInvoker(
                budget=_ContextBudgetGuard(),
                approval=InterruptApprovalGate(),
                dispatcher=LangChainToolDispatcher(self._external_tools_by_name),
            )
            resolver = AuthorizedToolResolver(self._external_tools_by_name)
            return invoker, resolver
        from agent_runtime.capabilities.interpreter.pure_compute import (  # noqa: PLC0415
            ClosedPolicyInvoker,
            PureComputeResolver,
        )

        return ClosedPolicyInvoker(), PureComputeResolver()

    def _code_mode_identity(self) -> object:
        from agent_runtime.capabilities.interpreter.code_mode_tool import (  # noqa: PLC0415
            RunIdentity,
        )

        ctx = self._runtime_context
        return RunIdentity(run_id=ctx.run_id, org_id=ctx.org_id, user_id=ctx.user_id)

    # -- Remote sandbox execute -------------------------------------------

    def sandbox_execute_tool(self) -> object | None:
        """Build the gated ``run_in_sandbox`` tool, or ``None`` when gated off.

        Returns ``None`` unless ``RUNTIME_ENABLE_REMOTE_SANDBOX`` + a configured
        provider are active AND the deployment profile is ``single_user_desktop``.
        """

        if not self._is_desktop():
            return None
        from agent_runtime.capabilities.sandbox import (  # noqa: PLC0415
            RemoteSandboxConfig,
            build_sandbox_backend,
        )

        config = RemoteSandboxConfig.from_env(self._env)
        service = build_sandbox_backend(config)
        if service is None:
            return None
        from agent_runtime.capabilities.sandbox.execute_tool import (  # noqa: PLC0415
            SandboxExecuteToolFactory,
        )

        return SandboxExecuteToolFactory.build(
            service=service,
            identity_provider=self._sandbox_identity,
            config=config,
        )

    def _sandbox_identity(self) -> object:
        from agent_runtime.capabilities.sandbox.execute_tool import (  # noqa: PLC0415
            SandboxRunIdentity,
        )

        ctx = self._runtime_context
        return SandboxRunIdentity(
            run_id=ctx.run_id, org_id=ctx.org_id, user_id=ctx.user_id
        )

    # -- helpers -----------------------------------------------------------

    def _object_store(self) -> object | None:
        """Return the file store's content-addressed object store, or ``None``."""

        if self._file_store is None:
            return None
        return getattr(self._file_store, "object_store", None)

    def _is_desktop(self) -> bool:
        return self._env_dict().get(_DEPLOYMENT_PROFILE_ENV, "") == _DESKTOP_PROFILE

    def _env_dict(self) -> dict[str, str]:
        if self._env is not None:
            return dict(self._env)
        import os  # noqa: PLC0415

        return dict(os.environ)


__all__ = ("CapabilityToolWiring",)
