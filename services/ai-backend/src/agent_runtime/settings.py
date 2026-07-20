"""Env-backed runtime settings."""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
import logging
import os
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from agent_runtime.execution.contracts import (
    ModelConfig,
    ModelReasoningConfig,
    ModelReasoningDisplay,
    ModelReasoningEffort,
    ModelReasoningSummary,
    ModelThinkingMode,
    RuntimeContract,
)


class _EnvFields:
    """Environment variable name constants."""

    ENVIRONMENT = "RUNTIME_ENVIRONMENT"
    DEFAULT_PROVIDER = "RUNTIME_DEFAULT_PROVIDER"
    DEFAULT_MODEL = "RUNTIME_DEFAULT_MODEL"
    DEFAULT_MAX_INPUT_TOKENS = "RUNTIME_DEFAULT_MAX_INPUT_TOKENS"
    DEFAULT_TIMEOUT_SECONDS = "RUNTIME_DEFAULT_TIMEOUT_SECONDS"
    DEFAULT_TEMPERATURE = "RUNTIME_DEFAULT_TEMPERATURE"
    DEFAULT_SUPPORTS_STREAMING = "RUNTIME_DEFAULT_SUPPORTS_STREAMING"
    MAX_RETRIES = "RUNTIME_MAX_RETRIES"
    MAX_PARALLEL_RUNS = "RUNTIME_MAX_PARALLEL_RUNS"
    MAX_PARALLEL_TASKS = "RUNTIME_MAX_PARALLEL_TASKS"
    MAX_PARALLEL_SUBAGENTS = "RUNTIME_MAX_PARALLEL_SUBAGENTS"
    TOOL_CALL_BUDGET = "RUNTIME_TOOL_CALL_BUDGET"
    WORKER_POLL_INTERVAL_SECONDS = "RUNTIME_WORKER_POLL_INTERVAL_SECONDS"
    WORKER_LOCK_SECONDS = "RUNTIME_WORKER_LOCK_SECONDS"
    START_IN_PROCESS_WORKER = "RUNTIME_START_IN_PROCESS_WORKER"
    ALLOW_EMPTY_CAPABILITIES = "RUNTIME_ALLOW_EMPTY_CAPABILITIES"
    # Worker-side ``MODEL_DELTA`` coalesce window in ms. When > 0, the streaming
    # executor accumulates chunks for the window and flushes via
    # ``append_events_batch`` (one DB round-trip per batch). Default 0 (disabled).
    DELTA_COALESCE_WINDOW_MS = "RUNTIME_DELTA_COALESCE_WINDOW_MS"
    # Hard cap on chunks per coalesce batch — forces a flush even if the window
    # has not expired, defending against pathological emit rates.
    DELTA_COALESCE_MAX_CHUNKS = "RUNTIME_DELTA_COALESCE_MAX_CHUNKS"
    # SSE event bus backend. ``in_memory`` uses the single-process
    # ``asyncio.Condition`` path; ``postgres`` switches to ``LISTEN/NOTIFY`` so
    # the worker's append wakes SSE adapters in a separate API process.
    EVENT_BUS_BACKEND = "RUNTIME_EVENT_BUS_BACKEND"
    ENABLE_LOCAL_MODELS = "RUNTIME_ENABLE_LOCAL_MODELS"
    # Optional directory where the models.dev catalog source persists its
    # last successful fetch (``models_dev.json``). Unset disables the disk
    # cache tier — live data then falls straight back to the vendored snapshot.
    MODEL_CATALOG_CACHE_DIR = "RUNTIME_MODEL_CATALOG_CACHE_DIR"
    STORE_BACKEND = "RUNTIME_STORE_BACKEND"
    DATABASE_URL = "DATABASE_URL"
    # Root directory for the ``file`` runtime store backend (JSONL folders +
    # object store + disposable SQLite index). Required when
    # ``RUNTIME_STORE_BACKEND=file`` under the single_user_desktop profile.
    FILE_STORE_ROOT = "RUNTIME_FILE_STORE_ROOT"
    # Desktop capacity controls for the ``file`` backend. Both default to 0
    # (unlimited / keep forever); only the single_user_desktop profile sets them.
    FILE_STORE_MAX_BYTES = "RUNTIME_FILE_STORE_MAX_BYTES"
    FILE_STORE_RETENTION_DAYS = "RUNTIME_FILE_STORE_RETENTION_DAYS"
    MCP_BACKEND_REGISTRY_URL = "MCP_BACKEND_REGISTRY_URL"
    MCP_AUTH_REDIRECT_URI = "MCP_AUTH_REDIRECT_URI"
    SKILLS_BACKEND_REGISTRY_URL = "SKILLS_BACKEND_REGISTRY_URL"
    SKILLS_CACHE_TTL_SECONDS = "SKILLS_CACHE_TTL_SECONDS"
    OPENAI_API_KEY = "OPENAI_API_KEY"
    ANTHROPIC_API_KEY = "ANTHROPIC_API_KEY"
    GOOGLE_API_KEY = "GOOGLE_API_KEY"
    # OpenRouter (OpenAI-wire-compatible gateway) deployment fallback key.
    # Per-user BYOK keys are the primary path and take precedence.
    OPENROUTER_API_KEY = "OPENROUTER_API_KEY"
    DEFAULT_REASONING_ENABLED = "RUNTIME_DEFAULT_REASONING_ENABLED"
    DEFAULT_REASONING_EFFORT = "RUNTIME_DEFAULT_REASONING_EFFORT"
    DEFAULT_REASONING_SUMMARY = "RUNTIME_DEFAULT_REASONING_SUMMARY"
    DEFAULT_REASONING_DISPLAY = "RUNTIME_DEFAULT_REASONING_DISPLAY"
    DEFAULT_REASONING_BUDGET_TOKENS = "RUNTIME_DEFAULT_REASONING_BUDGET_TOKENS"
    DEFAULT_REASONING_INCLUDE_ENCRYPTED_CONTENT = (
        "RUNTIME_DEFAULT_REASONING_INCLUDE_ENCRYPTED_CONTENT"
    )
    DEFAULT_THINKING_MODE = "RUNTIME_DEFAULT_THINKING_MODE"

    _BOOL_TRUTHY = frozenset({"1", "true", "yes", "on"})
    _SDK_KEYS = (OPENAI_API_KEY, ANTHROPIC_API_KEY, GOOGLE_API_KEY)


