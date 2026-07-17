"""Local-model orchestration over Ollama + Hugging Face.

Composes :class:`OllamaClient` and :class:`HfGgufResolver` into the typed
operations the routes expose: status, list (with GPU/CPU placement), a
pre-download size lookup, a progress-annotated pull stream (speed + ETA
computed here), and delete. Dependency-inverted for tests (inject fakes).
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Callable, Mapping
from typing import Any

from runtime_api.local_models.hf_metadata import HfGgufResolver
from runtime_api.local_models.ollama_client import OllamaClient
from runtime_api.schemas.local_models import (
    LocalModelPullEvent,
    LocalModelSize,
    LocalModelSummary,
    LocalModelsList,
    LocalModelsStatus,
    RunPlacement,
)

_SUCCESS_STATUS = "success"


class LocalModelService:
    """High-level local-model operations consumed by the HTTP routes."""

    def __init__(
        self,
        *,
        ollama: OllamaClient,
        hf: HfGgufResolver,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ollama = ollama
        self._hf = hf
        self._clock = clock

    async def status(self, *, enabled: bool) -> LocalModelsStatus:
        # Only probe Ollama when the feature is on — a disabled deployment
        # should never reach out.
        version = await self._ollama.running_version() if enabled else None
        return LocalModelsStatus(
            enabled=enabled,
            ollama_running=version is not None,
            ollama_version=version,
        )

    async def list_models(self) -> LocalModelsList:
        tags = await self._ollama.list_tags()
        placements = {
            str(row.get("name", "")): self._placement(row)
            for row in await self._ollama.list_running()
        }
        models = [
            LocalModelSummary(
                name=str(tag.get("name", "")),
                size_bytes=self._int(tag.get("size")),
                quantization=self._detail(tag, "quantization_level"),
                parameter_size=self._detail(tag, "parameter_size"),
                run_placement=placements.get(str(tag.get("name", ""))),
            )
            for tag in tags
            if tag.get("name")
        ]
        return LocalModelsList(models=models)

    async def download_size(self, *, repo: str, quant: str) -> LocalModelSize:
        return await self._hf.size(repo=repo, quant=quant)

    async def pull_events(
        self, *, repo: str, quant: str
    ) -> AsyncIterator[LocalModelPullEvent]:
        """Yield typed, progress-annotated frames for an HF GGUF pull.

        Ollama pulls a Hugging Face GGUF directly via the ``hf.co/{repo}:{quant}``
        model name. ``speed_bps``/``eta_seconds`` are computed from the byte
        deltas so the client renders them without deriving anything.
        """

        model = f"hf.co/{repo}:{quant}"
        sequence = 0
        last_completed: int | None = None
        last_time: float | None = None
        async for frame in self._ollama.pull(model):
            sequence += 1
            status = str(frame.get("status", ""))
            total = self._opt_int(frame.get("total"))
            completed = self._opt_int(frame.get("completed"))
            speed, eta = self._rate(completed, total, last_completed, last_time)
            if completed is not None:
                last_completed, last_time = completed, self._clock()
            yield LocalModelPullEvent(
                sequence_no=sequence,
                status=status,
                bytes_total=total,
                bytes_completed=completed,
                speed_bps=speed,
                eta_seconds=eta,
                done=status == _SUCCESS_STATUS,
            )

    async def delete(self, *, name: str) -> bool:
        return await self._ollama.delete(name)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _rate(
        self,
        completed: int | None,
        total: int | None,
        last_completed: int | None,
        last_time: float | None,
    ) -> tuple[float | None, float | None]:
        if completed is None or last_completed is None or last_time is None:
            return None, None
        elapsed = self._clock() - last_time
        delta = completed - last_completed
        if elapsed <= 0 or delta <= 0:
            return None, None
        speed = delta / elapsed
        eta = (
            (total - completed) / speed
            if total is not None and total > completed
            else 0.0
        )
        return speed, eta

    @staticmethod
    def _placement(row: Mapping[str, Any]) -> RunPlacement | None:
        size = LocalModelService._int(row.get("size"))
        vram = LocalModelService._int(row.get("size_vram"))
        if size <= 0:
            return None
        if vram >= size:
            return "gpu"
        if vram <= 0:
            return "cpu"
        return "partial"

    @staticmethod
    def _detail(tag: Mapping[str, Any], key: str) -> str | None:
        details = tag.get("details")
        if isinstance(details, Mapping):
            value = details.get(key)
            if value:
                return str(value)
        return None

    @staticmethod
    def _int(value: Any) -> int:
        return int(value) if isinstance(value, int) else 0

    @staticmethod
    def _opt_int(value: Any) -> int | None:
        return int(value) if isinstance(value, int) else None


__all__ = ["LocalModelService"]
