"""Compare-and-append revision of an artifact the agent already published.

Publication mints a new durable object. Without a revise verb, "add one more
row" could only mint a second one — which is exactly what the product did: two
artifact ids, both at revision 1, both titled the same, two canvas tabs.

The domain service already had ``append_revision_from_stream`` and the human
edit path already used it. Only the model surface was missing the verb, so this
adds the exposure rather than a second write lane: the request goes through the
same operation gateway and lands in the same A2 service call.

Losing that compare-and-append to a hand edit is the ORDINARY case, not an edge
one — publish, tweak a cell, ask for one more row is a normal loop — so the tool
recovers from it here instead of describing the recovery and hoping. A live
journey caught the difference: told to re-read and retry, one run did and one
run simply reported a dead end to the user, same prompt, same product. Recovery
that depends on model compliance is a coin flip, and a coin flip is not a
feature. See :mod:`agent_runtime.artifacts.content_merge` for what "recover"
can and cannot decide.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable
from uuid import UUID

from pydantic import (
    Field,
    PositiveInt,
    ValidationError,
    field_validator,
    model_validator,
)

from agent_runtime.artifacts.content_merge import (
    ArtifactMergeStatus,
    ThreeWayTextMerge,
)
from agent_runtime.artifacts.contracts import (
    ArtifactStoredRecord,
    ArtifactStoredRevision,
)
from agent_runtime.artifacts.errors import ArtifactErrorCode
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
        "The artifact changed since that revision — the user may have edited it. "
        "Read the artifact's current revision, re-apply your change on top of it, "
        "and revise again from that revision number. Nothing was overwritten. "
        "That retry is your next step, not a result to report to the user."
    )
    #: Reached only after the automatic re-apply has already run and declined.
    #: Distinct wording because the advice differs: an identical blind retry
    #: cannot help here, and saying so is what stops the model from making one.
    STALE_OVERLAPS = (
        "The artifact changed since that revision, and your change rewrites the "
        "same lines the user changed, so re-applying it on top of theirs would "
        "have discarded their edit. Nothing was overwritten. Read the "
        "artifact's current revision, combine the two changes yourself, and "
        "revise again from that revision number. That is your next step, not a "
        "result to report to the user."
    )
    SEALED = (
        "That run has finished, so this revision could not be attributed to it. "
        "Do not retry with the same run."
    )

    #: Failure codes this tool can turn into an instruction the model can act on.
    #: Anything absent falls back to the gateway's generic summary, which is the
    #: honest answer when there is no better move to suggest.
    _BY_CODE = {
        ArtifactErrorCode.CONFLICT.value: STALE,
        ArtifactErrorCode.DIGEST_MISMATCH.value: STALE,
        ArtifactErrorCode.SEALED_RUN.value: SEALED,
    }

    @classmethod
    def for_failure(cls, code: str | None) -> str | None:
        """Return actionable wording for a known failure code, else ``None``.

        Deliberately a lookup over a closed vocabulary rather than substring
        matching on a message: the mapping breaks loudly if a code is renamed,
        where matching on prose would silently stop recognising the case and
        quietly regress the model to "it failed".
        """

        return None if code is None else cls._BY_CODE.get(code)


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


@runtime_checkable
class ArtifactContentReaderPort(Protocol):
    """Read-only artifact content, injected — never traversed out of a context.

    The gateway's presentation context withholds the artifact service from
    execution on purpose, so that a provider adapter cannot walk a request back
    to presentation authority. A tool that legitimately needs to READ an
    artifact is therefore handed exactly this at its construction site, where
    the wiring is visible, rather than reaching for authority it was not given.

    Structurally satisfied by ``ArtifactService`` as it already stands: both
    methods enforce caller scope, so the re-base can only ever read an artifact
    this principal could already open.
    """

    def get_metadata(
        self, *, org_id: str, user_id: str, artifact_id: str
    ) -> Awaitable[ArtifactStoredRecord]:
        """Return caller-scoped metadata, including the current revision."""

    def stream_revision(
        self, *, org_id: str, user_id: str, artifact_id: str, revision: int
    ) -> Awaitable[
        tuple[ArtifactStoredRecord, ArtifactStoredRevision, AsyncIterator[bytes]]
    ]:
        """Open one immutable revision's bytes in caller scope."""


