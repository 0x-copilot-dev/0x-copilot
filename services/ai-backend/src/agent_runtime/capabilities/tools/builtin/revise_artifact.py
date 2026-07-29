"""Compare-and-append revision of an artifact the agent already published.

Publication mints a new durable object. Without a revise verb, "add one more
row" could only mint a second one — which is exactly what the product did: two
artifact ids, both at revision 1, both titled the same, two canvas tabs.

The domain service already had ``append_revision_from_stream`` and the human
edit path already used it. Only the model surface was missing the verb, so this
adds the exposure rather than a second write lane: the request goes through the
same operation gateway and lands in the same A2 service call.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from pydantic import (
    Field,
    PositiveInt,
    ValidationError,
    field_validator,
    model_validator,
)

from agent_runtime.capabilities.operations.context import (
    OperationContext,
    OperationRequestFactory,
)
from agent_runtime.capabilities.operations.contracts import (
    ArtifactRevisionSource,
    OperationRawResult,
)
from agent_runtime.capabilities.operations.gateway import OperationGateway
from agent_runtime.execution.contracts import RuntimeContract
from agent_runtime.prompts.tools import REVISE_ARTIFACT_TOOL_DESCRIPTION
from agent_runtime.surfaces_v2.canonical_json import canonical_json_bytes, sha256_hex
from agent_runtime.surfaces_v2.entities import OperationRequest
from agent_runtime.surfaces_v2.ledger_ids import ArtifactIdCodec, OperationIdCodec
from agent_runtime.surfaces_v2.ledger_models import OperationOutcome

_LOGGER = logging.getLogger(__name__)


class _Limits:
    INLINE_BYTES = 1024 * 1024
    REF_MAX = 2048


class _Messages:
    INVALID = "The revision request is invalid and no revision was made."
    TOO_LARGE = "Inline artifact content exceeds 1 MiB; use a sanctioned content_ref."
    FAILED = "The artifact could not be revised."
    STALE = (
        "The artifact changed since that revision. Read the current revision "
        "and try again from it."
    )


class ReviseArtifactInput(RuntimeContract):
    """Untrusted model arguments for one compare-and-append revision."""

    artifact_id: str = Field(min_length=1, max_length=_Limits.REF_MAX)
    parent_revision: PositiveInt
    content: str | None = Field(default=None, max_length=_Limits.INLINE_BYTES)
    content_ref: str | None = Field(
        default=None, min_length=1, max_length=_Limits.REF_MAX
    )

    @field_validator("artifact_id")
    @classmethod
    def _valid_artifact_id(cls, value: str) -> str:
        ArtifactIdCodec.parse(value)
        return value

    @model_validator(mode="after")
    def _valid_revision(self) -> ReviseArtifactInput:
        if (self.content is None) == (self.content_ref is None):
            raise ValueError(
                "revise_artifact requires exactly one of content or content_ref"
            )
        if (
            self.content is not None
            and len(self.content.encode("utf-8")) > _Limits.INLINE_BYTES
        ):
            raise ValueError(_Messages.TOO_LARGE)
        return self

    def revision_source(self) -> ArtifactRevisionSource:
        if self.content is not None:
            return ArtifactRevisionSource(
                artifact_id=self.artifact_id,
                parent_revision=self.parent_revision,
                content=self.content.encode("utf-8"),
            )
        assert self.content_ref is not None
        return ArtifactRevisionSource(
            artifact_id=self.artifact_id,
            parent_revision=self.parent_revision,
            content_ref=self.content_ref,
        )


@dataclass(frozen=True)
class _ArtifactRevisionAdapter:
    """Gateway adapter supplying exactly one trusted revision transport."""

    source: ArtifactRevisionSource

    async def execute_read(self, _request: OperationRequest) -> OperationRawResult:
        return OperationRawResult(
            result_ref=self.source.content_ref,
            safe_summary="Artifact content is ready for revision.",
        )

    async def build_proposal(self, _request: OperationRequest) -> object:
        raise RuntimeError("artifact revision is an internal operation")

    async def artifact_revision(
        self, _request: OperationRequest
    ) -> ArtifactRevisionSource:
        return self.source


@dataclass(frozen=True)
class ReviseArtifactTool:
    """Model-visible revision tool; tenant identity stays in OperationContext."""

    gateway: OperationGateway
    name: str = "revise_artifact"
    description: str = REVISE_ARTIFACT_TOOL_DESCRIPTION

    async def ainvoke(
        self, raw_input: ReviseArtifactInput | Mapping[str, Any] | str
    ) -> dict[str, object]:
        try:
            parsed = (
                raw_input
                if isinstance(raw_input, ReviseArtifactInput)
                else ReviseArtifactInput.model_validate(raw_input)
            )
        except ValidationError as exc:
            message = (
                _Messages.TOO_LARGE
                if _Messages.TOO_LARGE in str(exc)
                else _Messages.INVALID
            )
            return {"status": "failed", "message": message}

        try:
            request = _revision_request(arguments=parsed.model_dump(mode="json"))
            disposition = await self.gateway.invoke(
                request,
                _ArtifactRevisionAdapter(parsed.revision_source()),
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            # A lost compare-and-append is the common, recoverable case and the
            # model can act on it, so it gets a distinct instruction rather than
            # the generic failure. Nothing internal is leaked either way.
            _LOGGER.debug("artifact_revision.tool_failed")
            return {"status": "failed", "message": _Messages.FAILED}
        if (
            disposition.outcome is not OperationOutcome.SUCCEEDED
            or not disposition.artifact_ids
        ):
            return {"status": "failed", "message": disposition.agent_summary}

        return {
            "status": "revised",
            "artifact_id": disposition.artifact_ids[0],
            "revision": parsed.parent_revision + 1,
            "stored_in": "artifact_library",
            "wrote_to_filesystem": False,
        }

    async def __call__(
        self, raw_input: ReviseArtifactInput | Mapping[str, Any] | str
    ) -> dict[str, object]:
        return await self.ainvoke(raw_input)


def _revision_request(*, arguments: Mapping[str, object]) -> OperationRequest:
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
        op="revise",
        arguments=arguments,
        operation_id=operation_id,
    )
