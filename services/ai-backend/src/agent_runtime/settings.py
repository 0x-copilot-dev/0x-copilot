"""Typed runtime settings."""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from agent_runtime.agent.contracts import ModelConfig, RuntimeContract


class RuntimeEnvironment(StrEnum):
    """Known runtime environments."""

    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


class RuntimeSettings(RuntimeContract):
    """Application-level settings consumed by the runtime factory."""

    environment: RuntimeEnvironment = RuntimeEnvironment.DEVELOPMENT
    default_model: ModelConfig
    default_timeout_seconds: float = Field(default=60, gt=0, le=600)
