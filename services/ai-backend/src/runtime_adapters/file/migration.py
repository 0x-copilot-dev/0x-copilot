"""Offline Postgres/in-memory -> file-store migration for the desktop profile.

This is the ``AC2`` "Migration from the legacy desktop AI-runtime store" step
(PRD ``docs/plan/desktop/agent-capabilities/02-ac2-file-session-store.md`` §702).
It moves every conversation and its full record set out of a *source* runtime
store and into a *destination* file store **before** the operator flips the
``file`` backend on. It does not change any default: the file store stays opt-in
behind ``COPILOT_DESKTOP_FILE_STORE_V1`` / ``RUNTIME_STORE_BACKEND=file``.

Design invariants:

* **Source-adapter-agnostic.** The source is read *only* through the shared
  runtime store port (``list_conversations`` / ``list_messages`` / ``get_run`` /
  ``list_events_after``). Because ``InMemoryRuntimeApiStore`` and
  ``PostgresRuntimeApiStore`` implement byte-identical port surfaces, an
  in-memory source and a Postgres source drive the *same* migration code path —
  the unit tests use in-memory as the source for exactly this reason.
* **Verbatim, id-preserving writes.** Records are written into the destination
  through the file store's own single-record write path (``_persist_conversation``
  / ``_persist_message`` / ``_persist_run`` / ``_persist_event`` = append, plus
  ``object_store.put`` = object-put, plus a final view reload + ``_rebuild_index``
  = index-rebuild). Every id, ``sequence_no``, timestamp, payload, and object
  content-address is preserved exactly — nothing is re-keyed (unlike
  ``export_import``, which mints fresh ids for a portable copy).
* **Idempotent.** A conversation whose canonical ``conversation.json`` already
  exists in the destination is skipped, so re-running is a safe no-op. A partial
  session directory left by an interrupted prior run (streams written but no
  metadata) is erased and re-migrated, so stable ids keep re-runs duplicate-free.
* **Dry-run.** Reports exactly what would migrate and writes nothing.
* **Verifiable.** A post-migration verify pass re-reads both stores through the
  port and asserts per-conversation record counts + content equality (including
  every referenced object blob's bytes). Any mismatch is reported and raised
  loudly; a clean report is the only signal that authorises the backend flip.

Objects: the offload seam (``FileOffloadWriter``) is wired only for the file
store, so a Postgres/in-memory source carries tool payloads inline and has no
separate object blobs — the referenced-digest set is empty and nothing is
copied. When the *source itself* is a file store (backout / re-forward), its
``/large_tool_results/<sha256>`` blobs are copied byte-for-byte into the
destination object store, where content-addressing keeps the digest identical.

CLI: ``python -m runtime_adapters.migrate`` (see ``runtime_adapters/migrate.py``).
"""

from __future__ import annotations

import re
import shutil
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from runtime_api.schemas import (
    ConversationRecord,
    MessageRecord,
    RunRecord,
    RuntimeEventEnvelope,
)

if TYPE_CHECKING:  # pragma: no cover - typing only, avoids an import cycle
    from runtime_adapters.file.runtime_api_store import FileRuntimeApiStore

# Read every conversation / message / run in one shot. The port exposes a
# ``limit`` but no cursor; the single_user_desktop store is small, so a high
# ceiling reads the whole scope. Saturation raises rather than silently
# truncating (see ``StoreMigrator._list_all``).
_READ_LIMIT = 1_000_000

# sha256 object digests render verbatim as 64 lowercase hex chars in the
# canonical JSONL (both object paths and ``/large_tool_results/<sha>`` refs).
# This mirrors ``ObjectReachabilityScanner._SHA256`` — over-collection is safe
# because a token is only treated as an object when the source actually holds
# that blob.
_SHA256_TOKEN = re.compile(r"[0-9a-f]{64}")


class MigrationError(RuntimeError):
    """A migration could not proceed (bad scope resolution, saturated read)."""


