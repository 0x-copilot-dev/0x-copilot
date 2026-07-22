"""Async client for a user-installed Ollama server's native HTTP API.

Wraps the endpoints we need (``/api/version``, ``/api/tags``, ``/api/ps``,
``/api/pull``, ``/api/delete``). All failures become :class:`LocalModelError`
with a safe public message — Ollama responses are untrusted input and their
raw text never reaches the client or the model.

Note the base URL: the OpenAI-compatible surface is ``…:11434/v1`` but the
management API lives at the server root ``…:11434``. Use
:meth:`api_root_from_openai_base` to derive one from the other so both share
a single ``OLLAMA_BASE_URL`` source.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from typing import Any

import httpx

from runtime_api.schemas.local_models import LocalModelErrorKind


class LocalModelError(Exception):
    """Typed error for local-model operations. ``str(err)`` is display-safe.

    ``kind`` classifies the failure for the client's recovery policy
    (PRD-P8 §4.1). It defaults to ``TERMINAL`` so a caller that raises with a
    bare message keeps the safest behaviour: no silent auto-retry.
    """

    def __init__(
        self,
        message: str,
        *,
        kind: LocalModelErrorKind = LocalModelErrorKind.TERMINAL,
    ) -> None:
        super().__init__(message)
        self.public_message = message
        self.kind = kind


class OllamaErrorClassifier:
    """Maps a transport/protocol failure onto :class:`LocalModelErrorKind`.

    Only the *exception type* is inspected — never the daemon's response body,
    which is untrusted input and must not reach a public message.
    """

    # The daemon is not answering at all: nothing was ever connected.
    _UNREACHABLE: tuple[type[BaseException], ...] = (
        httpx.ConnectError,
        httpx.ConnectTimeout,
    )
    # A connection existed and broke (or stalled) mid-flight. Ollama keeps its
    # partial blobs, so these are safe to retry with backoff.
    _TRANSIENT: tuple[type[BaseException], ...] = (
        httpx.ReadTimeout,
        httpx.WriteTimeout,
        httpx.PoolTimeout,
        httpx.RemoteProtocolError,
        httpx.ReadError,
        httpx.WriteError,
        httpx.CloseError,
    )

    @classmethod
    def classify(cls, exc: BaseException | None) -> LocalModelErrorKind:
        """Return the recovery class for ``exc``; unknown failures are terminal."""

        if isinstance(exc, cls._UNREACHABLE):
            return LocalModelErrorKind.RUNTIME_UNREACHABLE
        if isinstance(exc, cls._TRANSIENT):
            return LocalModelErrorKind.TRANSIENT
        return LocalModelErrorKind.TERMINAL


class OllamaClient:
    """Minimal async wrapper over the Ollama daemon's HTTP API."""

    # NDJSON key the daemon uses to report an in-band pull failure.
    _ERROR_FIELD = "error"

    class Messages:
        """Public failure messages. Never interpolate daemon-supplied text.

        ``model`` / ``path`` are values *we* built (a client-supplied repo and
        quant, length-capped by the route's query validation), not anything the
        daemon said back.
        """

        REQUEST_FAILED = "Ollama request failed: {path}"
        DELETE_FAILED = "Ollama delete failed"
        PULL_FAILED = "Ollama could not pull '{model}'"
        PULL_STREAM_FAILED = "Ollama pull stream failed for '{model}'"

    def __init__(
        self,
        *,
        base_url: str,
        timeout: float = 10.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        # ``base_url`` is the server root (no ``/v1``). ``client`` is injectable
        # for tests (httpx.MockTransport) and is never closed by this class.
        self._base = base_url.rstrip("/")
        self._timeout = timeout
        self._client = client

    @staticmethod
    def api_root_from_openai_base(openai_base: str) -> str:
        """``http://host:11434/v1`` -> ``http://host:11434`` (root for /api/*)."""

        base = openai_base.rstrip("/")
        if base.endswith("/v1"):
            base = base[: -len("/v1")]
        return base.rstrip("/")

    @asynccontextmanager
    async def _acquire(self) -> AsyncIterator[httpx.AsyncClient]:
        if self._client is not None:
            yield self._client
            return
        client = httpx.AsyncClient(timeout=self._timeout)
        try:
            yield client
        finally:
            await client.aclose()

    async def _get_json(self, path: str) -> Any:
        try:
            async with self._acquire() as client:
                response = await client.get(f"{self._base}{path}")
                response.raise_for_status()
                return response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise LocalModelError(
                self.Messages.REQUEST_FAILED.format(path=path),
                kind=OllamaErrorClassifier.classify(exc),
            ) from exc

    async def running_version(self) -> str | None:
        """Ollama version string if reachable, else ``None`` (not running)."""

        try:
            data = await self._get_json("/api/version")
        except LocalModelError:
            return None
        version = data.get("version") if isinstance(data, Mapping) else None
        return str(version) if version else "unknown"

    async def list_tags(self) -> list[dict[str, Any]]:
        """Installed models (``GET /api/tags``)."""

        data = await self._get_json("/api/tags")
        models = data.get("models") if isinstance(data, Mapping) else None
        return [m for m in models or [] if isinstance(m, Mapping)]

    async def list_running(self) -> list[dict[str, Any]]:
        """Currently-loaded models with GPU/CPU residency (``GET /api/ps``)."""

        data = await self._get_json("/api/ps")
        models = data.get("models") if isinstance(data, Mapping) else None
        return [m for m in models or [] if isinstance(m, Mapping)]

    async def delete(self, model: str) -> bool:
        """Remove an installed model (``DELETE /api/delete``). True if removed."""

        try:
            async with self._acquire() as client:
                response = await client.request(
                    "DELETE",
                    f"{self._base}/api/delete",
                    json={"model": model},
                )
        except httpx.HTTPError as exc:
            raise LocalModelError(
                self.Messages.DELETE_FAILED,
                kind=OllamaErrorClassifier.classify(exc),
            ) from exc
        if response.status_code == httpx.codes.NOT_FOUND:
            return False
        if response.status_code >= 400:
            raise LocalModelError(self.Messages.DELETE_FAILED)
        return True

    async def pull(self, model: str) -> AsyncIterator[dict[str, Any]]:
        """Stream ``POST /api/pull`` progress frames (parsed NDJSON).

        Yields each daemon line as a dict, e.g.
        ``{"status": "pulling …", "total": N, "completed": M}`` and finally
        ``{"status": "success"}``. Blank / non-JSON lines are skipped.

        Ollama reports most pull failures **in band**: the response is 200 and
        the stream carries ``{"error": "…"}`` before ending (a missing repo or
        quant, the PRD's "404 repo" case, arrives this way). Such a frame is
        raised as a terminal :class:`LocalModelError` — otherwise the stream
        just stops and the client waits forever on a download that will never
        land. The daemon's own text is untrusted and is not carried out.
        """

        async with self._acquire() as client:
            try:
                async with client.stream(
                    "POST",
                    f"{self._base}/api/pull",
                    json={"model": model, "stream": True},
                    timeout=None,
                ) as response:
                    if response.status_code >= 400:
                        # Body is drained but deliberately discarded: the
                        # daemon's text is untrusted and never made public.
                        await response.aread()
                        raise LocalModelError(
                            self.Messages.PULL_FAILED.format(model=model),
                            kind=LocalModelErrorKind.TERMINAL,
                        )
                    async for line in response.aiter_lines():
                        stripped = line.strip()
                        if not stripped:
                            continue
                        try:
                            frame = json.loads(stripped)
                        except ValueError:
                            continue
                        if not isinstance(frame, dict):
                            continue
                        if frame.get(self._ERROR_FIELD):
                            raise LocalModelError(
                                self.Messages.PULL_FAILED.format(model=model),
                                kind=LocalModelErrorKind.TERMINAL,
                            )
                        yield frame
            except httpx.HTTPError as exc:
                raise LocalModelError(
                    self.Messages.PULL_STREAM_FAILED.format(model=model),
                    kind=OllamaErrorClassifier.classify(exc),
                ) from exc


__all__ = ["LocalModelError", "OllamaClient", "OllamaErrorClassifier"]
