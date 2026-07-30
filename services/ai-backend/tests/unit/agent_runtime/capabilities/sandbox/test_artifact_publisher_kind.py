"""Which kind sandbox output takes, and why it is not the publish tool's rule.

``publish_artifact`` refuses ``kind: file`` for a media type a structured
renderer owns.  The sandbox publishers keep ``FILE`` for those same media
types.  That divergence is a decision, so it is asserted rather than left to be
rediscovered from two files that happen to disagree.
"""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import cast

import pytest
from pydantic import ValidationError

from agent_runtime.artifacts.contracts import ArtifactCreateRequest, ArtifactLimits
from agent_runtime.capabilities.sandbox.artifact_publisher import (
    ArtifactServiceSandboxPublisher,
)
from agent_runtime.capabilities.sandbox.contracts import SandboxArtifactPublication
from agent_runtime.capabilities.sandbox.result_publisher import (
    ArtifactServiceSandboxResultPublisher,
    SandboxResultPublication,
)
from agent_runtime.capabilities.tools.builtin.publish_artifact import (
    PublishArtifactInput,
    _ArtifactMediaPolicy,
)
from agent_runtime.surfaces_v2.ledger_models import ArtifactKind

_REVISION_REF = "artifact://art_550e8400-e29b-41d4-a716-446655440001/revisions/1"

#: Every media type exactly one structured renderer owns, derived from the
#: policy's own allow-lists rather than restated, so a type added to one of them
#: is covered here without anyone remembering to update this tuple.
_OWNED_MEDIA_TYPES = tuple(
    sorted(
        media_type
        for media_type in (
            _ArtifactMediaPolicy._DATASET
            | _ArtifactMediaPolicy._DOCUMENT
            | _ArtifactMediaPolicy._CODE_EXACT
        )
        if _ArtifactMediaPolicy.owning_kind(media_type) is not None
    )
)


class _ArtifactService:
    """Structural stand-in for A2 that records the request it was handed."""

    def __init__(self) -> None:
        self.requests: list[ArtifactCreateRequest] = []

    async def publish_from_stream(self, **kwargs: object) -> object:
        chunks = cast(AsyncIterator[bytes], kwargs["chunks"])
        content = b"".join([chunk async for chunk in chunks])
        self.requests.append(cast(ArtifactCreateRequest, kwargs["request"]))
        return SimpleNamespace(
            record=SimpleNamespace(
                current_revision=SimpleNamespace(
                    revision=SimpleNamespace(
                        artifact_id="art_550e8400-e29b-41d4-a716-446655440001",
                        content_ref=_REVISION_REF,
                        content_digest=hashlib.sha256(content).hexdigest(),
                        byte_size=len(content),
                    )
                )
            )
        )


class SandboxPublicationMixin:
    """Builders for one deliverable publication and one result publication."""

    ORG_ID = "org_1"
    USER_ID = "user_1"
    RUN_ID = "run_1"
    OPERATION_ID = "operation_1"
    CONTENT = b"month,bookings\n2026-05,128400\n"

    @staticmethod
    async def _chunks(content: bytes) -> AsyncIterator[bytes]:
        yield content

    async def publish_deliverable(self, media_type: str) -> ArtifactCreateRequest:
        """Publish one sandbox file and return the request that reached A2."""

        service = _ArtifactService()
        publisher = ArtifactServiceSandboxPublisher(
            service=cast(object, service),  # structural test double for A2 port
            org_id=self.ORG_ID,
            user_id=self.USER_ID,
        )
        await publisher.publish(
            publication=SandboxArtifactPublication(
                run_id=self.RUN_ID,
                operation_id=self.OPERATION_ID,
                source_path="/workspace/report.csv",
                media_type=media_type,
                suggested_filename="report.csv",
                title="Sandbox report",
                idempotency_key="sandbox-artifact:" + "e" * 32,
            ),
            chunks=self._chunks(self.CONTENT),
        )
        return service.requests[0]

    async def publish_result(self) -> ArtifactCreateRequest:
        """Publish one sandbox result envelope and return A2's request."""

        service = _ArtifactService()
        publisher = ArtifactServiceSandboxResultPublisher(
            service=cast(object, service),  # structural test double for A2 port
            org_id=self.ORG_ID,
            user_id=self.USER_ID,
        )
        content = b'{"exit_code":0}'
        await publisher.publish_result(
            publication=SandboxResultPublication(
                run_id=self.RUN_ID,
                operation_id=self.OPERATION_ID,
                content_digest=hashlib.sha256(content).hexdigest(),
                byte_size=len(content),
                idempotency_key="sandbox-result:" + "e" * 32,
            ),
            chunks=self._chunks(content),
        )
        return service.requests[0]


