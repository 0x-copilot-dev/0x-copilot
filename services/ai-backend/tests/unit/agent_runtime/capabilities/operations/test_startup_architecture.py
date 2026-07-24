from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from agent_runtime.capabilities.operations.contracts import (
    OperationAdapter,
    OperationGatewayMode,
)
from agent_runtime.capabilities.operations.errors import (
    OperationEnforcementNotReadyError,
)
from agent_runtime.settings import RuntimeSettings
from runtime_api.app import RuntimeApiAppFactory
from runtime_worker.loop import RuntimeWorker


def _settings(mode: OperationGatewayMode) -> RuntimeSettings:
    return RuntimeSettings.load(environ={"OPERATION_GATEWAY_MODE": mode.value})


class TestStartupGuardWiring:
    def test_api_refuses_enforce_before_building_any_ports(self) -> None:
        with pytest.raises(OperationEnforcementNotReadyError):
            RuntimeApiAppFactory.create_app(
                settings=_settings(OperationGatewayMode.ENFORCE),
                configure_logging_on_create=False,
                configure_telemetry_on_create=False,
            )

    def test_worker_refuses_enforce_before_constructing_handlers(self) -> None:
        with pytest.raises(OperationEnforcementNotReadyError):
            RuntimeWorker(
                persistence=object(),  # type: ignore[arg-type]
                event_store=object(),  # type: ignore[arg-type]
                queue=object(),  # type: ignore[arg-type]
                settings=_settings(OperationGatewayMode.ENFORCE),
            )

    def test_off_and_shadow_do_not_require_future_dependencies(self) -> None:
        from agent_runtime.capabilities.operations.context import (
            OperationGatewayStartupGuard,
        )

        for mode in (
            OperationGatewayMode.OFF,
            OperationGatewayMode.SHADOW,
        ):
            OperationGatewayStartupGuard.validate(mode=mode)


class TestNoNewAuthority:
    def test_adapter_protocol_has_no_apply_or_external_commit_method(self) -> None:
        methods = {
            name
            for name, value in inspect.getmembers(OperationAdapter, inspect.isfunction)
            if not name.startswith("_")
        }
        assert methods == {"execute_read", "build_proposal"}

    def test_operation_package_has_no_model_builder_or_provider_dispatch(
        self,
    ) -> None:
        package = (
            Path(__file__).parents[5]
            / "src"
            / "agent_runtime"
            / "capabilities"
            / "operations"
        )
        source = "\n".join(
            path.read_text(encoding="utf-8") for path in sorted(package.glob("*.py"))
        )
        for forbidden in (
            "build_chat_model(",
            "client.call_tool(",
            ".awrite(",
            ".aedit(",
            "commit_effect(",
            "apply_effect(",
        ):
            assert forbidden not in source
