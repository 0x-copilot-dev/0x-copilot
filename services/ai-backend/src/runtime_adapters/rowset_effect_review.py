"""Digest-pinned material adapter for canonical row-set review projections."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
import hashlib
import json

from pydantic import ValidationError

from agent_runtime.api.rowset_effect_review import RowSetProposalResolverPort
from agent_runtime.artifacts.ports import ArtifactBlobStorePort
from agent_runtime.capabilities.tools.builtin.stage_rowset_write import (
    RowSetEffectProposal,
)
from agent_runtime.surfaces_v2.canonical_json import canonical_json_bytes
from agent_runtime.surfaces_v2.ledger_ids import OperationArgsRefCodec
from agent_runtime.surfaces_v2.rowset import (
    RowsetValidationError,
    RowsetValidator,
)
from runtime_adapters.artifact_references import (
    ArtifactReferenceKind,
    ArtifactReferenceRepositoryPort,
)

_MAX_PROPOSAL_BYTES = 1_048_576


async def _read_bounded(
    stream: AsyncIterator[bytes],
    *,
    limit: int,
) -> bytes | None:
    body = bytearray()
    async for chunk in stream:
        if not isinstance(chunk, bytes) or len(body) + len(chunk) > limit:
            return None
        body.extend(chunk)
    return bytes(body)


@dataclass(frozen=True)
class RuntimeRowSetProposalResolver(RowSetProposalResolverPort):
    """Resolve only an active, owner-scoped, digest-matching effect reference."""

    blobs: ArtifactBlobStorePort
    references: ArtifactReferenceRepositoryPort

    async def resolve(
        self,
        *,
        org_id: str,
        user_id: str,
        content_ref: str,
        digest: str,
    ) -> RowSetEffectProposal | None:
        try:
            OperationArgsRefCodec.parse(content_ref)
        except ValueError:
            return None
        edges = await self.references.list_edges(org_id=org_id, user_id=user_id)
        edge = next(
            (
                candidate
                for candidate in edges
                if candidate.reference_kind is ArtifactReferenceKind.EFFECT
                and candidate.reference_id == content_ref
                and candidate.blob_key == digest
                and candidate.released_at is None
            ),
            None,
        )
        if edge is None:
            return None
        try:
            body = await _read_bounded(
                await self.blobs.open_stream(edge.blob_key),
                limit=_MAX_PROPOSAL_BYTES,
            )
        except (FileNotFoundError, OSError, ValueError):
            return None
        if body is None or hashlib.sha256(body).hexdigest() != digest:
            return None
        try:
            parsed = json.loads(body)
            proposal = RowSetEffectProposal.model_validate(parsed)
            RowsetValidator.validate(
                rows=proposal.rows,
                agent_holds=proposal.agent_holds,
            )
        except (
            UnicodeDecodeError,
            json.JSONDecodeError,
            ValidationError,
            RowsetValidationError,
            ValueError,
        ):
            return None
        if canonical_json_bytes(proposal.model_dump(mode="json")) != body:
            return None
        return proposal


__all__ = ["RuntimeRowSetProposalResolver"]
