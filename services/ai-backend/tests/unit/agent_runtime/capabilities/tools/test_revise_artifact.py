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

#: The AS-6 journey, as bytes. The agent publishes r1, the user fixes a cell by
#: hand in Studio (r2), then asks the agent for one more row — still holding r1.
PUBLISHED = b"id,name\n1,alice\n2,bob\n"
HAND_EDITED = b"id,name\n1,ALICE\n2,bob\n"
AGENT_APPENDED = b"id,name\n1,alice\n2,bob\n3,carol\n"
BOTH_CHANGES = b"id,name\n1,ALICE\n2,bob\n3,carol\n"


@dataclass
class RecordingArtifactService:
    """Records the exact A2 call the gateway makes, and enforces the real CAS.

    One object playing both roles the production wiring hands the tool — the
    write service and the read seam — because in production they ARE one
    object. A split fake could not catch a re-base that reads a revision the
    store never wrote, which is the whole risk of reading before retrying.
    """

    artifact_id: str = ARTIFACT_ID
    revision_calls: list[dict[str, object]] = field(default_factory=list)
    error: Exception | None = None
    #: Stored bytes in revision order; index 0 is revision 1.
    revisions: list[bytes] = field(default_factory=list)

    @property
    def head(self) -> int:
        return len(self.revisions)

    async def append_revision_from_stream(self, **kwargs: object) -> object:
        self.revision_calls.append(kwargs)
        if self.error is not None:
            raise self.error
        content = b"".join([chunk async for chunk in kwargs["chunks"]])
        if self.revisions and kwargs["request"].parent_revision != self.head:
            raise ArtifactConflictError()
        self.revisions.append(content)
        return SimpleNamespace(
            record=SimpleNamespace(
                artifact=SimpleNamespace(artifact_id=self.artifact_id)
            )
        )

    async def get_metadata(self, **_kwargs: object) -> object:
        return SimpleNamespace(artifact=SimpleNamespace(current_revision=self.head))

    async def stream_revision(self, *, revision: int, **_kwargs: object) -> object:
        return (None, None, self._chunks(self.revisions[revision - 1]))

    @staticmethod
    async def _chunks(content: bytes):
        yield content

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


class ReviseToolMixin(BoundContextMixin):
    @staticmethod
    def _tool(content_reader: object | None = None) -> ReviseArtifactTool:
        return ReviseArtifactTool(
            gateway=OperationGateway(descriptors=DEFAULT_OPERATION_DESCRIPTORS),
            content_reader=content_reader,  # type: ignore[arg-type]
        )

    async def _revise(
        self,
        service: RecordingArtifactService,
        *,
        with_reader: bool = True,
        **arguments: object,
    ) -> dict[str, object]:
        token = self.bind(artifact_service=service, mode=OperationGatewayMode.OFF)
        try:
            return await self._tool(service if with_reader else None).ainvoke(
                {"artifact_id": ARTIFACT_ID, **arguments}
            )
        finally:
            OperationContext.unbind(token)


class TestReviseArtifactTool(ReviseToolMixin):
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
        # With no read seam wired there is nothing to re-base against, so the
        # tool must not have tried a second write on a guess.
        assert len(service.revision_calls) == 1

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


