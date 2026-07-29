"""B1 publication tool and provider-content normalization tests."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from uuid import uuid4

import pytest

from agent_runtime.capabilities.operations.catalog import DEFAULT_OPERATION_DESCRIPTORS
from agent_runtime.capabilities.operations.context import OperationContext
from agent_runtime.capabilities.operations.contracts import OperationGatewayMode
from agent_runtime.capabilities.operations.gateway import OperationGateway
from agent_runtime.capabilities.tools.builtin.publish_artifact import (
    ArtifactContentPartPublisher,
    PublishArtifactTool,
)
from agent_runtime.surfaces_v2.ledger_ids import ArtifactIdCodec
from agent_runtime.surfaces_v2.ledger_models import ArtifactAuthor, Producer
from tests.unit.agent_runtime.capabilities.operations.helpers import (
    BoundContextMixin,
)


@dataclass
class RecordingArtifactService:
    artifact_id: str = field(default_factory=lambda: ArtifactIdCodec.format(uuid4()))
    byte_calls: list[dict[str, object]] = field(default_factory=list)
    source_calls: list[dict[str, object]] = field(default_factory=list)

    async def publish_from_bytes(self, **kwargs: object) -> object:
        self.byte_calls.append(kwargs)
        return self._result()

    async def publish_from_source(self, **kwargs: object) -> object:
        self.source_calls.append(kwargs)
        return self._result()

    async def promote_source(self, **_kwargs: object) -> object:
        raise AssertionError("B1 publication must not use user promotion semantics")

    def _result(self) -> object:
        return SimpleNamespace(
            record=SimpleNamespace(
                artifact=SimpleNamespace(artifact_id=self.artifact_id)
            )
        )


class TestPublishArtifactTool(BoundContextMixin):
    @staticmethod
    def _tool() -> PublishArtifactTool:
        return PublishArtifactTool(
            gateway=OperationGateway(descriptors=DEFAULT_OPERATION_DESCRIPTORS)
        )

    @pytest.mark.asyncio
    async def test_inline_content_uses_canonical_repository_once(self) -> None:
        service = RecordingArtifactService()
        token = self.bind(artifact_service=service, mode=OperationGatewayMode.OFF)
        try:
            tool = self._tool()
            raw = {
                "kind": "code",
                "title": "hello.py",
                "media_type": "text/x-python",
                "content": "print('hello')\n",
                "suggested_filename": "hello.py",
                "presentation_preference": "none",
            }
            first = await tool.ainvoke(raw)
            duplicate = await tool.ainvoke(raw)
        finally:
            OperationContext.unbind(token)

        assert first == {
            "status": "created",
            "artifact_id": service.artifact_id,
            "revision": 1,
            "kind": "code",
            "title": "hello.py",
            "presentation": "none",
            # Destination stated in the result, not left to inference. A result
            # silent on this is what let the model claim a published CSV was
            # "saved to your documents folder".
            "stored_in": "artifact_library",
            "wrote_to_filesystem": False,
        }
        assert duplicate == first
        assert len(service.byte_calls) == 1
        call = service.byte_calls[0]
        assert call["content"] == b"print('hello')\n"
        assert call["provenance"].author is ArtifactAuthor.MODEL
        assert call["request"].idempotency_key.startswith("op_")

    @pytest.mark.asyncio
    async def test_same_publication_identity_with_different_content_conflicts(
        self,
    ) -> None:
        service = RecordingArtifactService()
        token = self.bind(artifact_service=service, mode=OperationGatewayMode.OFF)
        try:
            tool = self._tool()
            created = await tool.ainvoke(
                {
                    "kind": "document",
                    "title": "notes.md",
                    "media_type": "text/markdown",
                    "content": "first version",
                }
            )
            conflict = await tool.ainvoke(
                {
                    "kind": "document",
                    "title": "notes.md",
                    "media_type": "text/markdown",
                    "content": "different version",
                }
            )
        finally:
            OperationContext.unbind(token)

        assert created["status"] == "created"
        assert conflict["status"] == "failed"
        assert len(service.byte_calls) == 1

    @pytest.mark.asyncio
    async def test_content_ref_uses_scoped_repository_source_and_subagent_author(
        self,
    ) -> None:
        service = RecordingArtifactService()
        token = self.bind(artifact_service=service, mode=OperationGatewayMode.OFF)
        try:
            with OperationContext.producer_scope(Producer.SUBAGENT):
                result = await self._tool().ainvoke(
                    {
                        "kind": "dataset",
                        "title": "report.csv",
                        "media_type": "text/csv",
                        "content_ref": "payload://report-output",
                    }
                )
        finally:
            OperationContext.unbind(token)

        assert result["status"] == "created"
        assert service.byte_calls == []
        assert len(service.source_calls) == 1
        call = service.source_calls[0]
        assert call["source_ref"] == "payload://report-output"
        assert call["provenance"].author is ArtifactAuthor.SUBAGENT
        assert call["provenance"].source_ref == "payload://report-output"

    @pytest.mark.asyncio
    async def test_invalid_media_and_oversize_input_create_nothing(self) -> None:
        service = RecordingArtifactService()
        token = self.bind(artifact_service=service, mode=OperationGatewayMode.OFF)
        try:
            tool = self._tool()
            invalid_media = await tool.ainvoke(
                {
                    "kind": "dataset",
                    "title": "report.csv",
                    "media_type": "text/markdown",
                    "content": "a,b\n1,2\n",
                }
            )
            oversize = await tool.ainvoke(
                {
                    "kind": "document",
                    "title": "large.md",
                    "media_type": "text/markdown",
                    "content": "x" * (1024 * 1024 + 1),
                }
            )
        finally:
            OperationContext.unbind(token)

        assert invalid_media["status"] == oversize["status"] == "failed"
        assert service.byte_calls == service.source_calls == []


class TestArtifactContentPartPublisher(BoundContextMixin):
    @pytest.mark.asyncio
    async def test_explicit_provider_part_normalizes_to_same_repository_path(
        self,
    ) -> None:
        service = RecordingArtifactService()
        token = self.bind(artifact_service=service, mode=OperationGatewayMode.OFF)
        try:
            artifact_ids = await ArtifactContentPartPublisher().publish(
                {
                    "messages": [
                        {
                            "content": [
                                {"type": "text", "text": "normal final prose"},
                                {
                                    "type": "artifact",
                                    "intent": {
                                        "kind": "document",
                                        "title": "notes.md",
                                        "media_type": "text/markdown",
                                        "presentation_preference": "chat_card",
                                    },
                                    "content": "# Notes\n",
                                },
                            ]
                        }
                    ]
                }
            )
        finally:
            OperationContext.unbind(token)

        assert artifact_ids == (service.artifact_id,)
        assert len(service.byte_calls) == 1
        assert service.byte_calls[0]["content"] == b"# Notes\n"

    @pytest.mark.asyncio
    async def test_prose_and_fenced_code_never_create_an_artifact(self) -> None:
        service = RecordingArtifactService()
        token = self.bind(artifact_service=service, mode=OperationGatewayMode.OFF)
        try:
            artifact_ids = await ArtifactContentPartPublisher().publish(
                {"content": [{"type": "text", "text": "```python\nprint(1)\n```"}]}
            )
        finally:
            OperationContext.unbind(token)

        assert artifact_ids == ()
        assert service.byte_calls == service.source_calls == []
