"""E2 D1 rollout contract, bridge, and startup-fatal canaries."""

from __future__ import annotations

from itertools import product
from types import SimpleNamespace

import pytest

from agent_runtime.rollout import (
    E2RolloutResolution,
    E2RolloutSettings,
    LegacyRolloutInputs,
    RolloutCapability,
    RolloutConfigurationError,
    RolloutMode,
    RolloutProvenance,
    RolloutStartupReadiness,
    RolloutStartupValidator,
)
from agent_runtime.settings import RuntimeSettings
from runtime_api import app as runtime_api_app
from runtime_api.app import RuntimeApiAppFactory
from runtime_worker.loop import RuntimeWorker


_CAPABILITIES = tuple(RolloutCapability)
_FIELD_NAMES = tuple(capability.value for capability in _CAPABILITIES)


def _safe_legacy() -> LegacyRolloutInputs:
    """Return a bridge snapshot with no legacy writable path."""

    return LegacyRolloutInputs(
        surfaces_v2=True,
        artifact_effects_v2=True,
        artifact_drafts_v2=True,
        operation_gateway_mode=RolloutMode.ENFORCE,
        workspace_effect_mode=RolloutMode.ENFORCE,
    )


def _resolution(
    modes: dict[RolloutCapability, RolloutMode],
    *,
    explicit_enforced: tuple[RolloutCapability, ...] | None = None,
) -> E2RolloutResolution:
    """Build a pure resolution for exhaustive validator coverage."""

    enforced = (
        explicit_enforced
        if explicit_enforced is not None
        else tuple(
            capability
            for capability, mode in modes.items()
            if mode is RolloutMode.ENFORCE
        )
    )
    return E2RolloutResolution.model_construct(
        modes=E2RolloutSettings.model_construct(
            **{capability.value: mode for capability, mode in modes.items()}
        ),
        provenance=RolloutProvenance.model_construct(
            explicit_enforced=enforced,
            legacy_elevated=(),
        ),
    )


def _all_modes(
    mode: RolloutMode = RolloutMode.OFF,
) -> dict[RolloutCapability, RolloutMode]:
    return {capability: mode for capability in _CAPABILITIES}


def _oracle_static_invalid(modes: dict[RolloutCapability, RolloutMode]) -> bool:
    """Independent truth table for the E2 D1 static dependency policy."""

    explicit = {
        capability for capability, mode in modes.items() if mode is RolloutMode.ENFORCE
    }

    def enforce(capability: RolloutCapability) -> bool:
        return modes[capability] is RolloutMode.ENFORCE

    if RolloutCapability.EFFECT_COMMIT in explicit and not (
        enforce(RolloutCapability.EFFECT_STAGER)
        and enforce(RolloutCapability.OPERATION_GATEWAY)
    ):
        return True
    for capability in (
        RolloutCapability.EFFECT_STAGER,
        RolloutCapability.MCP_GATEWAY,
        RolloutCapability.WORKSPACE_OVERLAY,
    ):
        if capability in explicit and not enforce(RolloutCapability.OPERATION_GATEWAY):
            return True
    if RolloutCapability.WORKSPACE_COMMIT in explicit and not all(
        enforce(dependency)
        for dependency in (
            RolloutCapability.WORKSPACE_OVERLAY,
            RolloutCapability.EFFECT_STAGER,
            RolloutCapability.EFFECT_COMMIT,
            RolloutCapability.OPERATION_GATEWAY,
        )
    ):
        return True
    for capability in (
        RolloutCapability.SANDBOX_ADAPTER,
        RolloutCapability.BROWSER_ADAPTER,
    ):
        if capability in explicit and not all(
            enforce(dependency)
            for dependency in (
                RolloutCapability.OPERATION_GATEWAY,
                RolloutCapability.EFFECT_STAGER,
                RolloutCapability.EFFECT_COMMIT,
            )
        ):
            return True
    return False


def test_contract_has_exactly_the_ten_e2_capabilities_and_dark_defaults() -> None:
    settings = RuntimeSettings.load(environ={})

    assert tuple(E2RolloutSettings.model_fields) == _FIELD_NAMES
    assert E2RolloutSettings.capabilities() == _CAPABILITIES
    assert {
        E2RolloutSettings.environment_name(capability) for capability in _CAPABILITIES
    } == {
        "ARTIFACT_REPOSITORY_MODE",
        "OPERATION_GATEWAY_MODE",
        "EFFECT_STAGER_MODE",
        "EFFECT_COMMIT_MODE",
        "PRESENTATION_V2_1_MODE",
        "WORKSPACE_OVERLAY_MODE",
        "WORKSPACE_COMMIT_MODE",
        "MCP_GATEWAY_MODE",
        "SANDBOX_ADAPTER_MODE",
        "BROWSER_ADAPTER_MODE",
    }
    assert all(
        settings.execution.rollout.modes.mode_for(capability) is RolloutMode.OFF
        for capability in _CAPABILITIES
    )


