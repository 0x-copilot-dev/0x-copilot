"""OpenAI-wire-compatible, non-OpenAI provider endpoints.

Single source of truth for provider slugs that speak the OpenAI **Chat
Completions** wire format but are not OpenAI itself. They are reached
through ``langchain_openai.ChatOpenAI`` (``init_chat_model(...,
model_provider="openai")``) with a **fixed** ``base_url`` and the OpenAI
**Responses API disabled** — these gateways implement only
``/chat/completions``.

Today the registry holds **OpenRouter** (BYOK gateway to 300+ models via
``vendor/model`` slugs). Round 2 adds a local runtime entry (Ollama at
``http://localhost:11434/v1``) — the download/VRAM UI is new, but the
*execution* path is this same row, so a local model is just another
model to the harness.

Why a registry rather than an ``if provider == "openrouter"`` branch:
OpenRouter and a local Ollama server differ only in
``(base_url, api-key source, attribution headers)``. Funnelling both
through one table keeps model construction (``deep_agent_builder``),
provider normalisation (``ModelConfigResolver``), and the credential
gate consistent, and makes a new compatible endpoint one row instead of
edits scattered across the runtime.

Credential precedence: a per-user BYOK key (``AgentRuntimeContext.
provider_keys[provider]``) is injected upstream in
``user_policy_model_kwargs`` and always wins; ``api_key_env`` is only the
deployment-level fallback read here for the credential-gate check.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class OpenAICompatibleEndpoint:
    """One OpenAI-wire-compatible, non-OpenAI provider.

    ``provider`` is the normalised runtime slug — it matches
    ``ModelConfigResolver._normalize_provider`` output and the BYOK
    ``provider_keys`` mapping key. ``base_url`` is the default endpoint;
    ``base_url_env`` (when set) lets a deployment override it — e.g. a
    self-host container reaching a local Ollama via ``host.docker.internal``.
    ``requires_api_key`` is ``False`` for keyless local runtimes (Ollama):
    the credential gate treats them as always-satisfied and a sentinel key
    is injected because ``ChatOpenAI`` rejects an empty one.
    ``attribution_headers`` maps a static header name to the env var that
    supplies its value, with a hard-coded default when the env var is unset
    (OpenRouter's optional ``HTTP-Referer`` / ``X-Title`` app-ranking headers).
    """

    provider: str
    base_url: str
    api_key_env: str
    requires_api_key: bool = True
    base_url_env: str | None = None
    attribution_headers: Mapping[
        str, tuple[str, str]
    ] = ()  # header -> (env_var, default)

    def resolve_base_url(self, environ: Mapping[str, str] | None = None) -> str:
        """Return ``base_url``, or its ``base_url_env`` override when set."""

        if self.base_url_env is None:
            return self.base_url
        env = environ if environ is not None else os.environ
        return env.get(self.base_url_env, "").strip() or self.base_url

    def default_headers(
        self, environ: Mapping[str, str] | None = None
    ) -> dict[str, str]:
        """Resolve the static attribution headers from the environment.

        Each header takes its deployment override from the env var, else
        the built-in default. Blank values are dropped so an operator can
        opt out of a header by exporting it empty.
        """

        env = environ if environ is not None else os.environ
        headers: dict[str, str] = {}
        for header, (env_var, default) in dict(self.attribution_headers).items():
            # An explicitly-set env var wins (including an explicit blank,
            # which opts the header out); an unset var falls back to the
            # built-in default.
            value = env[env_var].strip() if env_var in env else default
            if value:
                headers[header] = value
        return headers

    def api_key_from_env(self, environ: Mapping[str, str] | None = None) -> str | None:
        """Return the deployment-level fallback key, or ``None``."""

        env = environ if environ is not None else os.environ
        value = env.get(self.api_key_env, "").strip()
        return value or None


class OpenAICompatibleProviders:
    """Registry of OpenAI-wire-compatible provider endpoints."""

    OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
    OLLAMA_BASE_URL = "http://localhost:11434/v1"

    _REGISTRY: Mapping[str, OpenAICompatibleEndpoint] = {
        "openrouter": OpenAICompatibleEndpoint(
            provider="openrouter",
            base_url=OPENROUTER_BASE_URL,
            api_key_env="OPENROUTER_API_KEY",
            # Optional app-attribution headers used for OpenRouter's public
            # rankings. Overridable per deployment via env; harmless
            # defaults point at the product's canonical identity.
            attribution_headers={
                "HTTP-Referer": ("OPENROUTER_APP_URL", "https://0xcopilot.tech"),
                "X-Title": ("OPENROUTER_APP_TITLE", "0xCopilot"),
            },
        ),
        # Round 2 — a local Ollama server (OpenAI-compatible at /v1). Keyless:
        # the harness talks to a runtime the user installed on their own
        # machine. ``OLLAMA_BASE_URL`` lets self-host containers repoint it.
        "ollama": OpenAICompatibleEndpoint(
            provider="ollama",
            base_url=OLLAMA_BASE_URL,
            base_url_env="OLLAMA_BASE_URL",
            api_key_env="",
            requires_api_key=False,
        ),
    }

    @classmethod
    def get(cls, provider: str) -> OpenAICompatibleEndpoint | None:
        """Return the endpoint for a normalised provider slug, or ``None``."""

        return cls._REGISTRY.get(provider)

    @classmethod
    def is_compatible(cls, provider: str) -> bool:
        """Whether ``provider`` routes through the OpenAI-compatible client."""

        return provider in cls._REGISTRY

    @classmethod
    def slugs(cls) -> tuple[str, ...]:
        """Every registered compatible-provider slug."""

        return tuple(cls._REGISTRY)


__all__ = ["OpenAICompatibleEndpoint", "OpenAICompatibleProviders"]
