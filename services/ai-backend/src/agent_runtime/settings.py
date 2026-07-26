"""Env-backed runtime settings."""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
import logging
import os
from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from agent_runtime.capabilities.operations.contracts import OperationGatewayMode
from agent_runtime.execution.contracts import (
    ModelConfig,
    ModelReasoningConfig,
    ModelReasoningDisplay,
    ModelReasoningEffort,
    ModelReasoningSummary,
    ModelThinkingMode,
    RuntimeContract,
)

# Imported from the leaf module rather than the ``records`` package so
# settings does not pull the whole persistence surface (and its imports)
# in at configuration-load time.
from agent_runtime.persistence.records.tool_budgets import DefaultToolBudget
from agent_runtime.rollout import (
    E2RolloutResolution,
    LegacyRolloutInputs,
    RolloutConfigurationError,
    RolloutMode,
)
from agent_runtime.rollout_control import (
    RolloutCohortConfigurationError,
    RolloutCohortPolicy,
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
    # Generative Surfaces v2 master flag (PRD-A1). Registered now but read by
    # nothing until emission lands in PRD-A3 — flag-off byte-identical.
    SURFACES_V2 = "SURFACES_V2"
    # SDR-19 single rollout gate for artifact/effect persistence. It stays
    # dark until E2 cutover; storage composition and API routes read it.
    ARTIFACT_EFFECTS_V2 = "ARTIFACT_EFFECTS_V2"
    # B1 migration seam. New `/drafts/` writes use canonical Artifact
    # revisions only when this is explicitly enabled alongside A2 storage.
    ARTIFACT_DRAFTS_V2 = "ARTIFACT_DRAFTS_V2"
    # Generative Surfaces v2.1 (PRD-A3). ``enforce`` is parsed so deployment
    # configuration fails honestly, but startup validation refuses it until
    # durable canonical arguments plus the generic stage/executor exist.
    OPERATION_GATEWAY_MODE = "OPERATION_GATEWAY_MODE"
    # C3 workspace migration lane. ``enforce`` routes every /workspace mutation
    # through A3 -> A4 -> A5; it never enables the retired direct broker path.
    WORKSPACE_EFFECT_MODE = "WORKSPACE_EFFECT_MODE"
    # E2 D1 owns exactly these independent rollout lanes.  They deliberately
    # have no global/master toggle: a deployment can pause one capability
    # without widening another capability's authority.
    ARTIFACT_REPOSITORY_MODE = "ARTIFACT_REPOSITORY_MODE"
    EFFECT_STAGER_MODE = "EFFECT_STAGER_MODE"
    EFFECT_COMMIT_MODE = "EFFECT_COMMIT_MODE"
    PRESENTATION_V2_1_MODE = "PRESENTATION_V2_1_MODE"
    WORKSPACE_OVERLAY_MODE = "WORKSPACE_OVERLAY_MODE"
    WORKSPACE_COMMIT_MODE = "WORKSPACE_COMMIT_MODE"
    MCP_GATEWAY_MODE = "MCP_GATEWAY_MODE"
    SANDBOX_ADAPTER_MODE = "SANDBOX_ADAPTER_MODE"
    BROWSER_ADAPTER_MODE = "BROWSER_ADAPTER_MODE"
    # D6 cohort policy is deployment-owned startup configuration.  It is a
    # JSON array of exact-match cohort rules, read only by RuntimeSettings;
    # request/runtime APIs never accept or reload it.  Absent stays empty.
    E2_ROLLOUT_COHORTS_JSON = "E2_ROLLOUT_COHORTS_JSON"
    # Trusted legacy desktop/sandbox controls are inputs to the E2 bridge.
    # They are not a new coarse rollout switch and are never written back from
    # E2 mode resolution.
    DESKTOP_BROKER_URL = "DESKTOP_BROKER_URL"
    DESKTOP_BROKER_TOKEN = "DESKTOP_BROKER_TOKEN"
    RUNTIME_ENABLE_DESKTOP_WORKSPACE = "RUNTIME_ENABLE_DESKTOP_WORKSPACE"
    DESKTOP_WORKSPACE_BROKER_URL = "DESKTOP_WORKSPACE_BROKER_URL"
    DESKTOP_WORKSPACE_BROKER_TOKEN = "DESKTOP_WORKSPACE_BROKER_TOKEN"
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
    # PRD-P8 D2 — allow this server to detect and spawn the host's Ollama
    # binary. Only ``tools/desktop-runtime`` + ``apps/desktop`` set it true;
    # a containerised self-host leaves it false and reports ``unknown``
    # rather than lying about a host filesystem it cannot see.
    LOCAL_MODELS_MANAGE_RUNTIME = "RUNTIME_LOCAL_MODELS_MANAGE_RUNTIME"
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
    # Explicit durable shared-volume root for artifact blobs when metadata uses
    # Postgres. There is deliberately no Postgres bytea fallback.
    ARTIFACT_BLOB_ROOT = "RUNTIME_ARTIFACT_BLOB_ROOT"
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
    # Per-tool call cap. Feeds BOTH the prompt suffix the model is given
    # and the ``runtime_tool_budgets`` seed row the middleware enforces —
    # they must agree, or the model is told a cap that is not the one it
    # actually hits. Keep in step with ``DefaultToolBudget``.
    tool_call_budget: int = Field(
        default=DefaultToolBudget.MAX_CALLS_PER_RUN, ge=1, le=100
    )
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
    # PRD-P8 D2 — gates binary detection AND process spawn for the local
    # runtime. Off by default; ``POST /v1/local-models/runtime/start`` 404s
    # and ``runtime_state`` stays ``unknown`` while it is false.
    local_models_manage_runtime: bool = False
    # Generative Surfaces v2 master flag (PRD-A1). **PRD-E3 flipped the default to
    # true**: v2 now owns surface emission (the v1 ``result["surface"]`` appendage
    # was retired), so unset ⇒ on. ``SURFACES_V2=false`` is the explicit kill
    # switch (chat-only degradation). Resolves identically to
    # ``SurfacesV2Flag.enabled()`` (same env var, same truthy set, same default).
    surfaces_v2: bool = True
    # SDR-19 is dark by default. When false product routes remain absent and
    # no artifact repository is composed.
    artifact_effects_v2: bool = False
    artifact_drafts_v2: bool = False
    # v2.1 Universal Operation Gateway. A3 is observational only, so the
    # initial default is explicitly off.
    operation_gateway_mode: OperationGatewayMode = OperationGatewayMode.OFF
    # v2.1 workspace product integration. Kept separately from the universal
    # gateway flag so the workspace cohort can roll out and roll back without
    # widening enforcement to unrelated capabilities.
    workspace_effect_mode: OperationGatewayMode = OperationGatewayMode.OFF
    # E2 D1: resolved exactly once by ``RuntimeSettings.load``.  Existing v2
    # controls are compatibility inputs to this immutable snapshot; callers
    # consume ``rollout.modes`` and never read a per-request environment flag.
    rollout: E2RolloutResolution = Field(default_factory=E2RolloutResolution)
    # D6 cohorts resolve alongside the E2 mode snapshot.  They remain empty
    # unless deployment injects E2_ROLLOUT_COHORTS_JSON at process startup.
    rollout_cohorts: RolloutCohortPolicy = Field(default_factory=RolloutCohortPolicy)

    @model_validator(mode="after")
    def _artifact_drafts_require_repository(self) -> "RuntimeExecutionSettings":
        if self.artifact_drafts_v2 and not self.artifact_effects_v2:
            raise ValueError("ARTIFACT_DRAFTS_V2 requires ARTIFACT_EFFECTS_V2")
        if (
            self.workspace_effect_mode is OperationGatewayMode.ENFORCE
            and self.operation_gateway_mode is not OperationGatewayMode.ENFORCE
        ):
            raise ValueError(
                "WORKSPACE_EFFECT_MODE=enforce requires OPERATION_GATEWAY_MODE=enforce"
            )
        return self


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
    # Required for the Postgres backend. API and worker processes must mount
    # this same durable root; factory construction fails closed when absent.
    artifact_blob_root: str | None = None


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
        # Custom OpenAI-compatible endpoint (BYOK decision D-2): no deployment
        # env credential — it is satisfied by the user's stored BYOK key
        # (``user_key_providers``). Empty settings (``is_configured`` False) so
        # the credential gate falls through to the user-key branch and still
        # fails closed when no key is stored.
        if provider == "openai_compatible":
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
        surfaces_v2 = _s(v, E.SURFACES_V2, "true").lower() in _truthy
        artifact_effects_v2 = _s(v, E.ARTIFACT_EFFECTS_V2, "false").lower() in _truthy
        artifact_drafts_v2 = _s(v, E.ARTIFACT_DRAFTS_V2, "false").lower() in _truthy
        try:
            operation_gateway_mode = OperationGatewayMode(
                _s(
                    v,
                    E.OPERATION_GATEWAY_MODE,
                    OperationGatewayMode.OFF.value,
                ).lower()
            )
        except ValueError as exc:
            raise RolloutConfigurationError(
                "OPERATION_GATEWAY_MODE must be a valid OperationGatewayMode "
                "(off, shadow, enforce)."
            ) from exc
        try:
            workspace_effect_mode = OperationGatewayMode(
                _s(
                    v,
                    E.WORKSPACE_EFFECT_MODE,
                    OperationGatewayMode.OFF.value,
                ).lower()
            )
        except ValueError as exc:
            raise RolloutConfigurationError(
                "WORKSPACE_EFFECT_MODE must be one of: off, shadow, enforce."
            ) from exc
        # Import only once settings has received its immutable environment
        # mapping.  Both existing capabilities retain their own runtime gates;
        # these booleans are only typed compatibility inputs to the E2 bridge.
        from agent_runtime.capabilities.browser.constants import BrowserEnv
        from agent_runtime.capabilities.sandbox.config import RemoteSandboxConfig

        rollout = E2RolloutResolution.resolve(
            environment=v,
            legacy=LegacyRolloutInputs(
                surfaces_v2=surfaces_v2,
                artifact_effects_v2=artifact_effects_v2,
                artifact_drafts_v2=artifact_drafts_v2,
                operation_gateway_mode=RolloutMode(operation_gateway_mode.value),
                workspace_effect_mode=RolloutMode(workspace_effect_mode.value),
                sandbox_adapter_active=RemoteSandboxConfig.from_env(v).is_active,
                browser_adapter_active=BrowserEnv.is_enabled(v.get(BrowserEnv.FLAG)),
                legacy_workspace_write_path_configured=bool(
                    _o(v, E.DESKTOP_BROKER_URL)
                    and _o(v, E.DESKTOP_BROKER_TOKEN)
                    or (
                        _s(v, E.RUNTIME_ENABLE_DESKTOP_WORKSPACE, "false").lower()
                        in _truthy
                        and _o(v, E.DESKTOP_WORKSPACE_BROKER_URL)
                        and _o(v, E.DESKTOP_WORKSPACE_BROKER_TOKEN)
                    )
                ),
            ),
        )
        try:
            rollout_cohorts = RolloutCohortPolicy.from_startup_environment(v)
        except RolloutCohortConfigurationError as exc:
            raise RolloutConfigurationError(str(exc)) from exc

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
                tool_call_budget=int(
                    _s(v, E.TOOL_CALL_BUDGET, str(DefaultToolBudget.MAX_CALLS_PER_RUN))
                ),
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
                local_models_manage_runtime=_s(
                    v, E.LOCAL_MODELS_MANAGE_RUNTIME, "false"
                ).lower()
                in _truthy,
                surfaces_v2=surfaces_v2,
                artifact_effects_v2=artifact_effects_v2,
                artifact_drafts_v2=artifact_drafts_v2,
                operation_gateway_mode=operation_gateway_mode,
                workspace_effect_mode=workspace_effect_mode,
                rollout=rollout,
                rollout_cohorts=rollout_cohorts,
            ),
            store=RuntimeStoreSettings(
                backend=_s(v, E.STORE_BACKEND, "in_memory").lower(),
                database_url=_o(v, E.DATABASE_URL),
                file_store_root=_o(v, E.FILE_STORE_ROOT),
                file_store_max_bytes=int(_s(v, E.FILE_STORE_MAX_BYTES, "0")),
                file_store_retention_days=int(_s(v, E.FILE_STORE_RETENTION_DAYS, "0")),
                artifact_blob_root=_o(v, E.ARTIFACT_BLOB_ROOT),
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
