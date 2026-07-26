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
import json
import re

from agent_runtime.api.effect_stage_decision_service import EffectStageDecisionService
from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.api.ports import PersistencePort, RuntimeQueuePort
from agent_runtime.artifacts.ports import ArtifactBlobStorePort
from agent_runtime.effects.contracts import EffectStageState
from agent_runtime.effects.errors import (
    EffectStageForbidden,
    EffectStageMalformedEvent,
)
from agent_runtime.surfaces_v2.ledger_models import (
    EffectDecisionKind,
    EffectExecutorKind,
)
from agent_runtime.surfaces_v2.canonical_json import canonical_json_bytes, sha256_hex
from agent_runtime.rollout import RolloutCapability
from agent_runtime.rollout_admission import (
    E2RolloutAdmission,
    E2RolloutAdmissionDenied,
    PersistedRunCohortFactsProvider,
)
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
    rollout_admission: E2RolloutAdmission
    blobs: ArtifactBlobStorePort | None = None
    references: ArtifactReferenceRepositoryPort | None = None
    decisions: EffectStageDecisionService | None = None

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

        decisions = self._decision_service()
        current = await decisions.current_stage(
            org_id=org_id,
            user_id=user_id,
            run_id=run_id,
            stage_id=stage_id,
            allowed_executors=frozenset({EffectExecutorKind.WORKSPACE}),
        )
        # ``current_stage`` above proves the requested run/stage belongs to the
        # authenticated principal and is a workspace executor.  Resolve the
        # same persisted run before recording a decision: a cohort denial or
        # emergency rollback must prevent both the ledger mutation and A5
        # enqueue, not merely suppress this route's UI.
        run = await self.persistence.get_run(org_id=org_id, run_id=run_id)
        if run is None or run.user_id != user_id:
            raise EffectStageForbidden()
        try:
            self.rollout_admission.require_all(
                capabilities=(
                    RolloutCapability.OPERATION_GATEWAY,
                    RolloutCapability.EFFECT_STAGER,
                    RolloutCapability.EFFECT_COMMIT,
                    RolloutCapability.WORKSPACE_OVERLAY,
                    RolloutCapability.WORKSPACE_COMMIT,
                ),
                facts_provider=PersistedRunCohortFactsProvider(
                    org_id=run.org_id,
                    user_id=run.user_id,
                ),
            )
        except E2RolloutAdmissionDenied as exc:
            raise EffectStageForbidden() from exc

        # Validate the immutable C1 material before changing the stage.  If it
        # is missing, tampered, or not the exact body C2 will prepare, the
        # request must not append a decision or enqueue an A5 command that can
        # never obtain a desktop permit.
        change_set_digest = await self._resolve_change_set_digest(
            org_id=org_id,
            user_id=user_id,
            state=current,
        )

        state = await decisions.record_decision(
            org_id=org_id,
            user_id=user_id,
            run_id=run_id,
            stage_id=stage_id,
            revision=revision,
            decision=decision,
            proposal_digest=proposal_digest,
            target_digest=target_digest,
            allowed_executors=frozenset({EffectExecutorKind.WORKSPACE}),
            # Preserve C3's established retry key while sharing the A4/A5
            # authorization and enqueue semantics with other effect routes.
            idempotency_namespace="workspace-decision",
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

    def _decision_service(self) -> EffectStageDecisionService:
        """Use app composition when available; retain direct-test compatibility."""

        if self.decisions is not None:
            return self.decisions
        return EffectStageDecisionService(
            persistence=self.persistence,
            event_producer=self.event_producer,
            queue=self.queue,
            rollout_admission=self.rollout_admission,
        )


async def _read_bounded(stream: AsyncIterator[bytes], *, limit: int) -> bytes | None:
    body = bytearray()
    async for chunk in stream:
        if not isinstance(chunk, bytes) or len(body) + len(chunk) > limit:
            return None
        body.extend(chunk)
    return bytes(body)


__all__ = ["WorkspaceApprovalDecision", "WorkspaceApprovalDecisionService"]
