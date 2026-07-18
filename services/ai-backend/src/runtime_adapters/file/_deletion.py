"""Physical-deletion primitives for the file-native runtime store.

The desktop ``single_user_desktop`` profile treats "delete my data" literally:
a purged conversation's session folder and JSONL streams are removed from disk
and any content-addressed object that *becomes* unreferenced as a result is
garbage-collected. This module owns the three cohesive, individually testable
pieces that make that safe:

* :class:`LegalHoldPolicy` — a conversation flagged for legal hold is never
  erased (its bytes, and every object it references, survive).
* :class:`ObjectReachabilityScanner` — computes which object digests are
  referenced by which conversation sessions by scanning the canonical JSONL,
  and derives the set that is safe to collect after a purge.
* :class:`SessionEraser` — verifies a deletion plan (every path is contained
  under the store's ``sessions`` root) *before* removing anything, then erases
  the session directories.

The store orchestrates: it snapshots victim references, erases the verified
plan, recomputes survivor references from the JSONL that remains, and collects
``victim − survivor`` blobs (skipping any with an in-flight write). Reference
counting is deliberately conservative — a digest seen in any surviving stream
protects the blob, so content-addressed sharing (identical payloads offloaded
by two conversations dedupe to one blob) keeps a shared object alive when only
one owner is deleted.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from runtime_adapters.file._paths import FileStoreLayout
from runtime_adapters.file.object_store import FileObjectStore
from runtime_api.schemas import ConversationRecord


class DeletionPlanError(RuntimeError):
    """Raised when a planned session path escapes the store's sessions root.

    Fail-safe: the store aborts the *entire* purge batch on this rather than
    deleting a subset, so a crafted or corrupt path can never remove bytes
    outside the tenant's own ``sessions`` subtree.
    """


class LegalHoldPolicy:
    """Whether a conversation is under a legal hold that blocks deletion.

    The file store has no separate ``runtime_legal_holds`` table (that is a
    Postgres deployment control); on the desktop a hold is expressed as a
    truthy ``legal_hold`` flag in the conversation's ``metadata``. Held
    conversations are skipped by both the user-initiated purge and the
    retention sweeper.
    """

    METADATA_KEY = "legal_hold"

    @classmethod
    def is_on_hold(cls, conversation: ConversationRecord) -> bool:
        """Return ``True`` when the conversation carries an active legal hold."""

        return bool((conversation.metadata or {}).get(cls.METADATA_KEY))


class ObjectReachabilityScanner:
    """Compute object-store reachability from canonical session JSONL on disk.

    Object references live inline in the session streams as the offload
    writer's ``/large_tool_results/<sha256>`` form. Rather than parse every
    record shape, the scanner reads the raw bytes of a conversation's canonical
    files and collects every 64-char hex token — a superset of the real object
    digests. Over-collection is the safe direction: it can only *retain* a blob
    that a garbage pass might otherwise remove, never delete a referenced one.
    """

    # sha256 digests are exactly 64 lowercase hex chars; object paths and the
    # ``/large_tool_results/<sha>`` refs both render them verbatim in the JSONL.
    _SHA256 = re.compile(rb"[0-9a-f]{64}")

    def __init__(self, layout: FileStoreLayout) -> None:
        self._layout = layout

    def _canonical_files(self, conversation_dir: Path) -> list[Path]:
        """Return every canonical stream file under one session directory."""

        files = [
            conversation_dir / self._layout.CONVERSATION_META,
            conversation_dir / self._layout.EVENTS_FILE,
            conversation_dir / self._layout.MESSAGES_FILE,
            conversation_dir / self._layout.RUNS_FILE,
        ]
        subagents_dir = conversation_dir / self._layout.SUBAGENTS_DIR
        if subagents_dir.is_dir():
            files.extend(
                path
                for path in sorted(subagents_dir.iterdir())
                if path.suffix == ".jsonl"
            )
        return files

    def scan(self, conversation: ConversationRecord) -> set[str]:
        """Return every object digest referenced by one conversation's session."""

        conversation_dir = self._layout.conversation_dir(
            conversation.org_id, conversation.conversation_id
        )
        refs: set[str] = set()
        if not conversation_dir.exists():
            return refs
        for path in self._canonical_files(conversation_dir):
            try:
                data = path.read_bytes()
            except (FileNotFoundError, IsADirectoryError):
                continue
            for match in self._SHA256.findall(data):
                refs.add(match.decode("ascii"))
        return refs

    def scan_all(self, conversations: object) -> set[str]:
        """Union of :meth:`scan` over an iterable of conversation records."""

        refs: set[str] = set()
        for conversation in conversations:  # type: ignore[attr-defined]
            refs |= self.scan(conversation)
        return refs

    def collectible(
        self,
        *,
        victim_refs: set[str],
        survivor_refs: set[str],
        object_store: FileObjectStore,
    ) -> set[str]:
        """Digests that *became* unreferenced: referenced by a purged session,
        absent from every survivor, and still present on disk.

        Only blobs the deleted conversations actually referenced are eligible —
        pre-existing orphans are left untouched — so the pass never reaches
        beyond the deletion that triggered it.
        """

        candidates = victim_refs - survivor_refs
        present = set(object_store.iter_digests())
        return candidates & present


class SessionEraser:
    """Verify then physically erase conversation session directories.

    The verification pass proves every planned path resolves *inside* its
    tenant's ``sessions`` root before a single directory is removed; a plan
    that fails aborts the whole batch (:class:`DeletionPlanError`).
    """

    def __init__(self, layout: FileStoreLayout) -> None:
        self._layout = layout

    def plan(self, conversations: list[ConversationRecord]) -> list[Path]:
        """Resolve + containment-check every session dir; raise on any escape."""

        planned: list[Path] = []
        for conversation in conversations:
            sessions_root = self._layout.sessions_dir(conversation.org_id).resolve()
            conversation_dir = self._layout.conversation_dir(
                conversation.org_id, conversation.conversation_id
            ).resolve()
            if (
                conversation_dir != sessions_root
                and sessions_root not in conversation_dir.parents
            ):
                raise DeletionPlanError(
                    "planned session path escapes the store sessions root"
                )
            planned.append(conversation_dir)
        return planned

    @staticmethod
    def erase(planned_dirs: list[Path]) -> None:
        """Remove every verified session directory (idempotent on absence)."""

        for directory in planned_dirs:
            if directory.exists():
                shutil.rmtree(directory)


__all__ = (
    "DeletionPlanError",
    "LegalHoldPolicy",
    "ObjectReachabilityScanner",
    "SessionEraser",
)
