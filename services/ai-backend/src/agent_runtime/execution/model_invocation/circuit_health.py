"""Bounded process-local provider circuit health with scoped BYOK isolation."""

from __future__ import annotations

from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from threading import Lock
from typing import Callable, Self

from pydantic import Field, PositiveInt, field_validator, model_validator

from agent_runtime.execution.contracts import RuntimeContract
from agent_runtime.execution.model_invocation.contracts import (
    ModelCredentialMode,
    ModelDeploymentHealth,
    ModelFailureClass,
)
from agent_runtime.validation import ValueNormalizer


class ProviderCircuitAdmission(StrEnum):
    ALLOW = "allow"
    BLOCK_AUTOMATIC = "block_automatic"
    ALLOW_PROBE = "allow_probe"


class ProviderCircuitKey(RuntimeContract):
    """Non-secret circuit partition; BYOK uses only an opaque credential digest."""

    provider: str = Field(min_length=1, max_length=64)
    deployment_id: str = Field(min_length=1, max_length=255)
    region: str = Field(min_length=1, max_length=64)
    credential_mode: ModelCredentialMode
    credential_fingerprint: str | None = Field(
        default=None, pattern=r"^sha256:[0-9a-f]{64}$"
    )

    @field_validator("provider", "region")
    @classmethod
    def _normalize_slug(cls, value: str, info) -> str:  # type: ignore[no-untyped-def]
        return ValueNormalizer.normalize_slug(value, info.field_name)

    @field_validator("deployment_id")
    @classmethod
    def _normalize_deployment(cls, value: str) -> str:
        return ValueNormalizer.normalize_nonempty_string(value, "deployment_id")

    @model_validator(mode="after")
    def _scope_matches_credential_mode(self) -> Self:
        if self.credential_mode is ModelCredentialMode.BYOK:
            if self.credential_fingerprint is None:
                raise ValueError("BYOK circuit key requires a credential fingerprint")
        elif self.credential_fingerprint is not None:
            raise ValueError("only BYOK circuit keys may carry a fingerprint")
        return self

    @property
    def stable_key(self) -> str:
        fingerprint = self.credential_fingerprint or self.credential_mode.value
        return "|".join((self.provider, self.deployment_id, self.region, fingerprint))


class ProviderCircuitConfig(RuntimeContract):
    max_entries: PositiveInt = Field(default=512, le=4096)
    max_samples_per_entry: PositiveInt = Field(default=8, le=64)
    open_failure_threshold: PositiveInt = Field(default=3, le=64)
    observation_window_seconds: PositiveInt = Field(default=120, le=3600)
    entry_ttl_seconds: PositiveInt = Field(default=900, le=86_400)
    cooldown_seconds: PositiveInt = Field(default=30, le=3600)

    @model_validator(mode="after")
    def _threshold_fits_sample_capacity(self) -> Self:
        if self.open_failure_threshold > self.max_samples_per_entry:
            raise ValueError("open threshold cannot exceed sample capacity")
        return self


