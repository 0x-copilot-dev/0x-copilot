from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from agent_runtime.artifacts.contracts import (
    ArtifactBlobStat,
    ArtifactBlobWriteResult,
    ArtifactCreateRequest,
    ArtifactListPage,
    ArtifactMutationResult,
    ArtifactProvenance,
    ArtifactPromotionRequest,
    ArtifactRevisionRequest,
    ArtifactScope,
    ArtifactSourceDescriptor,
    ArtifactStoredRecord,
    ByteRange,
)
from agent_runtime.artifacts.errors import (
    ArtifactBlobUnavailableError,
    ArtifactConflictError,
    ArtifactDigestMismatchError,
    ArtifactNotFoundError,
    ArtifactRangeError,
    ArtifactTooLargeError,
)
from agent_runtime.artifacts.service import ArtifactService
from agent_runtime.surfaces_v2.ledger_models import (
    ArtifactAuthor,
    ArtifactKind,
    LedgerEventType,
)

NOW = datetime(2026, 7, 24, 6, 30, tzinfo=timezone.utc)
SCOPE = ArtifactScope(
    org_id="org_1",
    user_id="user_1",
    conversation_id="conv_1",
    run_id="run_1",
    trace_id="trace_1",
)


class ArtifactServiceFakes:
    class Metadata:
        def __init__(self) -> None:
            self.record: ArtifactStoredRecord | None = None
            self.create_command = None
            self.append_command = None
            self.delete_command = None
            self.list_query = None
            self.delete_keys: dict[tuple[str, str], str] = {}

        async def create_artifact(self, command):
            self.create_command = command
            self.record = command.record
            return ArtifactMutationResult(record=command.record)

        async def append_revision(self, command):
            self.append_command = command
            assert self.record is not None
            artifact = self.record.artifact.model_copy(
                update={
                    "current_revision": command.revision.revision.revision,
                    "updated_at": command.revision.revision.created_at,
                }
            )
            self.record = self.record.model_copy(
                update={
                    "artifact": artifact,
                    "current_revision": command.revision,
                }
            )
            return ArtifactMutationResult(record=self.record)

        async def get_artifact(
            self,
            *,
            org_id,
            user_id,
            artifact_id,
            include_deleted=False,
        ):
            if self.record is None:
                return None
            artifact = self.record.artifact
            if (
                artifact.org_id != org_id
                or artifact.user_id != user_id
                or artifact.artifact_id != artifact_id
                or (artifact.deleted_at is not None and not include_deleted)
            ):
                return None
            return self.record

        async def get_revision(
            self,
            *,
            org_id,
            user_id,
            artifact_id,
            revision,
            include_deleted=False,
        ):
            record = await self.get_artifact(
                org_id=org_id,
                user_id=user_id,
                artifact_id=artifact_id,
                include_deleted=include_deleted,
            )
            if record is None or record.current_revision.revision.revision != revision:
                return None
            return record.current_revision

        async def list_artifacts(self, query):
            self.list_query = query
            return ArtifactListPage(
                artifacts=(self.record,) if self.record is not None else ()
            )

        async def soft_delete(self, command):
            self.delete_command = command
            if self.record is None:
                return None
            key = (command.idempotency.route, command.idempotency.key)
            prior_digest = self.delete_keys.get(key)
            if prior_digest is not None:
                if prior_digest == command.idempotency.request_digest:
                    return self.record
                return None
            if self.record.artifact.deleted_at is not None:
                return None
            self.delete_keys[key] = command.idempotency.request_digest
            deleted = self.record.artifact.model_copy(
                update={"deleted_at": command.deleted_at.isoformat()}
            )
            self.record = self.record.model_copy(update={"artifact": deleted})
            return self.record

    class Blobs:
        def __init__(self) -> None:
            self.data_by_key: dict[str, bytes] = {}
            self.put_calls = 0

        async def put_stream(self, *, expected_digest, chunks, byte_limit):
            self.put_calls += 1
            data = bytearray()
            async for chunk in chunks:
                data.extend(chunk)
                if len(data) > byte_limit:
                    raise ArtifactTooLargeError()
            body = bytes(data)
            digest = hashlib.sha256(body).hexdigest()
            if expected_digest is not None and expected_digest != digest:
                raise ArtifactDigestMismatchError()
            created = digest not in self.data_by_key
            self.data_by_key[digest] = body
            return ArtifactBlobWriteResult(
                blob_key=digest,
                content_digest=digest,
                byte_size=len(body),
                range_supported=True,
                created=created,
            )

        async def stat(self, blob_key):
            body = self.data_by_key[blob_key]
            return ArtifactBlobStat(
                blob_key=blob_key,
                byte_size=len(body),
                range_supported=True,
                created_at=NOW,
            )

        async def open_stream(self, blob_key, *, start=None, end=None):
            body = self.data_by_key[blob_key]
            first = 0 if start is None else start
            last = len(body) - 1 if end is None else end

            async def stream() -> AsyncIterator[bytes]:
                yield body[first : last + 1]

            return stream()

    class Scopes:
        def __init__(self, scope: ArtifactScope | None = SCOPE) -> None:
            self.scope = scope

        async def resolve_run(self, *, org_id, user_id, run_id):
            if (
                self.scope is None
                or self.scope.org_id != org_id
                or self.scope.user_id != user_id
                or self.scope.run_id != run_id
            ):
                return None
            return self.scope

    class MultiRunScopes:
        """Resolve several runs, so a revision can act in a run it did not create.

        The single-scope ``Scopes`` fake cannot express PRD-02's Flow B at all:
        it only ever knows one run, which is exactly the assumption the defect
        was hiding behind.
        """

        def __init__(self, scopes: dict[str, ArtifactScope]) -> None:
            self.scopes = scopes
            self.resolved: list[str] = []

        async def resolve_run(self, *, org_id, user_id, run_id):
            self.resolved.append(run_id)
            scope = self.scopes.get(run_id)
            if scope is None or scope.org_id != org_id or scope.user_id != user_id:
                return None
            return scope

    class Sources:
        def __init__(self, body: bytes, *, source_ref: str = "message://msg_1") -> None:
            self.body = body
            self.source_ref = source_ref
            self.resolve_calls = 0

        async def resolve_source(self, *, scope, source_ref):
            self.resolve_calls += 1
            if source_ref != self.source_ref:
                return None
            return ArtifactSourceDescriptor(
                source_ref=source_ref,
                byte_size=len(self.body),
                content_digest=hashlib.sha256(self.body).hexdigest(),
                media_type="text/markdown",
                title="Promoted note",
                suggested_filename="note.md",
            )

        async def open_source(self, *, scope, source):
            async def stream() -> AsyncIterator[bytes]:
                yield self.body

            return stream()

    @staticmethod
    async def chunks(*parts: bytes) -> AsyncIterator[bytes]:
        for part in parts:
            yield part

    @classmethod
    def service(
        cls,
        *,
        metadata=None,
        blobs=None,
        scopes=None,
        sources=None,
    ):
        return ArtifactService(
            metadata=metadata or cls.Metadata(),
            blobs=blobs or cls.Blobs(),
            run_scopes=scopes or cls.Scopes(),
            sources=sources,
            now=lambda: NOW,
        )


