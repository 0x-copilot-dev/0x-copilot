"""Local-models (Ollama) management — Round 2.

Thin adapters over a user-installed Ollama server (its own HTTP API) plus a
Hugging Face GGUF size lookup. Consumed by
``runtime_api.http.local_models_routes``. Ollama is the source of truth for
installed models; nothing is persisted here.

PRD-P8 adds :class:`OllamaRuntimeController` — the only piece that touches the
host outside an HTTP client — gated by ``RUNTIME_LOCAL_MODELS_MANAGE_RUNTIME``.
"""

from runtime_api.local_models.hf_metadata import HfGgufResolver
from runtime_api.local_models.ollama_client import (
    LocalModelError,
    OllamaClient,
    OllamaErrorClassifier,
)
from runtime_api.local_models.ollama_runtime import OllamaRuntimeController
from runtime_api.local_models.service import LocalModelService

__all__ = [
    "HfGgufResolver",
    "LocalModelError",
    "LocalModelService",
    "OllamaClient",
    "OllamaErrorClassifier",
    "OllamaRuntimeController",
]
