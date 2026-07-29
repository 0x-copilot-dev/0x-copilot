"""Explicit, provider-neutral publication of agent-authored artifacts."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from pydantic import Field, ValidationError, model_validator

from agent_runtime.capabilities.operations.catalog import DEFAULT_OPERATION_DESCRIPTORS
from agent_runtime.capabilities.operations.context import (
    OperationContext,
    OperationRequestFactory,
)
from agent_runtime.capabilities.operations.contracts import (
    ArtifactContentPart,
    ArtifactPublicationSource,
    OperationRawResult,
    OperationResultSummary,
)
from agent_runtime.capabilities.operations.disposition import (
    PresentationDispositionPolicy,
)
from agent_runtime.capabilities.operations.gateway import OperationGateway
from agent_runtime.execution.contracts import RuntimeContract
from agent_runtime.prompts.tools import PUBLISH_ARTIFACT_TOOL_DESCRIPTION
from agent_runtime.surfaces_v2.canonical_json import canonical_json_bytes, sha256_hex
from agent_runtime.surfaces_v2.entities import ArtifactIntent, OperationRequest
from agent_runtime.surfaces_v2.ledger_ids import OperationIdCodec
from agent_runtime.surfaces_v2.ledger_models import (
    ArtifactKind,
    ArtifactPresentationPreference,
    OperationOutcome,
    PresentationDecision,
)

_LOGGER = logging.getLogger(__name__)


class _Limits:
    INLINE_BYTES = 1024 * 1024
    TITLE_MAX = 240
    MEDIA_TYPE_MAX = 255
    FILENAME_MAX = 255
    REF_MAX = 2048


class _Messages:
    INVALID = "The artifact request is invalid and was not published."
    TOO_LARGE = "Inline artifact content exceeds 1 MiB; use a sanctioned content_ref."
    FAILED = "The artifact could not be published."


class _ArtifactMediaPolicy:
    """Small reviewed media-type allow-list for authored inline artifacts."""

    _DATASET = frozenset({"text/csv", "text/tab-separated-values", "application/json"})
    _DOCUMENT = frozenset({"text/markdown", "text/plain"})
    _CODE_EXACT = frozenset(
        {
            "text/plain",
            "text/javascript",
            "text/typescript",
            "application/json",
            "application/javascript",
            "application/typescript",
        }
    )

    @classmethod
    def validate(cls, *, kind: ArtifactKind, media_type: str) -> None:
        normalized = media_type.lower()
        bare = normalized.split(";", 1)[0].strip()
        if kind is ArtifactKind.FILE:
            return
        if kind is ArtifactKind.DATASET and bare in cls._DATASET:
            return
        if kind is ArtifactKind.DOCUMENT and bare in cls._DOCUMENT:
            return
        if kind is ArtifactKind.CODE and (
            bare in cls._CODE_EXACT
            or bare.startswith("text/x-")
            or bare.startswith("application/x-")
        ):
            return
        raise ValueError("media_type is not allowed for artifact kind")


class PublishArtifactInput(RuntimeContract):
    """Untrusted model arguments for the one explicit publication tool."""

    kind: ArtifactKind
    title: str = Field(min_length=1, max_length=_Limits.TITLE_MAX)
    media_type: str = Field(min_length=1, max_length=_Limits.MEDIA_TYPE_MAX)
    content: str | None = Field(default=None, max_length=_Limits.INLINE_BYTES)
    content_ref: str | None = Field(
        default=None, min_length=1, max_length=_Limits.REF_MAX
    )
    suggested_filename: str | None = Field(
        default=None, min_length=1, max_length=_Limits.FILENAME_MAX
    )
    presentation_preference: ArtifactPresentationPreference = (
        ArtifactPresentationPreference.AUTO
    )

    @model_validator(mode="after")
    def _valid_publication(self) -> PublishArtifactInput:
        if (self.content is None) == (self.content_ref is None):
            raise ValueError(
                "publish_artifact requires exactly one of content or content_ref"
            )
        _ArtifactMediaPolicy.validate(kind=self.kind, media_type=self.media_type)
        if (
            self.content is not None
            and len(self.content.encode("utf-8")) > _Limits.INLINE_BYTES
        ):
            raise ValueError(_Messages.TOO_LARGE)
        return self

    def artifact_intent(self) -> ArtifactIntent:
        return ArtifactIntent(
            kind=self.kind,
            title=self.title,
            media_type=self.media_type,
            suggested_filename=self.suggested_filename,
            presentation_preference=self.presentation_preference,
        )

    def publication_source(self) -> ArtifactPublicationSource:
        if self.content is not None:
            return ArtifactPublicationSource(content=self.content.encode("utf-8"))
        assert self.content_ref is not None
        return ArtifactPublicationSource(content_ref=self.content_ref)


@dataclass(frozen=True)
class _ArtifactPublicationAdapter:
    """Gateway adapter that supplies exactly one trusted publication transport."""

    source: ArtifactPublicationSource

    async def execute_read(self, _request: OperationRequest) -> OperationRawResult:
        return OperationRawResult(
            result_ref=self.source.content_ref,
            safe_summary="Artifact content is ready for publication.",
        )

    async def build_proposal(self, _request: OperationRequest) -> object:
        raise RuntimeError("artifact publication is an internal operation")

    async def artifact_publication(
        self, _request: OperationRequest
    ) -> ArtifactPublicationSource:
        return self.source


@dataclass(frozen=True)
class PublishArtifactTool:
    """Model-visible B1 publication tool; tenant identity stays in OperationContext."""

    gateway: OperationGateway
    name: str = "publish_artifact"
    description: str = PUBLISH_ARTIFACT_TOOL_DESCRIPTION

    async def ainvoke(
        self, raw_input: PublishArtifactInput | Mapping[str, Any] | str
    ) -> dict[str, object]:
        try:
            parsed = (
                raw_input
                if isinstance(raw_input, PublishArtifactInput)
                else PublishArtifactInput.model_validate(raw_input)
            )
        except ValidationError as exc:
            message = (
                _Messages.TOO_LARGE
                if _Messages.TOO_LARGE in str(exc)
                else _Messages.INVALID
            )
            return {"status": "failed", "message": message}

        try:
            request = _publication_request(
                intent=parsed.artifact_intent(),
                arguments=parsed.model_dump(mode="json"),
            )
            disposition = await self.gateway.invoke(
                request,
                _ArtifactPublicationAdapter(parsed.publication_source()),
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            _LOGGER.debug("artifact_publication.tool_failed")
            return {"status": "failed", "message": _Messages.FAILED}
        if (
            disposition.outcome is not OperationOutcome.SUCCEEDED
            or not disposition.artifact_ids
        ):
            return {"status": "failed", "message": disposition.agent_summary}

        presentation = _presentation_for(request)
        return {
            "status": "created",
            "artifact_id": disposition.artifact_ids[0],
            "revision": 1,
            "kind": parsed.kind.value,
            "title": parsed.title,
            "presentation": presentation.value,
            # Where the content actually went, stated in the result rather than
            # left to inference. A result silent on destination is what let the
            # model tell users their CSV was "saved to your documents folder"
            # while the process had no filesystem capability at all. Both fields
            # are server-derived; model input cannot set them.
            "stored_in": "artifact_library",
            "wrote_to_filesystem": False,
        }

    async def __call__(
        self, raw_input: PublishArtifactInput | Mapping[str, Any] | str
    ) -> dict[str, object]:
        return await self.ainvoke(raw_input)


class ArtifactContentPartPublisher:
    """Normalize provider content parts into the same gateway publication path."""

    def __init__(self, gateway: OperationGateway | None = None) -> None:
        self._gateway = gateway or OperationGateway(
            descriptors=DEFAULT_OPERATION_DESCRIPTORS
        )

    async def publish(self, result: object) -> tuple[str, ...]:
        """Publish valid explicit parts; malformed parts leave final prose untouched."""

        artifact_ids: list[str] = []
        for index, part in enumerate(iter_artifact_content_parts(result)):
            try:
                parsed = _input_from_content_part(part)
                request = _publication_request(
                    intent=parsed.artifact_intent(),
                    arguments={
                        "content_part_index": index,
                        **part.model_dump(mode="json"),
                    },
                )
                disposition = await self._gateway.invoke(
                    request,
                    _ArtifactPublicationAdapter(parsed.publication_source()),
                )
                if (
                    disposition.outcome is OperationOutcome.SUCCEEDED
                    and disposition.artifact_ids
                ):
                    artifact_ids.append(disposition.artifact_ids[0])
            except asyncio.CancelledError:
                raise
            except Exception:
                _LOGGER.debug("artifact_publication.content_part_failed")
        return tuple(artifact_ids)


def iter_artifact_content_parts(result: object) -> tuple[ArtifactContentPart, ...]:
    """Extract only explicit ``type: artifact`` parts; never inspect prose/fences."""

    candidates: list[object] = []
    if isinstance(result, Mapping):
        _append_content_candidates(candidates, result.get("content"))
        messages = result.get("messages")
        if isinstance(messages, Sequence) and not isinstance(
            messages, (str, bytes, bytearray)
        ):
            for message in messages:
                _append_content_candidates(
                    candidates,
                    message.get("content")
                    if isinstance(message, Mapping)
                    else getattr(message, "content", None),
                )
    else:
        _append_content_candidates(candidates, getattr(result, "content", None))

    parts: list[ArtifactContentPart] = []
    for candidate in candidates:
        if not isinstance(candidate, Mapping) or candidate.get("type") != "artifact":
            continue
        try:
            parts.append(ArtifactContentPart.model_validate(candidate))
        except ValidationError:
            _LOGGER.debug("artifact_publication.invalid_content_part")
    return tuple(parts)


def _append_content_candidates(candidates: list[object], value: object) -> None:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        candidates.extend(value)


def _input_from_content_part(part: ArtifactContentPart) -> PublishArtifactInput:
    """Apply B1's metadata/media/byte policy to a normalized provider part."""

    return PublishArtifactInput(
        kind=part.intent.kind,
        title=part.intent.title or "",
        media_type=part.intent.media_type or "",
        content=part.content,
        content_ref=part.content_ref,
        suggested_filename=part.intent.suggested_filename,
        presentation_preference=part.intent.presentation_preference,
    )


