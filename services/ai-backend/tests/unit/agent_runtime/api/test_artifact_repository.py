"""Tests for runtime Artifact Repository scope/source composition."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from agent_runtime.api.artifact_repository import (
    ArtifactServiceComposition,
    ArtifactSourceSnapshot,
    INDEXED_ARTIFACT_SOURCE_SCHEMES,
    RuntimeArtifactRunScopeResolver,
    RuntimeArtifactSourceLookup,
    RuntimeArtifactSourceResolver,
    UNINDEXED_ARTIFACT_SOURCE_SCHEMES,
)
from agent_runtime.artifacts import (
    ArtifactDigestMismatchError,
    ArtifactPromotionRequest,
    ArtifactScope,
    ArtifactService,
)
from agent_runtime.surfaces_v2.ledger_models import ArtifactKind


@dataclass(frozen=True)
class _Run:
    org_id: str = "org_artifacts"
    user_id: str = "user_artifacts"
    conversation_id: str = "conv_artifacts"
    run_id: str = "run_artifacts"
    trace_id: str = "trace_artifacts"


@dataclass(frozen=True)
class _Message:
    message_id: str
    run_id: str
    content_text: str
    content_format: str = "markdown"


class _Persistence:
    def __init__(self) -> None:
        self.run: _Run | None = _Run()
        self.run_calls: list[tuple[str, str]] = []

    async def get_run(self, *, org_id: str, run_id: str):
        self.run_calls.append((org_id, run_id))
        if self.run is None or self.run.org_id != org_id or self.run.run_id != run_id:
            return None
        return self.run


class _MessageStore:
    def __init__(self) -> None:
        self.message: _Message | None = None
        self.calls: list[dict[str, str]] = []

    async def get_message_by_id(self, **kwargs):
        self.calls.append(kwargs)
        message = self.message
        if (
            message is None
            or message.message_id != kwargs["message_id"]
            or message.run_id != kwargs["run_id"]
        ):
            return None
        return message


class _Lookup:
    def __init__(self, *snapshots: ArtifactSourceSnapshot | None) -> None:
        self._snapshots = list(snapshots)
        self.calls = 0

    async def get_message_snapshot(self, **kwargs):
        self.calls += 1
        if not self._snapshots:
            return None
        return self._snapshots.pop(0)


class ArtifactResolverMixin:
    SCOPE = ArtifactScope(
        org_id="org_artifacts",
        user_id="user_artifacts",
        conversation_id="conv_artifacts",
        run_id="run_artifacts",
        trace_id="trace_artifacts",
    )
    OPERATION_ID = "op_123e4567-e89b-42d3-a456-426614174000"

    @staticmethod
    async def read(chunks) -> bytes:
        return b"".join([chunk async for chunk in chunks])


class TestRuntimeArtifactRunScopeResolver(ArtifactResolverMixin):
    @pytest.mark.asyncio
    async def test_requires_both_org_and_user(self) -> None:
        persistence = _Persistence()
        resolver = RuntimeArtifactRunScopeResolver(persistence)  # type: ignore[arg-type]

        allowed = await resolver.resolve_run(
            org_id=self.SCOPE.org_id,
            user_id=self.SCOPE.user_id,
            run_id=self.SCOPE.run_id,
        )
        foreign_user = await resolver.resolve_run(
            org_id=self.SCOPE.org_id,
            user_id="user_foreign",
            run_id=self.SCOPE.run_id,
        )
        missing = await resolver.resolve_run(
            org_id=self.SCOPE.org_id,
            user_id=self.SCOPE.user_id,
            run_id="run_missing",
        )

        assert allowed == self.SCOPE
        assert foreign_user is None
        assert missing is None


class TestRuntimeArtifactSourceResolver(ArtifactResolverMixin):
    def test_indexed_source_contract_exemption_is_explicit_and_closed(self) -> None:
        assert INDEXED_ARTIFACT_SOURCE_SCHEMES == frozenset({"message"})
        assert UNINDEXED_ARTIFACT_SOURCE_SCHEMES == frozenset({"operation", "payload"})
        assert INDEXED_ARTIFACT_SOURCE_SCHEMES.isdisjoint(
            UNINDEXED_ARTIFACT_SOURCE_SCHEMES
        )

    @pytest.mark.asyncio
    async def test_rejects_physical_source_before_any_lookup(self) -> None:
        lookup = _Lookup()
        resolved = await RuntimeArtifactSourceResolver(lookup).resolve_source(
            scope=self.SCOPE,
            source_ref="file:///private/tenant/secret.txt",
        )
        assert resolved is None
        assert lookup.calls == 0

    @pytest.mark.asyncio
    async def test_message_uses_one_exact_scoped_lookup(self) -> None:
        store = _MessageStore()
        store.message = _Message(
            message_id="msg_target",
            run_id=self.SCOPE.run_id,
            content_text="# Scoped notes",
        )
        resolver = RuntimeArtifactSourceResolver(RuntimeArtifactSourceLookup(store))

        descriptor = await resolver.resolve_source(
            scope=self.SCOPE,
            source_ref="message://msg_target",
        )
        assert descriptor is not None
        assert descriptor.byte_size == len(b"# Scoped notes")
        assert (
            descriptor.content_digest == hashlib.sha256(b"# Scoped notes").hexdigest()
        )
        assert descriptor.media_type == "text/markdown; charset=utf-8"
        chunks = await resolver.open_source(scope=self.SCOPE, source=descriptor)
        assert await self.read(chunks) == b"# Scoped notes"
        assert store.calls == [
            {
                "org_id": self.SCOPE.org_id,
                "conversation_id": self.SCOPE.conversation_id,
                "run_id": self.SCOPE.run_id,
                "message_id": "msg_target",
            },
            {
                "org_id": self.SCOPE.org_id,
                "conversation_id": self.SCOPE.conversation_id,
                "run_id": self.SCOPE.run_id,
                "message_id": "msg_target",
            },
        ]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "source_ref",
        (
            "payload://call/result",
            f"operation://{ArtifactResolverMixin.OPERATION_ID}/result",
        ),
    )
    async def test_unindexed_schemes_are_honest_not_found_without_scan(
        self,
        source_ref: str,
    ) -> None:
        lookup = _Lookup()
        resolved = await RuntimeArtifactSourceResolver(lookup).resolve_source(
            scope=self.SCOPE,
            source_ref=source_ref,
        )
        assert resolved is None
        assert lookup.calls == 0

    @pytest.mark.asyncio
    async def test_changed_source_fails_digest_before_metadata_creation(self) -> None:
        old = ArtifactSourceSnapshot.from_bytes(
            source_ref="message://msg_target",
            content=b"approved snapshot",
            media_type="text/plain; charset=utf-8",
            title="Message",
        )
        changed = ArtifactSourceSnapshot.from_bytes(
            source_ref="message://msg_target",
            content=b"changed after resolve",
            media_type="text/plain; charset=utf-8",
            title="Message",
        )
        metadata = _NeverMetadata()
        service = ArtifactService(
            metadata=metadata,  # type: ignore[arg-type]
            blobs=_DigestCheckingBlob(),  # type: ignore[arg-type]
            run_scopes=RuntimeArtifactRunScopeResolver(_Persistence()),  # type: ignore[arg-type]
            sources=RuntimeArtifactSourceResolver(_Lookup(old, changed)),
        )

        with pytest.raises(ArtifactDigestMismatchError):
            await service.promote_source(
                org_id=self.SCOPE.org_id,
                user_id=self.SCOPE.user_id,
                request=ArtifactPromotionRequest(
                    run_id=self.SCOPE.run_id,
                    source_ref="message://msg_target",
                    kind=ArtifactKind.FILE,
                    idempotency_key="promote-race",
                ),
            )
        assert metadata.create_calls == 0


class _NeverMetadata:
    def __init__(self) -> None:
        self.create_calls = 0

    async def create_artifact(self, command):
        self.create_calls += 1
        raise AssertionError("metadata must not be created after digest mismatch")


class _DigestCheckingBlob:
    async def put_stream(self, *, expected_digest, chunks, byte_limit):
        body = b"".join([chunk async for chunk in chunks])
        if hashlib.sha256(body).hexdigest() != expected_digest:
            raise ArtifactDigestMismatchError()
        raise AssertionError("the changed snapshot must not pass its expected digest")


class TestArtifactServiceComposition:
    def test_requires_storage_owned_repository_ports(self) -> None:
        persistence = _Persistence()
        incomplete = SimpleNamespace(
            persistence=persistence,
        )
        assert ArtifactServiceComposition.build(incomplete) is None

        complete = SimpleNamespace(
            persistence=persistence,
            artifact_metadata_store=object(),
            artifact_blob_store=object(),
            artifact_source_lookup=_Lookup(),
        )
        service = ArtifactServiceComposition.build(complete)
        assert isinstance(service, ArtifactService)
