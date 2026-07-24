from __future__ import annotations

import asyncio

import pytest
from langchain_core.tools import StructuredTool

from agent_runtime.capabilities.operations.context import OperationContext
from agent_runtime.capabilities.operations.contracts import OperationGatewayMode
from agent_runtime.capabilities.operations.probes import (
    OperationShadowProbe,
    wrap_model_tool_for_shadow,
)
from agent_runtime.surfaces_v2.ledger_models import (
    LedgerEventType,
    PresentationDecision,
)
from tests.unit.agent_runtime.capabilities.operations.helpers import (
    BoundContextMixin,
    RecordingArtifactService,
    RecordingEmitter,
    RecordingMetrics,
)


class CancellingComparisonMetrics(RecordingMetrics):
    def classification_mismatch(self, **_values: object) -> None:
        raise asyncio.CancelledError

    def disposition_mismatch(self, **_values: object) -> None:
        raise asyncio.CancelledError


class TestLegacyShadowProbe(BoundContextMixin):
    @pytest.mark.asyncio
    async def test_absent_context_is_strict_identity_and_exactly_once(self) -> None:
        calls = 0
        result = {"mutable": []}

        async def legacy() -> object:
            nonlocal calls
            calls += 1
            return result

        observed = await OperationShadowProbe.invoke_legacy(
            capability="builtin",
            op="web_search",
            arguments={"query": "hello"},
            legacy=legacy,
        )

        assert observed is result
        assert calls == 1

    @pytest.mark.asyncio
    async def test_off_context_is_strict_identity_and_emits_nothing(self) -> None:
        emitter = RecordingEmitter()
        token = self.bind(
            emitter=emitter,
            mode=OperationGatewayMode.OFF,
        )
        calls = 0
        result = b"\x00legacy-bytes\xff"

        async def legacy() -> bytes:
            nonlocal calls
            calls += 1
            return result

        try:
            observed = await OperationShadowProbe.invoke_legacy(
                capability="builtin",
                op="web_search",
                arguments={"query": "hello"},
                legacy=legacy,
            )
        finally:
            OperationContext.unbind(token)

        assert observed is result
        assert calls == 1
        assert emitter.calls == 0
        assert emitter.events == []

    @pytest.mark.asyncio
    async def test_shadow_emits_once_without_fabricated_result_ref(self) -> None:
        emitter = RecordingEmitter()
        metrics = RecordingMetrics()
        token = self.bind(emitter=emitter, metrics=metrics)
        calls = 0
        result = {"provider": "opaque"}

        async def legacy() -> object:
            nonlocal calls
            calls += 1
            return result

        try:
            observed = await OperationShadowProbe.invoke_legacy(
                capability="builtin",
                op="web_search",
                arguments={"query": "hello"},
                legacy=legacy,
            )
        finally:
            OperationContext.unbind(token)

        assert observed is result
        assert calls == 1
        assert [item[0] for item in emitter.events] == [
            LedgerEventType.OPERATION_REQUESTED,
            LedgerEventType.OPERATION_CLASSIFIED,
            LedgerEventType.OPERATION_COMPLETED,
        ]
        completed = emitter.events[-1][1]
        assert "result_ref" not in completed
        assert "operation://" not in repr(emitter.events)
        assert [name for name, _ in metrics.calls] == [
            "requested",
            "completed",
        ]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("fail_on_call", "expected_emitter_calls"),
        [(1, 2), (2, 3), (3, 3)],
    )
    async def test_emitter_failure_at_every_shadow_event_is_fail_soft(
        self, fail_on_call: int, expected_emitter_calls: int
    ) -> None:
        emitter = RecordingEmitter(fail_on_call=fail_on_call)
        token = self.bind(emitter=emitter)
        calls = 0
        result = {"unchanged": True}

        async def legacy() -> object:
            nonlocal calls
            calls += 1
            return result

        try:
            observed = await OperationShadowProbe.invoke_legacy(
                capability="builtin",
                op="web_search",
                arguments={},
                legacy=legacy,
            )
        finally:
            OperationContext.unbind(token)

        assert observed is result
        assert calls == 1
        assert emitter.calls == expected_emitter_calls
        assert all(
            event_type
            in {
                LedgerEventType.OPERATION_REQUESTED,
                LedgerEventType.OPERATION_CLASSIFIED,
                LedgerEventType.OPERATION_COMPLETED,
            }
            for event_type, _, _ in emitter.events
        )

    @pytest.mark.asyncio
    async def test_metric_failure_is_fail_soft_and_uses_bounded_unknown_key(
        self,
    ) -> None:
        metrics = RecordingMetrics(fail=True)
        emitter = RecordingEmitter()
        token = self.bind(emitter=emitter, metrics=metrics)
        calls = 0

        async def legacy() -> str:
            nonlocal calls
            calls += 1
            return "unchanged"

        try:
            result = await OperationShadowProbe.invoke_legacy(
                capability="provider-with-user-id-123",
                op="dynamic-user-tool-456",
                arguments={},
                legacy=legacy,
            )
        finally:
            OperationContext.unbind(token)

        assert result == "unchanged"
        assert calls == 1
        assert len(emitter.events) == 3

    @pytest.mark.asyncio
    async def test_comparison_telemetry_cancellation_cannot_cancel_legacy(
        self,
    ) -> None:
        metrics = CancellingComparisonMetrics()
        token = self.bind(metrics=metrics)
        result = {"identity": []}
        calls = 0

        async def legacy() -> object:
            nonlocal calls
            calls += 1
            return result

        try:
            observed = await OperationShadowProbe.invoke_legacy(
                capability="workspace",
                op="write",
                arguments={"file_path": "notes.md", "content": "hello"},
                legacy=legacy,
                legacy_class="none",
                legacy_disposition=PresentationDecision.ACTIVITY_ONLY,
            )
        finally:
            OperationContext.unbind(token)

        assert observed is result
        assert calls == 1

    @pytest.mark.asyncio
    async def test_unknown_metric_labels_collapse_to_one_bounded_bucket(
        self,
    ) -> None:
        metrics = RecordingMetrics()
        token = self.bind(metrics=metrics)
        try:
            await OperationShadowProbe.invoke_legacy(
                capability="provider-with-user-id-123",
                op="dynamic-user-tool-456",
                arguments={"path": "/private/user-text"},
                legacy=lambda: self._value("unchanged"),
            )
        finally:
            OperationContext.unbind(token)

        for _name, values in metrics.calls:
            assert values["capability"] == "unknown"
            assert values["op"] == "unknown"
            assert "/private/user-text" not in repr(values)

    @pytest.mark.asyncio
    async def test_unserializable_arguments_degrade_to_direct_legacy_call(
        self,
    ) -> None:
        emitter = RecordingEmitter()
        token = self.bind(emitter=emitter)
        calls = 0

        async def legacy() -> str:
            nonlocal calls
            calls += 1
            return "legacy"

        try:
            result = await OperationShadowProbe.invoke_legacy(
                capability="builtin",
                op="web_search",
                arguments={"not-json": {object()}},
                legacy=legacy,
            )
        finally:
            OperationContext.unbind(token)

        assert result == "legacy"
        assert calls == 1
        assert emitter.events == []

    @pytest.mark.asyncio
    async def test_legacy_exception_and_cancellation_are_not_rewritten(
        self,
    ) -> None:
        token = self.bind()
        try:
            with pytest.raises(RuntimeError, match="legacy-visible-error"):
                await OperationShadowProbe.invoke_legacy(
                    capability="builtin",
                    op="web_search",
                    arguments={},
                    legacy=self._raise_runtime_error,
                )
            with pytest.raises(asyncio.CancelledError):
                await OperationShadowProbe.invoke_legacy(
                    capability="builtin",
                    op="web_search",
                    arguments={},
                    legacy=self._raise_cancelled,
                )
        finally:
            OperationContext.unbind(token)

    @staticmethod
    async def _raise_runtime_error() -> object:
        raise RuntimeError("legacy-visible-error")

    @staticmethod
    async def _raise_cancelled() -> object:
        raise asyncio.CancelledError

    @pytest.mark.asyncio
    async def test_predicted_disposition_is_metric_only(self) -> None:
        emitter = RecordingEmitter()
        metrics = RecordingMetrics()
        token = self.bind(emitter=emitter, metrics=metrics)
        try:
            await OperationShadowProbe.invoke_legacy(
                capability="workspace",
                op="write",
                arguments={"path": "notes.md", "content": "hello"},
                legacy=lambda: self._value("legacy-write-result"),
                legacy_disposition=PresentationDecision.ACTIVITY_ONLY,
            )
        finally:
            OperationContext.unbind(token)

        assert "disposition_mismatch" in [name for name, _ in metrics.calls]
        assert all(
            event_type.value.startswith("operation.")
            for event_type, _, _ in emitter.events
        )
        assert not any(
            key in payload
            for _, payload, _ in emitter.events
            for key in ("surface_id", "stage_id", "proposal_ref", "result_ref")
        )

    @staticmethod
    async def _value(value: object) -> object:
        return value