class TestLostCompareAndAppendRecovers(ReviseToolMixin):
    """AS-6, live: whether the user's request happens must not be a coin flip.

    Two identical packaged-source runs of the same journey phase disagreed. One
    agent read the conflict, re-read the artifact and retried; the other read
    the same conflict and told the user it could not append the row. Nothing
    differed but the sampling. These pin the recovery in the tool, where it is
    decided the same way every time.
    """

    @pytest.mark.asyncio
    async def test_a_hand_edit_elsewhere_is_re_applied_automatically(self) -> None:
        """The reported case end to end: r1 published, r2 by hand, agent at r1."""

        service = RecordingArtifactService(revisions=[PUBLISHED, HAND_EDITED])

        result = await self._revise(
            service, parent_revision=1, content=AGENT_APPENDED.decode()
        )

        assert result["status"] == "revised"
        assert result["revision"] == 3
        # The agent asked to write r2 and got r3, so it must be told which
        # revision exists or its next revise starts stale all over again.
        assert result["rebased_onto_revision"] == 2
        # The point of the whole exercise: BOTH changes are in the document.
        assert service.revisions[-1] == BOTH_CHANGES
        assert len(service.revision_calls) == 2
        assert service.revision_calls[1]["request"].parent_revision == 2

    @pytest.mark.asyncio
    async def test_an_overlapping_change_is_refused_not_guessed(self) -> None:
        """The guard's real job: a rewrite of the edited row still loses."""

        service = RecordingArtifactService(revisions=[PUBLISHED, HAND_EDITED])

        result = await self._revise(
            service, parent_revision=1, content="id,name\n1,alicia\n2,bob\n"
        )

        assert result["status"] == "failed"
        assert result["message"] == _Messages.STALE_OVERLAPS
        # Not merely refused — untouched. The hand edit is still the head.
        assert service.revisions == [PUBLISHED, HAND_EDITED]
        assert len(service.revision_calls) == 1

    @pytest.mark.asyncio
    async def test_the_automatic_retry_happens_at_most_once(self) -> None:
        """A second lost race is the model's to resolve, not a retry loop's."""

        service = RecordingArtifactService(
            revisions=[PUBLISHED, HAND_EDITED], error=ArtifactConflictError()
        )

        result = await self._revise(
            service, parent_revision=1, content=AGENT_APPENDED.decode()
        )

        assert result["status"] == "failed"
        assert result["message"] == _Messages.STALE
        assert len(service.revision_calls) == 2

    @pytest.mark.asyncio
    async def test_a_conflict_with_nothing_newer_is_not_re_based(self) -> None:
        """Head still equals the parent, so the conflict came from elsewhere."""

        service = RecordingArtifactService(
            revisions=[PUBLISHED], error=ArtifactConflictError()
        )

        result = await self._revise(
            service, parent_revision=1, content=AGENT_APPENDED.decode()
        )

        assert result["message"] == _Messages.STALE
        assert len(service.revision_calls) == 1

    @pytest.mark.asyncio
    async def test_a_content_ref_body_cannot_be_re_based(self) -> None:
        """The bytes are behind a reference, so there is nothing in hand to merge."""

        service = RecordingArtifactService(
            revisions=[PUBLISHED, HAND_EDITED], error=ArtifactConflictError()
        )

        result = await self._revise(
            service, parent_revision=1, content_ref="payload://source-1"
        )

        # The plain instruction, not the overlap wording: no merge was tried,
        # so claiming the two changes collide would be an invented reason.
        assert result["message"] == _Messages.STALE
        assert len(service.revision_calls) == 1

    @pytest.mark.asyncio
    async def test_a_sealed_run_is_never_re_based(self) -> None:
        """Only a lost CAS is recoverable; retrying a sealed run is not."""

        service = RecordingArtifactService(
            revisions=[PUBLISHED, HAND_EDITED], error=ArtifactSealedRunError()
        )

        result = await self._revise(
            service, parent_revision=1, content=AGENT_APPENDED.decode()
        )

        assert result["message"] == _Messages.SEALED
        assert len(service.revision_calls) == 1

    @pytest.mark.asyncio
    async def test_a_re_based_write_stays_one_artifact(self) -> None:
        """The original BUG 2 must not come back through the recovery path."""

        service = RecordingArtifactService(revisions=[PUBLISHED, HAND_EDITED])

        result = await self._revise(
            service, parent_revision=1, content=AGENT_APPENDED.decode()
        )

        # ``RecordingArtifactService`` raises from every publish entry point, so
        # reaching a revised result at all proves nothing was minted; this pins
        # the id the agent is told to keep using.
        assert result["artifact_id"] == ARTIFACT_ID
