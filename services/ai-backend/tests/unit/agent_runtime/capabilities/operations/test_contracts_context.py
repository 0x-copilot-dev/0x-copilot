from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from agent_runtime.capabilities.operations.context import (
    OperationContext,
    OperationGatewayStartupGuard,
    OperationRequestFactory,
)
from agent_runtime.capabilities.operations.contracts import (
    ModelArtifactContentPart,
    OperationArgumentStore,
    OperationGatewayMode,
    OperationRawResult,
    ProposedEffect,
)
from agent_runtime.capabilities.operations.errors import (
    OperationEnforcementNotReadyError,
)
from agent_runtime.settings import RuntimeSettings
from agent_runtime.surfaces_v2.ledger_models import Producer
from tests.unit.agent_runtime.capabilities.operations.helpers import (
    BoundContextMixin,
)


class TestBoundOperationContext(BoundContextMixin):
    def test_request_uses_verified_run_identity_and_reference_only_arguments(
        self,
    ) -> None:
        token = self.bind()
        try:
            request = OperationRequestFactory.create(
                capability="seed:Linear",
                op="Create Issue!",
                arguments={"secret": "never-on-public-event", "count": 2},
                producer=Producer.MODEL,
            )
            active = OperationContext.require()
            stored = active.arguments.get(request.canonical_args_ref)
        finally:
            OperationContext.unbind(token)

        assert request.run_id == "run-1"
        assert request.capability == "linear"
        assert request.op == "create_issue_"
        assert request.args_digest
        assert "secret" not in request.model_dump(mode="json")
        assert stored is not None
        assert b"never-on-public-event" in stored[1]

    def test_subagent_scope_sets_producer_and_parent(self) -> None:
        token = self.bind()
        try:
            parent = OperationRequestFactory.create(
                capability="builtin",
                op="task",
                arguments={},
            )
            with OperationContext.operation_scope(parent.operation_id):
                with OperationContext.producer_scope(Producer.SUBAGENT):
                    child = OperationRequestFactory.create(
                        capability="builtin",
                        op="web_search",
                        arguments={"query": "bounded"},
                    )
        finally:
            OperationContext.unbind(token)

        assert child.producer is Producer.SUBAGENT
        assert child.parent_operation_id == parent.operation_id

    def test_run_local_argument_store_is_idempotent_but_not_durable(self) -> None:
        store = OperationArgumentStore()
        store.put(ref="operation://one/args", digest="a", canonical_bytes=b"{}")
        store.put(ref="operation://one/args", digest="a", canonical_bytes=b"{}")
        with pytest.raises(ValueError, match="different bytes"):
            store.put(
                ref="operation://one/args",
                digest="b",
                canonical_bytes=b'{"changed":true}',
            )
        assert OperationArgumentStore().get("operation://one/args") is None

    def test_validated_tuple_values_normalize_to_canonical_json_lists(
        self,
    ) -> None:
        token = self.bind()
        try:
            request = OperationRequestFactory.create(
                capability="builtin",
                op="ask_a_question",
                arguments={"options": ("one", "two")},
            )
            stored = OperationContext.require().arguments.get(
                request.canonical_args_ref
            )
        finally:
            OperationContext.unbind(token)

        assert stored is not None
        assert stored[1] == b'{"options":["one","two"]}'


class TestBoundedResultContracts:
    @pytest.mark.parametrize("model", [OperationRawResult, ProposedEffect])
    def test_huge_raw_bytes_cannot_ride_result_contract(self, model: type) -> None:
        payload: dict[str, object]
        if model is OperationRawResult:
            payload = {
                "safe_summary": "done",
                "artifact_content": b"x" * (8 * 1024 * 1024),
            }
        else:
            payload = {
                "stage_id": "stage",
                "proposal_ref": "proposal://stage/revisions/1",
                "safe_summary": "proposed",
                "artifact_content": b"x" * (8 * 1024 * 1024),
            }
        with pytest.raises(ValidationError):
            model.model_validate(payload)

    def test_plain_code_fence_is_not_a_typed_artifact_part(self) -> None:
        with pytest.raises(ValidationError):
            ModelArtifactContentPart.model_validate(
                "```python\nprint('not intent')\n```"
            )
        with pytest.raises(ValidationError):
            ModelArtifactContentPart.model_validate(
                {
                    "type": "text",
                    "content": "```python\nprint('not intent')\n```",
                }
            )

    def test_typed_artifact_part_requires_a_logical_content_ref(self) -> None:
        with pytest.raises(ValidationError, match="must be logical"):
            ModelArtifactContentPart.model_validate(
                {
                    "type": "artifact",
                    "intent": {
                        "kind": "code",
                        "presentation_preference": "canvas",
                    },
                    "content_ref": "/tmp/provider-output.bin",
                }
            )


class TestGatewayModeSettings:
    def test_default_is_off_and_closed_values_parse(self) -> None:
        assert (
            RuntimeSettings.load(environ={}).execution.operation_gateway_mode
            is OperationGatewayMode.OFF
        )
        for mode in OperationGatewayMode:
            settings = RuntimeSettings.load(
                environ={"OPERATION_GATEWAY_MODE": mode.value}
            )
            assert settings.execution.operation_gateway_mode is mode
        with pytest.raises(ValueError, match="OperationGatewayMode"):
            RuntimeSettings.load(environ={"OPERATION_GATEWAY_MODE": "sometimes"})

    def test_enforce_requires_all_three_authoritative_dependencies(self) -> None:
        for values in (
            {},
            {"stage_dependency_ready": True},
            {"executor_dependency_ready": True},
            {"durable_arguments_ready": True},
            {
                "stage_dependency_ready": True,
                "executor_dependency_ready": True,
            },
        ):
            with pytest.raises(OperationEnforcementNotReadyError) as exc_info:
                OperationGatewayStartupGuard.validate(
                    mode=OperationGatewayMode.ENFORCE,
                    **values,
                )
            assert "durable canonical arguments" in exc_info.value.safe_message
        OperationGatewayStartupGuard.validate(
            mode=OperationGatewayMode.ENFORCE,
            stage_dependency_ready=True,
            executor_dependency_ready=True,
            durable_arguments_ready=True,
        )

    def test_operation_code_has_no_ad_hoc_environment_reads(self) -> None:
        package = (
            Path(__file__).parents[5]
            / "src"
            / "agent_runtime"
            / "capabilities"
            / "operations"
        )
        source = "\n".join(path.read_text() for path in package.glob("*.py"))
        assert "os.environ" not in source
        assert "os.getenv" not in source
        assert "getenv(" not in source
