"""Unit tests for OllamaClient + HfGgufResolver + LocalModelService.

Uses httpx.MockTransport to fake the Ollama daemon and Hugging Face — no
network. Covers placement math, pull speed/ETA, HF size parsing, error
degradation, and the PRD-P8 error taxonomy (which recovery class each
transport/protocol failure maps to, and that no daemon text leaks out).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable

import httpx
import pytest

from runtime_api.local_models import (
    HfGgufResolver,
    LocalModelError,
    OllamaClient,
    OllamaErrorClassifier,
)
from runtime_api.local_models.service import LocalModelService
from runtime_api.schemas.local_models import LocalModelErrorKind


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


_DAEMON_SECRET = "internal ollama detail: /Users/p/.ollama/models"


class TestErrorTaxonomy:
    """PRD-P8 §4.1 — classification + public-message safety."""

    @staticmethod
    def _raising(exc: Exception) -> Callable[[httpx.Request], httpx.Response]:
        def handler(_request: httpx.Request) -> httpx.Response:
            raise exc

        return handler

    def test_bare_construction_defaults_to_terminal(self) -> None:
        assert LocalModelError("nope").kind is LocalModelErrorKind.TERMINAL

    def test_connect_error_is_runtime_unreachable(self) -> None:
        assert (
            OllamaErrorClassifier.classify(httpx.ConnectError("refused"))
            is LocalModelErrorKind.RUNTIME_UNREACHABLE
        )

    def test_connect_timeout_is_runtime_unreachable(self) -> None:
        assert (
            OllamaErrorClassifier.classify(httpx.ConnectTimeout("timed out"))
            is LocalModelErrorKind.RUNTIME_UNREACHABLE
        )

    def test_read_timeout_is_transient(self) -> None:
        assert (
            OllamaErrorClassifier.classify(httpx.ReadTimeout("slow"))
            is LocalModelErrorKind.TRANSIENT
        )

    def test_remote_protocol_error_is_transient(self) -> None:
        assert (
            OllamaErrorClassifier.classify(httpx.RemoteProtocolError("broke"))
            is LocalModelErrorKind.TRANSIENT
        )

    def test_mid_stream_transport_break_is_transient(self) -> None:
        assert (
            OllamaErrorClassifier.classify(httpx.ReadError("stream died"))
            is LocalModelErrorKind.TRANSIENT
        )

    def test_value_error_is_terminal(self) -> None:
        assert (
            OllamaErrorClassifier.classify(ValueError("bad json"))
            is LocalModelErrorKind.TERMINAL
        )

    def test_unknown_exception_is_terminal(self) -> None:
        assert (
            OllamaErrorClassifier.classify(RuntimeError("?"))
            is LocalModelErrorKind.TERMINAL
        )

    def test_get_json_propagates_unreachable_kind(self) -> None:
        client = _ollama(self._raising(httpx.ConnectError("refused")))
        with pytest.raises(LocalModelError) as excinfo:
            asyncio.run(client.list_tags())
        assert excinfo.value.kind is LocalModelErrorKind.RUNTIME_UNREACHABLE

    def test_http_error_status_is_terminal_and_hides_daemon_text(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text=_DAEMON_SECRET)

        with pytest.raises(LocalModelError) as excinfo:
            asyncio.run(_ollama(handler).list_tags())
        assert excinfo.value.kind is LocalModelErrorKind.TERMINAL
        assert _DAEMON_SECRET not in excinfo.value.public_message

    def test_delete_failure_classifies_transport(self) -> None:
        client = _ollama(self._raising(httpx.ConnectError("refused")))
        with pytest.raises(LocalModelError) as excinfo:
            asyncio.run(client.delete("m:tag"))
        assert excinfo.value.kind is LocalModelErrorKind.RUNTIME_UNREACHABLE

    def test_pull_4xx_is_terminal_and_hides_daemon_text(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, text=_DAEMON_SECRET)

        async def drain() -> None:
            async for _frame in _ollama(handler).pull("hf.co/a/b:Q4_K_M"):
                pass

        with pytest.raises(LocalModelError) as excinfo:
            asyncio.run(drain())
        assert excinfo.value.kind is LocalModelErrorKind.TERMINAL
        assert _DAEMON_SECRET not in excinfo.value.public_message

    def test_pull_stream_break_is_transient(self) -> None:
        client = _ollama(self._raising(httpx.RemoteProtocolError("stream broke")))

        async def drain() -> None:
            async for _frame in client.pull("hf.co/a/b:Q4_K_M"):
                pass

        with pytest.raises(LocalModelError) as excinfo:
            asyncio.run(drain())
        assert excinfo.value.kind is LocalModelErrorKind.TRANSIENT
        assert "stream broke" not in excinfo.value.public_message

    def test_pull_refused_connection_is_runtime_unreachable(self) -> None:
        client = _ollama(self._raising(httpx.ConnectError("refused")))

        async def drain() -> None:
            async for _frame in client.pull("hf.co/a/b:Q4_K_M"):
                pass

        with pytest.raises(LocalModelError) as excinfo:
            asyncio.run(drain())
        assert excinfo.value.kind is LocalModelErrorKind.RUNTIME_UNREACHABLE

    def test_in_band_error_frame_is_terminal_not_a_silent_stop(self) -> None:
        """A missing repo/quant comes back as HTTP 200 + an ``error`` line.

        Without this the stream simply ends and the card waits forever on a
        download that will never land — the exact permanent hang PRD-P8 exists
        to kill.
        """

        def handler(_request: httpx.Request) -> httpx.Response:
            body = (
                '{"status":"pulling manifest"}\n'
                f'{{"error":"pull model manifest: {_DAEMON_SECRET}"}}\n'
            )
            return httpx.Response(200, text=body)

        frames: list[dict[str, object]] = []

        async def drain() -> None:
            async for frame in _ollama(handler).pull("hf.co/a/b:Q4_K_M"):
                frames.append(frame)

        with pytest.raises(LocalModelError) as excinfo:
            asyncio.run(drain())
        assert excinfo.value.kind is LocalModelErrorKind.TERMINAL
        assert _DAEMON_SECRET not in excinfo.value.public_message
        # Frames before the error line are still delivered.
        assert [frame["status"] for frame in frames] == ["pulling manifest"]

    def test_hf_network_failure_is_transient_not_terminal(self) -> None:
        """A CDN blip on the size lookup must stay retryable.

        ``_guard`` derives ``retryable`` from ``kind``, so a terminal
        classification here would tell the first-run card "do not retry" for a
        failure that will very likely succeed on the next attempt.
        """

        resolver = HfGgufResolver(
            client=httpx.AsyncClient(
                transport=httpx.MockTransport(self._raising(httpx.ConnectError("x")))
            )
        )
        with pytest.raises(LocalModelError) as excinfo:
            asyncio.run(resolver.size(repo="a/b", quant="Q4_K_M"))
        # And never runtime_unreachable: huggingface.co is not the local daemon.
        assert excinfo.value.kind is LocalModelErrorKind.TRANSIENT

    def test_hf_status_error_is_terminal(self) -> None:
        resolver = HfGgufResolver(
            client=httpx.AsyncClient(
                transport=httpx.MockTransport(lambda _r: httpx.Response(404))
            )
        )
        with pytest.raises(LocalModelError) as excinfo:
            asyncio.run(resolver.size(repo="a/b", quant="Q4_K_M"))
        assert excinfo.value.kind is LocalModelErrorKind.TERMINAL

    def test_hf_missing_quant_is_terminal(self) -> None:
        resolver = HfGgufResolver(
            client=httpx.AsyncClient(
                transport=httpx.MockTransport(
                    lambda _r: httpx.Response(200, json=[{"path": "readme.md"}])
                )
            )
        )
        with pytest.raises(LocalModelError) as excinfo:
            asyncio.run(resolver.size(repo="a/b", quant="Q4_K_M"))
        assert excinfo.value.kind is LocalModelErrorKind.TERMINAL

    def test_service_pull_events_stop_on_an_in_band_error(self) -> None:
        """The typed error reaches the route, which turns it into an SSE frame."""

        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text='{"error":"file does not exist"}\n')

        service = LocalModelService(
            ollama=_ollama(handler),
            hf=HfGgufResolver(
                client=httpx.AsyncClient(
                    transport=httpx.MockTransport(lambda _r: httpx.Response(404))
                )
            ),
        )

        async def drain() -> None:
            async for _event in service.pull_events(repo="a/b", quant="Q4_K_M"):
                pass

        with pytest.raises(LocalModelError) as excinfo:
            asyncio.run(drain())
        assert excinfo.value.kind is LocalModelErrorKind.TERMINAL


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
