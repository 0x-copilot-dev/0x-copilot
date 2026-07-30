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
    SurfaceAccent,
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
    NOT_ALLOWED = "media_type is not allowed for artifact kind"
    # Name the kind that WAS required: a bare "invalid" is what makes a model
    # retry the identical call. The submitted media_type is deliberately not
    # echoed — it is model input, and repeating it into the next turn's context
    # carries injection risk for no diagnostic gain, since the required kind
    # already says everything the model needs to correct the call.
    _WRONG_KIND = (
        "This media_type must be published as kind '{kind}', not 'file'. Use "
        "'file' only for media no structured renderer can parse — the file view "
        "shows metadata and a download only, with no table and no editor, so a "
        "reader cannot edit what it holds."
    )

    @classmethod
    def wrong_kind(cls, kind: ArtifactKind) -> str:
        return cls._WRONG_KIND.format(kind=kind.value)


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
    def _bare(cls, media_type: str) -> str:
        return media_type.lower().split(";", 1)[0].strip()

    @classmethod
    def owning_kind(cls, media_type: str) -> ArtifactKind | None:
        """The one structured kind that owns a media type, when exactly one does.

        Derived from the allow-lists above rather than a second hand-kept table,
        so adding a media type to ``_DATASET`` also stops ``file`` from claiming
        it. ``text/plain`` and ``application/json`` each sit in two lists and are
        therefore owned by neither — ``file`` stays legal for them, because which
        structured renderer should own those bytes is genuinely undecidable here.
        """

        bare = cls._bare(media_type)
        owners = tuple(
            kind
            for kind, allowed in (
                (ArtifactKind.DATASET, cls._DATASET),
                (ArtifactKind.DOCUMENT, cls._DOCUMENT),
                (ArtifactKind.CODE, cls._CODE_EXACT),
            )
            if bare in allowed
        )
        return owners[0] if len(owners) == 1 else None

    @classmethod
    def file_kind_rejection(cls, raw_input: object) -> str | None:
        """Guidance for a ``kind: file`` request refused above, else ``None``.

        Reads untrusted model arguments, so it re-consults ``owning_kind``
        instead of restating the rule: one decision, asked twice.
        """

        if not isinstance(raw_input, Mapping):
            return None
        kind = raw_input.get("kind")
        if not isinstance(kind, str) or kind.strip().lower() != ArtifactKind.FILE.value:
            return None
        media_type = raw_input.get("media_type")
        if not isinstance(media_type, str):
            return None
        owner = cls.owning_kind(media_type)
        return None if owner is None else _Messages.wrong_kind(owner)

    @classmethod
    def validate(cls, *, kind: ArtifactKind, media_type: str) -> None:
        bare = cls._bare(media_type)
        if kind is ArtifactKind.FILE:
            # PRD-B2 D5 scopes the file renderer to unsupported/binary media: it
            # shows filename, size, digest and a download, and nothing else. A
            # media type that D4's dataset grid already parses must therefore not
            # be published as `file`, or the artifact lands in a renderer that by
            # contract cannot preview or edit it. That is exactly how a CSV
            # reached a canvas tab offering the reader no way to change a cell.
            owner = cls.owning_kind(bare)
            if owner is None:
                return
            raise ValueError(_Messages.wrong_kind(owner))
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
        raise ValueError(_Messages.NOT_ALLOWED)


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
    # A name from a closed set, never a colour. Pydantic rejects anything else
    # before the value can reach a surface, so a model that tries to send
    # `#ff00ff` or a CSS fragment fails validation rather than styling a page.
    accent: SurfaceAccent | None = None

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
            accent=self.accent,
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
            if _Messages.TOO_LARGE in str(exc):
                message = _Messages.TOO_LARGE
            else:
                message = (
                    _ArtifactMediaPolicy.file_kind_rejection(raw_input)
                    or _Messages.INVALID
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
        accent=part.intent.accent,
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