class TestArtifactService(ArtifactServiceFakes):
    def test_app_request_cannot_forge_authorship_or_source(self) -> None:
        with pytest.raises(ValidationError):
            ArtifactCreateRequest.model_validate(
                {
                    "run_id": SCOPE.run_id,
                    "kind": "document",
                    "title": "note",
                    "media_type": "text/markdown",
                    "author": "system",
                    "source_ref": "message://forged",
                    "idempotency_key": "create-forged",
                }
            )

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("media_type", "text/plain\r\nX-Injected: yes"),
            ("suggested_filename", "../secret.txt"),
            ("suggested_filename", "report.csv\r\nX-Injected: yes"),
            ("suggested_filename", "CON"),
        ],
    )
    def test_download_metadata_rejects_header_and_path_injection(
        self,
        field: str,
        value: str,
    ) -> None:
        body = {
            "run_id": SCOPE.run_id,
            "kind": "file",
            "title": "download",
            "media_type": "application/octet-stream",
            "suggested_filename": "safe.bin",
            "idempotency_key": "safe-metadata",
        }
        body[field] = value

        with pytest.raises(ValidationError):
            ArtifactCreateRequest.model_validate(body)

    def test_invalid_promotion_source_is_rejected_before_resolver(self) -> None:
        sources = self.Sources(b"secret")
        self.service(sources=sources)

        with pytest.raises(ValidationError):
            ArtifactPromotionRequest(
                run_id=SCOPE.run_id,
                source_ref="file:///etc/passwd",
                kind=ArtifactKind.FILE,
                idempotency_key="promote-invalid",
            )

        assert sources.resolve_calls == 0

    @pytest.mark.asyncio
    async def test_create_streams_revision_one_and_outbox_event(self) -> None:
        metadata = self.Metadata()
        blobs = self.Blobs()
        service = self.service(metadata=metadata, blobs=blobs)

        result = await service.create_from_stream(
            org_id=SCOPE.org_id,
            user_id=SCOPE.user_id,
            request=ArtifactCreateRequest(
                run_id=SCOPE.run_id,
                kind=ArtifactKind.CODE,
                title="parser.py",
                media_type="text/x-python",
                suggested_filename="parser.py",
                idempotency_key="create-1",
            ),
            provenance=ArtifactProvenance(author=ArtifactAuthor.MODEL),
            chunks=self.chunks(b"print(", b"'ok')\n"),
        )

        assert result.record.artifact.current_revision == 1
        assert result.record.current_revision.revision.content_ref.startswith(
            "artifact://art_"
        )
        assert metadata.create_command is not None
        assert tuple(
            event.event_type for event in metadata.create_command.ledger_events
        ) == (
            LedgerEventType.ARTIFACT_CREATED,
            LedgerEventType.ARTIFACT_PRESENTATION_DECIDED,
        )
        assert metadata.create_command.ledger_events[1].payload == {
            "v": 1,
            "artifact_id": result.record.artifact.artifact_id,
            "decision": "canvas",
            "basis": "durable_supported_artifact_auto",
        }
        assert "print" not in str(metadata.create_command.ledger_events)
        assert blobs.put_calls == 1

    @pytest.mark.asyncio
    async def test_publish_uses_create_transaction_with_model_attribution(self) -> None:
        metadata = self.Metadata()
        service = self.service(metadata=metadata)

        result = await service.publish_from_bytes(
            org_id=SCOPE.org_id,
            user_id=SCOPE.user_id,
            request=ArtifactCreateRequest(
                run_id=SCOPE.run_id,
                kind=ArtifactKind.CODE,
                title="published.py",
                media_type="text/x-python",
                idempotency_key="test-publish",
            ),
            provenance=ArtifactProvenance(author=ArtifactAuthor.MODEL),
            content=b"print('published')\n",
        )

        assert result.record.artifact.created_by is ArtifactAuthor.MODEL
        assert metadata.create_command is not None
        assert metadata.create_command.idempotency.route == "INTERNAL:artifact.publish"
        assert tuple(
            event.event_type for event in metadata.create_command.ledger_events
        ) == (
            LedgerEventType.ARTIFACT_CREATED,
            LedgerEventType.ARTIFACT_PRESENTATION_DECIDED,
        )
        assert "published" not in str(metadata.create_command.ledger_events)

    @pytest.mark.asyncio
    async def test_foreign_run_is_rejected_before_blob_ingest(self) -> None:
        blobs = self.Blobs()
        service = self.service(scopes=self.Scopes(None), blobs=blobs)

        with pytest.raises(ArtifactNotFoundError) as captured:
            await service.create_from_stream(
                org_id=SCOPE.org_id,
                user_id=SCOPE.user_id,
                request=ArtifactCreateRequest(
                    run_id=SCOPE.run_id,
                    kind=ArtifactKind.DOCUMENT,
                    title="note",
                    media_type="text/markdown",
                    idempotency_key="create-2",
                ),
                provenance=ArtifactProvenance(author=ArtifactAuthor.USER),
                chunks=self.chunks(b"secret"),
            )

        assert captured.value.safe_message == "Artifact was not found for this scope."
        assert blobs.put_calls == 0

    @pytest.mark.asyncio
    async def test_revision_pins_parent_and_emits_revised(self) -> None:
        metadata = self.Metadata()
        service = self.service(metadata=metadata)
        created = await service.create_from_bytes(
            org_id=SCOPE.org_id,
            user_id=SCOPE.user_id,
            request=ArtifactCreateRequest(
                run_id=SCOPE.run_id,
                kind=ArtifactKind.DOCUMENT,
                title="README",
                media_type="text/markdown",
                idempotency_key="create-3",
            ),
            provenance=ArtifactProvenance(author=ArtifactAuthor.MODEL),
            content=b"v1",
        )

        revised = await service.append_revision_from_stream(
            org_id=SCOPE.org_id,
            user_id=SCOPE.user_id,
            request=ArtifactRevisionRequest(
                artifact_id=created.record.artifact.artifact_id,
                parent_revision=1,
                idempotency_key="rev-1",
            ),
            provenance=ArtifactProvenance(author=ArtifactAuthor.USER),
            chunks=self.chunks(b"v2"),
        )

        assert revised.record.artifact.current_revision == 2
        assert metadata.append_command.expected_revision == 1
        assert (
            metadata.append_command.ledger_event.event_type
            is LedgerEventType.ARTIFACT_REVISED
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "source_ref",
        (
            "message://msg_1",
            "operation://op_123e4567-e89b-42d3-a456-426614174000/result",
            "payload://payload_result_01",
        ),
    )
    async def test_promotion_resolves_and_copies_server_owned_source(
        self, source_ref: str
    ) -> None:
        metadata = self.Metadata()
        service = self.service(
            metadata=metadata,
            sources=self.Sources(b"# Hello\n", source_ref=source_ref),
        )

        result = await service.promote_source(
            org_id=SCOPE.org_id,
            user_id=SCOPE.user_id,
            request=ArtifactPromotionRequest(
                run_id=SCOPE.run_id,
                source_ref=source_ref,
                kind=ArtifactKind.DOCUMENT,
                idempotency_key="promote-1",
            ),
        )

        assert result.record.artifact.title == "Promoted note"
        assert result.record.suggested_filename == "note.md"
        assert tuple(
            event.event_type for event in metadata.create_command.ledger_events
        ) == (
            LedgerEventType.ARTIFACT_CREATED,
            LedgerEventType.ARTIFACT_PRESENTATION_DECIDED,
            LedgerEventType.ARTIFACT_PROMOTED,
        )
        promoted = metadata.create_command.ledger_events[-1]
        assert promoted.payload["source_ref"] == source_ref
        assert "# Hello" not in str(metadata.create_command.ledger_events)

    @pytest.mark.asyncio
    async def test_publish_from_source_preserves_subagent_authorship(self) -> None:
        metadata = self.Metadata()
        source_ref = "payload://subagent-output"
        service = self.service(
            metadata=metadata,
            sources=self.Sources(b"# Subagent notes\n", source_ref=source_ref),
        )

        result = await service.publish_from_source(
            org_id=SCOPE.org_id,
            user_id=SCOPE.user_id,
            request=ArtifactCreateRequest(
                run_id=SCOPE.run_id,
                kind=ArtifactKind.DOCUMENT,
                title="subagent-notes.md",
                media_type="text/markdown",
                idempotency_key="operation-publish-source-1",
            ),
            provenance=ArtifactProvenance(
                author=ArtifactAuthor.SUBAGENT,
                source_ref=source_ref,
            ),
            source_ref=source_ref,
        )

        assert result.record.artifact.created_by is ArtifactAuthor.SUBAGENT
        assert metadata.create_command is not None
        revision = metadata.create_command.record.current_revision.revision
        assert revision.author is ArtifactAuthor.SUBAGENT
        assert revision.source_ref == source_ref
        assert tuple(
            event.event_type for event in metadata.create_command.ledger_events
        ) == (
            LedgerEventType.ARTIFACT_CREATED,
            LedgerEventType.ARTIFACT_PRESENTATION_DECIDED,
        )

    @pytest.mark.asyncio
    async def test_range_stream_returns_exact_bytes(self) -> None:
        service = self.service()
        created = await service.create_from_bytes(
            org_id=SCOPE.org_id,
            user_id=SCOPE.user_id,
            request=ArtifactCreateRequest(
                run_id=SCOPE.run_id,
                kind=ArtifactKind.FILE,
                title="bytes",
                media_type="application/octet-stream",
                idempotency_key="create-4",
            ),
            provenance=ArtifactProvenance(author=ArtifactAuthor.USER),
            content=b"0123456789",
        )
        artifact_id = created.record.artifact.artifact_id

        _, _, stream = await service.stream_revision(
            org_id=SCOPE.org_id,
            user_id=SCOPE.user_id,
            artifact_id=artifact_id,
            revision=1,
            byte_range=ByteRange(start=3, end=6),
        )

        assert b"".join([chunk async for chunk in stream]) == b"3456"

        with pytest.raises(ArtifactRangeError):
            await service.stream_revision(
                org_id=SCOPE.org_id,
                user_id=SCOPE.user_id,
                artifact_id=artifact_id,
                revision=1,
                byte_range=ByteRange(start=9, end=10),
            )

    @pytest.mark.asyncio
    async def test_list_authorizes_run_before_querying_store(self) -> None:
        metadata = self.Metadata()
        service = self.service(metadata=metadata)

        page = await service.list_for_run(
            org_id=SCOPE.org_id,
            user_id=SCOPE.user_id,
            run_id=SCOPE.run_id,
            kind=ArtifactKind.DATASET,
            limit=25,
        )

        assert page.artifacts == ()
        assert metadata.list_query.kind is ArtifactKind.DATASET
        assert metadata.list_query.limit == 25

    @pytest.mark.asyncio
    async def test_soft_delete_is_metadata_only(self) -> None:
        metadata = self.Metadata()
        blobs = self.Blobs()
        service = self.service(metadata=metadata, blobs=blobs)
        created = await service.create_from_bytes(
            org_id=SCOPE.org_id,
            user_id=SCOPE.user_id,
            request=ArtifactCreateRequest(
                run_id=SCOPE.run_id,
                kind=ArtifactKind.FILE,
                title="download",
                media_type="application/octet-stream",
                idempotency_key="create-5",
            ),
            provenance=ArtifactProvenance(author=ArtifactAuthor.USER),
            content=b"kept",
        )
        digest = created.record.current_revision.blob_key

        await service.soft_delete(
            org_id=SCOPE.org_id,
            user_id=SCOPE.user_id,
            artifact_id=created.record.artifact.artifact_id,
            idempotency_key="delete-1",
        )

        assert metadata.record.artifact.deleted_at is not None
        assert blobs.data_by_key[digest] == b"kept"

        # Identical retry succeeds, but a fresh idempotency key cannot discover
        # or mutate the tombstone.
        await service.soft_delete(
            org_id=SCOPE.org_id,
            user_id=SCOPE.user_id,
            artifact_id=created.record.artifact.artifact_id,
            idempotency_key="delete-1",
        )
        with pytest.raises(ArtifactNotFoundError):
            await service.soft_delete(
                org_id=SCOPE.org_id,
                user_id=SCOPE.user_id,
                artifact_id=created.record.artifact.artifact_id,
                idempotency_key="delete-2",
            )

    @pytest.mark.asyncio
    async def test_stream_iteration_translates_adapter_failure(self) -> None:
        class BrokenStreamBlobs(self.Blobs):
            async def open_stream(self, blob_key, *, start=None, end=None):
                async def stream() -> AsyncIterator[bytes]:
                    yield b"prefix"
                    raise OSError("private adapter detail")

                return stream()

        blobs = BrokenStreamBlobs()
        service = self.service(blobs=blobs)
        created = await service.create_from_bytes(
            org_id=SCOPE.org_id,
            user_id=SCOPE.user_id,
            request=ArtifactCreateRequest(
                run_id=SCOPE.run_id,
                kind=ArtifactKind.FILE,
                title="download",
                media_type="application/octet-stream",
                idempotency_key="broken-stream",
            ),
            provenance=ArtifactProvenance(author=ArtifactAuthor.USER),
            content=b"content",
        )
        _, _, stream = await service.stream_revision(
            org_id=SCOPE.org_id,
            user_id=SCOPE.user_id,
            artifact_id=created.record.artifact.artifact_id,
            revision=1,
        )

        with pytest.raises(ArtifactBlobUnavailableError) as captured:
            _ = [chunk async for chunk in stream]
        assert "private adapter detail" not in captured.value.safe_message