class RuntimeEnvironment(StrEnum):
    """Known runtime environments."""

    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


class ProviderSettings(RuntimeContract):
    """Credential settings for one LLM provider."""

    api_key: str | None = Field(default=None, repr=False, exclude=True)

    @property
    def is_configured(self) -> bool:
        """Return whether this provider has a usable API key."""

        return bool(self.api_key)


class RuntimeExecutionSettings(RuntimeContract):
    """Runtime execution limits loaded from environment."""

    max_retries: int = Field(default=2, ge=0, le=10)
    max_parallel_runs: int = Field(default=4, ge=1, le=100)
    max_parallel_tasks: int = Field(default=4, ge=1, le=100)
    max_parallel_subagents: int = Field(default=4, ge=1, le=100)
    tool_call_budget: int = Field(default=6, ge=1, le=100)
    worker_poll_interval_seconds: float = Field(default=1, gt=0, le=60)
    worker_lock_seconds: int = Field(default=60, gt=0, le=3600)
    start_in_process_worker: bool = True
    allow_empty_capabilities: bool = False
    # Coalesce window in ms for worker-side ``MODEL_DELTA`` batching.
    # ``0`` disables coalescing (default). Increase on staging after measuring.
    delta_coalesce_window_ms: int = Field(default=0, ge=0, le=1000)
    # Hard cap on chunks per coalesce batch — forces a flush even if the
    # window has not expired, preventing unbounded buffer growth.
    delta_coalesce_max_chunks: int = Field(default=64, ge=1, le=1024)
    # SSE event bus backend. ``auto`` (the default) resolves to ``postgres``
    # when ``DATABASE_URL`` is configured and ``in_memory`` otherwise — see
    # ``RuntimeSettings.resolved_event_bus_backend``. Explicit values
    # (``in_memory`` | ``postgres``) skip the resolver and pass through.
    event_bus_backend: str = "auto"
    # Round 2 — expose the local-models (Ollama) management API. Off by
    # default (cloud/multi-tenant can't run a user's local GPU model); the
    # desktop-runtime and self-host set it true. Every /v1/local-models route
    # 404s when this is false — server-authoritative, never client-trust.
    enable_local_models: bool = False