def test_surfaces_v2_is_not_a_hidden_e2_presentation_master_switch() -> None:
    settings = RuntimeSettings.load(environ={"SURFACES_V2": "true"})

    assert settings.execution.surfaces_v2 is True
    assert settings.execution.rollout.modes.presentation_v2_1 is RolloutMode.OFF


def test_legacy_bridge_is_monotonic_and_explicit_off_cannot_restore_direct_write() -> (
    None
):
    settings = RuntimeSettings.load(
        environ={
            "ARTIFACT_EFFECTS_V2": "true",
            "ARTIFACT_DRAFTS_V2": "true",
            "ARTIFACT_REPOSITORY_MODE": "off",
            "OPERATION_GATEWAY_MODE": "enforce",
            "WORKSPACE_EFFECT_MODE": "enforce",
        }
    )

    modes = settings.execution.rollout.modes
    assert modes.artifact_repository is RolloutMode.ENFORCE
    assert modes.operation_gateway is RolloutMode.ENFORCE
    assert modes.effect_stager is RolloutMode.ENFORCE
    assert modes.effect_commit is RolloutMode.ENFORCE
    assert modes.workspace_overlay is RolloutMode.ENFORCE
    assert modes.workspace_commit is RolloutMode.ENFORCE
    assert RolloutCapability.ARTIFACT_REPOSITORY in (
        settings.execution.rollout.provenance.legacy_elevated
    )


@pytest.mark.parametrize(
    "environment",
    (
        {"EFFECT_COMMIT_MODE": "enforce"},
        {
            "WORKSPACE_COMMIT_MODE": "enforce",
        },
    ),
)
def test_invalid_static_combinations_are_startup_fatal(
    environment: dict[str, str],
) -> None:
    with pytest.raises(RolloutConfigurationError):
        RuntimeSettings.load(environ=environment)


def test_new_enforce_rejects_legacy_direct_write_coexistence() -> None:
    with pytest.raises(RolloutConfigurationError, match="legacy writable path"):
        RuntimeSettings.load(environ={"ARTIFACT_REPOSITORY_MODE": "enforce"})

    with pytest.raises(RolloutConfigurationError, match="legacy writable path"):
        RuntimeSettings.load(
            environ={
                "SURFACES_V2": "false",
                "OPERATION_GATEWAY_MODE": "enforce",
                "EFFECT_STAGER_MODE": "enforce",
            }
        )

    with pytest.raises(RolloutConfigurationError, match="legacy writable path"):
        RuntimeSettings.load(
            environ={
                "OPERATION_GATEWAY_MODE": "enforce",
                "WORKSPACE_OVERLAY_MODE": "enforce",
                "WORKSPACE_COMMIT_MODE": "enforce",
                "DESKTOP_BROKER_URL": "http://127.0.0.1:1",
                "DESKTOP_BROKER_TOKEN": "redacted",
            }
        )


def test_invalid_mode_is_closed_and_names_only_the_safe_environment_key() -> None:
    for environment, safe_key in (
        ({"BROWSER_ADAPTER_MODE": "sometimes"}, "BROWSER_ADAPTER_MODE"),
        ({"OPERATION_GATEWAY_MODE": "sometimes"}, "OPERATION_GATEWAY_MODE"),
    ):
        with pytest.raises(RolloutConfigurationError) as error:
            RuntimeSettings.load(environ=environment)

        assert safe_key in str(error.value)
        assert "sometimes" not in str(error.value)


@pytest.mark.parametrize(
    "workspace_environment",
    (
        {
            "DESKTOP_BROKER_URL": "http://127.0.0.1:1",
            "DESKTOP_BROKER_TOKEN": "redacted",
        },
        {
            "RUNTIME_ENABLE_DESKTOP_WORKSPACE": "true",
            "DESKTOP_WORKSPACE_BROKER_URL": "http://127.0.0.1:1",
            "DESKTOP_WORKSPACE_BROKER_TOKEN": "redacted",
        },
    ),
)
def test_new_workspace_enforce_rejects_each_pre_e2_writable_broker_path(
    workspace_environment: dict[str, str],
) -> None:
    with pytest.raises(RolloutConfigurationError, match="legacy writable path"):
        RuntimeSettings.load(
            environ={
                "OPERATION_GATEWAY_MODE": "enforce",
                "WORKSPACE_OVERLAY_MODE": "enforce",
                "WORKSPACE_COMMIT_MODE": "enforce",
                **workspace_environment,
            }
        )


