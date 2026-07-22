"""Pre-download size lookup for a Hugging Face GGUF file.

Uses the public HF tree API (no auth, no ``huggingface_hub`` dependency):
``GET https://huggingface.co/api/models/{repo}/tree/main`` returns
``[{path, size, lfs: {size}}, …]``. For LFS files (GGUF weights) the real
byte size is ``lfs.size``; the top-level ``size`` is only the pointer stub.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import httpx

from runtime_api.local_models.ollama_client import LocalModelError
from runtime_api.schemas.local_models import LocalModelErrorKind, LocalModelSize


class HfGgufResolver:
    """Resolve the download byte-size of a repo's GGUF for a given quant."""

    _HF_TREE = "https://huggingface.co/api/models/{repo}/tree/main"

    def __init__(
        self,
        *,
        timeout: float = 10.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._timeout = timeout
        self._client = client

    async def size(self, *, repo: str, quant: str) -> LocalModelSize:
        """Return the size of the ``.gguf`` whose filename carries ``quant``.

        Matching is case-insensitive on the quant token (Ollama treats
        ``Q4_K_M`` == ``q4_k_m``). Raises :class:`LocalModelError` when the
        repo is unreachable or no matching GGUF exists.
        """

        entries = await self._tree(repo)
        needle = quant.strip().lower()
        best: tuple[str, int] | None = None
        for entry in entries:
            if not isinstance(entry, Mapping):
                continue
            path = str(entry.get("path", ""))
            if not path.lower().endswith(".gguf"):
                continue
            if needle not in path.lower():
                continue
            size = self._entry_size(entry)
            if best is None or size > best[1]:
                best = (path, size)
        if best is None:
            # Genuinely terminal: the repo answered and has no such GGUF.
            raise LocalModelError(
                f"No '{quant}' GGUF found in '{repo}'",
                kind=LocalModelErrorKind.TERMINAL,
            )
        return LocalModelSize(
            repo=repo, quant=quant, filename=best[0], size_bytes=best[1]
        )

    async def _tree(self, repo: str) -> list[Any]:
        url = self._HF_TREE.format(repo=repo)
        try:
            if self._client is not None:
                response = await self._client.get(url)
            else:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    response = await client.get(url)
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise LocalModelError(
                f"Could not read model files for '{repo}'",
                kind=self._classify(exc),
            ) from exc
        return data if isinstance(data, list) else []

    @staticmethod
    def _classify(exc: BaseException) -> LocalModelErrorKind:
        """PRD-P8 §4.1 for the Hugging Face hop.

        Deliberately *not* ``OllamaErrorClassifier``: this hop talks to
        huggingface.co, so a refused connection says nothing about the local
        runtime and must never be reported as ``runtime_unreachable`` — that
        would make the client render "Ollama stopped responding" because a CDN
        blipped. A reachability or timeout failure here is a network blip
        (``transient``, safe to retry); a status code or an unparseable body is
        an answer we will get again (``terminal``).
        """

        if isinstance(exc, httpx.HTTPStatusError | ValueError):
            return LocalModelErrorKind.TERMINAL
        if isinstance(exc, httpx.TransportError):
            return LocalModelErrorKind.TRANSIENT
        return LocalModelErrorKind.TERMINAL

    @staticmethod
    def _entry_size(entry: Mapping[str, Any]) -> int:
        lfs = entry.get("lfs")
        if isinstance(lfs, Mapping) and isinstance(lfs.get("size"), int):
            return int(lfs["size"])
        raw = entry.get("size")
        return int(raw) if isinstance(raw, int) else 0


__all__ = ["HfGgufResolver"]