class _RebaseUnavailable(Exception):
    """The re-base cannot be attempted, so the compare-and-append failure stands.

    Deliberately internal and never rendered: every path that raises it falls
    back to the same model-visible instruction, and the CAS guard has already
    guaranteed that nothing was written.
    """


@dataclass(frozen=True)
class _Rebase:
    """The agent's change, re-expressed against the revision that beat it."""

    parent_revision: int
    text: str


@dataclass(frozen=True)
class _Attempt:
    """One trip through the gateway, reduced to what the tool decides on."""

    artifact_id: str | None
    failure_code: str | None
    summary: str

    @property
    def revised(self) -> bool:
        return self.artifact_id is not None

    @property
    def lost_the_compare_and_append(self) -> bool:
        return self.failure_code == ArtifactErrorCode.CONFLICT.value


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
    #: Absent, the tool behaves exactly as it did before automatic re-basing:
    #: a lost compare-and-append is reported with the instruction to retry.
    content_reader: ArtifactContentReaderPort | None = None
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

        arguments = parsed.model_dump(mode="json")
        attempt = await self._append(
            arguments=arguments, source=parsed.revision_source()
        )
        if attempt.revised:
            return self._revised(attempt, revision=parsed.parent_revision + 1)
        if not attempt.lost_the_compare_and_append:
            return {
                "status": "failed",
                "message": _Messages.for_failure(attempt.failure_code)
                or attempt.summary,
            }
        return await self._recover(parsed, arguments=arguments)

    async def __call__(
        self, raw_input: ReviseArtifactInput | Mapping[str, Any] | str
    ) -> dict[str, object]:
        return await self.ainvoke(raw_input)

    async def _recover(
        self, parsed: ReviseArtifactInput, *, arguments: Mapping[str, object]
    ) -> dict[str, object]:
        """Re-apply the agent's change on top of the revision that beat it.

        Exactly one retry, and only from a decided merge — never a blind
        re-send at the newer revision, which is the same overwrite the guard
        exists to refuse. A second lost race goes back to the model: two hand
        edits landing inside one tool call is no longer the ordinary loop this
        recovers, and a retry loop against a live editor would never settle.
        """

        try:
            rebase = await self._rebase(parsed)
        except _RebaseUnavailable:
            return {"status": "failed", "message": _Messages.STALE}
        if rebase is None:
            return {"status": "failed", "message": _Messages.STALE_OVERLAPS}

        retry = await self._append(
            arguments={
                **arguments,
                "parent_revision": rebase.parent_revision,
                "content": rebase.text,
            },
            source=ArtifactRevisionSource(
                artifact_id=parsed.artifact_id,
                parent_revision=rebase.parent_revision,
                content=rebase.text.encode("utf-8"),
            ),
        )
        if not retry.revised:
            # Including a SECOND conflict, which maps back to the plain stale
            # instruction: a third automatic attempt would just race again.
            return {
                "status": "failed",
                "message": _Messages.for_failure(retry.failure_code) or retry.summary,
            }
        result = self._revised(retry, revision=rebase.parent_revision + 1)
        # Named so the agent narrates the revision that exists rather than the
        # one it asked for, and knows the user's edit is still in the document.
        result["rebased_onto_revision"] = rebase.parent_revision
        return result

    async def _rebase(self, parsed: ReviseArtifactInput) -> _Rebase | None:
        """Return the merged retry, ``None`` if the two changes collide.

        Raises ``_RebaseUnavailable`` when no merge could be attempted at all,
        which is a different fact from a merge that was attempted and refused.
        """

        if self.content_reader is None or parsed.content is None:
            # A ``content_ref`` body is not in hand, so there is nothing to
            # re-apply — the bytes live behind a reference the gateway resolves.
            raise _RebaseUnavailable
        identity = OperationContext.require().identity
        try:
            record = await self.content_reader.get_metadata(
                org_id=identity.org_id,
                user_id=identity.user_id,
                artifact_id=parsed.artifact_id,
            )
            current_revision = record.artifact.current_revision
            if current_revision <= parsed.parent_revision:
                # Not a lost race after all: nothing newer exists to re-base
                # onto, so the conflict came from somewhere this cannot fix.
                raise _RebaseUnavailable
            base = await self._read(parsed.artifact_id, parsed.parent_revision)
            current = await self._read(parsed.artifact_id, current_revision)
        except (asyncio.CancelledError, _RebaseUnavailable):
            raise
        except Exception:
            _LOGGER.debug("artifact_revision.rebase_read_failed")
            raise _RebaseUnavailable from None

        merged = await asyncio.to_thread(
            ThreeWayTextMerge.merge,
            base=base,
            current=current,
            proposed=parsed.content.encode("utf-8"),
        )
        _LOGGER.debug("artifact_revision.rebase merge_status=%s", merged.status.value)
        if merged.status is ArtifactMergeStatus.AMBIGUOUS:
            return None
        if merged.content is None:
            # Undecodable or too large to diff — a fact about the content, not
            # about the two changes, so it must not be reported as an overlap.
            raise _RebaseUnavailable
        if len(merged.content) > _Limits.INLINE_BYTES:
            # Two independently valid changes can still add up to a document
            # the inline transport will not carry.
            raise _RebaseUnavailable
        # Safe by construction: the merge only ever joins lines it decoded.
        return _Rebase(
            parent_revision=current_revision,
            text=merged.content.decode("utf-8"),
        )

    async def _read(self, artifact_id: str, revision: int) -> bytes:
        """Materialise one revision, bounded by the same cap the model gets."""

        assert self.content_reader is not None
        identity = OperationContext.require().identity
        _record, _stored, stream = await self.content_reader.stream_revision(
            org_id=identity.org_id,
            user_id=identity.user_id,
            artifact_id=artifact_id,
            revision=revision,
        )
        chunks: list[bytes] = []
        total = 0
        try:
            async for chunk in stream:
                total += len(chunk)
                if total > _Limits.INLINE_BYTES:
                    raise _RebaseUnavailable
                chunks.append(chunk)
        finally:
            # Abandoning the stream on the oversize path would leave the file
            # store's handle open until the collector happens to run.
            if isinstance(stream, AsyncGenerator):
                await stream.aclose()
        return b"".join(chunks)

    async def _append(
        self,
        *,
        arguments: Mapping[str, object],
        source: ArtifactRevisionSource,
    ) -> _Attempt:
        """Drive one revision through the gateway and classify what came back."""

        try:
            disposition = await self.gateway.invoke(
                _revision_request(arguments=arguments),
                _ArtifactRevisionAdapter(source),
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            # Nothing internal is leaked either way; the distinct instructions
            # below exist because a lost compare-and-append is recoverable and
            # a generic adapter failure is not.
            _LOGGER.debug("artifact_revision.tool_failed")
            return _Attempt(None, None, _Messages.FAILED)
        if (
            disposition.outcome is not OperationOutcome.SUCCEEDED
            or not disposition.artifact_ids
        ):
            return _Attempt(None, disposition.failure_code, disposition.agent_summary)
        return _Attempt(disposition.artifact_ids[0], None, disposition.agent_summary)

    @staticmethod
    def _revised(attempt: _Attempt, *, revision: int) -> dict[str, object]:
        return {
            "status": "revised",
            "artifact_id": attempt.artifact_id,
            "revision": revision,
            "stored_in": "artifact_library",
            "wrote_to_filesystem": False,
        }


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
