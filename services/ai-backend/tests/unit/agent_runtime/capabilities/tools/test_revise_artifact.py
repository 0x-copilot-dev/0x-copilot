"""PRD-02 — the model's compare-and-append revision verb.

Without this verb the only way to "add one more row" was to publish again,
which minted a second artifact and a second canvas tab. These pin that the verb
reaches the same A2 service the human edit path uses, that it cannot overwrite a
revision the user wrote, and that it cannot change what an artifact is.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from agent_runtime.artifacts.errors import (
    ArtifactConflictError,
    ArtifactNotFoundError,
    ArtifactSealedRunError,
)
from agent_runtime.capabilities.operations.catalog import DEFAULT_OPERATION_DESCRIPTORS
from agent_runtime.capabilities.operations.context import OperationContext
from agent_runtime.capabilities.operations.contracts import OperationGatewayMode
from agent_runtime.capabilities.operations.gateway import OperationGateway
from agent_runtime.capabilities.tools.builtin.revise_artifact import (
    ReviseArtifactInput,
    ReviseArtifactTool,
    _Messages,
)
from agent_runtime.surfaces_v2.ledger_ids import ArtifactIdCodec
from agent_runtime.surfaces_v2.ledger_models import ArtifactAuthor
from tests.unit.agent_runtime.capabilities.operations.helpers import (
    BoundContextMixin,
)

ARTIFACT_ID = ArtifactIdCodec.format(uuid4())


@dataclass
class RecordingArtifactService:
    """Records the exact A2 call the gateway makes for a revision."""

    artifact_id: str = ARTIFACT_ID
    revision_calls: list[dict[str, object]] = field(default_factory=list)
    error: Exception | None = None

    async def append_revision_from_stream(self, **kwargs: object) -> object:
        self.revision_calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return SimpleNamespace(
            record=SimpleNamespace(
                artifact=SimpleNamespace(artifact_id=self.artifact_id)
            )
        )

    async def publish_from_bytes(self, **_kwargs: object) -> object:
        raise AssertionError("a revision must not create a second artifact")

    async def publish_from_source(self, **_kwargs: object) -> object:
        raise AssertionError("a revision must not create a second artifact")

    async def promote_source(self, **_kwargs: object) -> object:
        raise AssertionError("a revision must not promote a new source")


class TestReviseArtifactInput:
    def test_kind_and_media_type_are_not_accepted(self) -> None:
        """Immutable properties of the artifact; a revise cannot restyle it."""

        with pytest.raises(ValidationError):
            ReviseArtifactInput.model_validate(
                {
                    "artifact_id": ARTIFACT_ID,
                    "parent_revision": 1,
                    "content": "x",
                    "kind": "document",
                }
            )

    def test_parent_revision_is_required(self) -> None:
        """No blind overwrite: the model must say what it edited from."""

        with pytest.raises(ValidationError):
            ReviseArtifactInput.model_validate(
                {"artifact_id": ARTIFACT_ID, "content": "x"}
            )

    def test_exactly_one_transport(self) -> None:
        for payload in (
            {"artifact_id": ARTIFACT_ID, "parent_revision": 1},
            {
                "artifact_id": ARTIFACT_ID,
                "parent_revision": 1,
                "content": "x",
                "content_ref": "payload://x",
            },
        ):
            with pytest.raises(ValidationError):
                ReviseArtifactInput.model_validate(payload)

    def test_a_malformed_artifact_id_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            ReviseArtifactInput.model_validate(
                {
                    "artifact_id": "../../etc/passwd",
                    "parent_revision": 1,
                    "content": "x",
                }
            )


class TestReviseArtifactTool(BoundContextMixin):
    @staticmethod
    def _tool() -> ReviseArtifactTool:
        return ReviseArtifactTool(
            gateway=OperationGateway(descriptors=DEFAULT_OPERATION_DESCRIPTORS)
        )

    @pytest.mark.asyncio
    async def test_revision_appends_to_the_same_artifact(self) -> None:
        """The live defect: "add one more row" must not mint a second artifact."""

        service = RecordingArtifactService()
        token = self.bind(artifact_service=service, mode=OperationGatewayMode.OFF)
        try:
            result = await self._tool().ainvoke(
                {
                    "artifact_id": ARTIFACT_ID,
                    "parent_revision": 1,
                    "content": "a,b\n1,2\n",
                }
            )
        finally:
            OperationContext.unbind(token)

        assert result["status"] == "revised"
        assert result["artifact_id"] == ARTIFACT_ID
        assert result["revision"] == 2
        assert len(service.revision_calls) == 1
        request = service.revision_calls[0]["request"]
        assert request.artifact_id == ARTIFACT_ID
        assert request.parent_revision == 1

    @pytest.mark.asyncio
    async def test_the_revision_is_agent_authored_in_the_live_run(self) -> None:
        """PRD-01: agent work stays in the RUN lane and names its open run."""

        service = RecordingArtifactService()
        token = self.bind(artifact_service=service, mode=OperationGatewayMode.OFF)
        try:
            await self._tool().ainvoke(
                {"artifact_id": ARTIFACT_ID, "parent_revision": 1, "content": "x"}
            )
        finally:
            OperationContext.unbind(token)

        call = service.revision_calls[0]
        assert call["provenance"].author is ArtifactAuthor.MODEL
        assert call["request"].acting_run_id is not None

    @pytest.mark.asyncio
    async def test_a_lost_compare_and_append_does_not_write(self) -> None:
        """A stale model must lose to the user's newer revision, not clobber it."""

        service = RecordingArtifactService(error=ArtifactConflictError())
        token = self.bind(artifact_service=service, mode=OperationGatewayMode.OFF)
        try:
            result = await self._tool().ainvoke(
                {"artifact_id": ARTIFACT_ID, "parent_revision": 1, "content": "stale"}
            )
        finally:
            OperationContext.unbind(token)

        # Reported as a failure, never as a revision, so the model cannot go on
        # to tell the user it changed something it did not.
        assert result["status"] == "failed"
        assert "artifact_id" not in result
        # Safe public wording only — no exception type, traceback, or internal
        # path reaches model-visible output.
        message = str(result["message"])
        assert "ArtifactConflictError" not in message
        assert "Traceback" not in message
        # ...and it must be RECOVERABLE, not merely safe. A live run showed the
        # model losing this CAS and being told only "Operation failed; no
        # external change was made", which leaves it nothing to do. The whole
        # point of the distinct reason is that re-reading and retrying is the
        # move, so the instruction has to say so.
        assert message == _Messages.STALE
        assert "current revision" in message
        assert "Nothing was overwritten" in message

    @pytest.mark.asyncio
    async def test_a_sealed_run_says_not_to_retry_the_same_run(self) -> None:
        """The opposite advice: a sealed run is not fixed by trying again."""

        service = RecordingArtifactService(error=ArtifactSealedRunError())
        token = self.bind(artifact_service=service, mode=OperationGatewayMode.OFF)
        try:
            result = await self._tool().ainvoke(
                {"artifact_id": ARTIFACT_ID, "parent_revision": 1, "content": "x"}
            )
        finally:
            OperationContext.unbind(token)

        assert result["status"] == "failed"
        assert str(result["message"]) == _Messages.SEALED
        # Distinct from the stale case, because the recoverable action differs —
        # one says retry from the current revision, the other says do not retry.
        assert str(result["message"]) != _Messages.STALE

    @pytest.mark.asyncio
    async def test_an_unmapped_failure_keeps_the_generic_summary(self) -> None:
        """No invented advice for a failure this tool cannot characterise."""

        service = RecordingArtifactService(error=ArtifactNotFoundError())
        token = self.bind(artifact_service=service, mode=OperationGatewayMode.OFF)
        try:
            result = await self._tool().ainvoke(
                {"artifact_id": ARTIFACT_ID, "parent_revision": 1, "content": "x"}
            )
        finally:
            OperationContext.unbind(token)

        assert result["status"] == "failed"
        message = str(result["message"])
        assert message not in {_Messages.STALE, _Messages.SEALED}
        assert message == "Operation failed; no external change was made."

    @pytest.mark.asyncio
    async def test_invalid_input_never_reaches_the_repository(self) -> None:
        service = RecordingArtifactService()
        token = self.bind(artifact_service=service, mode=OperationGatewayMode.OFF)
        try:
            result = await self._tool().ainvoke({"artifact_id": ARTIFACT_ID})
        finally:
            OperationContext.unbind(token)

        assert result["status"] == "failed"
        assert service.revision_calls == []

    @pytest.mark.asyncio
    async def test_the_result_states_where_the_content_went(self) -> None:
        """PRD-04 — narration is grounded in the result, not in inference."""

        service = RecordingArtifactService()
        token = self.bind(artifact_service=service, mode=OperationGatewayMode.OFF)
        try:
            result = await self._tool().ainvoke(
                {"artifact_id": ARTIFACT_ID, "parent_revision": 1, "content": "x"}
            )
        finally:
            OperationContext.unbind(token)

        assert result["stored_in"] == "artifact_library"
        assert result["wrote_to_filesystem"] is False
