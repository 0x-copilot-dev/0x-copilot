"""Local-models (Ollama) API schemas — Round 2.

Wire shapes for ``/v1/local-models/*``. Mirrored by
``packages/api-types/src/localModels.ts``. Kept deliberately small: the
source of truth for installed models is Ollama itself, so these carry only
what the settings UI + model picker render.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from agent_runtime.execution.contracts import RuntimeContract

# Where a loaded model actually runs, derived from Ollama ``/api/ps``
# (``size`` vs ``size_vram``). ``None`` = not currently loaded.
RunPlacement = Literal["gpu", "cpu", "partial"]


class LocalModelsStatus(RuntimeContract):
    """Capability probe: is the feature enabled + is Ollama reachable."""

    enabled: bool
    ollama_running: bool
    ollama_version: str | None = None


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


__all__ = [
    "LocalModelPullEvent",
    "LocalModelSize",
    "LocalModelSummary",
    "LocalModelsList",
    "LocalModelsStatus",
    "PullLocalModelRequest",
    "RunPlacement",
]
