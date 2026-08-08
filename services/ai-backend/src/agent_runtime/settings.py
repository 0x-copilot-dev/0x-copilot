"""Env-backed runtime settings."""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
import logging
import os
from pathlib import Path
from typing import Final

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from agent_runtime.capabilities.operations.contracts import OperationGatewayMode
from agent_runtime.hyperparameters.contracts import (
    ExecutionHyperparameters,
    Hyperparameters,
)
from agent_runtime.hyperparameters.loader import HyperparameterLoader
from agent_runtime.execution.contracts import (
    ModelConfig,
    ModelReasoningConfig,
    ModelReasoningDisplay,
    ModelReasoningEffort,
    ModelReasoningSummary,
    ModelThinkingMode,
    RuntimeContract,
)

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
from agent_runtime.rollout_admission import (
    E2RolloutKillSwitches,
    RolloutKillSwitchConfigurationError,
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
    # Targeted production rollback controls. They resolve only into the
    # immutable settings snapshot; requests and model tools cannot mutate it.
    E2_ROLLOUT_KILL_SWITCHES_JSON = "E2_ROLLOUT_KILL_SWITCHES_JSON"
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
    EVALUATION_STORE_ROOT = "RUNTIME_EVALUATION_STORE_ROOT"
    EVALUATION_STORE_MAX_BYTES = "RUNTIME_EVALUATION_STORE_MAX_BYTES"
    # Explicit durable shared-volume root for artifact blobs when metadata uses
    # Postgres. There is deliberately no Postgres bytea fallback.
    ARTIFACT_BLOB_ROOT = "RUNTIME_ARTIFACT_BLOB_ROOT"
    MCP_BACKEND_REGISTRY_URL = "MCP_BACKEND_REGISTRY_URL"
    MCP_AUTH_REDIRECT_URI = "MCP_AUTH_REDIRECT_URI"
    SKILLS_BACKEND_REGISTRY_URL = "SKILLS_BACKEND_REGISTRY_URL"
    SKILLS_CACHE_TTL_SECONDS = "SKILLS_CACHE_TTL_SECONDS"
    EVALUATION_PROJECTION_ENABLED = "RUNTIME_EVALUATION_PROJECTION_ENABLED"
    EVALUATION_USER_CONSENTED = "RUNTIME_EVALUATION_USER_CONSENTED"
    EVALUATION_ALLOW_DEVELOPMENT_RUNS = "RUNTIME_EVALUATION_ALLOW_DEVELOPMENT_RUNS"
    EVALUATION_PROFILE_ID = "RUNTIME_EVALUATION_PROFILE_ID"
    EVALUATION_PROJECT_ID = "RUNTIME_EVALUATION_PROJECT_ID"
    EVALUATION_POLICY_REVISION = "RUNTIME_EVALUATION_POLICY_REVISION"
    EVALUATION_REDACTION_REVISION = "RUNTIME_EVALUATION_REDACTION_REVISION"
    EVALUATION_MAX_EVENTS_PER_RUN = "RUNTIME_EVALUATION_MAX_EVENTS_PER_RUN"
    EVALUATION_MAX_PROJECTION_ATTEMPTS = "RUNTIME_EVALUATION_MAX_PROJECTION_ATTEMPTS"
    EVALUATION_PROJECTION_LEASE_SECONDS = "RUNTIME_EVALUATION_PROJECTION_LEASE_SECONDS"
    EVALUATION_PROJECTION_CLAIM_BATCH = "RUNTIME_EVALUATION_PROJECTION_CLAIM_BATCH"
    HARNESS_RELEASE_CONFIG_PATH = "RUNTIME_HARNESS_RELEASE_CONFIG_PATH"
    LOCAL_RELEASE_CONTROL_ENABLED = "RUNTIME_LOCAL_RELEASE_CONTROL_ENABLED"
    OPENAI_API_KEY = "OPENAI_API_KEY"
    ANTHROPIC_API_KEY = "ANTHROPIC_API_KEY"
    GOOGLE_API_KEY = "GOOGLE_API_KEY"
    # OpenRouter (OpenAI-wire-compatible gateway) deployment fallback key.
    # Per-user BYOK keys are the primary path and take precedence.
    OPENROUTER_API_KEY = "OPENROUTER_API_KEY"
    # Virtuals compute (second OpenAI-wire gateway). DEV-ONLY convenience so
    # `make dev` and the test suites can reach it without a keychain entry —
    # the product path is a per-user BYOK key, which takes precedence. Named
    # for the variable the key ships under.
    VIRTUALS_ACP_KEY = "VIRTUALS_ACP_KEY"
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


#: The ``execution`` section's declared defaults, used as the ``Field`` defaults
#: below. A composition root loads the real document through
#: :meth:`RuntimeSettings.load`; this is the fallback for a settings object built
#: without one, and the two agree by the document's own test. Module level
#: because these models are Pydantic, where an underscore-prefixed class
#: attribute is claimed as a private attribute rather than staying a constant.
_EXECUTION: Final = ExecutionHyperparameters()


class _DeprecatedTunableEnv:
    """Legacy ``RUNTIME_*`` variables for values that moved to the document.

    These variables still apply — this is the one-release shim the migration
    plan requires. The risk it exists for is specific: a
    ``RUNTIME_MAX_PARALLEL_SUBAGENTS=1`` set in a desktop supervisor, a compose
    file, or an operator's shell that quietly stopped applying does not fail. It
    makes the agent four times more parallel than the operator believes, in
    production, with a green test suite. So the variable keeps winning and says
    so loudly, once per boot, naming the ``COPILOT_HP__`` form that replaces it.

    The pointer is the JSON pointer into ``hyperparameters.json``; the
    replacement variable name is derived from it rather than restated, so a
    renamed field cannot leave the message advertising a variable that no longer
    resolves.
    """

    _LOGGER: Final = logging.getLogger("agent_runtime.settings")

    MESSAGE: Final[str] = (
        "deprecated_hyperparameter_env var=%s pointer=%s replacement=%s — this "
        "variable still applies but will stop being read; move the value into "
        "hyperparameters.json or set the replacement variable."
    )

    #: Legacy variable -> the JSON pointer that now owns the value.
    POINTERS: Final[Mapping[str, str]] = {
        _EnvFields.DEFAULT_TIMEOUT_SECONDS: "/execution/default_timeout_seconds",
        _EnvFields.MAX_RETRIES: "/execution/max_retries",
        _EnvFields.MAX_PARALLEL_RUNS: "/execution/max_parallel_runs",
        _EnvFields.MAX_PARALLEL_TASKS: "/execution/max_parallel_tasks",
        _EnvFields.MAX_PARALLEL_SUBAGENTS: "/execution/max_parallel_subagents",
        _EnvFields.TOOL_CALL_BUDGET: "/execution/tool_call_budget",
        _EnvFields.WORKER_POLL_INTERVAL_SECONDS: (
            "/execution/worker_poll_interval_seconds"
        ),
        _EnvFields.WORKER_LOCK_SECONDS: "/execution/worker_lock_seconds",
        _EnvFields.DELTA_COALESCE_WINDOW_MS: "/execution/delta_coalesce_window_ms",
        _EnvFields.DELTA_COALESCE_MAX_CHUNKS: "/execution/delta_coalesce_max_chunks",
    }

    @classmethod
    def replacement(cls, pointer: str) -> str:
        """Return the ``COPILOT_HP__`` variable that supersedes ``pointer``."""

        separator = HyperparameterLoader.Env.PATH_SEPARATOR
        segments = [segment for segment in pointer.split("/") if segment]
        return HyperparameterLoader.Env.PREFIX + separator.join(
            segment.upper() for segment in segments
        )

    @classmethod
    def warn_all(cls, values: Mapping[str, str]) -> None:
        """Log every legacy tunable variable present in ``values``.

        Emitted before the settings object exists, so a boot that fails on one
        of these values still has the line naming it.
        """

        for name, pointer in cls.POINTERS.items():
            raw = values.get(name)
            if raw is None or not raw.strip():
                continue
            cls._LOGGER.warning(cls.MESSAGE, name, pointer, cls.replacement(pointer))


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
    """Runtime execution: deployment switches, plus tunables kept for the shim.

    The numbers below are no longer authored here — ``hyperparameters.json``'s
    ``execution`` section owns them, and their defaults are read from it. They
    remain *fields* because the deprecation shim still lets a legacy
    ``RUNTIME_*`` variable set them, and because every existing consumer reads
    them through this object. The switches (``start_in_process_worker``,
    the rollout block, ...) are genuine deployment
    concerns and stay authored here.
    """

    max_retries: int = Field(default=_EXECUTION.max_retries, ge=0, le=10)
    max_parallel_runs: int = Field(default=_EXECUTION.max_parallel_runs, ge=1, le=100)
    max_parallel_tasks: int = Field(default=_EXECUTION.max_parallel_tasks, ge=1, le=100)
    max_parallel_subagents: int = Field(
        default=_EXECUTION.max_parallel_subagents, ge=1, le=100
    )
    # Per-tool call cap. Feeds BOTH the prompt suffix the model is given
    # and the ``runtime_tool_budgets`` seed row the middleware enforces —
    # they must agree, or the model is told a cap that is not the one it
    # actually hits. The document's copy is pinned to ``DefaultToolBudget`` by
    # the hyperparameter document test, and all four sites by the SSOT gate.
    tool_call_budget: int = Field(default=_EXECUTION.tool_call_budget, ge=1, le=100)
    worker_poll_interval_seconds: float = Field(
        default=_EXECUTION.worker_poll_interval_seconds, gt=0, le=60
    )
    worker_lock_seconds: int = Field(
        default=_EXECUTION.worker_lock_seconds, gt=0, le=3600
    )
    start_in_process_worker: bool = True
    allow_empty_capabilities: bool = False
    # Coalesce window in ms for worker-side ``MODEL_DELTA`` batching.
    # ``0`` disables coalescing (default). Increase on staging after measuring.
    delta_coalesce_window_ms: int = Field(
        default=_EXECUTION.delta_coalesce_window_ms, ge=0, le=1000
    )
    # Hard cap on chunks per coalesce batch — forces a flush even if the
    # window has not expired, preventing unbounded buffer growth.
    delta_coalesce_max_chunks: int = Field(
        default=_EXECUTION.delta_coalesce_max_chunks, ge=1, le=1024
    )
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
    # E2 production rollback is a closed, targeted capability list resolved
    # with the mode/cohort snapshots. It never accepts request data and cannot
    # become a hidden global feature switch.
    rollout_kill_switches: E2RolloutKillSwitches = Field(
        default_factory=E2RolloutKillSwitches
    )

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
    # Explicit shared/local root for the evaluation ledger when the primary
    # runtime store is not the desktop file store.
    evaluation_store_root: str | None = None
    # Independent ceiling for an evaluation root mounted beside Postgres.
    # Desktop's file backend shares its existing object store and quota instead.
    evaluation_store_max_bytes: int = Field(default=536_870_912, gt=0)
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


class RuntimeEvaluationSettings(RuntimeContract):
    """Desktop-first local evaluation policy resolved once at startup."""

    projection_enabled: bool = False
    user_consented: bool = False
    allow_development_runs: bool = False
    profile_id: str = Field(
        default="desktop-local-profile", min_length=1, max_length=160
    )
    project_id: str | None = Field(default=None, min_length=1, max_length=160)
    policy_revision: str = Field(
        default="evaluation-projection-policy-v1",
        min_length=1,
        max_length=160,
    )
    redaction_revision: str = Field(
        default="evaluation-redaction-v1",
        min_length=1,
        max_length=160,
    )
    max_events_per_run: int = Field(default=10_000, ge=1, le=100_000)
    max_projection_attempts: int = Field(default=3, ge=1, le=10)
    projection_lease_seconds: int = Field(default=60, ge=1, le=3_600)
    projection_claim_batch: int = Field(default=20, ge=1, le=100)
    release_config_path: str | None = None
    local_release_control_enabled: bool = False


class RuntimeSettings(BaseSettings):
    """Application-level settings consumed by API and worker components."""

    model_config = SettingsConfigDict(
        extra="ignore",
        frozen=True,
        validate_assignment=True,
    )

    environment: RuntimeEnvironment = RuntimeEnvironment.DEVELOPMENT
    default_model: ModelConfig
    default_timeout_seconds: float = Field(
        default=_EXECUTION.default_timeout_seconds, gt=0, le=600
    )
    execution: RuntimeExecutionSettings = Field(
        default_factory=RuntimeExecutionSettings
    )
    # The loaded tuning document, carried on the settings object rather than
    # threaded separately: settings is already the load-once, inject-everywhere
    # seam, so this is what makes a hyperparameters.json edit reach a consumer
    # without a second parallel injection path. ``load()`` replaces this with
    # the on-disk document (COPILOT_HP__ overrides applied); the default is the
    # model's own defaults, which that document equals.
    hyperparameters: Hyperparameters = Field(default_factory=Hyperparameters)
    store: RuntimeStoreSettings = Field(default_factory=RuntimeStoreSettings)
    mcp: RuntimeMcpSettings = Field(default_factory=RuntimeMcpSettings)
    skills: RuntimeSkillSettings = Field(default_factory=RuntimeSkillSettings)
    evaluation: RuntimeEvaluationSettings = Field(
        default_factory=RuntimeEvaluationSettings
    )
    openai: ProviderSettings = Field(default_factory=ProviderSettings)
    anthropic: ProviderSettings = Field(default_factory=ProviderSettings)
    gemini: ProviderSettings = Field(default_factory=ProviderSettings)
    openrouter: ProviderSettings = Field(default_factory=ProviderSettings)
    virtuals: ProviderSettings = Field(default_factory=ProviderSettings)

    @model_validator(mode="after")
    def _local_release_control_is_never_production(
        self,
    ) -> "RuntimeSettings":
        if self.evaluation.local_release_control_enabled:
            if self.environment is RuntimeEnvironment.PRODUCTION:
                raise ValueError(
                    "local release control cannot be enabled in production"
                )
            if self.evaluation.release_config_path is None:
                raise ValueError(
                    "local release control requires a release configuration path"
                )
        return self

    @classmethod
    def load(
        cls,
        *,
        env_file: str | Path | None = None,
        template_file: str | Path | None = None,
        environ: Mapping[str, str] | None = None,
        hyperparameters: Hyperparameters | None = None,
    ) -> "RuntimeSettings":
        """Load settings from env_example, .env, the environment, and the document.

        This is the composition root for tunables as well as deployment
        settings: the document is read from disk exactly once here, with
        ``COPILOT_HP__`` overrides resolved against the same merged mapping the
        rest of settings uses, so an override set in ``.env`` behaves like one
        set in the process environment. A caller that has already loaded the
        document passes it in rather than re-reading the file.
        """

        service_root = Path(__file__).resolve().parents[2]
        values: dict[str, str] = {}
        values.update(
            cls._load_env_file(
                Path(template_file)
                if template_file is not None
                else service_root / "env_example"
            )
        )
        # Deliberately warned over the operator-supplied layers only, not over
        # ``env_example``: the template is this repo's to update, and a
        # deprecation that fires on every boot of an untouched checkout is the
        # kind of warning people learn to scroll past.
        operator_values: dict[str, str] = {}
        operator_values.update(
            cls._load_env_file(
                Path(env_file) if env_file is not None else service_root / ".env"
            )
        )
        operator_values.update(dict(environ if environ is not None else os.environ))
        _DeprecatedTunableEnv.warn_all(operator_values)
        values.update(operator_values)

        document = (
            HyperparameterLoader.with_overrides(HyperparameterLoader.default(), values)
            if hyperparameters is None
            else hyperparameters
        )
        return cls._from_env_values(values, hyperparameters=document)

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
        if provider == "virtuals":
            return self.virtuals
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
            # Same rationale as OpenRouter: base_url is compute.virtuals.io,
            # so the client must NOT fall back to OPENAI_API_KEY.
            _EnvFields.VIRTUALS_ACP_KEY: settings.virtuals,
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
    def _from_env_values(
        cls,
        v: Mapping[str, str],
        *,
        hyperparameters: Hyperparameters | None = None,
    ) -> "RuntimeSettings":
        """Assemble nested settings from a flat environment mapping.

        Every tunable's fallback is the document's value; the legacy
        ``RUNTIME_*`` variable still overrides it, which is the shim
        :class:`_DeprecatedTunableEnv` documents. ``load`` emits the deprecation
        lines, because only it can tell an operator-set variable from the
        checked-in ``env_example`` template.
        """

        E = _EnvFields
        _s = cls._env_str
        _o = cls._env_opt
        _truthy = E._BOOL_TRUTHY
        document = Hyperparameters() if hyperparameters is None else hyperparameters
        execution_tunables = document.execution

        timeout = float(
            _s(
                v,
                E.DEFAULT_TIMEOUT_SECONDS,
                str(execution_tunables.default_timeout_seconds),
            )
        )
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
        try:
            rollout_kill_switches = E2RolloutKillSwitches.from_startup_environment(v)
        except RolloutKillSwitchConfigurationError as exc:
            raise RolloutConfigurationError(str(exc)) from exc

        return cls(
            environment=RuntimeEnvironment(
                _s(v, E.ENVIRONMENT, RuntimeEnvironment.DEVELOPMENT.value)
            ),
            default_model=ModelConfig(
                provider=_s(v, E.DEFAULT_PROVIDER, "openai"),
                # A REASONING model by default. `gpt-5.4-mini` emits no
                # reasoning summaries at all, so every deployment that took the
                # default got a transcript with no thinking in it — and the gap
                # read as a bug in the runtime rather than a property of the
                # model that had been chosen for it.
                model_name=_s(v, E.DEFAULT_MODEL, "gpt-5.6-luna"),
                max_input_tokens=int(_s(v, E.DEFAULT_MAX_INPUT_TOKENS, "128000")),
                timeout_seconds=timeout,
                temperature=float(_s(v, E.DEFAULT_TEMPERATURE, "0")),
                supports_streaming=_s(v, E.DEFAULT_SUPPORTS_STREAMING, "true").lower()
                in _truthy,
                reasoning=cls._build_reasoning_config(v),
            ),
            default_timeout_seconds=timeout,
            hyperparameters=document,
            execution=RuntimeExecutionSettings(
                max_retries=int(
                    _s(v, E.MAX_RETRIES, str(execution_tunables.max_retries))
                ),
                max_parallel_runs=int(
                    _s(
                        v,
                        E.MAX_PARALLEL_RUNS,
                        str(execution_tunables.max_parallel_runs),
                    )
                ),
                max_parallel_tasks=int(
                    _s(
                        v,
                        E.MAX_PARALLEL_TASKS,
                        str(execution_tunables.max_parallel_tasks),
                    )
                ),
                max_parallel_subagents=int(
                    _s(
                        v,
                        E.MAX_PARALLEL_SUBAGENTS,
                        str(execution_tunables.max_parallel_subagents),
                    )
                ),
                tool_call_budget=int(
                    _s(v, E.TOOL_CALL_BUDGET, str(execution_tunables.tool_call_budget))
                ),
                worker_poll_interval_seconds=float(
                    _s(
                        v,
                        E.WORKER_POLL_INTERVAL_SECONDS,
                        str(execution_tunables.worker_poll_interval_seconds),
                    )
                ),
                worker_lock_seconds=int(
                    _s(
                        v,
                        E.WORKER_LOCK_SECONDS,
                        str(execution_tunables.worker_lock_seconds),
                    )
                ),
                start_in_process_worker=_s(v, E.START_IN_PROCESS_WORKER, "true").lower()
                in _truthy,
                allow_empty_capabilities=_s(
                    v, E.ALLOW_EMPTY_CAPABILITIES, "false"
                ).lower()
                in _truthy,
                delta_coalesce_window_ms=int(
                    _s(
                        v,
                        E.DELTA_COALESCE_WINDOW_MS,
                        str(execution_tunables.delta_coalesce_window_ms),
                    )
                ),
                delta_coalesce_max_chunks=int(
                    _s(
                        v,
                        E.DELTA_COALESCE_MAX_CHUNKS,
                        str(execution_tunables.delta_coalesce_max_chunks),
                    )
                ),
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
                rollout_kill_switches=rollout_kill_switches,
            ),
            store=RuntimeStoreSettings(
                backend=_s(v, E.STORE_BACKEND, "in_memory").lower(),
                database_url=_o(v, E.DATABASE_URL),
                file_store_root=_o(v, E.FILE_STORE_ROOT),
                file_store_max_bytes=int(_s(v, E.FILE_STORE_MAX_BYTES, "0")),
                file_store_retention_days=int(_s(v, E.FILE_STORE_RETENTION_DAYS, "0")),
                evaluation_store_root=_o(v, E.EVALUATION_STORE_ROOT),
                evaluation_store_max_bytes=int(
                    _s(v, E.EVALUATION_STORE_MAX_BYTES, "536870912")
                ),
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
            evaluation=RuntimeEvaluationSettings(
                projection_enabled=_s(
                    v,
                    E.EVALUATION_PROJECTION_ENABLED,
                    "false",
                ).lower()
                in _truthy,
                user_consented=_s(
                    v,
                    E.EVALUATION_USER_CONSENTED,
                    "false",
                ).lower()
                in _truthy,
                allow_development_runs=_s(
                    v,
                    E.EVALUATION_ALLOW_DEVELOPMENT_RUNS,
                    "false",
                ).lower()
                in _truthy,
                profile_id=_s(
                    v,
                    E.EVALUATION_PROFILE_ID,
                    "desktop-local-profile",
                ),
                project_id=_o(v, E.EVALUATION_PROJECT_ID),
                policy_revision=_s(
                    v,
                    E.EVALUATION_POLICY_REVISION,
                    "evaluation-projection-policy-v1",
                ),
                redaction_revision=_s(
                    v,
                    E.EVALUATION_REDACTION_REVISION,
                    "evaluation-redaction-v1",
                ),
                max_events_per_run=int(_s(v, E.EVALUATION_MAX_EVENTS_PER_RUN, "10000")),
                max_projection_attempts=int(
                    _s(v, E.EVALUATION_MAX_PROJECTION_ATTEMPTS, "3")
                ),
                projection_lease_seconds=int(
                    _s(v, E.EVALUATION_PROJECTION_LEASE_SECONDS, "60")
                ),
                projection_claim_batch=int(
                    _s(v, E.EVALUATION_PROJECTION_CLAIM_BATCH, "20")
                ),
                release_config_path=_o(v, E.HARNESS_RELEASE_CONFIG_PATH),
                local_release_control_enabled=_s(
                    v,
                    E.LOCAL_RELEASE_CONTROL_ENABLED,
                    "false",
                ).lower()
                in _truthy,
            ),
            openai=ProviderSettings(api_key=_o(v, E.OPENAI_API_KEY)),
            anthropic=ProviderSettings(api_key=_o(v, E.ANTHROPIC_API_KEY)),
            gemini=ProviderSettings(api_key=_o(v, E.GOOGLE_API_KEY)),
            openrouter=ProviderSettings(api_key=_o(v, E.OPENROUTER_API_KEY)),
            virtuals=ProviderSettings(api_key=_o(v, E.VIRTUALS_ACP_KEY)),
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