class TestSandboxOutputStaysAFile(SandboxPublicationMixin):
    @pytest.mark.parametrize("media_type", _OWNED_MEDIA_TYPES)
    @pytest.mark.asyncio
    async def test_a_renderer_owned_media_type_publishes_as_a_file(
        self, media_type: str
    ) -> None:
        """The sandbox states what it knows: bytes came out of a sandbox.

        The media type on a deliverable is a label put on a path before the
        command ran, and the bytes at that path are whatever an untrusted
        process wrote — nothing reads them.  Promoting that label to a kind
        would route unverified bytes into a parser and an editor.
        """
        request = await self.publish_deliverable(media_type)

        assert request.kind is ArtifactKind.FILE
        assert request.media_type == media_type

    @pytest.mark.asyncio
    async def test_the_result_envelope_publishes_as_a_file(self) -> None:
        request = await self.publish_result()

        assert request.kind is ArtifactKind.FILE
        assert request.media_type == "application/json"

    @pytest.mark.asyncio
    async def test_patch_entry_bytes_publish_as_a_file(self) -> None:
        """``SandboxPatchCollector`` shares this port for bytes that are never a
        tab — they back a patch entry the user reviews as a patch.  A media-type
        rule at this seam could not tell them from a deliverable."""
        request = await self.publish_deliverable("application/octet-stream")

        assert request.kind is ArtifactKind.FILE


class TestTheDivergenceFromTheToolPath:
    """The rule the tool applies to the same media types, asserted here so the
    two behaviours cannot silently converge.  ``TestPublishArtifactFileKindOwnership``
    owns the tool path's own coverage; this is only the contrast."""

    def test_the_owned_set_is_the_reviewed_one(self) -> None:
        """Named, not only derived, for two reasons.

        An empty derivation would leave every parametrized case above passing
        vacuously; and a media type added to an allow-list should stop here
        first, so whoever adds it decides what sandbox output of that type
        should render as instead of inheriting an answer by accident.
        """
        assert set(_OWNED_MEDIA_TYPES) == {
            "application/javascript",
            "application/typescript",
            "text/csv",
            "text/javascript",
            "text/markdown",
            "text/tab-separated-values",
            "text/typescript",
        }

    def test_an_author_may_not_publish_as_a_file_what_the_sandbox_does(self) -> None:
        """The asymmetry is deliberate, not an oversight.

        There ``kind`` and ``media_type`` are two halves of one statement by one
        author, so the policy only has to make them agree; nothing is inferred
        about bytes nobody read.
        """
        with pytest.raises(ValidationError):
            PublishArtifactInput(
                kind=ArtifactKind.FILE,
                title="report",
                media_type="text/csv",
                content=SandboxPublicationMixin.CONTENT.decode(),
            )


class TestKindIsAlsoAByteCeiling:
    def test_file_has_the_largest_ceiling_of_any_kind(self) -> None:
        """Reclassifying sandbox output could only lower what it may publish.

        A2 writes blobs under ``ArtifactLimits.for_kind``, and the sandbox's own
        deliverable ceiling (``download_changed_bytes``) defaults to 512 MiB —
        above every kind ceiling.  So a 12 MB bundle published as ``code`` would
        be refused, and the coordinator turns one failed publication into a
        failed collection for the whole operation.
        """
        limits = ArtifactLimits()
        file_ceiling = limits.for_kind(ArtifactKind.FILE).maximum_bytes

        assert file_ceiling == max(
            limits.for_kind(kind).maximum_bytes for kind in ArtifactKind
        )
        assert all(
            limits.for_kind(kind).maximum_bytes < file_ceiling
            for kind in ArtifactKind
            if kind is not ArtifactKind.FILE
        )
