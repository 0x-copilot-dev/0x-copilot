"""Unit tests for OllamaClient + HfGgufResolver + LocalModelService.

Uses httpx.MockTransport to fake the Ollama daemon and Hugging Face — no
network. Covers placement math, pull speed/ETA, HF size parsing, and error
degradation.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable

import httpx
import pytest

from runtime_api.local_models import HfGgufResolver, LocalModelError, OllamaClient
from runtime_api.local_models.service import LocalModelService


def _ollama(handler: Callable[[httpx.Request], httpx.Response]) -> OllamaClient:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return OllamaClient(base_url="http://localhost:11434", client=client)


def _hf(handler: Callable[[httpx.Request], httpx.Response]) -> HfGgufResolver:
    return HfGgufResolver(
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )


class TestOllamaClient:
    def test_api_root_strips_v1(self) -> None:
        assert (
            OllamaClient.api_root_from_openai_base("http://localhost:11434/v1")
            == "http://localhost:11434"
        )
        assert (
            OllamaClient.api_root_from_openai_base("http://host:11434/v1/")
            == "http://host:11434"
        )

    def test_running_version_none_when_unreachable(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(500)

        assert asyncio.run(_ollama(handler).running_version()) is None

    def test_delete_returns_false_on_404(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(404)

        assert asyncio.run(_ollama(handler).delete("missing:tag")) is False


class TestPlacement:
    @staticmethod
    def _ps_handler(size: int, vram: int) -> Callable[[httpx.Request], httpx.Response]:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/tags":
                return httpx.Response(
                    200, json={"models": [{"name": "m:tag", "size": size}]}
                )
            if request.url.path == "/api/ps":
                return httpx.Response(
                    200,
                    json={
                        "models": [{"name": "m:tag", "size": size, "size_vram": vram}]
                    },
                )
            return httpx.Response(404)

        return handler

    def _placement(self, size: int, vram: int) -> str | None:
        service = LocalModelService(
            ollama=_ollama(self._ps_handler(size, vram)),
            hf=_hf(lambda r: httpx.Response(404)),
        )
        models = asyncio.run(service.list_models()).models
        return models[0].run_placement

    def test_full_gpu(self) -> None:
        assert self._placement(1000, 1000) == "gpu"

    def test_cpu_only(self) -> None:
        assert self._placement(1000, 0) == "cpu"

    def test_partial(self) -> None:
        assert self._placement(1000, 400) == "partial"


class TestPullRateAndHf:
    def test_pull_computes_speed_and_eta(self) -> None:
        lines = [
            {"status": "pulling manifest"},
            {"status": "downloading", "total": 1000, "completed": 200},
            {"status": "downloading", "total": 1000, "completed": 700},
            {"status": "success"},
        ]

        def handler(request: httpx.Request) -> httpx.Response:
            body = "\n".join(json.dumps(x) for x in lines) + "\n"
            return httpx.Response(200, content=body)

        clock = {"t": 0.0}
        service = LocalModelService(
            ollama=_ollama(handler),
            hf=_hf(lambda r: httpx.Response(404)),
            clock=lambda: clock["t"],
        )

        async def collect() -> list:
            out = []
            async for event in service.pull_events(repo="a/b", quant="Q4_K_M"):
                clock["t"] += 1.0
                out.append(event)
            return out

        events = asyncio.run(collect())
        assert events[-1].done is True
        # Third frame: 700-200=500 bytes over 1s → 500 B/s, eta=(1000-700)/500=0.6
        assert events[2].speed_bps == 500.0
        assert events[2].eta_seconds == pytest.approx(0.6)

    def test_hf_size_picks_matching_quant_lfs_size(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json=[
                    {
                        "path": "Model-Q4_K_M.gguf",
                        "size": 135,
                        "lfs": {"size": 808_000_000},
                    },
                    {
                        "path": "Model-Q8_0.gguf",
                        "size": 135,
                        "lfs": {"size": 1_600_000_000},
                    },
                    {"path": "README.md", "size": 900},
                ],
            )

        size = asyncio.run(_hf(handler).size(repo="a/b", quant="q4_k_m"))
        assert size.size_bytes == 808_000_000
        assert size.filename == "Model-Q4_K_M.gguf"

    def test_hf_size_missing_quant_raises(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, json=[{"path": "Model-Q8_0.gguf", "lfs": {"size": 1}}]
            )

        with pytest.raises(LocalModelError):
            asyncio.run(_hf(handler).size(repo="a/b", quant="Q4_K_M"))
