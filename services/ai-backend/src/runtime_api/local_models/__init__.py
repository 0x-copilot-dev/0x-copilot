"""Local-models (Ollama) management — Round 2.

Thin adapters over a user-installed Ollama server (its own HTTP API) plus a
Hugging Face GGUF size lookup. Consumed by
``runtime_api.http.local_models_routes``. Ollama is the source of truth for
installed models; nothing is persisted here.
"""

from runtime_api.local_models.hf_metadata import HfGgufResolver
from runtime_api.local_models.ollama_client import LocalModelError, OllamaClient
from runtime_api.local_models.service import LocalModelService

__all__ = [
    "HfGgufResolver",
    "LocalModelError",
    "LocalModelService",
    "OllamaClient",
]
