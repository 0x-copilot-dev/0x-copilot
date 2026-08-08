from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime, timezone
import hashlib
import json

import pytest

from agent_runtime.capabilities.tools.builtin.stage_rowset_write import (
    RowSetEffectProposal,
)
from agent_runtime.surfaces_v2.canonical_json import canonical_json_bytes
from agent_runtime.surfaces_v2.ledger_ids import OperationArgsRefCodec
from runtime_adapters.artifact_references import (
    ArtifactReferenceEdge,
    ArtifactReferenceKind,
)
from runtime_adapters.rowset_effect_review import RuntimeRowSetProposalResolver

pytestmark = pytest.mark.anyio

_ORG = "org-rowset-review"
_USER = "user-rowset-review"
_REF = OperationArgsRefCodec.format("op_00000000-0000-4000-8000-000000000001")
_PAYLOAD = {
    "proposal_kind": "row_set",
    "target_connector": "linear",
    "target_op": "update_issue",
    "title": "Reprioritize",
    "rows": [
        {
            "row_key": "row-a",
            "title": "Acme renewal",
            "target_args": {"id": "row-a", "priority": 2},
            "changes": [{"field": "priority", "old": 1, "new": 2}],
            "sends": [
                {
                    "arg": "id",
                    "origin": "proposed",
                    "column": None,
                    "old": None,
                    "new": "row-a",
                },
                {
                    "arg": "priority",
                    "origin": "proposed",
                    "column": "priority",
                    "old": 1,
                    "new": 2,
                },
            ],
        }
    ],
    "agent_holds": [],
}


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class _Blobs:
    def __init__(self, values: dict[str, bytes]) -> None:
        self.values = values

    async def open_stream(
        self,
        blob_key: str,
        *,
        start: int | None = None,
        end: int | None = None,
    ) -> AsyncIterator[bytes]:
        del start, end

        async def _stream() -> AsyncIterator[bytes]:
            body = self.values[blob_key]
            midpoint = max(1, len(body) // 2)
            yield body[:midpoint]
            yield body[midpoint:]

        return _stream()


class _References:
    def __init__(self, edges: tuple[ArtifactReferenceEdge, ...]) -> None:
        self.edges = edges
        self.calls: list[tuple[str, str | None]] = []

    async def list_edges(
        self,
        *,
        org_id: str,
        user_id: str | None = None,
    ) -> tuple[ArtifactReferenceEdge, ...]:
        self.calls.append((org_id, user_id))
        return tuple(
            edge
            for edge in self.edges
            if edge.org_id == org_id and (user_id is None or edge.user_id == user_id)
        )


def _edge(*, digest: str, user_id: str = _USER) -> ArtifactReferenceEdge:
    return ArtifactReferenceEdge(
        org_id=_ORG,
        edge_id=f"effect-{digest}",
        user_id=user_id,
        blob_key=digest,
        reference_kind=ArtifactReferenceKind.EFFECT,
        reference_id=_REF,
        created_at=datetime(2026, 7, 24, tzinfo=timezone.utc),
    )


async def test_resolves_only_owner_scoped_digest_pinned_canonical_material() -> None:
    proposal = RowSetEffectProposal.model_validate(_PAYLOAD)
    body = canonical_json_bytes(proposal.model_dump(mode="json"))
    digest = hashlib.sha256(body).hexdigest()
    references = _References((_edge(digest=digest),))
    resolver = RuntimeRowSetProposalResolver(
        blobs=_Blobs({digest: body}),  # type: ignore[arg-type]
        references=references,  # type: ignore[arg-type]
    )

    resolved = await resolver.resolve(
        org_id=_ORG,
        user_id=_USER,
        content_ref=_REF,
        digest=digest,
    )

    assert resolved == proposal
    assert references.calls == [(_ORG, _USER)]


async def test_foreign_reference_is_indistinguishable_from_missing() -> None:
    body = canonical_json_bytes(
        RowSetEffectProposal.model_validate(_PAYLOAD).model_dump(mode="json")
    )
    digest = hashlib.sha256(body).hexdigest()
    resolver = RuntimeRowSetProposalResolver(
        blobs=_Blobs({digest: body}),  # type: ignore[arg-type]
        references=_References((_edge(digest=digest, user_id="other-user"),)),  # type: ignore[arg-type]
    )

    assert (
        await resolver.resolve(
            org_id=_ORG,
            user_id=_USER,
            content_ref=_REF,
            digest=digest,
        )
        is None
    )


async def test_noncanonical_or_invalid_rowset_material_fails_closed() -> None:
    noncanonical = json.dumps(_PAYLOAD, indent=2).encode()
    noncanonical_digest = hashlib.sha256(noncanonical).hexdigest()
    invalid_payload = {
        **_PAYLOAD,
        "agent_holds": [{"row_key": "missing-row", "reason": "unknown"}],
    }
    invalid = canonical_json_bytes(invalid_payload)
    invalid_digest = hashlib.sha256(invalid).hexdigest()

    for body, digest in (
        (noncanonical, noncanonical_digest),
        (invalid, invalid_digest),
    ):
        resolver = RuntimeRowSetProposalResolver(
            blobs=_Blobs({digest: body}),  # type: ignore[arg-type]
            references=_References((_edge(digest=digest),)),  # type: ignore[arg-type]
        )

        assert (
            await resolver.resolve(
                org_id=_ORG,
                user_id=_USER,
                content_ref=_REF,
                digest=digest,
            )
            is None
        )
