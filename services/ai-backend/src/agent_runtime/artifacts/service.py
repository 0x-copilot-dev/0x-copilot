"""Application service for immutable, tenant-scoped artifacts."""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import AsyncIterator, Callable
from datetime import datetime, timezone
from uuid import uuid4

from agent_runtime.artifacts.contracts import (
    ArtifactAppendCommand,
    ArtifactCreateCommand,
    ArtifactCreateRequest,
    ArtifactIdempotencyBinding,
    ArtifactLedgerEvent,
    ArtifactLimits,
    ArtifactListPage,
    ArtifactListQuery,
    ArtifactMutationResult,
    ArtifactProvenance,
    ArtifactPromotionRequest,
    ArtifactRevisionRequest,
    ArtifactScope,
    ArtifactSoftDeleteCommand,
    ArtifactStoredRecord,
    ArtifactStoredRevision,
    ByteRange,
    validate_artifact_source_ref,
)
from agent_runtime.artifacts.errors import (
    ArtifactBlobUnavailableError,
    ArtifactInvalidSourceError,
    ArtifactNotFoundError,
    ArtifactRangeError,
    ArtifactSealedRunError,
)
from agent_runtime.artifacts.ports import (
    ArtifactBlobStorePort,
    ArtifactLedgerPublisherPort,
    ArtifactMetadataStorePort,
    ArtifactRunScopeResolverPort,
    ArtifactSourceResolverPort,
)
from agent_runtime.presentation.policy import (
    PresentationPolicy,
    PresentationPolicyInput,
)
from agent_runtime.surfaces_v2.canonical_json import canonical_json_sha256
from agent_runtime.surfaces_v2.entities import Artifact, ArtifactRevision
from agent_runtime.surfaces_v2.ledger_ids import (
    ArtifactContentRefCodec,
    ArtifactIdCodec,
)
from agent_runtime.surfaces_v2.ledger_models import (
    ArtifactAuthor,
    ArtifactCausalLane,
    ArtifactCreatedPayload,
    ArtifactKind,
    ArtifactPresentationDecidedPayload,
    ArtifactPromotedPayload,
    ArtifactRevisedPayload,
    LedgerEventType,
)

_LOGGER = logging.getLogger(__name__)


