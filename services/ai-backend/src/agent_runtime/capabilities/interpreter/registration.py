"""Registration seam for AC6 code mode.

`execution/factory.py` wires this in a later PR (kept separate to avoid a merge
conflict with a parallel factory edit). The contract is:

    port = build_monty_interpreter(config)
    if port is not None:
        tool = build_code_mode_tool(port=port, ...)
        # add `tool` to the model-visible tool list

`build_monty_interpreter` returns ``None`` — and the tool is therefore **absent
from the model-visible tool list** — unless *every* server-side gate is
satisfied (PRD "Configuration"):

* ``RUNTIME_ENABLE_MONTY`` is true;
* ``ENTERPRISE_DEPLOYMENT_PROFILE == single_user_desktop``;
* ``RUNTIME_INTERPRETER_PROVIDER == monty``;
* the ``pydantic_monty`` package is importable.

A renderer flag can never enable it; all gates are server-side. When the seam
returns ``None`` the runtime is byte-for-byte unchanged — normal sequential tool
calling continues.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from agent_runtime.capabilities.interpreter.code_mode_tool import (
    CodeModeToolFactory,
    RunIdentityProvider,
)
from agent_runtime.capabilities.interpreter.monty_adapter import MontyInterpreterPort
from agent_runtime.capabilities.interpreter.ports import (
    InterpreterEventSink,
    InterpreterPort,
    InterpreterSnapshotStore,
    PolicyToolInvoker,
)
from agent_runtime.capabilities.interpreter.service import (
    ExternalFunctionResolver,
    InterpreterService,
    InterpreterServiceConfig,
)
from agent_runtime.capabilities.interpreter.snapshot_store import (
    ContentAddressedBlobStore,
    ObjectStoreSnapshotStore,
)


@dataclass(frozen=True)
class MontyCodeModeConfig:
    """Resolved server-side gate state for code mode.

    Build from the environment with :meth:`from_env`, or construct directly in
    tests. ``enabled`` is the single boolean the factory checks.
    """

    runtime_enable_monty: bool = False
    deployment_profile: str = ""
    interpreter_provider: str = "monty"
    limit_profile_name: str = "desktop_v1"

    _DESKTOP_PROFILE = "single_user_desktop"

    @property
    def enabled(self) -> bool:
        """Whether all non-library gates pass (library availability is separate)."""

        return (
            self.runtime_enable_monty
            and self.deployment_profile == self._DESKTOP_PROFILE
            and self.interpreter_provider == "monty"
        )

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "MontyCodeModeConfig":
        """Read the four gate variables from the environment (default OFF)."""

        source = env if env is not None else dict(os.environ)
        return cls(
            runtime_enable_monty=cls._truthy(source.get("RUNTIME_ENABLE_MONTY")),
            deployment_profile=source.get("ENTERPRISE_DEPLOYMENT_PROFILE", ""),
            interpreter_provider=source.get("RUNTIME_INTERPRETER_PROVIDER", "monty"),
            limit_profile_name=source.get("RUNTIME_MONTY_LIMIT_PROFILE", "desktop_v1"),
        )

    @staticmethod
    def _truthy(value: str | None) -> bool:
        return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def build_monty_interpreter(
    config: MontyCodeModeConfig,
    *,
    snapshot_store: InterpreterSnapshotStore,
) -> InterpreterPort | None:
    """Return a Monty :class:`InterpreterPort`, or ``None`` when gated off.

    ``None`` means the ``run_code_mode`` tool must not be registered. This is the
    seam ``execution/factory.py`` calls; keeping it here lets factory wiring land
    without touching this subtree.
    """

    if not config.enabled:
        return None
    if not MontyInterpreterPort.is_available():
        # Gate on the package too: an enabled flag with no library is a no-go
        # (PRD spike "No-go: keep the tool unregistered"), not a hard crash.
        return None
    return MontyInterpreterPort(snapshot_store=snapshot_store)


def build_snapshot_store(
    blob_store: ContentAddressedBlobStore,
) -> ObjectStoreSnapshotStore:
    """Wrap a content-addressed blob store (AC4 object store) for snapshots."""

    return ObjectStoreSnapshotStore(blob_store)


def build_code_mode_tool(
    *,
    port: InterpreterPort,
    policy_invoker: PolicyToolInvoker,
    resolver: ExternalFunctionResolver,
    identity_provider: RunIdentityProvider,
    config: MontyCodeModeConfig,
    event_sink: InterpreterEventSink | None = None,
    result_store: ContentAddressedBlobStore | None = None,
) -> object:
    """Assemble the model-visible ``run_code_mode`` tool over a live port.

    Returns a LangChain ``StructuredTool``. Only call this when
    :func:`build_monty_interpreter` returned a non-``None`` port.
    """

    service = InterpreterService(
        port=port,
        policy_invoker=policy_invoker,
        resolver=resolver,
        config=InterpreterServiceConfig(limit_profile_name=config.limit_profile_name),
        event_sink=event_sink,
        result_store=result_store,
    )
    return CodeModeToolFactory.build(
        service=service, identity_provider=identity_provider
    )


__all__ = (
    "MontyCodeModeConfig",
    "build_code_mode_tool",
    "build_monty_interpreter",
    "build_snapshot_store",
)