class ProviderCircuitSample(RuntimeContract):
    observed_at: datetime
    failure_class: ModelFailureClass | None = None

    @field_validator("observed_at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("observed_at must be timezone-aware")
        return value


class ProviderCircuitEntry(RuntimeContract):
    key: ProviderCircuitKey
    samples: tuple[ProviderCircuitSample, ...] = ()
    updated_at: datetime
    opened_at: datetime | None = None

    @field_validator("updated_at", "opened_at")
    @classmethod
    def _aware(cls, value: datetime | None, info) -> datetime | None:  # type: ignore[no-untyped-def]
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError(f"{info.field_name} must be timezone-aware")
        return value


class ProviderCircuitSnapshot(RuntimeContract):
    schema_version: str = "provider-circuit-snapshot.v1"
    captured_at: datetime
    entries: tuple[ProviderCircuitEntry, ...] = ()

    @field_validator("captured_at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("captured_at must be timezone-aware")
        return value


_CIRCUIT_RELEVANT_FAILURES = frozenset(
    {
        ModelFailureClass.PRE_DISPATCH_TRANSIENT,
        ModelFailureClass.PROVIDER_OVERLOADED,
        ModelFailureClass.REGION_UNAVAILABLE,
        ModelFailureClass.STREAM_INTERRUPTED_BEFORE_CONTENT,
        ModelFailureClass.STREAM_INTERRUPTED_AFTER_CONTENT,
        ModelFailureClass.AMBIGUOUS_PROVIDER_STATE,
        ModelFailureClass.DEADLINE_EXCEEDED,
        ModelFailureClass.AUTH_INVALID,
    }
)


class ProcessLocalProviderCircuitHealth:
    """Thread-safe O(1)-amortized reducer with deterministic LRU eviction."""

    def __init__(
        self,
        config: ProviderCircuitConfig | None = None,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._config = config or ProviderCircuitConfig()
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._entries: OrderedDict[str, ProviderCircuitEntry] = OrderedDict()
        self._lock = Lock()

    def observe_failure(
        self, key: ProviderCircuitKey, failure_class: ModelFailureClass
    ) -> ModelDeploymentHealth:
        now = self._checked_now()
        with self._lock:
            self._purge(now)
            if failure_class not in _CIRCUIT_RELEVANT_FAILURES:
                return self._health_for(self._entries.get(key.stable_key), now)
            entry = self._entries.get(key.stable_key)
            samples = self._active_samples(entry, now)
            samples = (
                *samples,
                ProviderCircuitSample(
                    observed_at=now,
                    failure_class=failure_class,
                ),
            )[-self._config.max_samples_per_entry :]
            failure_count = sum(sample.failure_class is not None for sample in samples)
            opened_at = (
                now
                if failure_count >= self._config.open_failure_threshold
                else entry.opened_at
                if entry is not None
                else None
            )
            updated = ProviderCircuitEntry(
                key=key,
                samples=samples,
                updated_at=now,
                opened_at=opened_at,
            )
            self._put(updated)
            return self._health_for(updated, now)

    def observe_success(self, key: ProviderCircuitKey) -> ModelDeploymentHealth:
        now = self._checked_now()
        with self._lock:
            self._purge(now)
            updated = ProviderCircuitEntry(
                key=key,
                samples=(ProviderCircuitSample(observed_at=now),),
                updated_at=now,
            )
            self._put(updated)
            return ModelDeploymentHealth.AVAILABLE

    def health(self, key: ProviderCircuitKey) -> ModelDeploymentHealth:
        now = self._checked_now()
        with self._lock:
            self._purge(now)
            entry = self._entries.get(key.stable_key)
            if entry is not None:
                self._entries.move_to_end(key.stable_key)
            return self._health_for(entry, now)

    def admission(self, key: ProviderCircuitKey) -> ProviderCircuitAdmission:
        now = self._checked_now()
        with self._lock:
            self._purge(now)
            entry = self._entries.get(key.stable_key)
            if entry is None or entry.opened_at is None:
                return ProviderCircuitAdmission.ALLOW
            if now - entry.opened_at >= timedelta(
                seconds=self._config.cooldown_seconds
            ):
                return ProviderCircuitAdmission.ALLOW_PROBE
            return ProviderCircuitAdmission.BLOCK_AUTOMATIC

    def snapshot(self) -> ProviderCircuitSnapshot:
        now = self._checked_now()
        with self._lock:
            self._purge(now)
            return ProviderCircuitSnapshot(
                captured_at=now, entries=tuple(self._entries.values())
            )

    def restore(self, snapshot: ProviderCircuitSnapshot) -> None:
        """Restore only fresh bounded facts; expired/excess facts are discarded."""

        now = self._checked_now()
        with self._lock:
            self._entries.clear()
            ordered = sorted(snapshot.entries, key=lambda item: item.updated_at)
            for entry in ordered[-self._config.max_entries :]:
                if now - entry.updated_at <= timedelta(
                    seconds=self._config.entry_ttl_seconds
                ):
                    samples = self._active_samples(entry, now)
                    if samples:
                        self._put(entry.model_copy(update={"samples": samples}))
            self._purge(now)

    @property
    def entry_count(self) -> int:
        with self._lock:
            return len(self._entries)

    def _put(self, entry: ProviderCircuitEntry) -> None:
        self._entries[entry.key.stable_key] = entry
        self._entries.move_to_end(entry.key.stable_key)
        while len(self._entries) > self._config.max_entries:
            self._entries.popitem(last=False)

    def _purge(self, now: datetime) -> None:
        ttl = timedelta(seconds=self._config.entry_ttl_seconds)
        expired = [
            stable_key
            for stable_key, entry in self._entries.items()
            if now - entry.updated_at > ttl
        ]
        for stable_key in expired:
            self._entries.pop(stable_key, None)

    def _active_samples(
        self, entry: ProviderCircuitEntry | None, now: datetime
    ) -> tuple[ProviderCircuitSample, ...]:
        if entry is None:
            return ()
        window = timedelta(seconds=self._config.observation_window_seconds)
        return tuple(
            sample for sample in entry.samples if now - sample.observed_at <= window
        )

    def _health_for(
        self, entry: ProviderCircuitEntry | None, now: datetime
    ) -> ModelDeploymentHealth:
        if entry is None:
            return ModelDeploymentHealth.AVAILABLE
        if entry.opened_at is not None and now - entry.opened_at < timedelta(
            seconds=self._config.cooldown_seconds
        ):
            return ModelDeploymentHealth.OPEN_CIRCUIT
        failures = sum(
            sample.failure_class is not None
            for sample in self._active_samples(entry, now)
        )
        return (
            ModelDeploymentHealth.DEGRADED
            if failures
            else ModelDeploymentHealth.AVAILABLE
        )

    def _checked_now(self) -> datetime:
        value = self._now()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("circuit clock must return a timezone-aware datetime")
        return value


__all__ = (
    "ProcessLocalProviderCircuitHealth",
    "ProviderCircuitAdmission",
    "ProviderCircuitConfig",
    "ProviderCircuitEntry",
    "ProviderCircuitKey",
    "ProviderCircuitSample",
    "ProviderCircuitSnapshot",
)