class RuntimeStoreSettings(RuntimeContract):
    """Runtime storage adapter configuration."""

    backend: str = "in_memory"
    database_url: str | None = Field(default=None, repr=False, exclude=True)
    # Filesystem root for the ``file`` backend. ``None`` unless the desktop
    # profile sets ``RUNTIME_FILE_STORE_ROOT``; the factory fails closed when
    # ``backend == "file"`` and this is unset.
    file_store_root: str | None = None
    # Byte ceiling on the ``file`` store root; ``0`` (default) is unlimited.
    # Writes that would grow the store past this fail closed with a typed
    # ``file_store_quota_exceeded`` error before any bytes land.
    file_store_max_bytes: int = Field(default=0, ge=0)
    # Age-based cleanup window for the ``file`` store in days; ``0`` (default)
    # keeps history forever. Conversations whose last activity predates the
    # window are physically reaped by the cleanup sweeper (startup + on demand).
    file_store_retention_days: int = Field(default=0, ge=0)


class RuntimeMcpSettings(RuntimeContract):
    """Internal backend integration settings for dynamic MCP registry access."""

    backend_registry_url: str | None = None
    auth_redirect_uri: str = "http://127.0.0.1:5173/mcp/oauth/callback"


class RuntimeSkillSettings(RuntimeContract):
    """Internal backend integration settings for virtual Skill registry access."""

    backend_registry_url: str | None = None
    cache_ttl_seconds: int = Field(default=60, ge=0, le=3600)