def test_exhaustive_three_to_the_tenth_static_combination_canary() -> None:
    """Every E2 mode combination agrees with an independent dependency oracle."""

    checked = 0
    for combination in product(tuple(RolloutMode), repeat=len(_CAPABILITIES)):
        modes = dict(zip(_CAPABILITIES, combination, strict=True))
        resolution = _resolution(modes)
        expected_invalid = _oracle_static_invalid(modes)
        try:
            RolloutStartupValidator.validate_static(resolution, legacy=_safe_legacy())
        except RolloutConfigurationError:
            actual_invalid = True
        else:
            actual_invalid = False
        assert actual_invalid is expected_invalid, modes
        checked += 1
    assert checked == 3 ** len(_CAPABILITIES)


def test_producer_enforce_requires_descriptor_and_executor_even_when_adapter_exists() -> (
    None
):
    modes = _all_modes()
    modes[RolloutCapability.OPERATION_GATEWAY] = RolloutMode.ENFORCE
    resolution = _resolution(modes)

    with pytest.raises(RolloutConfigurationError, match="descriptor and executor"):
        RolloutStartupValidator.validate_startup(
            resolution,
            readiness=RolloutStartupReadiness(
                operation_gateway_ready=True,
                descriptor_catalog_ready=False,
                executor_registry_ready=True,
            ),
        )


def test_workspace_commit_requires_real_c2_native_attestation() -> None:
    modes = _all_modes(RolloutMode.ENFORCE)
    resolution = _resolution(modes)
    ready_but_unattested = RolloutStartupReadiness(
        descriptor_catalog_ready=True,
        executor_registry_ready=True,
        artifact_repository_ready=True,
        operation_gateway_ready=True,
        effect_stager_ready=True,
        effect_commit_ready=True,
        presentation_v2_1_ready=True,
        workspace_overlay_ready=True,
        workspace_commit_ready=True,
        workspace_c2_native_attested=False,
        mcp_gateway_ready=True,
        sandbox_adapter_ready=True,
        browser_adapter_ready=True,
    )

    with pytest.raises(RolloutConfigurationError, match="C2 isolation"):
        RolloutStartupValidator.validate_startup(
            resolution,
            readiness=ready_but_unattested,
        )


def test_fully_ready_startup_accepts_all_enforced_capabilities() -> None:
    modes = _all_modes(RolloutMode.ENFORCE)
    resolution = _resolution(modes)
    readiness = RolloutStartupReadiness(
        descriptor_catalog_ready=True,
        executor_registry_ready=True,
        artifact_repository_ready=True,
        operation_gateway_ready=True,
        effect_stager_ready=True,
        effect_commit_ready=True,
        presentation_v2_1_ready=True,
        workspace_overlay_ready=True,
        workspace_commit_ready=True,
        workspace_c2_native_attested=True,
        mcp_gateway_ready=True,
        sandbox_adapter_ready=True,
        browser_adapter_ready=True,
    )

    RolloutStartupValidator.validate_startup(resolution, readiness=readiness)


def test_settings_resolution_is_an_immutable_startup_snapshot() -> None:
    environment = {"PRESENTATION_V2_1_MODE": "shadow"}
    settings = RuntimeSettings.load(environ=environment)
    environment["PRESENTATION_V2_1_MODE"] = "enforce"

    assert settings.execution.rollout.modes.presentation_v2_1 is RolloutMode.SHADOW


def test_api_boot_stores_and_logs_only_safe_rollout_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded: list[tuple[str, dict[str, object]]] = []

    class _Logger:
        def info(self, event: str, *, metadata: dict[str, object]) -> None:
            recorded.append((event, metadata))

    monkeypatch.setattr(runtime_api_app, "_STRUCTURED_LOGGER", _Logger())
    settings = RuntimeSettings.load(environ={})
    app = RuntimeApiAppFactory.create_app(
        settings=settings,
        configure_logging_on_create=False,
        configure_telemetry_on_create=False,
    )

    assert app.state.e2_rollout is settings.execution.rollout
    event, metadata = next(
        item for item in recorded if item[0] == "e2_rollout_resolved"
    )
    assert event == "e2_rollout_resolved"
    assert metadata["process"] == "api"
    assert metadata["modes"] == settings.execution.rollout.modes.as_safe_mapping()
    assert set(metadata).isdisjoint({"path", "token", "url", "credential"})


def test_worker_rejects_unwired_new_presentation_enforce_before_handlers() -> None:
    settings = RuntimeSettings.load(environ={"PRESENTATION_V2_1_MODE": "enforce"})

    with pytest.raises(RolloutConfigurationError, match="startup adapter/executor"):
        RuntimeWorker(
            persistence=SimpleNamespace(),  # type: ignore[arg-type]
            event_store=SimpleNamespace(),  # type: ignore[arg-type]
            queue=SimpleNamespace(),  # type: ignore[arg-type]
            settings=settings,
        )
