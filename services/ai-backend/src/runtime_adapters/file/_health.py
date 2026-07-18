"""Machine-readable per-conversation health for the file store ("needs repair").

AC2 promises a safe *"this chat needs repair"* state: when a conversation's
canonical JSONL fails closed on interior corruption (:class:`JsonlCorruptionError`),
its disposable catalog had to be discarded and rebuilt, or one of its events
references an object blob that is missing, that conversation is flagged
``needs_repair`` with a machine-readable reason a client can render.

The reason vocabulary is **not** re-derived here: it reuses
:class:`runtime_adapters.file.repair.JsonlLineKind` /
:class:`~runtime_adapters.file.repair.ConversationDiagnosis`, which already
classify every corruption/dangling-ref verdict. :meth:`ConversationHealth.from_diagnosis`
maps a repair diagnosis into the wire shape; :class:`FileStoreHealthTracker`
records the same reasons live on the store's fail-closed / catalog-rebuild
paths so a caller that just caught an open() failure can ask which conversation
is at fault without re-scanning the tree.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from runtime_adapters.file.repair import ConversationDiagnosis, JsonlLineKind


class FileStoreRepairReason(StrEnum):
    """Why a conversation is flagged ``needs_repair``.

    ``INTERIOR_CORRUPTION`` reuses :attr:`JsonlLineKind.INTERIOR_CORRUPT`
    verbatim (the module-level assert below pins them together); the remaining
    reasons name the two other ways :meth:`StoreRepair.diagnose` /
    ``CatalogIndex.connect`` report a conversation unhealthy.
    """

    INTERIOR_CORRUPTION = "interior_corrupt"
    DANGLING_OBJECT_REF = "dangling_object_ref"
    META_UNREADABLE = "meta_unreadable"
    CATALOG_REBUILT = "catalog_rebuilt"


# Lock the corruption reason to the repair module's vocabulary so a rename on
# one side can never silently desync the health signal from the diagnosis.
assert FileStoreRepairReason.INTERIOR_CORRUPTION.value == JsonlLineKind.INTERIOR_CORRUPT


class ConversationHealth(BaseModel):
    """Per-conversation health verdict for the "needs repair" UX."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    conversation_id: str | None = None
    relative_dir: str | None = None
    needs_repair: bool = False
    reason_codes: tuple[FileStoreRepairReason, ...] = ()

    @classmethod
    def clean(cls, conversation_id: str) -> "ConversationHealth":
        """A healthy verdict for a known conversation id."""

        return cls(conversation_id=conversation_id, needs_repair=False)

    @classmethod
    def from_diagnosis(cls, diagnosis: ConversationDiagnosis) -> "ConversationHealth":
        """Map a :class:`ConversationDiagnosis` into the wire health shape.

        Reuses the diagnosis' own ``healthy`` / ``needs_repair`` computed fields
        and reason data — this never re-classifies corruption itself.
        """

        reasons: list[FileStoreRepairReason] = []
        if any(stream.needs_repair for stream in diagnosis.streams):
            reasons.append(FileStoreRepairReason.INTERIOR_CORRUPTION)
        if diagnosis.dangling_object_refs:
            reasons.append(FileStoreRepairReason.DANGLING_OBJECT_REF)
        if not diagnosis.meta_readable:
            reasons.append(FileStoreRepairReason.META_UNREADABLE)
        return cls(
            conversation_id=diagnosis.conversation_id,
            relative_dir=diagnosis.relative_dir,
            needs_repair=not diagnosis.healthy,
            reason_codes=tuple(reasons),
        )


class StoreHealthReport(BaseModel):
    """Whole-store health: every conversation that needs repair + store flags."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    healthy: bool
    catalog_rebuilt: bool = False
    orphan_object_count: int = 0
    conversations: tuple[ConversationHealth, ...] = ()

    def needs_repair_ids(self) -> frozenset[str]:
        """Conversation ids flagged ``needs_repair`` (missing ids dropped)."""

        return frozenset(
            health.conversation_id
            for health in self.conversations
            if health.needs_repair and health.conversation_id is not None
        )


class FileStoreHealthTracker:
    """In-memory record of health signals observed on the live store paths.

    Populated on the store's fail-closed replay and catalog-rebuild paths so a
    caller that just caught an ``open()`` failure (or wants a cheap flag on a
    listing) can learn which conversation is at fault without a fresh disk scan.
    The authoritative, exhaustive verdict is still
    :meth:`FileRuntimeApiStore.store_health`, which re-diagnoses from disk.
    """

    def __init__(self) -> None:
        self._reasons: dict[str, set[FileStoreRepairReason]] = {}
        self._catalog_rebuilt = False

    def mark_needs_repair(
        self, conversation_id: str, reason: FileStoreRepairReason
    ) -> None:
        """Flag ``conversation_id`` with ``reason`` (idempotent per reason)."""

        self._reasons.setdefault(conversation_id, set()).add(reason)

    def mark_catalog_rebuilt(self) -> None:
        """Record that the disposable catalog was discarded and rebuilt."""

        self._catalog_rebuilt = True

    @property
    def catalog_rebuilt(self) -> bool:
        return self._catalog_rebuilt

    def needs_repair_ids(self) -> frozenset[str]:
        """Conversation ids recorded as needing repair on the live paths."""

        return frozenset(self._reasons)

    def reasons_for(self, conversation_id: str) -> tuple[FileStoreRepairReason, ...]:
        return tuple(sorted(self._reasons.get(conversation_id, set())))


__all__ = (
    "FileStoreRepairReason",
    "ConversationHealth",
    "StoreHealthReport",
    "FileStoreHealthTracker",
)