class MigrationVerificationError(MigrationError):
    """The post-migration verify pass found a source/destination mismatch."""

    def __init__(self, mismatches: Sequence[str]) -> None:
        self.mismatches = tuple(mismatches)
        joined = "; ".join(self.mismatches[:10])
        suffix = (
            ""
            if len(self.mismatches) <= 10
            else f" (+{len(self.mismatches) - 10} more)"
        )
        super().__init__(f"file-store migration verification failed: {joined}{suffix}")


@runtime_checkable
class ScopeDiscoverySource(Protocol):
    """A source that can enumerate its own ``(org_id, user_id)`` tenant scopes.

    The Postgres adapter satisfies this via ``list_conversation_scopes`` (a
    ``SELECT DISTINCT org_id, user_id FROM agent_conversations``), which is how a
    Postgres source migrates *every* tenant without hand-passed
    ``--org-id``/``--user-id`` scopes. In-memory / file stores instead expose
    their conversations as a ``.conversations`` mapping, so they auto-discover
    without this method (see :meth:`StoreMigrator._resolve_scopes`).

    An empty result is legitimate — a brand-new install with no history — and
    yields a clean no-op migration, never an error. This makes the desktop
    first-file-boot import safe to run unconditionally.
    """

    async def list_conversation_scopes(self) -> Sequence[tuple[str, str]]: ...


@runtime_checkable
class MigrationSourcePort(Protocol):
    """The exact slice of the runtime store port the migration reads from.

    ``InMemoryRuntimeApiStore``, ``PostgresRuntimeApiStore``, and
    ``FileRuntimeApiStore`` all satisfy this structurally, so any of them can be
    a migration source with no adapter-specific code.
    """

    async def list_conversations(
        self,
        *,
        org_id: str,
        user_id: str,
        limit: int,
        include_archived: bool = ...,
        include_deleted: bool = ...,
    ) -> Sequence[ConversationRecord]: ...

    async def get_conversation(
        self, *, org_id: str, user_id: str, conversation_id: str
    ) -> ConversationRecord | None: ...

    async def list_messages(
        self,
        *,
        org_id: str,
        conversation_id: str,
        limit: int,
        include_deleted: bool = ...,
    ) -> Sequence[MessageRecord]: ...

    async def get_run(self, *, org_id: str, run_id: str) -> RunRecord | None: ...

    async def list_events_after(
        self,
        *,
        org_id: str,
        run_id: str,
        after_sequence: int,
    ) -> Sequence[RuntimeEventEnvelope]: ...


class MigrationScope(BaseModel):
    """One (org, user) tenant slice to migrate.

    The single_user_desktop profile has exactly one scope; the field is a tuple
    so the CLI / callers can migrate several explicitly when needed.
    """

    model_config = ConfigDict(frozen=True)

    org_id: str
    user_id: str


MIGRATED = "migrated"
SKIPPED = "skipped"
PLANNED = "planned"


class ConversationOutcome(BaseModel):
    """Per-conversation result line carried in the report."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    org_id: str
    user_id: str
    conversation_id: str
    status: str
    messages: int = Field(ge=0, default=0)
    runs: int = Field(ge=0, default=0)
    events: int = Field(ge=0, default=0)
    objects: int = Field(ge=0, default=0)
    bytes: int = Field(ge=0, default=0)


class MigrationReport(BaseModel):
    """Progress + summary for one migration (or dry-run / verify) invocation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    dry_run: bool
    scopes: tuple[MigrationScope, ...]
    conversations_total: int = Field(ge=0, default=0)
    conversations_migrated: int = Field(ge=0, default=0)
    conversations_skipped: int = Field(ge=0, default=0)
    messages: int = Field(ge=0, default=0)
    runs: int = Field(ge=0, default=0)
    events: int = Field(ge=0, default=0)
    objects: int = Field(ge=0, default=0)
    bytes: int = Field(ge=0, default=0)
    verified: bool | None = None
    mismatches: tuple[str, ...] = ()
    outcomes: tuple[ConversationOutcome, ...] = ()

    def summary_line(self) -> str:
        """One-line human summary for CLI / logs."""

        mode = "DRY-RUN" if self.dry_run else "MIGRATE"
        verify = (
            ""
            if self.verified is None
            else (" verified=OK" if self.verified else " verified=FAILED")
        )
        return (
            f"[{mode}] conversations={self.conversations_total} "
            f"migrated={self.conversations_migrated} "
            f"skipped={self.conversations_skipped} messages={self.messages} "
            f"runs={self.runs} events={self.events} objects={self.objects} "
            f"bytes={self.bytes}{verify}"
        )


