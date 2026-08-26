from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib

from agent_runtime.execution.model_invocation.circuit_health import (
    ProcessLocalProviderCircuitHealth,
    ProviderCircuitAdmission,
    ProviderCircuitConfig,
    ProviderCircuitKey,
)
from agent_runtime.execution.model_invocation.contracts import (
    ModelCredentialMode,
    ModelDeploymentHealth,
    ModelFailureClass,
)


class _Clock:
    def __init__(self) -> None:
        self.value = datetime(2026, 7, 28, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.value

    def advance(self, seconds: int) -> None:
        self.value += timedelta(seconds=seconds)


def _key(
    deployment: str,
    *,
    credential_mode: ModelCredentialMode = ModelCredentialMode.DEPLOYMENT,
    subject: str | None = None,
) -> ProviderCircuitKey:
    fingerprint = (
        f"sha256:{hashlib.sha256(subject.encode()).hexdigest()}"
        if subject is not None
        else None
    )
    return ProviderCircuitKey(
        provider="openai",
        deployment_id=deployment,
        region="us-east",
        credential_mode=credential_mode,
        credential_fingerprint=fingerprint,
    )


def test_failure_threshold_cooldown_and_probe_admission() -> None:
    clock = _Clock()
    health = ProcessLocalProviderCircuitHealth(
        ProviderCircuitConfig(open_failure_threshold=2, cooldown_seconds=10),
        now=clock,
    )
    key = _key("primary")
    assert (
        health.observe_failure(key, ModelFailureClass.PROVIDER_OVERLOADED)
        is ModelDeploymentHealth.DEGRADED
    )
    assert (
        health.observe_failure(key, ModelFailureClass.PRE_DISPATCH_TRANSIENT)
        is ModelDeploymentHealth.OPEN_CIRCUIT
    )
    assert health.admission(key) is ProviderCircuitAdmission.BLOCK_AUTOMATIC
    clock.advance(10)
    assert health.admission(key) is ProviderCircuitAdmission.ALLOW_PROBE
    assert health.observe_success(key) is ModelDeploymentHealth.AVAILABLE
    assert health.admission(key) is ProviderCircuitAdmission.ALLOW


def test_a_model_the_provider_will_not_serve_is_not_evidence_about_the_provider() -> (
    None
):
    """A 404 for one model id says nothing about the deployment's health.

    ``_CIRCUIT_RELEVANT_FAILURES`` is an allow-set, so omission is the whole
    mechanism. Asserted rather than assumed: counting these would trip the
    circuit on a provider that is answering perfectly well, and then block
    every *working* model behind the same key for the cooldown.
    """

    clock = _Clock()
    health = ProcessLocalProviderCircuitHealth(
        ProviderCircuitConfig(open_failure_threshold=1, cooldown_seconds=10),
        now=clock,
    )
    key = _key("primary")

    assert (
        health.observe_failure(key, ModelFailureClass.MODEL_NOT_FOUND)
        is ModelDeploymentHealth.AVAILABLE
    )
    assert health.admission(key) is ProviderCircuitAdmission.ALLOW


def test_capacity_and_ttl_are_bounded() -> None:
    clock = _Clock()
    health = ProcessLocalProviderCircuitHealth(
        ProviderCircuitConfig(max_entries=2, entry_ttl_seconds=10),
        now=clock,
    )
    for deployment in ("one", "two", "three"):
        health.observe_failure(_key(deployment), ModelFailureClass.PROVIDER_OVERLOADED)
    assert health.entry_count == 2
    assert health.health(_key("one")) is ModelDeploymentHealth.AVAILABLE
    clock.advance(11)
    assert health.health(_key("three")) is ModelDeploymentHealth.AVAILABLE
    assert health.entry_count == 0


def test_byok_auth_failure_isolated_by_opaque_user_credential_scope() -> None:
    health = ProcessLocalProviderCircuitHealth(
        ProviderCircuitConfig(open_failure_threshold=1)
    )
    alice = _key(
        "primary", credential_mode=ModelCredentialMode.BYOK, subject="alice-key"
    )
    bob = _key("primary", credential_mode=ModelCredentialMode.BYOK, subject="bob-key")
    deployment = _key("primary")

    assert (
        health.observe_failure(alice, ModelFailureClass.AUTH_INVALID)
        is ModelDeploymentHealth.OPEN_CIRCUIT
    )
    assert health.health(bob) is ModelDeploymentHealth.AVAILABLE
    assert health.health(deployment) is ModelDeploymentHealth.AVAILABLE
    assert "alice" not in alice.stable_key


def test_snapshot_restore_retains_fresh_state_and_drops_expired_state() -> None:
    clock = _Clock()
    config = ProviderCircuitConfig(open_failure_threshold=1, entry_ttl_seconds=10)
    first = ProcessLocalProviderCircuitHealth(config, now=clock)
    key = _key("primary")
    first.observe_failure(key, ModelFailureClass.PROVIDER_OVERLOADED)
    snapshot = first.snapshot()

    restarted = ProcessLocalProviderCircuitHealth(config, now=clock)
    restarted.restore(snapshot)
    assert restarted.health(key) is ModelDeploymentHealth.OPEN_CIRCUIT
    clock.advance(11)
    restarted.restore(snapshot)
    assert restarted.health(key) is ModelDeploymentHealth.AVAILABLE