class RuntimeSettings(BaseSettings):
    """Application-level settings consumed by API and worker components."""

    model_config = SettingsConfigDict(
        extra="ignore",
        frozen=True,
        validate_assignment=True,
    )

    environment: RuntimeEnvironment = RuntimeEnvironment.DEVELOPMENT
    default_model: ModelConfig
    default_timeout_seconds: float = Field(default=60, gt=0, le=600)
    execution: RuntimeExecutionSettings = Field(
        default_factory=RuntimeExecutionSettings
    )
    store: RuntimeStoreSettings = Field(default_factory=RuntimeStoreSettings)
    mcp: RuntimeMcpSettings = Field(default_factory=RuntimeMcpSettings)
    skills: RuntimeSkillSettings = Field(default_factory=RuntimeSkillSettings)
    openai: ProviderSettings = Field(default_factory=ProviderSettings)
    anthropic: ProviderSettings = Field(default_factory=ProviderSettings)
    gemini: ProviderSettings = Field(default_factory=ProviderSettings)
    openrouter: ProviderSettings = Field(default_factory=ProviderSettings)
    # Disk-cache directory for the models.dev catalog source; ``None``
    # disables the cache tier (see ``agent_runtime.api.models_dev_source``).
    model_catalog_cache_dir: str | None = None

    @classmethod
    def load(
        cls,
        *,
        env_file: str | Path | None = None,
        template_file: str | Path | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> "RuntimeSettings":
        """Load settings from env_example, .env, and process environment."""

        service_root = Path(__file__).resolve().parents[2]
        values: dict[str, str] = {}
        values.update(
            cls._load_env_file(
                Path(template_file)
                if template_file is not None
                else service_root / "env_example"
            )
        )
        values.update(
            cls._load_env_file(
                Path(env_file) if env_file is not None else service_root / ".env"
            )
        )
        values.update(dict(environ if environ is not None else os.environ))

        return cls._from_env_values(values)

    def provider_settings(self, provider: str) -> ProviderSettings:
        """Return credential settings for a normalized provider slug."""

        if provider == "openai":
            return self.openai
        if provider == "anthropic":
            return self.anthropic
        if provider in {"google", "gemini"}:
            return self.gemini
        if provider == "openrouter":
            return self.openrouter
        # Keyless local runtime (Ollama): no env credential. Return an empty
        # ProviderSettings so the credential gate's keyless branch handles it
        # rather than raising here.
        if provider == "ollama":
            return ProviderSettings()
        raise ValueError(f"Unsupported model provider: {provider}")

    def resolved_event_bus_backend(self) -> str:
        """Resolve the SSE event bus to ``"postgres"`` or ``"in_memory"``.

        ``auto`` (the default) picks ``postgres`` when ``DATABASE_URL`` is
        configured and ``in_memory`` otherwise. Explicit values pass through.

        Why this matters: API and worker run in separate processes in prod.
        ``InMemoryEventBus`` only delivers within one process, so a separate
        worker's ``notify_sync`` never wakes the API's SSE handler — the
        2-second poll fallback becomes the actual delivery mechanism (~1s
        p50 latency). ``PostgresEventBus`` uses ``LISTEN/NOTIFY`` for
        sub-50ms cross-process wakeups. The right default in prod is
        "postgres when postgres is available," not "in_memory with a
        2-second floor."

        Both ``RuntimeAdapterFactory`` (notify_after_append flag) and
        ``RuntimeApiAppFactory.default_event_bus`` (bus construction) read
        this; keep the selection in one place so they cannot disagree.
        """

        explicit = self.execution.event_bus_backend.lower()
        if explicit == "auto":
            return "postgres" if self.store.database_url else "in_memory"
        return explicit

    @classmethod
    def configure_sdk_environment(cls, settings: "RuntimeSettings") -> None:
        """Expose provider API keys to SDKs that read credentials from os.environ.

        Call this explicitly after ``load()`` in process entry points.
        """

        mapping = {
            _EnvFields.OPENAI_API_KEY: settings.openai,
            _EnvFields.ANTHROPIC_API_KEY: settings.anthropic,
            _EnvFields.GOOGLE_API_KEY: settings.gemini,
            # OpenRouter's fallback key is passed explicitly to the OpenAI
            # client in ``build_chat_model`` (base_url is openrouter.ai, so
            # it must NOT read OPENAI_API_KEY); exporting it here lets that
            # code read the deployment key from the environment.
            _EnvFields.OPENROUTER_API_KEY: settings.openrouter,
        }
        for key, provider in mapping.items():
            if provider.api_key is not None:
                os.environ.setdefault(key, provider.api_key)

    @classmethod
    def _load_env_file(cls, path: Path) -> dict[str, str]:
        if not path.exists():
            return {}
        try:
            from dotenv import dotenv_values

            raw_values = dotenv_values(path)
            return {
                key: str(value)
                for key, value in raw_values.items()
                if value is not None
            }
        except Exception:
            logging.getLogger(__name__).warning(
                "Failed to load env file %s via dotenv",
                path,
                exc_info=True,
            )
            return {}

    @classmethod
    def _env_str(cls, v: Mapping[str, str], key: str, default: str) -> str:
        raw = v.get(key)
        return raw.strip() if raw and raw.strip() else default

    @classmethod
    def _env_opt(cls, v: Mapping[str, str], key: str) -> str | None:
        raw = v.get(key)
        return raw.strip() if raw and raw.strip() else None

    @classmethod
    def _from_env_values(cls, v: Mapping[str, str]) -> "RuntimeSettings":
        """Assemble nested settings from a flat environment mapping."""

        E = _EnvFields
        _s = cls._env_str
        _o = cls._env_opt
        _truthy = E._BOOL_TRUTHY

        timeout = float(_s(v, E.DEFAULT_TIMEOUT_SECONDS, "60"))

        return cls(
            environment=RuntimeEnvironment(
                _s(v, E.ENVIRONMENT, RuntimeEnvironment.DEVELOPMENT.value)
            ),
            default_model=ModelConfig(
                provider=_s(v, E.DEFAULT_PROVIDER, "openai"),
                model_name=_s(v, E.DEFAULT_MODEL, "gpt-5.4-mini"),
                max_input_tokens=int(_s(v, E.DEFAULT_MAX_INPUT_TOKENS, "128000")),
                timeout_seconds=timeout,
                temperature=float(_s(v, E.DEFAULT_TEMPERATURE, "0")),
                supports_streaming=_s(v, E.DEFAULT_SUPPORTS_STREAMING, "true").lower()
                in _truthy,
                reasoning=cls._build_reasoning_config(v),
            ),
            default_timeout_seconds=timeout,
            execution=RuntimeExecutionSettings(
                max_retries=int(_s(v, E.MAX_RETRIES, "2")),
                max_parallel_runs=int(_s(v, E.MAX_PARALLEL_RUNS, "4")),
                max_parallel_tasks=int(_s(v, E.MAX_PARALLEL_TASKS, "4")),
                max_parallel_subagents=int(_s(v, E.MAX_PARALLEL_SUBAGENTS, "4")),
                tool_call_budget=int(_s(v, E.TOOL_CALL_BUDGET, "6")),
                worker_poll_interval_seconds=float(
                    _s(v, E.WORKER_POLL_INTERVAL_SECONDS, "1")
                ),
                worker_lock_seconds=int(_s(v, E.WORKER_LOCK_SECONDS, "60")),
                start_in_process_worker=_s(v, E.START_IN_PROCESS_WORKER, "true").lower()
                in _truthy,
                allow_empty_capabilities=_s(
                    v, E.ALLOW_EMPTY_CAPABILITIES, "false"
                ).lower()
                in _truthy,
                delta_coalesce_window_ms=int(_s(v, E.DELTA_COALESCE_WINDOW_MS, "0")),
                delta_coalesce_max_chunks=int(_s(v, E.DELTA_COALESCE_MAX_CHUNKS, "64")),
                event_bus_backend=_s(v, E.EVENT_BUS_BACKEND, "auto").lower(),
                enable_local_models=_s(v, E.ENABLE_LOCAL_MODELS, "false").lower()
                in _truthy,
            ),
            store=RuntimeStoreSettings(
                backend=_s(v, E.STORE_BACKEND, "in_memory").lower(),
                database_url=_o(v, E.DATABASE_URL),
                file_store_root=_o(v, E.FILE_STORE_ROOT),
                file_store_max_bytes=int(_s(v, E.FILE_STORE_MAX_BYTES, "0")),
                file_store_retention_days=int(_s(v, E.FILE_STORE_RETENTION_DAYS, "0")),
            ),
            mcp=RuntimeMcpSettings(
                backend_registry_url=_o(v, E.MCP_BACKEND_REGISTRY_URL),
                auth_redirect_uri=_s(
                    v,
                    E.MCP_AUTH_REDIRECT_URI,
                    "http://127.0.0.1:5173/mcp/oauth/callback",
                ),
            ),
            skills=RuntimeSkillSettings(
                backend_registry_url=_o(v, E.SKILLS_BACKEND_REGISTRY_URL),
                cache_ttl_seconds=int(_s(v, E.SKILLS_CACHE_TTL_SECONDS, "60")),
            ),
            openai=ProviderSettings(api_key=_o(v, E.OPENAI_API_KEY)),
            anthropic=ProviderSettings(api_key=_o(v, E.ANTHROPIC_API_KEY)),
            gemini=ProviderSettings(api_key=_o(v, E.GOOGLE_API_KEY)),
            openrouter=ProviderSettings(api_key=_o(v, E.OPENROUTER_API_KEY)),
            model_catalog_cache_dir=_o(v, E.MODEL_CATALOG_CACHE_DIR),
        )

    @classmethod
    def _build_reasoning_config(
        cls, v: Mapping[str, str]
    ) -> ModelReasoningConfig | None:
        E = _EnvFields
        _o = cls._env_opt
        _truthy = E._BOOL_TRUTHY

        enabled_raw = _o(v, E.DEFAULT_REASONING_ENABLED)
        effort = _o(v, E.DEFAULT_REASONING_EFFORT)
        summary = _o(v, E.DEFAULT_REASONING_SUMMARY)
        display = _o(v, E.DEFAULT_REASONING_DISPLAY)
        budget_raw = _o(v, E.DEFAULT_REASONING_BUDGET_TOKENS)
        encrypted_raw = _o(v, E.DEFAULT_REASONING_INCLUDE_ENCRYPTED_CONTENT)
        thinking_mode = _o(v, E.DEFAULT_THINKING_MODE)

        if all(
            x is None
            for x in (
                enabled_raw,
                effort,
                summary,
                display,
                budget_raw,
                encrypted_raw,
                thinking_mode,
            )
        ):
            return None

        enabled = enabled_raw.lower() in _truthy if enabled_raw else None

        return ModelReasoningConfig(
            enabled=True if enabled is None else enabled,
            effort=ModelReasoningEffort(effort.lower()) if effort else None,
            summary=ModelReasoningSummary(summary.lower()) if summary else None,
            display=ModelReasoningDisplay(display.lower()) if display else None,
            budget_tokens=int(budget_raw) if budget_raw else None,
            include_encrypted_content=bool(
                encrypted_raw and encrypted_raw.lower() in _truthy
            ),
            thinking_mode=ModelThinkingMode(thinking_mode.lower())
            if thinking_mode
            else None,
        )
