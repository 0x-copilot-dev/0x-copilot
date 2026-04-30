"""Env-backed runtime settings."""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
import os
from pathlib import Path

from pydantic import Field

from agent_runtime.execution.contracts import ModelConfig, RuntimeContract


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
    max_parallel_subagents: int = Field(default=4, ge=1, le=100)


class RuntimeSettings(RuntimeContract):
    """Application-level settings consumed by API and worker components."""

    environment: RuntimeEnvironment = RuntimeEnvironment.DEVELOPMENT
    default_model: ModelConfig
    default_timeout_seconds: float = Field(default=60, gt=0, le=600)
    execution: RuntimeExecutionSettings = Field(default_factory=RuntimeExecutionSettings)
    openai: ProviderSettings = Field(default_factory=ProviderSettings)
    anthropic: ProviderSettings = Field(default_factory=ProviderSettings)
    gemini: ProviderSettings = Field(default_factory=ProviderSettings)

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
                Path(template_file) if template_file is not None else service_root / "env_example"
            )
        )
        values.update(
            cls._load_env_file(Path(env_file) if env_file is not None else service_root / ".env")
        )
        values.update(dict(environ if environ is not None else os.environ))

        default_provider = cls._get(values, "RUNTIME_DEFAULT_PROVIDER", "openai")
        default_timeout = cls._float(values, "RUNTIME_DEFAULT_TIMEOUT_SECONDS", 60)
        default_model = ModelConfig(
            provider=default_provider,
            model_name=cls._get(values, "RUNTIME_DEFAULT_MODEL", "gpt-4.1-mini"),
            max_input_tokens=cls._int(values, "RUNTIME_DEFAULT_MAX_INPUT_TOKENS", 128000),
            timeout_seconds=default_timeout,
            temperature=cls._float(values, "RUNTIME_DEFAULT_TEMPERATURE", 0),
            supports_streaming=cls._bool(values, "RUNTIME_DEFAULT_SUPPORTS_STREAMING", True),
        )
        return cls(
            environment=RuntimeEnvironment(
                cls._get(values, "RUNTIME_ENVIRONMENT", RuntimeEnvironment.DEVELOPMENT.value)
            ),
            default_model=default_model,
            default_timeout_seconds=default_timeout,
            execution=RuntimeExecutionSettings(
                max_retries=cls._int(values, "RUNTIME_MAX_RETRIES", 2),
                max_parallel_runs=cls._int(values, "RUNTIME_MAX_PARALLEL_RUNS", 4),
                max_parallel_subagents=cls._int(values, "RUNTIME_MAX_PARALLEL_SUBAGENTS", 4),
            ),
            openai=ProviderSettings(api_key=cls._optional(values, "OPENAI_API_KEY")),
            anthropic=ProviderSettings(api_key=cls._optional(values, "ANTHROPIC_API_KEY")),
            gemini=ProviderSettings(api_key=cls._optional(values, "GOOGLE_API_KEY")),
        )

    def provider_settings(self, provider: str) -> ProviderSettings:
        """Return credential settings for a normalized provider slug."""

        if provider == "openai":
            return self.openai
        if provider == "anthropic":
            return self.anthropic
        if provider in {"google", "gemini"}:
            return self.gemini
        raise ValueError(f"Unsupported model provider: {provider}")

    @classmethod
    def _load_env_file(cls, path: Path) -> dict[str, str]:
        if not path.exists():
            return {}
        try:
            from dotenv import dotenv_values

            raw_values = dotenv_values(path)
            return {key: str(value) for key, value in raw_values.items() if value is not None}
        except Exception:
            return cls._parse_env_file(path)

    @classmethod
    def _parse_env_file(cls, path: Path) -> dict[str, str]:
        values: dict[str, str] = {}
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", maxsplit=1)
            values[key.strip()] = value.strip().strip('"').strip("'")
        return values

    @classmethod
    def _get(cls, values: Mapping[str, str], key: str, default: str) -> str:
        value = values.get(key)
        if value is None or value.strip() == "":
            return default
        return value.strip()

    @classmethod
    def _optional(cls, values: Mapping[str, str], key: str) -> str | None:
        value = values.get(key)
        if value is None or value.strip() == "":
            return None
        return value.strip()

    @classmethod
    def _int(cls, values: Mapping[str, str], key: str, default: int) -> int:
        return int(cls._get(values, key, str(default)))

    @classmethod
    def _float(cls, values: Mapping[str, str], key: str, default: float) -> float:
        return float(cls._get(values, key, str(default)))

    @classmethod
    def _bool(cls, values: Mapping[str, str], key: str, default: bool) -> bool:
        value = cls._get(values, key, "true" if default else "false").lower()
        return value in {"1", "true", "yes", "on"}
