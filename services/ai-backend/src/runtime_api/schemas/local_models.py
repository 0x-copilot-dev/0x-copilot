"""Local-models (Ollama) API schemas — Round 2.

Wire shapes for ``/v1/local-models/*``. Mirrored by
``packages/api-types/src/localModels.ts``. Kept deliberately small: the
source of truth for installed models is Ollama itself, so these carry only
what the settings UI + model picker render.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import Field

from agent_runtime.execution.contracts import RuntimeContract

# Where a loaded model actually runs, derived from Ollama ``/api/ps``
# (``size`` vs ``size_vram``). ``None`` = not currently loaded.
RunPlacement = Literal["gpu", "cpu", "partial"]


class LocalRuntimeState(StrEnum):
    """PRD-P8 §4.1 — what this server can honestly say about the local runtime.

    Derived server-side only (§4.2). ``UNKNOWN`` is the honest answer whenever
    the daemon is silent *and* this deployment is not allowed to inspect the
    host filesystem (containerised self-host, web).
    """

    UNKNOWN = "unknown"  # cannot determine (remote/containerised)
    NOT_INSTALLED = "not_installed"  # binary absent on this machine
    STOPPED = "stopped"  # binary present, daemon not answering
    RUNNING = "running"


class LocalModelErrorKind(StrEnum):
    """PRD-P8 §4.1 — how a local-model failure should be recovered from.

    Drives the client's retry policy (D1: no red terminal state). Classified
    server-side from transport/protocol signals; the daemon's own response
    text is untrusted and never travels with it.
    """

    RUNTIME_UNREACHABLE = "runtime_unreachable"  # daemon died / refused
    TRANSIENT = "transient"  # network blip, stream break
    TERMINAL = "terminal"  # 4xx, disk full, bad repo


class LocalModelsStatus(RuntimeContract):
    """Capability probe: is the feature enabled + is Ollama reachable.

    ``ollama_running`` / ``ollama_version`` are the Round-2 fields and stay
    populated exactly as before — five client call sites still read them.
    ``runtime_state`` / ``runtime_managed`` are PRD-P8 additive fields
    (D3: every new field optional, every consumer tolerates its absence).
    """

    enabled: bool
    ollama_running: bool
    ollama_version: str | None = None
    runtime_state: LocalRuntimeState = LocalRuntimeState.UNKNOWN
    # True only when this server may start/restart the runtime itself
    # (``RUNTIME_LOCAL_MODELS_MANAGE_RUNTIME`` + the feature flag).
    runtime_managed: bool = False


class LocalModelSummary(RuntimeContract):
    """One installed local model (Ollama tag), with its live placement."""

    name: str
    size_bytes: int = Field(ge=0)
    quantization: str | None = None
    parameter_size: str | None = None
    run_placement: RunPlacement | None = None


class LocalModelsList(RuntimeContract):
    models: list[LocalModelSummary]


class LocalModelSize(RuntimeContract):
    """Pre-download size heads-up for one HF GGUF (repo + quant)."""

    repo: str
    quant: str
    filename: str
    size_bytes: int = Field(ge=0)


class PullLocalModelRequest(RuntimeContract):
    """Body for ``POST /v1/local-models/pull``."""

    repo: str = Field(min_length=1, max_length=200)
    quant: str = Field(min_length=1, max_length=64)


class LocalModelPullEvent(RuntimeContract):
    """One SSE frame of pull progress. ``bytes_*`` are present only on
    download lines; ``speed_bps``/``eta_seconds`` are computed server-side."""

    sequence_no: int = Field(ge=1)
    status: str
    bytes_total: int | None = Field(default=None, ge=0)
    bytes_completed: int | None = Field(default=None, ge=0)
    speed_bps: float | None = Field(default=None, ge=0)
    eta_seconds: float | None = Field(default=None, ge=0)
    done: bool = False
    error: str | None = None
    # Present only on a terminal error frame. Tells the client whether to
    # auto-resume (transient / runtime_unreachable) or stop and ask (terminal).
    error_kind: LocalModelErrorKind | None = None


__all__ = [
    "LocalModelErrorKind",
    "LocalModelPullEvent",
    "LocalModelSize",
    "LocalModelSummary",
    "LocalModelsList",
    "LocalModelsStatus",
    "LocalRuntimeState",
    "PullLocalModelRequest",
    "RunPlacement",
]