class _ConversationBundle(BaseModel):
    """A conversation's full record set read through the port (in memory)."""

    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    conversation: ConversationRecord
    messages: tuple[MessageRecord, ...]
    runs: tuple[RunRecord, ...]
    # run_id -> events ordered by sequence_no (main + subagent events mixed).
    events_by_run: dict[str, tuple[RuntimeEventEnvelope, ...]]
    object_digests: frozenset[str]

    @property
    def event_count(self) -> int:
        return sum(len(events) for events in self.events_by_run.values())


class StoreMigrator:
    """Move conversations from a port-compatible source into a file store.

    The destination is always a concrete :class:`FileRuntimeApiStore` (the
    migration writes through its private single-record append path); the source
    is anything satisfying :class:`MigrationSourcePort`. Both stores must already
    be ``open()``-ed by the caller.
    """

    def __init__(
        self,
        *,
        source: MigrationSourcePort,
        dest: FileRuntimeApiStore,
        progress: Callable[[str], None] | None = None,
    ) -> None:
        self._source = source
        self._dest = dest
        self._progress = progress

    # ==================================================================
    # Public API
    # ==================================================================

    async def migrate(
        self,
        *,
        scopes: Iterable[MigrationScope] | None = None,
        dry_run: bool = False,
        verify: bool = False,
    ) -> MigrationReport:
        """Migrate every conversation for ``scopes`` (or all discoverable).

        ``dry_run`` reports what would migrate and writes nothing. ``verify``
        runs the post-migration equality pass and raises
        :class:`MigrationVerificationError` on any mismatch (ignored under
        ``dry_run`` — nothing was written to verify).
        """

        resolved = await self._resolve_scopes(scopes)
        outcomes: list[ConversationOutcome] = []
        written_any = False

        for scope in resolved:
            conversations = await self._list_all(
                self._source.list_conversations,
                org_id=scope.org_id,
                user_id=scope.user_id,
            )
            for conversation in conversations:
                outcome, wrote = await self._process_conversation(
                    scope, conversation, dry_run=dry_run
                )
                outcomes.append(outcome)
                written_any = written_any or wrote

        if written_any and not dry_run:
            # index-rebuild: repopulate the materialized view from the canonical
            # JSONL and rebuild the disposable catalog so the destination serves
            # exactly what was written (and the verify pass can read it back).
            self._reload_destination()

        mismatches: tuple[str, ...] = ()
        verified: bool | None = None
        if verify and not dry_run:
            mismatches = tuple(await self._verify_scopes(resolved))
            verified = not mismatches

        report = self._build_report(
            resolved,
            outcomes,
            dry_run=dry_run,
            verified=verified,
            mismatches=mismatches,
        )
        self._emit(report.summary_line())
        if verified is False:
            raise MigrationVerificationError(mismatches)
        return report

    async def verify(
        self, *, scopes: Iterable[MigrationScope] | None = None
    ) -> MigrationReport:
        """Run only the equality pass against an already-migrated destination."""

        resolved = await self._resolve_scopes(scopes)
        mismatches = tuple(await self._verify_scopes(resolved))
        report = self._build_report(
            resolved, [], dry_run=False, verified=not mismatches, mismatches=mismatches
        )
        self._emit(report.summary_line())
        if mismatches:
            raise MigrationVerificationError(mismatches)
        return report

    # ==================================================================
    # Scope resolution
    # ==================================================================

    async def _resolve_scopes(
        self, scopes: Iterable[MigrationScope] | None
    ) -> tuple[MigrationScope, ...]:
        if scopes is not None:
            resolved = tuple(dict.fromkeys(scopes))
            if not resolved:
                raise MigrationError("no migration scopes were provided")
            return resolved
        # Auto-discovery. A source that can enumerate its own tenant scopes (the
        # Postgres adapter, via ``SELECT DISTINCT org_id, user_id FROM
        # agent_conversations``) is queried directly — this is how a Postgres
        # source migrates *every* tenant without hand-passed --org-id/--user-id.
        # An empty result is legitimate (a brand-new install with no history) and
        # yields a clean no-op migration, not an error.
        if isinstance(self._source, ScopeDiscoverySource):
            pairs = await self._source.list_conversation_scopes()
            return self._dedupe_scopes(
                MigrationScope(org_id=org, user_id=user) for org, user in pairs
            )
        # Fallback: in-memory and file stores expose a ``conversations`` mapping
        # keyed by id, so their scopes are discoverable without a query.
        conversations = getattr(self._source, "conversations", None)
        if isinstance(conversations, Mapping):
            return self._dedupe_scopes(
                MigrationScope(org_id=c.org_id, user_id=c.user_id)
                for c in conversations.values()
            )
        raise MigrationError(
            "source does not support scope auto-discovery; pass explicit "
            "(org_id, user_id) scopes (e.g. --org-id/--user-id on the CLI)"
        )

    @staticmethod
    def _dedupe_scopes(
        scopes: Iterable[MigrationScope],
    ) -> tuple[MigrationScope, ...]:
        """Deterministically ordered, de-duplicated scope tuple."""

        return tuple(sorted(set(scopes), key=lambda s: (s.org_id, s.user_id)))

    # ==================================================================
    # Per-conversation processing
    # ==================================================================

    async def _process_conversation(
        self,
        scope: MigrationScope,
        conversation: ConversationRecord,
        *,
        dry_run: bool,
    ) -> tuple[ConversationOutcome, bool]:
        cid = conversation.conversation_id
        meta_path = self._dest.layout.conversation_meta_path(scope.org_id, cid)
        if meta_path.exists():
            self._emit(f"skip {cid} (already migrated)")
            return (
                ConversationOutcome(
                    org_id=scope.org_id,
                    user_id=scope.user_id,
                    conversation_id=cid,
                    status=SKIPPED,
                ),
                False,
            )

        bundle = await self._read_bundle(self._source, scope, conversation)
        byte_size = self._bundle_bytes(bundle)

        if dry_run:
            self._emit(f"plan {cid} (would migrate)")
            return (
                self._outcome(scope, bundle, status=PLANNED, byte_size=byte_size),
                False,
            )

        self._write_bundle(scope, bundle)
        self._emit(
            f"migrate {cid} "
            f"(messages={len(bundle.messages)} runs={len(bundle.runs)} "
            f"events={bundle.event_count} objects={len(bundle.object_digests)})"
        )
        return (
            self._outcome(scope, bundle, status=MIGRATED, byte_size=byte_size),
            True,
        )

    async def _read_bundle(
        self,
        store: MigrationSourcePort,
        scope: MigrationScope,
        conversation: ConversationRecord,
    ) -> _ConversationBundle:
        """Read a conversation's full record set through the port only."""

        cid = conversation.conversation_id
        messages = tuple(
            await store.list_messages(
                org_id=scope.org_id,
                conversation_id=cid,
                limit=_READ_LIMIT,
                include_deleted=True,
            )
        )
        if len(messages) >= _READ_LIMIT:
            raise MigrationError(f"conversation {cid} exceeds the read ceiling")

        # Every run is created with a user message, and messages carry ``run_id``;
        # the ordered distinct set of message run_ids is therefore the run set.
        run_ids: list[str] = []
        seen: set[str] = set()
        for message in messages:
            rid = message.run_id
            if rid and rid not in seen:
                seen.add(rid)
                run_ids.append(rid)

        runs: list[RunRecord] = []
        events_by_run: dict[str, tuple[RuntimeEventEnvelope, ...]] = {}
        for rid in run_ids:
            run = await store.get_run(org_id=scope.org_id, run_id=rid)
            if run is None:
                continue
            runs.append(run)
            events = await store.list_events_after(
                org_id=scope.org_id, run_id=rid, after_sequence=0
            )
            events_by_run[rid] = tuple(
                sorted(events, key=lambda event: event.sequence_no)
            )

        digests = self._collect_object_digests(
            conversation, messages, tuple(runs), events_by_run
        )
        return _ConversationBundle(
            conversation=conversation,
            messages=messages,
            runs=tuple(runs),
            events_by_run=events_by_run,
            object_digests=frozenset(digests),
        )

    def _write_bundle(self, scope: MigrationScope, bundle: _ConversationBundle) -> None:
        """Persist a bundle verbatim through the file store's write path."""

        dest = self._dest
        org_id = scope.org_id
        cid = bundle.conversation.conversation_id
        conv_dir = dest.layout.conversation_dir(org_id, cid)

        # A partial directory (streams but no metadata) is the fingerprint of an
        # interrupted prior run — erase it so append-based writes stay
        # duplicate-free. Fully-migrated conversations never reach here (skipped
        # on the metadata check upstream).
        if conv_dir.exists():
            shutil.rmtree(conv_dir)

        for message in bundle.messages:
            dest._persist_message(message)
        for run in bundle.runs:
            dest._persist_run(run)
        for events in bundle.events_by_run.values():
            for envelope in events:
                dest._persist_event(envelope, org_id=org_id)

        self._copy_objects(bundle.object_digests)

        # Metadata written LAST: its presence is the "fully migrated" marker that
        # makes the skip check above correct after a crash mid-conversation.
        dest._persist_conversation(bundle.conversation)

    def _copy_objects(self, digests: frozenset[str]) -> None:
        """Copy referenced object blobs source -> destination, byte-for-byte.

        Only digests the source actually holds as objects are copied; the rest
        are incidental hex tokens (or the source has no object store at all, as
        with Postgres/in-memory). Content-addressing keeps the digest identical
        in the destination.
        """

        source_objects = self._source_object_store()
        if source_objects is None:
            return
        dest_objects = self._dest.object_store
        for digest in sorted(digests):
            if not source_objects.exists(digest):
                continue
            data = source_objects.get(digest)
            dest_objects.put(data)

    # ==================================================================
    # Verification
    # ==================================================================

    async def _verify_scopes(self, scopes: Sequence[MigrationScope]) -> list[str]:
        mismatches: list[str] = []
        source_objects = self._source_object_store()
        for scope in scopes:
            source_convs = {
                c.conversation_id: c
                for c in await self._list_all(
                    self._source.list_conversations,
                    org_id=scope.org_id,
                    user_id=scope.user_id,
                )
            }
            dest_convs = {
                c.conversation_id: c
                for c in await self._list_all(
                    self._dest.list_conversations,
                    org_id=scope.org_id,
                    user_id=scope.user_id,
                )
            }
            for cid in source_convs:
                if cid not in dest_convs:
                    mismatches.append(f"{cid}: missing in destination")
                    continue
                await self._verify_conversation(scope, cid, source_objects, mismatches)
        return mismatches

    async def _verify_conversation(
        self,
        scope: MigrationScope,
        conversation_id: str,
        source_objects: object,
        mismatches: list[str],
    ) -> None:
        source_conv = await self._source.get_conversation(
            org_id=scope.org_id, user_id=scope.user_id, conversation_id=conversation_id
        )
        dest_conv = await self._dest.get_conversation(
            org_id=scope.org_id, user_id=scope.user_id, conversation_id=conversation_id
        )
        if source_conv is None or dest_conv is None:
            mismatches.append(f"{conversation_id}: conversation not readable")
            return
        source_bundle = await self._read_bundle(self._source, scope, source_conv)
        dest_bundle = await self._read_bundle(self._dest, scope, dest_conv)

        if _dump(source_bundle.conversation) != _dump(dest_bundle.conversation):
            mismatches.append(f"{conversation_id}: conversation record differs")

        self._compare_by_id(
            conversation_id,
            "message",
            {m.message_id: m for m in source_bundle.messages},
            {m.message_id: m for m in dest_bundle.messages},
            mismatches,
        )
        self._compare_by_id(
            conversation_id,
            "run",
            {r.run_id: r for r in source_bundle.runs},
            {r.run_id: r for r in dest_bundle.runs},
            mismatches,
        )
        self._compare_events(conversation_id, source_bundle, dest_bundle, mismatches)
        self._compare_objects(
            conversation_id, source_bundle, source_objects, mismatches
        )

    def _compare_events(
        self,
        conversation_id: str,
        source_bundle: _ConversationBundle,
        dest_bundle: _ConversationBundle,
        mismatches: list[str],
    ) -> None:
        source_runs = set(source_bundle.events_by_run)
        dest_runs = set(dest_bundle.events_by_run)
        if source_runs != dest_runs:
            mismatches.append(
                f"{conversation_id}: event run set differs "
                f"(source={len(source_runs)} dest={len(dest_runs)})"
            )
        for rid in source_runs & dest_runs:
            source_events = source_bundle.events_by_run[rid]
            dest_events = dest_bundle.events_by_run[rid]
            if len(source_events) != len(dest_events):
                mismatches.append(
                    f"{conversation_id}/{rid}: event count "
                    f"{len(source_events)} != {len(dest_events)}"
                )
                continue
            for src_event, dst_event in zip(source_events, dest_events):
                if src_event.sequence_no != dst_event.sequence_no:
                    mismatches.append(
                        f"{conversation_id}/{rid}: sequence_no "
                        f"{src_event.sequence_no} != {dst_event.sequence_no}"
                    )
                elif _dump(src_event) != _dump(dst_event):
                    mismatches.append(
                        f"{conversation_id}/{rid}: event "
                        f"seq={src_event.sequence_no} differs"
                    )

    def _compare_objects(
        self,
        conversation_id: str,
        source_bundle: _ConversationBundle,
        source_objects: object,
        mismatches: list[str],
    ) -> None:
        if source_objects is None:
            return
        dest_objects = self._dest.object_store
        for digest in source_bundle.object_digests:
            if not source_objects.exists(digest):  # type: ignore[attr-defined]
                continue
            if not dest_objects.exists(digest):
                mismatches.append(f"{conversation_id}: object {digest} missing")
                continue
            if source_objects.get(digest) != dest_objects.get(digest):  # type: ignore[attr-defined]
                mismatches.append(f"{conversation_id}: object {digest} bytes differ")

    @staticmethod
    def _compare_by_id(
        conversation_id: str,
        kind: str,
        source: Mapping[str, BaseModel],
        dest: Mapping[str, BaseModel],
        mismatches: list[str],
    ) -> None:
        if source.keys() != dest.keys():
            missing = sorted(set(source) - set(dest))
            extra = sorted(set(dest) - set(source))
            mismatches.append(
                f"{conversation_id}: {kind} id set differs "
                f"(missing={missing[:3]} extra={extra[:3]})"
            )
            return
        for record_id, record in source.items():
            if _dump(record) != _dump(dest[record_id]):
                mismatches.append(f"{conversation_id}: {kind} {record_id} differs")

    # ==================================================================
    # Helpers
    # ==================================================================

    async def _list_all(
        self, list_fn: Callable, *, org_id: str, user_id: str
    ) -> Sequence[ConversationRecord]:
        result = await list_fn(
            org_id=org_id,
            user_id=user_id,
            limit=_READ_LIMIT,
            include_archived=True,
            include_deleted=True,
        )
        if len(result) >= _READ_LIMIT:
            raise MigrationError(
                f"scope ({org_id}, {user_id}) exceeds the conversation read ceiling"
            )
        return result

    def _source_object_store(self) -> object | None:
        store = getattr(self._source, "object_store", None)
        if store is None:
            return None
        if hasattr(store, "exists") and hasattr(store, "get"):
            return store
        return None

    @staticmethod
    def _collect_object_digests(
        conversation: ConversationRecord,
        messages: Sequence[MessageRecord],
        runs: Sequence[RunRecord],
        events_by_run: Mapping[str, Sequence[RuntimeEventEnvelope]],
    ) -> set[str]:
        digests: set[str] = set()
        blobs: list[BaseModel] = [conversation, *messages, *runs]
        for events in events_by_run.values():
            blobs.extend(events)
        for record in blobs:
            digests.update(_SHA256_TOKEN.findall(record.model_dump_json()))
        return digests

    @staticmethod
    def _bundle_bytes(bundle: _ConversationBundle) -> int:
        total = len(bundle.conversation.model_dump_json().encode("utf-8"))
        for message in bundle.messages:
            total += len(message.model_dump_json().encode("utf-8"))
        for run in bundle.runs:
            total += len(run.model_dump_json().encode("utf-8"))
        for events in bundle.events_by_run.values():
            for envelope in events:
                total += len(envelope.model_dump_json().encode("utf-8"))
        return total

    @staticmethod
    def _outcome(
        scope: MigrationScope,
        bundle: _ConversationBundle,
        *,
        status: str,
        byte_size: int,
    ) -> ConversationOutcome:
        return ConversationOutcome(
            org_id=scope.org_id,
            user_id=scope.user_id,
            conversation_id=bundle.conversation.conversation_id,
            status=status,
            messages=len(bundle.messages),
            runs=len(bundle.runs),
            events=bundle.event_count,
            objects=len(bundle.object_digests),
            bytes=byte_size,
        )

    def _reload_destination(self) -> None:
        """Rebuild the destination's in-memory view + catalog from the JSONL.

        A full reload (rather than incremental view updates) is idempotent no
        matter what partial state a prior interrupted run left behind, and is
        exactly what the file store does on ``open()``.
        """

        dest = self._dest
        dest.conversations.clear()
        dest.messages.clear()
        dest.runs.clear()
        dest.events_by_run.clear()
        dest._conversation_idempotency.clear()
        dest._run_idempotency.clear()
        dest._run_idempotency_fingerprint.clear()
        dest._load_sessions_from_disk()
        dest._rebuild_index()

    @staticmethod
    def _build_report(
        scopes: Sequence[MigrationScope],
        outcomes: Sequence[ConversationOutcome],
        *,
        dry_run: bool,
        verified: bool | None,
        mismatches: Sequence[str],
    ) -> MigrationReport:
        migrated = [o for o in outcomes if o.status in (MIGRATED, PLANNED)]
        skipped = [o for o in outcomes if o.status == SKIPPED]
        return MigrationReport(
            dry_run=dry_run,
            scopes=tuple(scopes),
            conversations_total=len(outcomes),
            conversations_migrated=len(migrated),
            conversations_skipped=len(skipped),
            messages=sum(o.messages for o in migrated),
            runs=sum(o.runs for o in migrated),
            events=sum(o.events for o in migrated),
            objects=sum(o.objects for o in migrated),
            bytes=sum(o.bytes for o in migrated),
            verified=verified,
            mismatches=tuple(mismatches),
            outcomes=tuple(outcomes),
        )

    def _emit(self, message: str) -> None:
        if self._progress is not None:
            self._progress(message)


def _dump(record: BaseModel) -> str:
    """Canonical JSON of a domain record for byte-faithful equality checks."""

    return record.model_dump_json()


__all__ = (
    "MIGRATED",
    "PLANNED",
    "SKIPPED",
    "ConversationOutcome",
    "MigrationError",
    "MigrationReport",
    "MigrationScope",
    "MigrationSourcePort",
    "MigrationVerificationError",
    "ScopeDiscoverySource",
    "StoreMigrator",
)
