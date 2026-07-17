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


class LocalModelError(Exception):
    """Typed error for local-model operations. ``str(err)`` is display-safe."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.public_message = message


class OllamaClient:
    """Minimal async wrapper over the Ollama daemon's HTTP API."""

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
            raise LocalModelError(f"Ollama request failed: {path}") from exc

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
            raise LocalModelError("Ollama delete failed") from exc
        if response.status_code == httpx.codes.NOT_FOUND:
            return False
        if response.status_code >= 400:
            raise LocalModelError("Ollama delete failed")
        return True

    async def pull(self, model: str) -> AsyncIterator[dict[str, Any]]:
        """Stream ``POST /api/pull`` progress frames (parsed NDJSON).

        Yields each daemon line as a dict, e.g.
        ``{"status": "pulling …", "total": N, "completed": M}`` and finally
        ``{"status": "success"}``. Blank / non-JSON lines are skipped.
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
                        await response.aread()
                        raise LocalModelError(f"Ollama could not pull '{model}'")
                    async for line in response.aiter_lines():
                        stripped = line.strip()
                        if not stripped:
                            continue
                        try:
                            frame = json.loads(stripped)
                        except ValueError:
                            continue
                        if isinstance(frame, dict):
                            yield frame
            except httpx.HTTPError as exc:
                raise LocalModelError(
                    f"Ollama pull stream failed for '{model}'"
                ) from exc


__all__ = ["LocalModelError", "OllamaClient"]
