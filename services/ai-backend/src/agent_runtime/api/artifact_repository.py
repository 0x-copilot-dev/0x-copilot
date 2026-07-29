"""Runtime composition adapters for the canonical Artifact Repository.

This module adapts existing run/message/event read ports to the artifact
domain. It never constructs storage and never dereferences filesystem paths.
The runtime adapter factory owns metadata/blob construction; this layer only
combines those injected ports into ``ArtifactService``.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol, cast, runtime_checkable

from agent_runtime.api.ports import PersistencePort
from agent_runtime.artifacts import (
    ArtifactBlobStorePort,
    ArtifactMetadataStorePort,
    ArtifactNotFoundError,
    ArtifactScope,
    ArtifactService,
    ArtifactSourceDescriptor,
)
from agent_runtime.api.conversation_query_service import ConversationQueryService
from agent_runtime.artifacts.contracts import validate_artifact_source_ref
from agent_runtime.surfaces_v2.ledger_models import ArtifactCausalLane

INDEXED_ARTIFACT_SOURCE_SCHEMES = frozenset({"message", "operation", "payload"})
# Kept as an exported compatibility value for callers that rendered the former
# rollout state.  All logical source schemes accepted by the A2 contract are
# now resolved through exact, scoped source-record lookups.
UNINDEXED_ARTIFACT_SOURCE_SCHEMES = frozenset()


class _ArtifactMessageByIdPort(Protocol):
    """Exact, scoped message lookup implemented by each runtime store."""

    async def get_message_by_id(
        self,
        *,
        org_id: str,
        conversation_id: str,
        run_id: str,
        message_id: str,
    ) -> object | None: ...


class _ArtifactEventByIdPort(Protocol):
    """Exact, tenant- and run-scoped immutable event-record lookup.

    ``event_id`` is the source-record key, not a search term.  Adapters must
    resolve it through their primary/materialized event-id index and must never
    replay a run's event stream to find a candidate.
    """

    async def get_event_by_id(
        self,
        *,
        org_id: str,
        run_id: str,
        event_id: str,
    ) -> object | None: ...


@dataclass(frozen=True)
class ArtifactSourceSnapshot:
    """One immutable byte snapshot returned by an indexed source lookup."""

    source_ref: str
    content: bytes
    byte_size: int
    content_digest: str
    media_type: str
    title: str

    @classmethod
    def from_bytes(
        cls,
        *,
        source_ref: str,
        content: bytes,
        media_type: str,
        title: str,
    ) -> ArtifactSourceSnapshot:
        return cls(
            source_ref=source_ref,
            content=content,
            byte_size=len(content),
            content_digest=hashlib.sha256(content).hexdigest(),
            media_type=media_type,
            title=title,
        )


@runtime_checkable
class ArtifactSourceLookupPort(Protocol):
    """O(1) or indexed lookup for promotable source bytes."""

    async def get_message_snapshot(
        self,
        *,
        scope: ArtifactScope,
        message_id: str,
    ) -> ArtifactSourceSnapshot | None: ...

    async def get_operation_result_snapshot(
        self,
        *,
        scope: ArtifactScope,
        operation_id: str,
    ) -> ArtifactSourceSnapshot | None: ...

    async def get_payload_snapshot(
        self,
        *,
        scope: ArtifactScope,
        payload_ref: str,
    ) -> ArtifactSourceSnapshot | None: ...


class RuntimeArtifactSourceLookup:
    """Adapt exact runtime records to safe promotion source snapshots.

    Operation and payload sources intentionally share the event-id index rather
    than introducing an event-history scan:

    * ``operation://op_…/result`` names the immutable result record with
      ``event_id == operation_id``;
    * ``payload://<event-id>`` names that result record directly.

    Producers that want a result to be promotable must therefore write one
    immutable ``tool_result`` source record with a stable event id.  Ordinary
    tool events remain untouched and are not discoverable by guessed call ids,
    result text, paths, or arguments.
    """

    _RESULT_EVENT_TYPE = "tool_result"
    _OUTPUT_FIELD = "output"
    _DEFAULT_TITLE = "Operation result"

    def __init__(
        self,
        messages: _ArtifactMessageByIdPort,
        *,
        events: _ArtifactEventByIdPort | None = None,
    ) -> None:
        self._messages = messages
        # Runtime adapter factories pass one store that satisfies both narrow
        # read ports.  The optional override keeps tests/future split stores
        # explicit without a structural cast at the call site.
        self._events = (
            events if events is not None else cast(_ArtifactEventByIdPort, messages)
        )

    async def get_message_snapshot(
        self,
        *,
        scope: ArtifactScope,
        message_id: str,
    ) -> ArtifactSourceSnapshot | None:
        message = await self._messages.get_message_by_id(
            org_id=scope.org_id,
            conversation_id=scope.conversation_id,
            run_id=scope.run_id,
            message_id=message_id,
        )
        if message is None:
            return None
        content = getattr(message, "content_text", None)
        if not isinstance(content, str) or not content:
            return None
        content_format = getattr(message, "content_format", "")
        media_type = (
            "text/markdown; charset=utf-8"
            if content_format in {"markdown", "md"}
            else "text/plain; charset=utf-8"
        )
        return ArtifactSourceSnapshot.from_bytes(
            source_ref=f"message://{message_id}",
            content=content.encode("utf-8"),
            media_type=media_type,
            title="Conversation message",
        )

    async def get_operation_result_snapshot(
        self,
        *,
        scope: ArtifactScope,
        operation_id: str,
    ) -> ArtifactSourceSnapshot | None:
        event = await self._get_result_event(scope=scope, event_id=operation_id)
        if event is None:
            return None
        return self._snapshot_from_result_event(
            event=event,
            source_ref=f"operation://{operation_id}/result",
            title=self._DEFAULT_TITLE,
        )

    async def get_payload_snapshot(
        self,
        *,
        scope: ArtifactScope,
        payload_ref: str,
    ) -> ArtifactSourceSnapshot | None:
        event_id = payload_ref.removeprefix("payload://")
        if not event_id:
            return None
        event = await self._get_result_event(scope=scope, event_id=event_id)
        if event is None:
            return None
        return self._snapshot_from_result_event(
            event=event,
            source_ref=payload_ref,
            title="Result payload",
        )

    async def _get_result_event(
        self,
        *,
        scope: ArtifactScope,
        event_id: str,
    ) -> object | None:
        event = await self._events.get_event_by_id(
            org_id=scope.org_id,
            run_id=scope.run_id,
            event_id=event_id,
        )
        if event is None:
            return None
        event_type = getattr(event, "event_type", None)
        event_type_value = getattr(event_type, "value", event_type)
        if event_type_value != self._RESULT_EVENT_TYPE:
            return None
        return event

    @classmethod
    def _snapshot_from_result_event(
        cls,
        *,
        event: object,
        source_ref: str,
        title: str,
    ) -> ArtifactSourceSnapshot | None:
        """Copy only a stored result value into an artifact byte snapshot.

        Result-source records must carry their content in the projected
        ``tool_result.payload.output`` field.  We deliberately do not inspect
        event metadata (which can include execution diagnostics), tool-call
        arguments, previews, offload paths, or any other fields.
        """

        payload = getattr(event, "payload", None)
        if not isinstance(payload, dict):
            return None
        output = payload.get(cls._OUTPUT_FIELD)
        if isinstance(output, str):
            content = output.encode("utf-8")
            media_type = "text/plain; charset=utf-8"
        elif isinstance(output, (dict, list)):
            try:
                content = json.dumps(
                    output,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode("utf-8")
            except (TypeError, ValueError):
                return None
            media_type = "application/json"
        else:
            return None
        return ArtifactSourceSnapshot.from_bytes(
            source_ref=source_ref,
            content=content,
            media_type=media_type,
            title=title,
        )


class RuntimeArtifactRunScopeResolver:
    """Resolve a causal subject only when both tenant and owner match."""

    def __init__(self, persistence: PersistencePort) -> None:
        self._persistence = persistence

    async def resolve_conversation(
        self,
        *,
        org_id: str,
        user_id: str,
        conversation_id: str,
    ) -> ArtifactScope | None:
        """Resolve CONVERSATION-lane scope for a user-authored mutation.

        Ownership is proved by the same tenant-and-owner filtered lookup the
        conversation surface uses; a conversation outside the caller's scope
        returns ``None`` and becomes the same not-found every other artifact
        scope failure produces, so this cannot confirm another tenant's data.

        The returned scope names no run: a user edit is not caused by any run,
        so there is no ledger for a terminal event to seal.
        """

        conversation = await self._persistence.get_conversation(
            org_id=org_id,
            user_id=user_id,
            conversation_id=conversation_id,
        )
        if conversation is None:
            return None
        return ArtifactScope(
            org_id=conversation.org_id,
            user_id=conversation.user_id,
            conversation_id=conversation.conversation_id,
            run_id=None,
            # A conversation carries no trace of its own, and this lane emits no
            # traced event (PRD-01 D3), so the conversation id is the honest
            # stable correlation value rather than a fabricated span.
            trace_id=conversation.conversation_id,
            lane=ArtifactCausalLane.CONVERSATION,
        )

    async def resolve_run(
        self,
        *,
        org_id: str,
        user_id: str,
        run_id: str,
    ) -> ArtifactScope | None:
        run = await self._persistence.get_run(org_id=org_id, run_id=run_id)
        if run is None or run.user_id != user_id:
            return None
        return ArtifactScope(
            org_id=run.org_id,
            user_id=run.user_id,
            conversation_id=run.conversation_id,
            run_id=run.run_id,
            trace_id=run.trace_id,
            run_is_terminal=run.status
            in ConversationQueryService.TERMINAL_RUN_STATUSES,
        )


class RuntimeArtifactSourceResolver:
    """Resolve supported logical sources without scanning run history.

    Every accepted logical scheme resolves only through an exact, scoped source
    record lookup.  The resolver never replays a run, searches message/event
    text, reads server-local paths, or accepts client-supplied bytes.
    """

    CHUNK_BYTES = 64 * 1024

    def __init__(self, lookup: ArtifactSourceLookupPort) -> None:
        self._lookup = lookup

    async def resolve_source(
        self,
        *,
        scope: ArtifactScope,
        source_ref: str,
    ) -> ArtifactSourceDescriptor | None:
        snapshot = await self._resolve(scope=scope, source_ref=source_ref)
        if snapshot is None:
            return None
        return ArtifactSourceDescriptor(
            source_ref=snapshot.source_ref,
            byte_size=snapshot.byte_size,
            content_digest=snapshot.content_digest,
            media_type=snapshot.media_type,
            title=snapshot.title,
        )

    async def open_source(
        self,
        *,
        scope: ArtifactScope,
        source: ArtifactSourceDescriptor,
    ) -> AsyncIterator[bytes]:
        snapshot = await self._resolve(scope=scope, source_ref=source.source_ref)
        if snapshot is None:
            raise ArtifactNotFoundError()
        return self._stream_content(snapshot.content)

    async def _resolve(
        self,
        *,
        scope: ArtifactScope,
        source_ref: str,
    ) -> ArtifactSourceSnapshot | None:
        try:
            canonical = validate_artifact_source_ref(source_ref)
        except ValueError:
            return None
        if canonical.startswith("message://"):
            return await self._lookup.get_message_snapshot(
                scope=scope,
                message_id=canonical.removeprefix("message://"),
            )
        if canonical.startswith("operation://"):
            operation_id = canonical.removeprefix("operation://").removesuffix(
                "/result"
            )
            return await self._lookup.get_operation_result_snapshot(
                scope=scope,
                operation_id=operation_id,
            )
        if canonical.startswith("payload://"):
            return await self._lookup.get_payload_snapshot(
                scope=scope,
                payload_ref=canonical,
            )
        return None

    @classmethod
    async def _stream_content(cls, content: bytes) -> AsyncIterator[bytes]:
        for offset in range(0, len(content), cls.CHUNK_BYTES):
            yield content[offset : offset + cls.CHUNK_BYTES]


class ArtifactServiceComposition:
    """Build the domain service from a storage-owned runtime port bundle."""

    @classmethod
    def build(cls, ports: object) -> ArtifactService | None:
        metadata = cast(
            ArtifactMetadataStorePort | None,
            getattr(ports, "artifact_metadata_store", None),
        )
        blobs = cast(
            ArtifactBlobStorePort | None,
            getattr(ports, "artifact_blob_store", None),
        )
        persistence = cast(
            PersistencePort | None,
            getattr(ports, "persistence", None),
        )
        source_lookup = cast(
            ArtifactSourceLookupPort | None,
            getattr(ports, "artifact_source_lookup", None),
        )
        if (
            metadata is None
            or blobs is None
            or persistence is None
            or source_lookup is None
        ):
            return None
        return ArtifactService(
            metadata=metadata,
            blobs=blobs,
            run_scopes=RuntimeArtifactRunScopeResolver(persistence),
            sources=RuntimeArtifactSourceResolver(source_lookup),
        )


__all__ = (
    "ArtifactServiceComposition",
    "ArtifactSourceLookupPort",
    "ArtifactSourceSnapshot",
    "INDEXED_ARTIFACT_SOURCE_SCHEMES",
    "RuntimeArtifactRunScopeResolver",
    "RuntimeArtifactSourceLookup",
    "RuntimeArtifactSourceResolver",
    "UNINDEXED_ARTIFACT_SOURCE_SCHEMES",
)