class ArtifactService:
    """Owns authorization, bounded ingest, immutable revisions, and promotion.

    Adapters own atomic persistence.  A create/revise transaction includes its
    idempotency binding and artifact ledger command in the existing runtime
    outbox, so this service never performs a fragile metadata-then-event pair.
    """

    class Routes:
        CREATE = "POST:/v1/agent/runs/{run_id}/artifacts"
        REVISE = "POST:/v1/agent/artifacts/{artifact_id}/revisions"
        PROMOTE = "POST:/v1/agent/artifacts:promote"
        PUBLISH = "INTERNAL:artifact.publish"
        DELETE = "DELETE:/v1/agent/artifacts/{artifact_id}"

    class Defaults:
        MEDIA_TYPE = "application/octet-stream"
        PROMOTED_TITLE = "Promoted artifact"

    def __init__(
        self,
        *,
        metadata: ArtifactMetadataStorePort,
        blobs: ArtifactBlobStorePort,
        run_scopes: ArtifactRunScopeResolverPort,
        sources: ArtifactSourceResolverPort | None = None,
        limits: ArtifactLimits | None = None,
        now: Callable[[], datetime] | None = None,
        ledger_publisher: ArtifactLedgerPublisherPort | None = None,
    ) -> None:
        self._metadata = metadata
        self._blobs = blobs
        self._run_scopes = run_scopes
        self._sources = sources
        self._limits = limits or ArtifactLimits()
        self._now = now or (lambda: datetime.now(timezone.utc))
        # Optional so the service stays usable in contexts with no run ledger
        # (tests, the artifact REST surface). Unset means the outbox drain is
        # the only delivery path — correct, just not live.
        self._ledger_publisher = ledger_publisher

    def bind_ledger_publisher(self, publisher: ArtifactLedgerPublisherPort) -> None:
        """Attach the run-ledger publisher once the event producer exists.

        Worker topology builds this service before the run handler that owns
        the event producer, so the live publication path cannot be supplied at
        construction. Binding afterwards keeps the ordering explicit instead of
        threading a half-built producer through the composition root.
        """

        self._ledger_publisher = publisher

    async def create_from_stream(
        self,
        *,
        org_id: str,
        user_id: str,
        request: ArtifactCreateRequest,
        provenance: ArtifactProvenance,
        chunks: AsyncIterator[bytes],
    ) -> ArtifactMutationResult:
        """Create revision 1 after verifying run scope and the streamed body."""

        scope = await self._require_run_scope(
            org_id=org_id,
            user_id=user_id,
            run_id=request.run_id,
        )
        return await self._create_in_scope(
            scope=scope,
            request=request,
            provenance=provenance,
            chunks=chunks,
            route=self.Routes.CREATE,
            promoted_source_ref=None,
        )

    async def create_from_bytes(
        self,
        *,
        org_id: str,
        user_id: str,
        request: ArtifactCreateRequest,
        provenance: ArtifactProvenance,
        content: bytes,
    ) -> ArtifactMutationResult:
        """Bounded convenience path for trusted internal callers."""

        return await self.create_from_stream(
            org_id=org_id,
            user_id=user_id,
            request=request,
            provenance=provenance,
            chunks=self._single_chunk(content),
        )

    async def publish_from_bytes(
        self,
        *,
        org_id: str,
        user_id: str,
        request: ArtifactCreateRequest,
        provenance: ArtifactProvenance,
        content: bytes,
    ) -> ArtifactMutationResult:
        """Persist explicitly authored bytes through the canonical repository.

        This is the server-only publication lane for model/internal producers.
        It deliberately shares A2's create transaction, idempotency record,
        blob store, and artifact outbox; no second content store is involved.
        """

        scope = await self._require_run_scope(
            org_id=org_id,
            user_id=user_id,
            run_id=request.run_id,
        )
        return await self._create_in_scope(
            scope=scope,
            request=request,
            provenance=provenance,
            chunks=self._single_chunk(content),
            route=self.Routes.PUBLISH,
            promoted_source_ref=None,
        )

    async def publish_from_stream(
        self,
        *,
        org_id: str,
        user_id: str,
        request: ArtifactCreateRequest,
        provenance: ArtifactProvenance,
        chunks: AsyncIterator[bytes],
    ) -> ArtifactMutationResult:
        """Persist trusted producer output without materializing its bytes.

        This is the A2 publication lane for bounded server-side producers such
        as D3.  It preserves the same scoped authorization, streaming digest
        verification, immutable revision, idempotency, and outbox behaviour as
        the bytes convenience method.
        """

        scope = await self._require_run_scope(
            org_id=org_id,
            user_id=user_id,
            run_id=request.run_id,
        )
        return await self._create_in_scope(
            scope=scope,
            request=request,
            provenance=provenance,
            chunks=chunks,
            route=self.Routes.PUBLISH,
            promoted_source_ref=None,
        )

    async def publish_from_source(
        self,
        *,
        org_id: str,
        user_id: str,
        request: ArtifactCreateRequest,
        provenance: ArtifactProvenance,
        source_ref: str,
    ) -> ArtifactMutationResult:
        """Publish one exact, already-authorized logical source with attribution.

        Unlike user-driven promotion this retains the supplied trusted author
        (model or subagent) and does not emit ``artifact.promoted``.  It still
        resolves bytes only through A2's scoped source resolver.
        """

        if self._sources is None:
            raise ArtifactNotFoundError()
        try:
            canonical_source_ref = validate_artifact_source_ref(source_ref)
        except ValueError as exc:
            raise ArtifactInvalidSourceError() from exc
        scope = await self._require_run_scope(
            org_id=org_id,
            user_id=user_id,
            run_id=request.run_id,
        )
        try:
            descriptor = await self._sources.resolve_source(
                scope=scope,
                source_ref=canonical_source_ref,
            )
        except ValueError as exc:
            raise ArtifactInvalidSourceError() from exc
        if descriptor is None:
            raise ArtifactNotFoundError()
        chunks = await self._sources.open_source(scope=scope, source=descriptor)
        return await self._create_in_scope(
            scope=scope,
            request=request,
            provenance=provenance.model_copy(
                update={"source_ref": canonical_source_ref}
            ),
            chunks=chunks,
            route=self.Routes.PUBLISH,
            promoted_source_ref=None,
        )

    async def create_draft_from_bytes(
        self,
        *,
        org_id: str,
        user_id: str,
        request: ArtifactCreateRequest,
        provenance: ArtifactProvenance,
        content: bytes,
        artifact_id: str,
        created_at: datetime | None = None,
    ) -> ArtifactMutationResult:
        """Create one canonical artifact for the internal ``/drafts`` adapter.

        ``artifact_id`` is server-derived from the bound draft scope and virtual
        path.  It is never an HTTP or model argument; using it here lets
        concurrent first writes converge without a second writable mapping.
        """

        ArtifactIdCodec.parse(artifact_id)
        scope = await self._require_run_scope(
            org_id=org_id,
            user_id=user_id,
            run_id=request.run_id,
        )
        return await self._create_in_scope(
            scope=scope,
            request=request,
            provenance=provenance,
            chunks=self._single_chunk(content),
            route=self.Routes.PUBLISH,
            promoted_source_ref=None,
            artifact_id=artifact_id,
            created_at=created_at,
        )

    async def append_revision_from_stream(
        self,
        *,
        org_id: str,
        user_id: str,
        request: ArtifactRevisionRequest,
        provenance: ArtifactProvenance,
        chunks: AsyncIterator[bytes],
        created_at: datetime | None = None,
    ) -> ArtifactMutationResult:
        """Compare-and-append one immutable artifact revision."""

        current = await self._require_artifact(
            org_id=org_id,
            user_id=user_id,
            artifact_id=request.artifact_id,
        )
        scope = await self._require_revision_scope(
            org_id=org_id,
            user_id=user_id,
            request=request,
            provenance=provenance,
            current=current,
        )
        limit = self._limits.for_kind(current.artifact.kind)
        written = await self._blobs.put_stream(
            expected_digest=request.expected_digest,
            chunks=chunks,
            byte_limit=limit.maximum_bytes,
        )
        next_revision = request.parent_revision + 1
        now = self._trusted_created_at(created_at)
        revision = ArtifactRevision(
            artifact_id=request.artifact_id,
            revision=next_revision,
            parent_revision=request.parent_revision,
            content_ref=ArtifactContentRefCodec.format(
                request.artifact_id, next_revision
            ),
            content_digest=written.content_digest,
            byte_size=written.byte_size,
            author=provenance.author,
            source_ref=provenance.source_ref,
            created_at=now.isoformat(),
        )
        stored_revision = ArtifactStoredRevision(
            revision=revision,
            blob_key=written.blob_key,
            range_supported=written.range_supported,
        )
        payload = ArtifactRevisedPayload(
            v=1,
            artifact_id=request.artifact_id,
            revision=next_revision,
            parent_revision=request.parent_revision,
            content_ref=revision.content_ref,
            content_digest=revision.content_digest,
            author=provenance.author,
        )
        request_digest = self._request_digest(
            route=self.Routes.REVISE,
            values={
                "artifact_id": request.artifact_id,
                "parent_revision": request.parent_revision,
                "author": provenance.author.value,
                "source_ref": provenance.source_ref,
                "content_digest": written.content_digest,
                **({"created_at": now.isoformat()} if created_at is not None else {}),
            },
        )
        command = ArtifactAppendCommand(
            scope=scope,
            artifact_id=request.artifact_id,
            expected_revision=request.parent_revision,
            revision=stored_revision,
            idempotency=self._idempotency(
                scope=scope,
                route=self.Routes.REVISE,
                key=request.idempotency_key,
                request_digest=request_digest,
            ),
            ledger_event=(
                self._event(
                    scope=scope,
                    event_type=LedgerEventType.ARTIFACT_REVISED,
                    artifact_id=request.artifact_id,
                    revision=next_revision,
                    ordinal=0,
                    payload=payload.model_dump(mode="json", by_alias=True),
                    created_at=now,
                )
                if scope.lane is ArtifactCausalLane.RUN
                else None
            ),
        )
        return await self._metadata.append_revision(command)

    async def get_metadata(
        self, *, org_id: str, user_id: str, artifact_id: str
    ) -> ArtifactStoredRecord:
        return await self._require_artifact(
            org_id=org_id,
            user_id=user_id,
            artifact_id=artifact_id,
        )

    async def get_metadata_for_org(
        self, *, org_id: str, artifact_id: str
    ) -> ArtifactStoredRecord:
        """Classify a canonical binding internally without granting it to a user.

        Callers use this only after a scoped read failed, to decide whether a
        missing Artifact is genuinely an unmigrated legacy draft or an opaque
        authorization/provenance failure. Never return this result from a
        public route.
        """

        record = await self._metadata.get_artifact_for_org(
            org_id=org_id,
            artifact_id=artifact_id,
            include_deleted=True,
        )
        if record is None:
            raise ArtifactNotFoundError()
        return record

    async def get_revision_metadata(
        self,
        *,
        org_id: str,
        user_id: str,
        artifact_id: str,
        revision: int,
    ) -> ArtifactStoredRevision:
        await self._require_artifact(
            org_id=org_id,
            user_id=user_id,
            artifact_id=artifact_id,
        )
        stored = await self._metadata.get_revision(
            org_id=org_id,
            user_id=user_id,
            artifact_id=artifact_id,
            revision=revision,
        )
        if stored is None:
            raise ArtifactNotFoundError()
        return stored

    async def stream_revision(
        self,
        *,
        org_id: str,
        user_id: str,
        artifact_id: str,
        revision: int,
        byte_range: ByteRange | None = None,
    ) -> tuple[ArtifactStoredRecord, ArtifactStoredRevision, AsyncIterator[bytes]]:
        """Authorize metadata, validate an optional range, then open the blob."""

        record = await self._require_artifact(
            org_id=org_id,
            user_id=user_id,
            artifact_id=artifact_id,
        )
        stored = await self.get_revision_metadata(
            org_id=org_id,
            user_id=user_id,
            artifact_id=artifact_id,
            revision=revision,
        )
        try:
            stat = await self._blobs.stat(stored.blob_key)
        except ArtifactBlobUnavailableError:
            raise
        except Exception as exc:
            raise ArtifactBlobUnavailableError() from exc
        if (
            stat.byte_size != stored.revision.byte_size
            or stat.blob_key != stored.revision.content_digest
        ):
            raise ArtifactBlobUnavailableError()
        if byte_range is not None:
            if (
                not stat.range_supported
                or byte_range.start >= stat.byte_size
                or byte_range.end >= stat.byte_size
            ):
                raise ArtifactRangeError()
        try:
            stream = await self._blobs.open_stream(
                stored.blob_key,
                start=byte_range.start if byte_range is not None else None,
                end=byte_range.end if byte_range is not None else None,
            )
        except ArtifactBlobUnavailableError:
            raise
        except Exception as exc:
            raise ArtifactBlobUnavailableError() from exc
        return record, stored, self._safe_blob_stream(stream)

    async def list_for_run(
        self,
        *,
        org_id: str,
        user_id: str,
        run_id: str,
        kind: ArtifactKind | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> ArtifactListPage:
        await self._require_run_scope(org_id=org_id, user_id=user_id, run_id=run_id)
        return await self._metadata.list_artifacts(
            ArtifactListQuery(
                org_id=org_id,
                user_id=user_id,
                run_id=run_id,
                kind=kind,
                include_deleted=False,
                limit=limit,
                cursor=cursor,
            )
        )

    async def promote_source(
        self,
        *,
        org_id: str,
        user_id: str,
        request: ArtifactPromotionRequest,
    ) -> ArtifactMutationResult:
        """Copy one authorized logical source into independent artifact storage."""

        if self._sources is None:
            raise ArtifactNotFoundError()
        try:
            source_ref = validate_artifact_source_ref(request.source_ref)
        except ValueError as exc:
            raise ArtifactInvalidSourceError() from exc
        scope = await self._require_run_scope(
            org_id=org_id,
            user_id=user_id,
            run_id=request.run_id,
        )
        try:
            descriptor = await self._sources.resolve_source(
                scope=scope,
                source_ref=source_ref,
            )
        except ValueError as exc:
            raise ArtifactInvalidSourceError() from exc
        if descriptor is None:
            raise ArtifactNotFoundError()
        chunks = await self._sources.open_source(scope=scope, source=descriptor)
        create_request = ArtifactCreateRequest(
            run_id=request.run_id,
            kind=request.kind,
            title=request.title or descriptor.title or self.Defaults.PROMOTED_TITLE,
            media_type=(
                request.media_type or descriptor.media_type or self.Defaults.MEDIA_TYPE
            ),
            suggested_filename=(
                request.suggested_filename or descriptor.suggested_filename
            ),
            expected_digest=descriptor.content_digest,
            idempotency_key=request.idempotency_key,
        )
        return await self._create_in_scope(
            scope=scope,
            request=create_request,
            provenance=ArtifactProvenance(
                author=ArtifactAuthor.IMPORT,
                source_ref=descriptor.source_ref,
            ),
            chunks=chunks,
            route=self.Routes.PROMOTE,
            promoted_source_ref=descriptor.source_ref,
        )

    async def soft_delete(
        self,
        *,
        org_id: str,
        user_id: str,
        artifact_id: str,
        idempotency_key: str,
    ) -> None:
        """Tombstone an artifact; shared bytes remain available to retention owners."""

        record = await self._metadata.get_artifact(
            org_id=org_id,
            user_id=user_id,
            artifact_id=artifact_id,
            include_deleted=True,
        )
        if record is None:
            raise ArtifactNotFoundError()
        digest = self._request_digest(
            route=self.Routes.DELETE,
            values={"artifact_id": artifact_id},
        )
        result = await self._metadata.soft_delete(
            ArtifactSoftDeleteCommand(
                org_id=org_id,
                user_id=user_id,
                artifact_id=artifact_id,
                deleted_at=self._utc_now(),
                idempotency=ArtifactIdempotencyBinding(
                    org_id=org_id,
                    user_id=user_id,
                    route=self.Routes.DELETE,
                    key=idempotency_key,
                    request_digest=digest,
                ),
            )
        )
        if result is None:
            raise ArtifactNotFoundError()

    async def _create_in_scope(
        self,
        *,
        scope: ArtifactScope,
        request: ArtifactCreateRequest,
        provenance: ArtifactProvenance,
        chunks: AsyncIterator[bytes],
        route: str,
        promoted_source_ref: str | None,
        artifact_id: str | None = None,
        created_at: datetime | None = None,
    ) -> ArtifactMutationResult:
        self._validate_title(request.title)
        kind_limit = self._limits.for_kind(request.kind)
        written = await self._blobs.put_stream(
            expected_digest=request.expected_digest,
            chunks=chunks,
            byte_limit=kind_limit.maximum_bytes,
        )
        now = self._trusted_created_at(created_at)
        artifact_id = artifact_id or ArtifactIdCodec.format(uuid4())
        revision = ArtifactRevision(
            artifact_id=artifact_id,
            revision=1,
            parent_revision=None,
            content_ref=ArtifactContentRefCodec.format(artifact_id, 1),
            content_digest=written.content_digest,
            byte_size=written.byte_size,
            author=provenance.author,
            source_ref=provenance.source_ref,
            created_at=now.isoformat(),
        )
        artifact = Artifact(
            artifact_id=artifact_id,
            org_id=scope.org_id,
            user_id=scope.user_id,
            conversation_id=scope.conversation_id,
            run_id=scope.run_id,
            kind=request.kind,
            title=request.title,
            media_type=request.media_type,
            current_revision=1,
            created_by=provenance.author,
            accent=request.accent,
            created_at=now.isoformat(),
            updated_at=now.isoformat(),
            deleted_at=None,
        )
        record = ArtifactStoredRecord(
            artifact=artifact,
            current_revision=ArtifactStoredRevision(
                revision=revision,
                blob_key=written.blob_key,
                range_supported=written.range_supported,
            ),
            suggested_filename=request.suggested_filename,
        )
        created = ArtifactCreatedPayload(
            v=1,
            artifact_id=artifact_id,
            kind=request.kind,
            revision=1,
            content_ref=revision.content_ref,
            content_digest=revision.content_digest,
            author=provenance.author,
        )
        events = [
            self._event(
                scope=scope,
                event_type=LedgerEventType.ARTIFACT_CREATED,
                artifact_id=artifact_id,
                revision=1,
                ordinal=0,
                payload=created.model_dump(mode="json", by_alias=True),
                created_at=now,
            )
        ]
        presentation = PresentationPolicy.decide(
            PresentationPolicyInput(
                explicit_preference=request.presentation_preference,
                artifact_kind=request.kind,
                artifact_size=written.byte_size,
                # Artifact kind/media-type validation happened before this
                # transaction. B2 owns the fixed renderer registry for each
                # launch kind; an unsupported payload still receives its safe
                # raw/file renderer rather than arbitrary UI.
                renderer_supported=True,
            )
        )
        decision = ArtifactPresentationDecidedPayload(
            v=1,
            artifact_id=artifact_id,
            decision=presentation.decision,
            basis=presentation.basis,
            surface_id=None,
        )
        events.append(
            self._event(
                scope=scope,
                event_type=LedgerEventType.ARTIFACT_PRESENTATION_DECIDED,
                artifact_id=artifact_id,
                revision=1,
                ordinal=1,
                payload=decision.model_dump(
                    mode="json", by_alias=True, exclude_none=True
                ),
                created_at=now,
            )
        )
        if promoted_source_ref is not None:
            promoted = ArtifactPromotedPayload(
                v=1,
                artifact_id=artifact_id,
                source_ref=promoted_source_ref,
                kind=request.kind,
                revision=1,
            )
            events.append(
                self._event(
                    scope=scope,
                    event_type=LedgerEventType.ARTIFACT_PROMOTED,
                    artifact_id=artifact_id,
                    revision=1,
                    ordinal=2,
                    payload=promoted.model_dump(mode="json", by_alias=True),
                    created_at=now,
                )
            )
        request_digest = self._request_digest(
            route=route,
            values={
                "run_id": request.run_id,
                "kind": request.kind.value,
                "title": request.title,
                "media_type": request.media_type,
                "suggested_filename": request.suggested_filename,
                "presentation_preference": request.presentation_preference.value,
                "author": provenance.author.value,
                "source_ref": provenance.source_ref,
                "content_digest": written.content_digest,
                **({"created_at": now.isoformat()} if created_at is not None else {}),
            },
        )
        command = ArtifactCreateCommand(
            record=record,
            idempotency=self._idempotency(
                scope=scope,
                route=route,
                key=request.idempotency_key,
                request_digest=request_digest,
            ),
            ledger_events=tuple(events),
        )
        mutation = await self._metadata.create_artifact(command)
        await self._publish_ledger_events(command.ledger_events)
        return mutation

    async def _publish_ledger_events(
        self, events: tuple[ArtifactLedgerEvent, ...]
    ) -> None:
        """Project committed artifact facts onto the run ledger immediately.

        The durable commit above is the source of truth; this is the *timely*
        path, not the reliable one. It matters because the events are causal:
        they describe something the run just did, so they belong inside the
        run's sealed prefix — and because the model tells the user the artifact
        is on the canvas *now*, which is only true if the canvas hears about it
        mid-run.

        Publication is best-effort by design. Two backstops already guarantee
        eventual delivery, both keyed on the same ``event_id`` so all three
        paths are idempotent and cannot disagree:

        * ``RunTerminationCoordinator`` drains the outbox before sealing, so a
          failure here is still repaired inside the prefix;
        * the queue bridge recovers rows orphaned by a crash, declaring them
          ``LATE_CAUSAL_RECOVERY`` amendments because by then the seal has
          passed and pretending otherwise would falsify the prefix.

        Swallowing the error is therefore correct rather than lax: raising
        would fail a publication that is already durably committed.
        """

        if self._ledger_publisher is None:
            return
        for event in events:
            try:
                await self._ledger_publisher.publish(event)
            except asyncio.CancelledError:
                raise
            except Exception:
                _LOGGER.debug(
                    "artifact_publication.inline_ledger_publish_failed",
                    extra={"metadata": {"event_id": event.event_id}},
                )

    async def _require_run_scope(
        self, *, org_id: str, user_id: str, run_id: str
    ) -> ArtifactScope:
        scope = await self._run_scopes.resolve_run(
            org_id=org_id,
            user_id=user_id,
            run_id=run_id,
        )
        if scope is None:
            raise ArtifactNotFoundError()
        return scope

    async def _require_revision_scope(
        self,
        *,
        org_id: str,
        user_id: str,
        request: ArtifactRevisionRequest,
        provenance: ArtifactProvenance,
        current: ArtifactStoredRecord,
    ) -> ArtifactScope:
        """Resolve the causal subject for one revision, derived from authorship.

        Authorship and causality are independent axes. ``author`` says who wrote
        the bytes; the lane says what activity the write belongs to, and only the
        latter decides whether a run's terminal event is entitled to seal it.

        The lane is derived here from server-held provenance and never from the
        request, so a caller cannot route an agent-authored write into the
        unsealed conversation lane.
        """

        if provenance.author is ArtifactAuthor.USER:
            # A user edit is caused by the conversation, not by any run. The
            # subject comes from the artifact's own record rather than the
            # request, so there is no forgeable input for a fact the server
            # already holds. ``resolve_conversation`` re-proves ownership.
            scope = await self._run_scopes.resolve_conversation(
                org_id=org_id,
                user_id=user_id,
                conversation_id=current.artifact.conversation_id,
            )
            if scope is None:
                raise ArtifactNotFoundError()
            return scope

        # Agent-authored: the revision is causal in the run the agent is acting
        # in, not necessarily the run that created the artifact. Resolving
        # re-proves the caller owns the claimed run, so a forged
        # ``acting_run_id`` cannot redirect a ledger event into someone else's
        # run. Unset falls back to the creating run, where the two coincide.
        scope = await self._require_run_scope(
            org_id=org_id,
            user_id=user_id,
            run_id=request.acting_run_id or current.artifact.run_id,
        )
        if request.acting_run_id is not None and scope.run_is_terminal:
            # A claimed run whose ledger is already sealed cannot carry the
            # causal ``artifact.revised`` this mutation produces, so the claim is
            # refused before the blob is written. Distinct from a stale-parent
            # conflict: nothing here is out of date and re-reading will not help.
            raise ArtifactSealedRunError()
        return scope

    async def _require_artifact(
        self, *, org_id: str, user_id: str, artifact_id: str
    ) -> ArtifactStoredRecord:
        record = await self._metadata.get_artifact(
            org_id=org_id,
            user_id=user_id,
            artifact_id=artifact_id,
        )
        if record is None:
            raise ArtifactNotFoundError()
        return record

    def _validate_title(self, title: str) -> None:
        if len(title) > self._limits.maximum_title_characters:
            raise ValueError("title exceeds maximum_title_characters")
        if len(title.encode("utf-8")) > self._limits.maximum_title_bytes:
            raise ValueError("title exceeds maximum_title_bytes")

    @classmethod
    def _idempotency(
        cls,
        *,
        scope: ArtifactScope,
        route: str,
        key: str,
        request_digest: str,
    ) -> ArtifactIdempotencyBinding:
        return ArtifactIdempotencyBinding(
            org_id=scope.org_id,
            user_id=scope.user_id,
            route=route,
            key=key,
            request_digest=request_digest,
        )

    @classmethod
    def _event(
        cls,
        *,
        scope: ArtifactScope,
        event_type: LedgerEventType,
        artifact_id: str,
        revision: int,
        ordinal: int,
        payload: dict[str, object],
        created_at: datetime,
    ) -> ArtifactLedgerEvent:
        publication_digest = canonical_json_sha256(
            {
                "run_id": scope.run_id,
                "artifact_id": artifact_id,
                "revision": revision,
                "event_type": event_type.value,
                "ordinal": ordinal,
            }
        )
        return ArtifactLedgerEvent(
            event_id=f"artevt_{publication_digest}",
            scope=scope,
            event_type=event_type,
            payload=payload,
            created_at=created_at,
        )

    @classmethod
    def _request_digest(cls, *, route: str, values: dict[str, object]) -> str:
        return canonical_json_sha256({"route": route, **values})

    def _utc_now(self) -> datetime:
        value = self._now()
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def _trusted_created_at(self, value: datetime | None) -> datetime:
        """Resolve an internal import timestamp without exposing it to HTTP.

        Normal application calls retain the current-clock default.  E2's
        server-side legacy importer alone passes a validated historic value so
        canonical revisions preserve their original order and timestamp.
        """

        if value is None:
            return self._utc_now()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("artifact import timestamp must include a timezone")
        return value.astimezone(timezone.utc)

    @staticmethod
    async def _single_chunk(content: bytes) -> AsyncIterator[bytes]:
        if content:
            yield content

    @staticmethod
    async def _safe_blob_stream(
        stream: AsyncIterator[bytes],
    ) -> AsyncIterator[bytes]:
        """Translate adapter failures that occur after response streaming starts."""

        try:
            async for chunk in stream:
                yield chunk
        except ArtifactBlobUnavailableError:
            raise
        except Exception as exc:
            raise ArtifactBlobUnavailableError() from exc

    @staticmethod
    def digest_bytes(content: bytes) -> str:
        """Expose the repository's byte digest algorithm for bounded callers."""

        return hashlib.sha256(content).hexdigest()


__all__ = ("ArtifactService",)