def _publication_request(
    *, intent: ArtifactIntent, arguments: Mapping[str, object]
) -> OperationRequest:
    """Derive a retry-stable operation id without accepting one from the model."""

    run_id = OperationContext.require().identity.run_id
    identity_arguments = {
        key: value
        for key, value in arguments.items()
        if key not in {"content", "content_ref"}
    }
    digest = sha256_hex(
        canonical_json_bytes({"run_id": run_id, "arguments": identity_arguments})
    )
    # Ledger operation ids intentionally accept only UUID4/7. Derive stable
    # UUID4-shaped bits from trusted canonical arguments so retry delivery
    # reuses A2's idempotency record without accepting a model-supplied id.
    identifier_bytes = bytearray(bytes.fromhex(digest[:32]))
    identifier_bytes[6] = (identifier_bytes[6] & 0x0F) | 0x40
    identifier_bytes[8] = (identifier_bytes[8] & 0x3F) | 0x80
    operation_id = OperationIdCodec.format(UUID(bytes=bytes(identifier_bytes)))
    return OperationRequestFactory.create(
        capability="artifact",
        op="publish",
        arguments=arguments,
        artifact_intent=intent,
        operation_id=operation_id,
    )


def _presentation_for(request: OperationRequest) -> PresentationDecision:
    descriptor = DEFAULT_OPERATION_DESCRIPTORS.resolve("artifact", "publish")
    if descriptor is None:  # pragma: no cover - checked-in catalog invariant
        return PresentationDecision.NONE
    return PresentationDispositionPolicy.decide(
        request,
        descriptor,
        OperationResultSummary(
            result_ref=None,
            safe_summary="Artifact publication completed.",
        ),
    )


__all__ = (
    "ArtifactContentPartPublisher",
    "PublishArtifactInput",
    "PublishArtifactTool",
    "iter_artifact_content_parts",
)