class TestActingRunAttribution(ArtifactServiceFakes):
    """PRD-02 Flow B — a revision is causal in the run the user is acting in.

    Attributing it to the run that *created* the artifact produces a surface
    that can be written to but never refreshes: the creating run is sealed, so
    ``artifact.revised`` is rejected there, the acting run's stream never carries
    it, the open tab stays on the old revision, and the next edit fails its
    compare-and-append against a stale parent.
    """

    CREATING_RUN = "run_1"
    ACTING_RUN = "run_2"

    @classmethod
    def _acting_scope(cls, *, terminal: bool = False) -> ArtifactScope:
        return ArtifactScope(
            org_id=SCOPE.org_id,
            user_id=SCOPE.user_id,
            conversation_id=SCOPE.conversation_id,
            run_id=cls.ACTING_RUN,
            trace_id="trace_2",
            run_is_terminal=terminal,
        )

    @classmethod
    async def _created(cls, service):
        return await service.create_from_bytes(
            org_id=SCOPE.org_id,
            user_id=SCOPE.user_id,
            request=ArtifactCreateRequest(
                run_id=cls.CREATING_RUN,
                kind=ArtifactKind.DOCUMENT,
                title="README",
                media_type="text/markdown",
                idempotency_key="create-acting",
            ),
            provenance=ArtifactProvenance(author=ArtifactAuthor.MODEL),
            content=b"v1",
        )

    async def _revise(self, service, artifact_id: str, *, acting_run_id, key="rev"):
        return await service.append_revision_from_stream(
            org_id=SCOPE.org_id,
            user_id=SCOPE.user_id,
            request=ArtifactRevisionRequest(
                artifact_id=artifact_id,
                parent_revision=1,
                idempotency_key=key,
                acting_run_id=acting_run_id,
            ),
            provenance=ArtifactProvenance(author=ArtifactAuthor.USER),
            chunks=self.chunks(b"v2"),
        )

    @pytest.mark.asyncio
    async def test_revision_is_attributed_to_the_acting_run(self) -> None:
        metadata = self.Metadata()
        scopes = self.MultiRunScopes(
            {self.CREATING_RUN: SCOPE, self.ACTING_RUN: self._acting_scope()}
        )
        service = self.service(metadata=metadata, scopes=scopes)
        created = await self._created(service)

        await self._revise(
            service,
            created.record.artifact.artifact_id,
            acting_run_id=self.ACTING_RUN,
        )

        # The ledger event lands in the OPEN run, so the seal accepts it and a
        # live client actually receives the revision.
        assert metadata.append_command.scope.run_id == self.ACTING_RUN
        assert metadata.append_command.ledger_event.scope.run_id == self.ACTING_RUN

    @pytest.mark.asyncio
    async def test_the_artifact_keeps_its_creating_run_as_provenance(self) -> None:
        metadata = self.Metadata()
        scopes = self.MultiRunScopes(
            {self.CREATING_RUN: SCOPE, self.ACTING_RUN: self._acting_scope()}
        )
        service = self.service(metadata=metadata, scopes=scopes)
        created = await self._created(service)

        revised = await self._revise(
            service,
            created.record.artifact.artifact_id,
            acting_run_id=self.ACTING_RUN,
        )

        # Where it was produced is a durable fact; where it was edited is not the
        # same question, and must not overwrite it.
        assert revised.record.artifact.run_id == self.CREATING_RUN

    @pytest.mark.asyncio
    async def test_unset_acting_run_still_uses_the_creating_run(self) -> None:
        """Agent-authored revisions act inside the creating run — unchanged."""

        metadata = self.Metadata()
        scopes = self.MultiRunScopes({self.CREATING_RUN: SCOPE})
        service = self.service(metadata=metadata, scopes=scopes)
        created = await self._created(service)

        await self._revise(
            service, created.record.artifact.artifact_id, acting_run_id=None
        )

        assert metadata.append_command.scope.run_id == self.CREATING_RUN

    @pytest.mark.asyncio
    async def test_a_run_the_caller_does_not_own_is_refused(self) -> None:
        """``acting_run_id`` is a claim, and claims are verified, not trusted."""

        scopes = self.MultiRunScopes({self.CREATING_RUN: SCOPE})
        service = self.service(scopes=scopes)
        created = await self._created(service)

        with pytest.raises(ArtifactNotFoundError):
            await self._revise(
                service,
                created.record.artifact.artifact_id,
                acting_run_id="run_belonging_to_someone_else",
            )

    @pytest.mark.asyncio
    async def test_a_sealed_acting_run_is_refused_before_any_write(self) -> None:
        """A terminal claimed run would recreate the very defect being fixed."""

        metadata = self.Metadata()
        blobs = self.Blobs()
        scopes = self.MultiRunScopes(
            {
                self.CREATING_RUN: SCOPE,
                self.ACTING_RUN: self._acting_scope(terminal=True),
            }
        )
        service = self.service(metadata=metadata, blobs=blobs, scopes=scopes)
        created = await self._created(service)
        writes_before = blobs.put_calls

        with pytest.raises(ArtifactConflictError):
            await self._revise(
                service,
                created.record.artifact.artifact_id,
                acting_run_id=self.ACTING_RUN,
            )

        # Refused before the body was streamed — no orphaned blob, no revision.
        assert blobs.put_calls == writes_before
        assert metadata.append_command is None
