"""C3 workspace-effect decision service and canonical receipt source.

The desktop host may submit an untrusted review snapshot, but it must receive
its approval facts from the canonical A4 ledger fold.  This service scopes the
run to the authenticated user, restricts the route to workspace effects, and
delegates the actual digest-pinned decision to :class:`EffectStager`.  It has
no executor or local-workspace dependency; A5 remains the sole execution path.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
import hashlib
import json
import re

from agent_runtime.api.effect_commit_queue import RuntimeEffectCommitOutbox
from agent_runtime.api.effect_ledger import RuntimeEffectLedger
from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.api.ports import PersistencePort, RuntimeQueuePort
from agent_runtime.artifacts.ports import ArtifactBlobStorePort
from agent_runtime.effects.contracts import (
    EffectActorIdentity,
    EffectStageScope,
    EffectStageState,
)
from agent_runtime.effects.errors import EffectStageMalformedEvent, EffectStageNotFound
from agent_runtime.effects.executor import EffectExecutionScope
from agent_runtime.effects.staging import EffectStager
from agent_runtime.surfaces_v2.ledger_ids import EffectStageIdCodec
from agent_runtime.surfaces_v2.ledger_models import (
    EffectActor,
    EffectDecisionKind,
    EffectExecutorKind,
)
from agent_runtime.surfaces_v2.canonical_json import canonical_json_bytes, sha256_hex
from runtime_adapters.artifact_references import (
    ArtifactReferenceKind,
    ArtifactReferenceRepositoryPort,
)

_MATERIAL_REF_PREFIX = "workspace-material://sha256/"
_MAX_MATERIAL_BYTES = 2 * 1024 * 1024
_SHA256_HEX = re.compile(r"^[a-f0-9]{64}$")


@dataclass(frozen=True)
class WorkspaceApprovalDecision:
    """Canonical post-decision state plus the server-derived C2 body digest."""

    state: EffectStageState
    change_set_digest: str


@dataclass(frozen=True)
class WorkspaceApprovalDecisionService:
    """Record a desktop workspace decision against one authenticated run.

    The service deliberately accepts the snapshot digests only as values for
    A4 to verify.  Its caller must project the response from the returned
    folded state, never by echoing those inputs.
    """

    persistence: PersistencePort
    event_producer: RuntimeEventProducer
    queue: RuntimeQueuePort
    blobs: ArtifactBlobStorePort | None = None
    references: ArtifactReferenceRepositoryPort | None = None

    async def record_decision(
        self,
        *,
        org_id: str,
        user_id: str,
        run_id: str,
        stage_id: str,
        revision: int,
        decision: EffectDecisionKind,
        proposal_digest: str,
        target_digest: str,
    ) -> WorkspaceApprovalDecision:
        """Persist one exact workspace approval/rejection and return its fold.

        Unknown, foreign, malformed, and non-workspace stages all fail as
        not-found.  This makes the workspace-only endpoint non-enumerable and
        prevents a desktop host from obtaining a local-write receipt for an
        unrelated executor kind.
        """

        try:
            EffectStageIdCodec.parse(stage_id)
        except ValueError as error:
            raise EffectStageNotFound() from error

        run = await self._owned_run(org_id=org_id, user_id=user_id, run_id=run_id)
        owner_ref = f"principal://users/{run.user_id}"
        stage_scope = EffectStageScope(run_id=run.run_id, owner_ref=owner_ref)
        stager = EffectStager(
            ledger=RuntimeEffectLedger(
                event_producer=self.event_producer,
                run=run,
                owner_ref=owner_ref,
            ),
            outbox=RuntimeEffectCommitOutbox(
                queue=self.queue,
                scope=EffectExecutionScope(
                    org_id=run.org_id,
                    user_id=run.user_id,
                    conversation_id=run.conversation_id,
                    run_id=run.run_id,
                    owner_ref=owner_ref,
                ),
            ),
        )
        current = await stager.get_state(scope=stage_scope, stage_id=stage_id)
        if current.executor is not EffectExecutorKind.WORKSPACE:
            raise EffectStageNotFound()

        # Validate the immutable C1 material before changing the stage.  If it
        # is missing, tampered, or not the exact body C2 will prepare, the
        # request must not append a decision or enqueue an A5 command that can
        # never obtain a desktop permit.
        change_set_digest = await self._resolve_change_set_digest(
            org_id=run.org_id,
            user_id=run.user_id,
            state=current,
        )

        state = await stager.decide(
            scope=stage_scope,
            stage_id=stage_id,
            revision=revision,
            decision=decision,
            proposal_digest=proposal_digest,
            target_digest=target_digest,
            actor=EffectActorIdentity(
                actor=EffectActor.USER,
                principal_ref=owner_ref,
            ),
            idempotency_key=self._decision_key(
                run_id=run.run_id,
                stage_id=stage_id,
                revision=revision,
                decision=decision,
                proposal_digest=proposal_digest,
                target_digest=target_digest,
            ),
        )
        return WorkspaceApprovalDecision(
            state=state,
            change_set_digest=change_set_digest,
        )

    async def _resolve_change_set_digest(
        self,
        *,
        org_id: str,
        user_id: str,
        state: EffectStageState,
    ) -> str:
        """Read and verify immutable C1 material before emitting permit evidence.

        ``proposal_digest`` alone identifies the server material, but Electron
        also needs the exact C2 change-set digest to reject a worker-crafted
        prepared body.  This resolver is intentionally server-side and returns
        only a SHA-256 digest; it never exposes paths, content, or references.
        """

        blobs = self.blobs
        references = self.references
        content_ref = state.current_revision.proposal_content_ref
        expected_digest = state.current_revision.proposal_digest
        if (
            blobs is None
            or references is None
            or content_ref is None
            or content_ref != f"{_MATERIAL_REF_PREFIX}{expected_digest}"
        ):
            raise EffectStageMalformedEvent(
                "The workspace approval material could not be verified."
            )
        edges = await references.list_edges(org_id=org_id, user_id=user_id)
        if not any(
            edge.reference_kind is ArtifactReferenceKind.EFFECT
            and edge.reference_id == content_ref
            and edge.blob_key == expected_digest
            and edge.released_at is None
            for edge in edges
        ):
            raise EffectStageMalformedEvent(
                "The workspace approval material could not be verified."
            )
        body = await _read_bounded(
            await blobs.open_stream(expected_digest),
            limit=_MAX_MATERIAL_BYTES,
        )
        if body is None or sha256_hex(body) != expected_digest:
            raise EffectStageMalformedEvent(
                "The workspace approval material could not be verified."
            )
        try:
            material = json.loads(body)
            if not isinstance(material, dict):
                raise ValueError
            grant_id = material["grant_id"]
            mount = material["mount"]
            entries = material["entries"]
            change_set_digest = material["change_set_digest"]
            target_digest = material["target_digest"]
            if (
                not isinstance(grant_id, str)
                or not isinstance(mount, str)
                or not isinstance(entries, list)
                or not isinstance(change_set_digest, str)
                or not _SHA256_HEX.fullmatch(change_set_digest)
                or target_digest != state.target_digest
            ):
                raise ValueError
            broker_entries: list[dict[str, object]] = []
            for entry in entries:
                if not isinstance(entry, dict):
                    raise ValueError
                broker_entries.append(
                    {key: value for key, value in entry.items() if key != "content_ref"}
                )
            calculated = sha256_hex(
                canonical_json_bytes(
                    {
                        "grant_id": grant_id,
                        "mount": mount,
                        "entries": broker_entries,
                    }
                )
            )
            if calculated != change_set_digest:
                raise ValueError
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            raise EffectStageMalformedEvent(
                "The workspace approval material could not be verified."
            ) from None
        return change_set_digest

    async def _owned_run(self, *, org_id: str, user_id: str, run_id: str):
        """Resolve a run without disclosing whether a foreign run exists."""

        run = await self.persistence.get_run(org_id=org_id, run_id=run_id)
        if run is None or run.user_id != user_id:
            raise EffectStageNotFound()
        return run

    @staticmethod
    def _decision_key(
        *,
        run_id: str,
        stage_id: str,
        revision: int,
        decision: EffectDecisionKind,
        proposal_digest: str,
        target_digest: str,
    ) -> str:
        """Derive a stable retry key from the exact reviewed snapshot only."""

        material = json.dumps(
            {
                "run_id": run_id,
                "stage_id": stage_id,
                "revision": revision,
                "decision": decision.value,
                "proposal_digest": proposal_digest,
                "target_digest": target_digest,
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
        return f"workspace-decision-{hashlib.sha256(material).hexdigest()}"


async def _read_bounded(stream: AsyncIterator[bytes], *, limit: int) -> bytes | None:
    body = bytearray()
    async for chunk in stream:
        if not isinstance(chunk, bytes) or len(body) + len(chunk) > limit:
            return None
        body.extend(chunk)
    return bytes(body)


__all__ = ["WorkspaceApprovalDecision", "WorkspaceApprovalDecisionService"]