class TestTypedModelArtifactObservation(BoundContextMixin):
    @pytest.mark.asyncio
    async def test_plain_text_and_code_fences_create_no_operation(self) -> None:
        emitter = RecordingEmitter()
        token = self.bind(emitter=emitter)
        try:
            await OperationShadowProbe.observe_model_result(
                {
                    "content": [
                        {
                            "type": "text",
                            "text": "```python\nprint('not intent')\n```",
                        }
                    ]
                }
            )
        finally:
            OperationContext.unbind(token)

        assert emitter.events == []

    @pytest.mark.asyncio
    async def test_huge_untyped_bytes_cannot_be_observed_as_artifact(self) -> None:
        emitter = RecordingEmitter()
        token = self.bind(emitter=emitter)
        try:
            await OperationShadowProbe.observe_model_result(
                {
                    "content": [
                        {
                            "type": "artifact",
                            "intent": {
                                "kind": "code",
                                "presentation_preference": "canvas",
                            },
                            "content_ref": "payload://source",
                            "artifact_content": b"x" * (1024 * 1024),
                        }
                    ]
                }
            )
        finally:
            OperationContext.unbind(token)

        assert emitter.events == []

    @pytest.mark.asyncio
    async def test_explicit_typed_part_records_supplied_ref_without_publishing(
        self,
    ) -> None:
        emitter = RecordingEmitter()
        artifact_service = RecordingArtifactService("unused")
        token = self.bind(
            emitter=emitter,
            artifact_service=artifact_service,
        )
        try:
            await OperationShadowProbe.observe_model_result(
                {
                    "content": [
                        {
                            "type": "artifact",
                            "intent": {
                                "kind": "document",
                                "title": "Notes",
                                "media_type": "text/markdown",
                                "suggested_filename": "notes.md",
                                "presentation_preference": "canvas",
                            },
                            "content_ref": "payload://immutable-model-output",
                        }
                    ]
                }
            )
        finally:
            OperationContext.unbind(token)

        assert [event[0] for event in emitter.events] == [
            LedgerEventType.OPERATION_REQUESTED,
            LedgerEventType.OPERATION_CLASSIFIED,
            LedgerEventType.OPERATION_COMPLETED,
        ]
        assert emitter.events[-1][1]["result_ref"] == "payload://immutable-model-output"
        assert artifact_service.calls == []

    @pytest.mark.asyncio
    async def test_typed_part_observation_is_fail_soft_after_first_event(
        self,
    ) -> None:
        emitter = RecordingEmitter(fail_on_call=2)
        result = {
            "content": [
                {
                    "type": "artifact",
                    "intent": {
                        "kind": "code",
                        "presentation_preference": "canvas",
                    },
                    "content_ref": "payload://immutable-code-output",
                }
            ]
        }
        token = self.bind(emitter=emitter)
        try:
            await OperationShadowProbe.observe_model_result(result)
        finally:
            OperationContext.unbind(token)

        assert result["content"][0]["content_ref"] == (
            "payload://immutable-code-output"
        )
        assert emitter.calls == 3


class TestModelToolWrapper(BoundContextMixin):
    @pytest.mark.asyncio
    async def test_wrapper_preserves_schema_result_and_single_execution(
        self,
    ) -> None:
        calls = 0
        result = {"same": []}

        async def original(value: str) -> object:
            nonlocal calls
            calls += 1
            assert value == "hello"
            return result

        tool = StructuredTool.from_function(
            coroutine=original,
            name="web_search",
            description="test",
        )
        emitter = RecordingEmitter(fail_on_call=2)
        token = self.bind(emitter=emitter)
        try:
            wrapped = wrap_model_tool_for_shadow(tool)
            observed = await wrapped.ainvoke({"value": "hello"})  # type: ignore[attr-defined]
        finally:
            OperationContext.unbind(token)

        assert observed is result
        assert calls == 1
        assert getattr(wrapped, "name") == tool.name
        assert getattr(wrapped, "args_schema") == tool.args_schema
