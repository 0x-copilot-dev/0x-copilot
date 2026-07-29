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
    ArtifactErrorCode,
    ArtifactNotFoundError,
    ArtifactRangeError,
    ArtifactSealedRunError,
    ArtifactTooLargeError,
)
from agent_runtime.artifacts.execution_mode import (
    ArtifactExecutionMode,
    ArtifactExecutionModeResolver,
    ArtifactOperation,
    ArtifactOperationAudit,
)
from agent_runtime.artifacts.service import ArtifactService
from agent_runtime.surfaces_v2.ledger_models import (
    ArtifactAuthor,
    ArtifactCausalLane,
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


def conversation_scope(
    org_id: str, user_id: str, conversation_id: str
) -> ArtifactScope:
    """The CONVERSATION-lane scope a user-authored revision resolves to."""

    return ArtifactScope(
        org_id=org_id,
        user_id=user_id,
        conversation_id=conversation_id,
        trace_id=conversation_id,
        lane=ArtifactCausalLane.CONVERSATION,
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

        async def resolve_conversation(self, *, org_id, user_id, conversation_id):
            if (
                self.scope is None
                or self.scope.org_id != org_id
                or self.scope.user_id != user_id
                or self.scope.conversation_id != conversation_id
            ):
                return None
            return conversation_scope(org_id, user_id, conversation_id)

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

        async def resolve_conversation(self, *, org_id, user_id, conversation_id):
            known = any(
                scope.conversation_id == conversation_id
                and scope.org_id == org_id
                and scope.user_id == user_id
                for scope in self.scopes.values()
            )
            if not known:
                return None
            return conversation_scope(org_id, user_id, conversation_id)

    class Audit:
        """Stand in for the runtime's HMAC hash-chained audit log.

        Keeps the exact ``(event_type, record)`` pair the real
        ``write_audit_log`` is handed, so a test reads what an auditor would
        read instead of reaching inside the service for it.
        """

        def __init__(self) -> None:
            self.rows: list[tuple[str, object]] = []

        async def write_audit_log(self, *, event_type: str, record: object) -> None:
            self.rows.append((event_type, record))

        def entries(self) -> list[ArtifactOperationAudit]:
            """Every captured row, recovered as the typed operation it encodes."""

            return [
                ArtifactOperationAudit.parse_audit_record(record)
                for _, record in self.rows
            ]

    class FailingAudit(Audit):
        """An audit log that is down, for proving the failure is not swallowed."""

        MESSAGE = "audit log unavailable"

        async def write_audit_log(self, *, event_type: str, record: object) -> None:
            raise RuntimeError(self.MESSAGE)

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
        audit=None,
    ):
        return ArtifactService(
            metadata=metadata or cls.Metadata(),
            blobs=blobs or cls.Blobs(),
            run_scopes=scopes or cls.Scopes(),
            sources=sources,
            now=lambda: NOW,
            audit=audit,
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

        # Agent-authored, so this stays in the RUN lane where a ledger event is
        # the point. The user-authored lane emits none by design and is covered
        # by ``TestRevisionCausalLane``.
        revised = await service.append_revision_from_stream(
            org_id=SCOPE.org_id,
            user_id=SCOPE.user_id,
            request=ArtifactRevisionRequest(
                artifact_id=created.record.artifact.artifact_id,
                parent_revision=1,
                idempotency_key="rev-1",
            ),
            provenance=ArtifactProvenance(author=ArtifactAuthor.MODEL),
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


class TestRevisionCausalLane(ArtifactServiceFakes):
    """PRD-01 — authorship decides which causal subject a revision belongs to.

    A run's terminal event promises "everything this run caused is already in
    the ledger". A user editing a cell minutes after the turn ended was not
    caused by that run, so attributing the revision there would make the seal
    lie — and every run on screen is normally sealed by the time anyone edits,
    which is why the previous model refused ordinary saves outright.

    Agent work keeps the run lane and its seal. User work takes the conversation
    lane, which has no terminal state to violate.
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

    async def _revise(
        self,
        service,
        artifact_id: str,
        *,
        acting_run_id=None,
        author=ArtifactAuthor.USER,
        key="rev",
    ):
        return await service.append_revision_from_stream(
            org_id=SCOPE.org_id,
            user_id=SCOPE.user_id,
            request=ArtifactRevisionRequest(
                artifact_id=artifact_id,
                parent_revision=1,
                idempotency_key=key,
                acting_run_id=acting_run_id,
            ),
            provenance=ArtifactProvenance(author=author),
            chunks=self.chunks(b"v2"),
        )

    @pytest.mark.asyncio
    async def test_a_user_edit_succeeds_although_every_run_has_sealed(self) -> None:
        """The live defect: saving a cell edit after the turn ended returned 409.

        Both runs are terminal here, exactly as they are in the app by the time a
        user can see a table and change it.
        """

        metadata = self.Metadata()
        scopes = self.MultiRunScopes(
            {
                self.CREATING_RUN: SCOPE.model_copy(update={"run_is_terminal": True}),
                self.ACTING_RUN: self._acting_scope(terminal=True),
            }
        )
        service = self.service(metadata=metadata, scopes=scopes)
        created = await self._created(service)

        revised = await self._revise(service, created.record.artifact.artifact_id)

        assert revised.record.artifact.current_revision == 2

    @pytest.mark.asyncio
    async def test_a_user_edit_claims_no_run_and_emits_no_run_event(self) -> None:
        """The lane exists so a sealed ledger is never asked to accept an event."""

        metadata = self.Metadata()
        scopes = self.MultiRunScopes({self.CREATING_RUN: SCOPE})
        service = self.service(metadata=metadata, scopes=scopes)
        created = await self._created(service)

        await self._revise(service, created.record.artifact.artifact_id)

        command = metadata.append_command
        assert command.scope.lane is ArtifactCausalLane.CONVERSATION
        assert command.scope.run_id is None
        assert command.scope.conversation_id == SCOPE.conversation_id
        assert command.ledger_event is None

    @pytest.mark.asyncio
    async def test_a_user_edit_ignores_a_supplied_acting_run(self) -> None:
        """``acting_run_id`` names a subject the conversation lane does not use.

        Supplying one must not drag the revision back into a run ledger, which is
        precisely how the defect was reintroduced.
        """

        metadata = self.Metadata()
        scopes = self.MultiRunScopes(
            {
                self.CREATING_RUN: SCOPE,
                self.ACTING_RUN: self._acting_scope(terminal=True),
            }
        )
        service = self.service(metadata=metadata, scopes=scopes)
        created = await self._created(service)

        revised = await self._revise(
            service,
            created.record.artifact.artifact_id,
            acting_run_id=self.ACTING_RUN,
        )

        assert revised.record.artifact.current_revision == 2
        assert metadata.append_command.scope.lane is ArtifactCausalLane.CONVERSATION
        assert metadata.append_command.scope.run_id is None

    @pytest.mark.asyncio
    async def test_the_lane_follows_authorship_not_the_request(self) -> None:
        """A caller cannot route an agent write into the unsealed lane.

        Identical request fields; only the server-held author differs.
        """

        metadata = self.Metadata()
        scopes = self.MultiRunScopes({self.CREATING_RUN: SCOPE})
        service = self.service(metadata=metadata, scopes=scopes)
        created = await self._created(service)

        await self._revise(
            service,
            created.record.artifact.artifact_id,
            author=ArtifactAuthor.MODEL,
        )

        assert metadata.append_command.scope.lane is ArtifactCausalLane.RUN
        assert metadata.append_command.scope.run_id == self.CREATING_RUN

    @pytest.mark.asyncio
    async def test_an_agent_revision_is_attributed_to_the_acting_run(self) -> None:
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
            author=ArtifactAuthor.MODEL,
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
            author=ArtifactAuthor.MODEL,
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
            service,
            created.record.artifact.artifact_id,
            acting_run_id=None,
            author=ArtifactAuthor.MODEL,
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
                author=ArtifactAuthor.MODEL,
            )

    @pytest.mark.asyncio
    async def test_a_sealed_acting_run_is_refused_before_any_write(self) -> None:
        """An agent claiming a terminal run still cannot write to its ledger."""

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

        with pytest.raises(ArtifactSealedRunError):
            await self._revise(
                service,
                created.record.artifact.artifact_id,
                acting_run_id=self.ACTING_RUN,
                author=ArtifactAuthor.MODEL,
            )

        # Refused before the body was streamed — no orphaned blob, no revision.
        assert blobs.put_calls == writes_before
        assert metadata.append_command is None

    @pytest.mark.asyncio
    async def test_a_sealed_run_is_not_reported_as_a_stale_revision(self) -> None:
        """The UI said "a newer revision exists" when none did.

        The two causes share HTTP 409, so the distinct type is what lets a client
        tell "you are out of date" from "that run has finished".
        """

        scopes = self.MultiRunScopes(
            {
                self.CREATING_RUN: SCOPE,
                self.ACTING_RUN: self._acting_scope(terminal=True),
            }
        )
        service = self.service(scopes=scopes)
        created = await self._created(service)

        with pytest.raises(ArtifactSealedRunError) as caught:
            await self._revise(
                service,
                created.record.artifact.artifact_id,
                acting_run_id=self.ACTING_RUN,
                author=ArtifactAuthor.MODEL,
            )

        assert not isinstance(caught.value, ArtifactConflictError)
        assert caught.value.code is ArtifactErrorCode.SEALED_RUN


class TestExecutionModeIsRecorded(ArtifactServiceFakes):
    """PRD-03 D2 — the mode an operation ran under is a fact, not a setting.

    An auto-send mode is planned that lets a user switch gating off per tool or
    per chat. Asking the settings afterwards cannot answer "was this gated?" —
    by then they may say something the operation never saw. So each operation
    records the mode it actually ran under, at the moment it ran.

    That mode does not exist yet, so every row below reads ``staged``. These
    tests hold the seam to its shape while the value is still a constant, which
    is the only time it is cheap to get wrong unnoticed.
    """

    #: Server-derived from the bound draft scope, so a retry names the same
    #: artifact instead of minting a fresh id the digests would move with.
    RETRIED_ID = "art_00000000-0000-4000-8000-0000000000e1"

    @classmethod
    def _create_request(cls, key: str) -> ArtifactCreateRequest:
        return ArtifactCreateRequest(
            run_id=SCOPE.run_id,
            kind=ArtifactKind.DOCUMENT,
            title="README",
            media_type="text/markdown",
            idempotency_key=key,
        )

    @classmethod
    async def _create(cls, service, *, key: str = "create-mode", author=None):
        return await service.create_from_bytes(
            org_id=SCOPE.org_id,
            user_id=SCOPE.user_id,
            request=cls._create_request(key),
            provenance=ArtifactProvenance(author=author or ArtifactAuthor.MODEL),
            content=b"v1",
        )

    @classmethod
    def _force_mode(
        cls, monkeypatch: pytest.MonkeyPatch, mode: ArtifactExecutionMode
    ) -> None:
        """Stand in for a mode the resolver cannot yet be made to return.

        Reaching past the resolver is the point: it is the only way to exercise
        what happens once auto-execute ships, while the resolver itself is
        still honestly a constant.
        """

        monkeypatch.setattr(ArtifactExecutionModeResolver, "resolve", lambda **_: mode)

    @classmethod
    async def _digests_of_every_write(cls, metadata) -> tuple[str, ...]:
        """Create, revise, and delete one artifact; return the keys they persist.

        Seeded through the draft path so the artifact id is fixed rather than
        freshly minted: the revise and delete digests are built from it, and a
        new id per run would make them differ for a reason that has nothing to
        do with the mode under test.
        """

        service = cls.service(metadata=metadata)
        created = await service.create_draft_from_bytes(
            org_id=SCOPE.org_id,
            user_id=SCOPE.user_id,
            request=cls._create_request("retried-create"),
            provenance=ArtifactProvenance(author=ArtifactAuthor.MODEL),
            content=b"v1",
            artifact_id=cls.RETRIED_ID,
        )
        artifact_id = created.record.artifact.artifact_id
        await service.append_revision_from_stream(
            org_id=SCOPE.org_id,
            user_id=SCOPE.user_id,
            request=ArtifactRevisionRequest(
                artifact_id=artifact_id,
                parent_revision=1,
                idempotency_key="retried-revise",
            ),
            provenance=ArtifactProvenance(author=ArtifactAuthor.MODEL),
            chunks=cls.chunks(b"v2"),
        )
        await service.soft_delete(
            org_id=SCOPE.org_id,
            user_id=SCOPE.user_id,
            artifact_id=artifact_id,
            idempotency_key="retried-delete",
        )
        return tuple(
            command.idempotency.request_digest
            for command in (
                metadata.create_command,
                metadata.append_command,
                metadata.delete_command,
            )
        )

    @pytest.mark.asyncio
    async def test_creating_an_artifact_records_a_staged_operation(self) -> None:
        audit = self.Audit()
        service = self.service(audit=audit)

        created = await self._create(service)

        ((event_type, _),) = audit.rows
        assert event_type == "artifact.create"
        (entry,) = audit.entries()
        assert entry.operation is ArtifactOperation.CREATE
        assert entry.execution_mode is ArtifactExecutionMode.STAGED
        assert entry.artifact_id == created.record.artifact.artifact_id
        assert entry.org_id == SCOPE.org_id
        assert entry.user_id == SCOPE.user_id
        assert entry.conversation_id == SCOPE.conversation_id
        assert entry.run_id == SCOPE.run_id
        assert entry.lane is ArtifactCausalLane.RUN
        assert entry.revision == 1
        assert entry.author is ArtifactAuthor.MODEL
        assert entry.occurred_at == NOW

    @pytest.mark.asyncio
    async def test_publishing_records_its_own_operation_not_a_generic_write(
        self,
    ) -> None:
        audit = self.Audit()
        service = self.service(audit=audit)

        await service.publish_from_bytes(
            org_id=SCOPE.org_id,
            user_id=SCOPE.user_id,
            request=self._create_request("publish-mode"),
            provenance=ArtifactProvenance(author=ArtifactAuthor.MODEL),
            content=b"published",
        )

        (entry,) = audit.entries()
        assert entry.operation is ArtifactOperation.PUBLISH
        assert entry.execution_mode is ArtifactExecutionMode.STAGED
        assert audit.rows[0][0] == "artifact.publish"

    @pytest.mark.asyncio
    async def test_promotion_records_the_subagent_that_authored_it(self) -> None:
        audit = self.Audit()
        service = self.service(audit=audit, sources=self.Sources(b"# Hello\n"))

        await service.promote_source(
            org_id=SCOPE.org_id,
            user_id=SCOPE.user_id,
            request=ArtifactPromotionRequest(
                run_id=SCOPE.run_id,
                source_ref="message://msg_1",
                kind=ArtifactKind.DOCUMENT,
                idempotency_key="promote-mode",
            ),
        )

        (entry,) = audit.entries()
        assert entry.operation is ArtifactOperation.PROMOTE
        assert entry.execution_mode is ArtifactExecutionMode.STAGED
        assert audit.rows[0][0] == "artifact.promote"

    @pytest.mark.asyncio
    async def test_a_revision_records_the_revision_it_appended(self) -> None:
        audit = self.Audit()
        service = self.service(audit=audit)
        created = await self._create(service)

        await service.append_revision_from_stream(
            org_id=SCOPE.org_id,
            user_id=SCOPE.user_id,
            request=ArtifactRevisionRequest(
                artifact_id=created.record.artifact.artifact_id,
                parent_revision=1,
                idempotency_key="revise-mode",
            ),
            provenance=ArtifactProvenance(author=ArtifactAuthor.MODEL),
            chunks=self.chunks(b"v2"),
        )

        create_entry, revise_entry = audit.entries()
        assert create_entry.operation is ArtifactOperation.CREATE
        assert revise_entry.operation is ArtifactOperation.REVISE
        assert revise_entry.revision == 2
        assert revise_entry.execution_mode is ArtifactExecutionMode.STAGED
        assert audit.rows[1][0] == "artifact.revise"

    @pytest.mark.asyncio
    async def test_a_user_edit_records_the_conversation_lane_it_ran_in(self) -> None:
        """PRD-01's lane split has to survive into the audit row.

        A user edit claims no run, so recording the artifact's creating run here
        would name a turn that did not perform the edit.
        """

        audit = self.Audit()
        service = self.service(audit=audit)
        created = await self._create(service)

        await service.append_revision_from_stream(
            org_id=SCOPE.org_id,
            user_id=SCOPE.user_id,
            request=ArtifactRevisionRequest(
                artifact_id=created.record.artifact.artifact_id,
                parent_revision=1,
                idempotency_key="user-revise-mode",
            ),
            provenance=ArtifactProvenance(author=ArtifactAuthor.USER),
            chunks=self.chunks(b"v2"),
        )

        _, revise_entry = audit.entries()
        assert revise_entry.author is ArtifactAuthor.USER
        assert revise_entry.lane is ArtifactCausalLane.CONVERSATION
        assert revise_entry.run_id is None
        assert revise_entry.execution_mode is ArtifactExecutionMode.STAGED

    @pytest.mark.asyncio
    async def test_deleting_records_the_user_who_asked_for_it(self) -> None:
        audit = self.Audit()
        service = self.service(audit=audit)
        created = await self._create(service)

        await service.soft_delete(
            org_id=SCOPE.org_id,
            user_id=SCOPE.user_id,
            artifact_id=created.record.artifact.artifact_id,
            idempotency_key="delete-mode",
        )

        _, delete_entry = audit.entries()
        assert delete_entry.operation is ArtifactOperation.DELETE
        assert delete_entry.execution_mode is ArtifactExecutionMode.STAGED
        assert delete_entry.author is ArtifactAuthor.USER
        assert delete_entry.lane is ArtifactCausalLane.CONVERSATION
        assert delete_entry.run_id is None
        assert delete_entry.revision is None
        assert audit.rows[1][0] == "artifact.delete"

    @pytest.mark.asyncio
    async def test_the_recorded_mode_is_the_one_the_command_committed_with(
        self,
    ) -> None:
        """The row and the write cannot disagree, because they are one value.

        Re-deriving the mode when writing the row would let a future resolver
        change between the two and audit the write as something it was not.
        """

        metadata = self.Metadata()
        audit = self.Audit()
        service = self.service(metadata=metadata, audit=audit)
        created = await self._create(service)

        await service.append_revision_from_stream(
            org_id=SCOPE.org_id,
            user_id=SCOPE.user_id,
            request=ArtifactRevisionRequest(
                artifact_id=created.record.artifact.artifact_id,
                parent_revision=1,
                idempotency_key="revise-same-mode",
            ),
            provenance=ArtifactProvenance(author=ArtifactAuthor.MODEL),
            chunks=self.chunks(b"v2"),
        )

        create_entry, revise_entry = audit.entries()
        assert create_entry.execution_mode is metadata.create_command.execution_mode
        assert revise_entry.execution_mode is metadata.append_command.execution_mode

    @pytest.mark.asyncio
    async def test_the_idempotency_digest_does_not_move_when_the_mode_does(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A retry must still replay when the toggle flipped underneath it.

        Auto-execute is switchable per tool and per chat, so it can change
        between a request and that request's own retry. If the mode reached the
        persisted request digest, the retry would come back as an idempotency
        conflict on a request the client never altered, and its only recovery
        would be a fresh key — the duplicate artifact idempotency exists to
        prevent. The mode of the one operation that ran is durable on its
        command and in its audit row; it has no business in the key.
        """

        by_mode: dict[ArtifactExecutionMode, tuple[str, ...]] = {}
        for mode in ArtifactExecutionMode:
            self._force_mode(monkeypatch, mode)
            metadata = self.Metadata()

            by_mode[mode] = await self._digests_of_every_write(metadata)

            # Proves the stand-in took, so the comparison below really spans
            # two modes rather than running the same one twice.
            assert {
                metadata.create_command.execution_mode,
                metadata.append_command.execution_mode,
                metadata.delete_command.execution_mode,
            } == {mode}

        assert len(set(by_mode.values())) == 1

    @pytest.mark.asyncio
    async def test_the_mode_is_derived_even_when_no_audit_sink_is_wired(self) -> None:
        """The fact is produced by the operation, not by the sink observing it.

        An unwired sink loses the row; it must never mean the write went out
        without a mode attached to it.
        """

        metadata = self.Metadata()
        service = self.service(metadata=metadata, audit=None)

        await self._create(service)

        assert metadata.create_command.execution_mode is ArtifactExecutionMode.STAGED

    @pytest.mark.asyncio
    async def test_an_idempotent_replay_appends_no_second_row(self) -> None:
        """A replay performed no write, so the log must not claim one.

        A second ``artifact.create`` carrying the replay's clock would read as
        an artifact created twice — false, and exactly the kind of thing an
        auditor counts.
        """

        class ReplayingMetadata(self.Metadata):
            async def create_artifact(self, command):
                await super().create_artifact(command)
                return ArtifactMutationResult(record=command.record, replayed=True)

        audit = self.Audit()
        service = self.service(metadata=ReplayingMetadata(), audit=audit)

        await self._create(service)

        assert audit.rows == []

    @pytest.mark.asyncio
    async def test_a_repeated_delete_records_the_deletion_once(self) -> None:
        audit = self.Audit()
        service = self.service(audit=audit)
        created = await self._create(service)
        artifact_id = created.record.artifact.artifact_id

        for _ in range(2):
            await service.soft_delete(
                org_id=SCOPE.org_id,
                user_id=SCOPE.user_id,
                artifact_id=artifact_id,
                idempotency_key="delete-twice",
            )

        deletes = [
            entry
            for entry in audit.entries()
            if entry.operation is ArtifactOperation.DELETE
        ]
        assert len(deletes) == 1

    @pytest.mark.asyncio
    async def test_a_failing_audit_log_is_not_swallowed(self) -> None:
        """Nothing redelivers this row, so quietly dropping it loses the fact.

        The whole point of the seam is that the record cannot go missing without
        anyone noticing; a caught-and-logged failure is exactly that.
        """

        audit = self.FailingAudit()
        service = self.service(audit=audit)

        with pytest.raises(RuntimeError) as caught:
            await self._create(service)

        assert str(caught.value) == self.FailingAudit.MESSAGE

    @pytest.mark.asyncio
    async def test_a_caller_cannot_choose_the_mode_it_is_audited_under(self) -> None:
        """Every request contract refuses the field outright.

        The model reaches these operations through the same request contracts an
        app does, so refusing the field here is what stops either of them
        nominating the mode their own operation is recorded under.
        """

        base = {
            "run_id": SCOPE.run_id,
            "kind": "document",
            "title": "note",
            "media_type": "text/markdown",
            "idempotency_key": "forge-1",
        }
        for payload, contract in (
            (base, ArtifactCreateRequest),
            ({**base, "source_ref": "message://msg_1"}, ArtifactPromotionRequest),
            (
                {
                    "artifact_id": "art_00000000-0000-4000-8000-000000000001",
                    "parent_revision": 1,
                    "idempotency_key": "forge-2",
                },
                ArtifactRevisionRequest,
            ),
        ):
            contract.model_validate(payload)
            with pytest.raises(ValidationError) as caught:
                contract.model_validate({**payload, "execution_mode": "auto"})
            assert "execution_mode" in str(caught.value)


class ArtifactWriteEntryPoints(ArtifactServiceFakes):
    """Every public write on the service, paired with the operation it records.

    ``_create_in_scope`` funnels six of these today, so they cannot drift apart
    by accident. But "they happen to share a private helper" is not the promise
    the seam makes — the promise is that no write reaches the store without
    stating the mode it ran under. The table below asserts that pairing per
    entry point, so a write that stops recording, or one that records the wrong
    operation, fails here rather than being found later by an auditor holding a
    log with a hole in it.
    """

    #: Server-derived from the bound draft scope, the way the internal
    #: ``/drafts`` adapter derives one. Never an HTTP or model argument.
    SEED_ID = "art_00000000-0000-4000-8000-0000000000d1"
    BODY = b"# Hello\n"

    #: Public methods that only read, and so record nothing: an execution mode
    #: answers "was this write gated?", which a read never performed.
    READS = frozenset(
        {
            "get_metadata",
            "get_metadata_for_org",
            "get_revision_metadata",
            "list_for_run",
            "stream_revision",
        }
    )
    #: Public methods that are neither a read nor a write: composition wiring
    #: and one pure digest helper.
    NON_OPERATIONS = frozenset({"bind_ledger_publisher", "digest_bytes"})

    #: One entry per public write: the service method, and the operation that
    #: write has to be audited as. Invoked through ``invoke_<method>`` below so
    #: this stays a table of facts rather than a table of closures.
    WRITES = (
        ("create_from_stream", ArtifactOperation.CREATE),
        ("create_from_bytes", ArtifactOperation.CREATE),
        ("publish_from_bytes", ArtifactOperation.PUBLISH),
        ("publish_from_stream", ArtifactOperation.PUBLISH),
        ("publish_from_source", ArtifactOperation.PUBLISH),
        ("create_draft_from_bytes", ArtifactOperation.PUBLISH),
        ("append_revision_from_stream", ArtifactOperation.REVISE),
        ("promote_source", ArtifactOperation.PROMOTE),
        ("soft_delete", ArtifactOperation.DELETE),
    )

    @classmethod
    def create_request(cls, key: str) -> ArtifactCreateRequest:
        return ArtifactCreateRequest(
            run_id=SCOPE.run_id,
            kind=ArtifactKind.DOCUMENT,
            title="README",
            media_type="text/markdown",
            idempotency_key=key,
        )

    @classmethod
    def model_provenance(cls) -> ArtifactProvenance:
        return ArtifactProvenance(author=ArtifactAuthor.MODEL)

    @classmethod
    async def seed(cls, service: ArtifactService) -> str:
        """Create the artifact the revise and delete entry points act on."""

        created = await service.create_draft_from_bytes(
            org_id=SCOPE.org_id,
            user_id=SCOPE.user_id,
            request=cls.create_request("seed"),
            provenance=cls.model_provenance(),
            content=b"v1",
            artifact_id=cls.SEED_ID,
        )
        return created.record.artifact.artifact_id

    # Each invoker takes the seeded artifact id so every entry point is driven
    # through one uniform signature; the ones that create ignore it.

    @classmethod
    async def invoke_create_from_stream(
        cls, service: ArtifactService, artifact_id: str
    ) -> None:
        await service.create_from_stream(
            org_id=SCOPE.org_id,
            user_id=SCOPE.user_id,
            request=cls.create_request("create-stream"),
            provenance=cls.model_provenance(),
            chunks=cls.chunks(cls.BODY),
        )

    @classmethod
    async def invoke_create_from_bytes(
        cls, service: ArtifactService, artifact_id: str
    ) -> None:
        await service.create_from_bytes(
            org_id=SCOPE.org_id,
            user_id=SCOPE.user_id,
            request=cls.create_request("create-bytes"),
            provenance=cls.model_provenance(),
            content=cls.BODY,
        )

    @classmethod
    async def invoke_publish_from_bytes(
        cls, service: ArtifactService, artifact_id: str
    ) -> None:
        await service.publish_from_bytes(
            org_id=SCOPE.org_id,
            user_id=SCOPE.user_id,
            request=cls.create_request("publish-bytes"),
            provenance=cls.model_provenance(),
            content=cls.BODY,
        )

    @classmethod
    async def invoke_publish_from_stream(
        cls, service: ArtifactService, artifact_id: str
    ) -> None:
        await service.publish_from_stream(
            org_id=SCOPE.org_id,
            user_id=SCOPE.user_id,
            request=cls.create_request("publish-stream"),
            provenance=cls.model_provenance(),
            chunks=cls.chunks(cls.BODY),
        )

    @classmethod
    async def invoke_publish_from_source(
        cls, service: ArtifactService, artifact_id: str
    ) -> None:
        await service.publish_from_source(
            org_id=SCOPE.org_id,
            user_id=SCOPE.user_id,
            request=cls.create_request("publish-source"),
            provenance=cls.model_provenance(),
            source_ref="message://msg_1",
        )

    @classmethod
    async def invoke_create_draft_from_bytes(
        cls, service: ArtifactService, artifact_id: str
    ) -> None:
        await service.create_draft_from_bytes(
            org_id=SCOPE.org_id,
            user_id=SCOPE.user_id,
            request=cls.create_request("draft-bytes"),
            provenance=cls.model_provenance(),
            content=cls.BODY,
            artifact_id=cls.SEED_ID,
        )

    @classmethod
    async def invoke_append_revision_from_stream(
        cls, service: ArtifactService, artifact_id: str
    ) -> None:
        await service.append_revision_from_stream(
            org_id=SCOPE.org_id,
            user_id=SCOPE.user_id,
            request=ArtifactRevisionRequest(
                artifact_id=artifact_id,
                parent_revision=1,
                idempotency_key="revise-entry",
            ),
            provenance=cls.model_provenance(),
            chunks=cls.chunks(b"v2"),
        )

    @classmethod
    async def invoke_promote_source(
        cls, service: ArtifactService, artifact_id: str
    ) -> None:
        await service.promote_source(
            org_id=SCOPE.org_id,
            user_id=SCOPE.user_id,
            request=ArtifactPromotionRequest(
                run_id=SCOPE.run_id,
                source_ref="message://msg_1",
                kind=ArtifactKind.DOCUMENT,
                idempotency_key="promote-entry",
            ),
        )

    @classmethod
    async def invoke_soft_delete(
        cls, service: ArtifactService, artifact_id: str
    ) -> None:
        await service.soft_delete(
            org_id=SCOPE.org_id,
            user_id=SCOPE.user_id,
            artifact_id=artifact_id,
            idempotency_key="delete-entry",
        )


class TestEveryWriteRecordsItsMode(ArtifactWriteEntryPoints):
    """PRD-03 D2 — the seam has to cover the whole write surface, not a sample.

    An auto-execute mode arriving on a write nobody checked would be exactly
    the silent loss this was built early to prevent, so coverage is asserted
    against the service's public surface rather than against a list someone
    remembered to extend.
    """

    @pytest.mark.parametrize(("method", "operation"), ArtifactWriteEntryPoints.WRITES)
    @pytest.mark.asyncio
    async def test_a_public_write_records_one_staged_operation(
        self, method: str, operation: ArtifactOperation
    ) -> None:
        audit = self.Audit()
        service = self.service(audit=audit, sources=self.Sources(self.BODY))
        artifact_id = await self.seed(service)
        # The seed is a write too, and it already recorded. Dropping its row
        # keeps the assertion below about the entry point under test.
        audit.rows.clear()

        await getattr(self, f"invoke_{method}")(service, artifact_id)

        (entry,) = audit.entries()
        assert entry.operation is operation
        assert entry.execution_mode is ArtifactExecutionMode.STAGED
        assert entry.event_type == f"artifact.{operation.value}"
        assert entry.org_id == SCOPE.org_id
        assert entry.user_id == SCOPE.user_id

    def test_the_table_accounts_for_every_public_method_on_the_service(self) -> None:
        """A write added later cannot quietly skip the table.

        Reflective on purpose: a hand-maintained list of writes would go stale
        exactly when it mattered, which is the failure this test exists to make
        impossible. Adding a public method now forces classifying it as a write
        that records, a read, or neither.
        """

        public = {
            name
            for name, value in vars(ArtifactService).items()
            if not name.startswith("_") and not isinstance(value, type)
        }
        writes = {method for method, _ in self.WRITES}

        assert writes | self.READS | self.NON_OPERATIONS == public
        assert not writes & self.READS

    @pytest.mark.parametrize("operation", tuple(ArtifactOperation))
    def test_every_operation_commits_under_a_distinct_durable_route(
        self, operation: ArtifactOperation
    ) -> None:
        """The route is half of the persisted idempotency key.

        An operation missing from the map raises only when that write first
        runs for real, and two operations sharing a route would let one
        client's key collide across them.
        """

        routes = [
            ArtifactService.Routes.for_operation(member) for member in ArtifactOperation
        ]

        assert ArtifactService.Routes.for_operation(operation) in routes
        assert len(set(routes)) == len(ArtifactOperation)
