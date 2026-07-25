"""Owner-routed opening for safe Sources v2 provenance facts.

The Sources v2 fold is deliberately a display-only, redacted read model.  A
``source_id`` is therefore never a capability: this service rechecks the run
owner, re-folds the persisted ledger, finds that exact fact, and then asks the
owning artifact repository to authorize the immutable artifact revision again.

Only artifact facts have an opener in this slice.  Other provenance kinds stay
honestly unavailable until their owning authority can provide the same
re-authorization boundary.  No physical path, ref, raw arguments, body,
cookie, secret, or provider token can cross this contract.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal, Protocol

from agent_runtime.artifacts import ArtifactNotFoundError
from agent_runtime.api.ports import EventStorePort, PersistencePort
from agent_runtime.execution.contracts import RuntimeContract
from agent_runtime.surfaces_v2.ledger_models import ArtifactKind
from agent_runtime.surfaces_v2.sources import SourceFactKindV2, SourcesProjectionV2


class SourceOpenDispositionV2(StrEnum):
    """The only outcomes exposed by the source-open boundary."""

    ARTIFACT = "artifact"
    UNAVAILABLE = "unavailable"


class SourceOpenResultV2(RuntimeContract):
    """A safe, re-authorized target for one Sources v2 fact.

    Artifact identifiers are returned only after the repository has rechecked
    the verified user at open time.  The unavailable variant intentionally
    carries no detail about an absent, revoked, or unsupported owner resource.
    """

    v: Literal[2] = 2
    source_id: str
    kind: SourceFactKindV2
    disposition: SourceOpenDispositionV2
    artifact_id: str | None = None
    artifact_revision: int | None = None
    artifact_kind: ArtifactKind | None = None


class SourceOpenNotFoundError(RuntimeError):
    """The source or its containing run is not available to the caller."""


class _ArtifactAccessPort(Protocol):
    """The minimal owner authority needed to resolve a safe artifact target."""

    async def get_metadata(
        self, *, org_id: str, user_id: str, artifact_id: str
    ) -> object: ...

    async def get_revision_metadata(
        self,
        *,
        org_id: str,
        user_id: str,
        artifact_id: str,
        revision: int,
    ) -> object: ...


class SourceOpenService:
    """Resolve a display fact through the resource owner, never a raw ref."""

    def __init__(
        self,
        *,
        persistence: PersistencePort,
        event_store: EventStorePort,
        artifact_service: _ArtifactAccessPort | None,
    ) -> None:
        self._persistence = persistence
        self._event_store = event_store
        self._artifact_service = artifact_service

    async def open_source(
        self,
        *,
        org_id: str,
        user_id: str,
        run_id: str,
        source_id: str,
    ) -> SourceOpenResultV2:
        """Return one safe target after both run and owner authorization.

        The initial run check happens before reading any event, so foreign and
        unknown runs are indistinguishable.  The exact source fact is then
        recovered from the persisted ledger rather than trusting a target from
        the browser.  Artifact metadata *and* the requested revision are
        checked through the repository with the same verified identity.
        """

        run = await self._persistence.get_run(org_id=org_id, run_id=run_id)
        if run is None or run.user_id != user_id:
            raise SourceOpenNotFoundError()
        conversation_id = getattr(run, "conversation_id", None)
        if not isinstance(conversation_id, str) or not conversation_id:
            raise SourceOpenNotFoundError()
        conversation = await self._persistence.get_conversation(
            org_id=org_id,
            user_id=user_id,
            conversation_id=conversation_id,
        )
        if (
            conversation is None
            or conversation.conversation_id != conversation_id
            or conversation.org_id != org_id
            or conversation.user_id != user_id
        ):
            raise SourceOpenNotFoundError()

        events = await self._event_store.list_events_after(
            org_id=org_id,
            run_id=run_id,
            after_sequence=0,
        )
        projection = SourcesProjectionV2.fold(run_id, events)
        fact = next(
            (
                candidate
                for candidate in projection.facts
                if candidate.source_id == source_id
            ),
            None,
        )
        if fact is None:
            raise SourceOpenNotFoundError()

        if (
            fact.kind is not SourceFactKindV2.ARTIFACT
            or fact.artifact_id is None
            or fact.artifact_revision is None
            or self._artifact_service is None
        ):
            return self._unavailable(fact.source_id, fact.kind)

        try:
            # The owner service performs the resource-scope check twice: once
            # for the artifact record and once for the immutable revision.  Do
            # not substitute the ledger fact for either authorization decision.
            record = await self._artifact_service.get_metadata(
                org_id=org_id,
                user_id=user_id,
                artifact_id=fact.artifact_id,
            )
            await self._artifact_service.get_revision_metadata(
                org_id=org_id,
                user_id=user_id,
                artifact_id=fact.artifact_id,
                revision=fact.artifact_revision,
            )
            artifact = getattr(record, "artifact", None)
            kind = getattr(artifact, "kind", None)
            artifact_kind = (
                kind if isinstance(kind, ArtifactKind) else ArtifactKind(str(kind))
            )
        except ArtifactNotFoundError as exc:
            # The source fact may remain in an immutable ledger after its
            # logical artifact was deleted, revoked, or moved out of scope.
            # Treat all of those owner-authority outcomes exactly like a
            # missing/foreign source: an opaque 404, never an "unavailable"
            # acknowledgement that the source fact still exists.
            raise SourceOpenNotFoundError() from exc
        except Exception:  # noqa: BLE001 - owner failures must fail closed and opaque
            return self._unavailable(fact.source_id, fact.kind)

        return SourceOpenResultV2(
            source_id=fact.source_id,
            kind=fact.kind,
            disposition=SourceOpenDispositionV2.ARTIFACT,
            artifact_id=fact.artifact_id,
            artifact_revision=fact.artifact_revision,
            artifact_kind=artifact_kind,
        )

    @staticmethod
    def _unavailable(
        source_id: str,
        kind: SourceFactKindV2,
    ) -> SourceOpenResultV2:
        """Return the same non-disclosing response for every unavailable owner."""

        return SourceOpenResultV2(
            source_id=source_id,
            kind=kind,
            disposition=SourceOpenDispositionV2.UNAVAILABLE,
        )


__all__ = (
    "SourceOpenDispositionV2",
    "SourceOpenNotFoundError",
    "SourceOpenResultV2",
    "SourceOpenService",
)
