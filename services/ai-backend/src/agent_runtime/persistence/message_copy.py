"""Pure-Python message-copy helper for the conversation fork (PR 6.2).

The fork service hands this helper a sequence of source messages and a
target conversation; the helper rewrites IDs into a fresh map, resets
run-/branch-scoped pointers (``run_id``, ``source_message_id``,
``branch_id``) to ``None``, stamps a fresh ``created_at`` so the
retention sweeper sees the fork's age (the original timestamp survives
in ``metadata.original_created_at``), and emits the records ready for
:meth:`PersistencePort.append_message`.

Encryption is **not** done here. The codec applies at the persistence
adapter boundary (postgres ``_insert_message`` runs ``encrypt_text`` /
``encrypt_jsonb`` on every insert), so each persisted row gets a fresh
IV automatically; the in-memory adapter stores plaintext and the test
harness exercises both. Re-encryption "in place" would re-derive the
same envelope and is therefore *not* what we want — the right invariant
("fresh IV per row") is what the adapter already gives us.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any
from uuid import uuid4

from runtime_api.schemas import MessageRecord


class _MetadataKeys:
    """Audit pointers stamped into each copied row's ``metadata`` JSONB."""

    ORIGINAL_CONVERSATION_ID = "original_conversation_id"
    ORIGINAL_MESSAGE_ID = "original_message_id"
    ORIGINAL_CREATED_AT = "original_created_at"


class ForkOrphanWarning:
    """Returned alongside the copy when a parent_message_id is unresolved.

    A copied message can reference a ``parent_message_id`` that is not
    present in the snapshot set (data integrity violation, or the parent
    was deleted before the snapshot). Rather than fail the whole fork,
    we set ``parent_message_id = None`` on the copy and surface a
    structured warning so the operator + audit row can record what
    happened. Callers must propagate (e.g. in audit metadata).
    """

    __slots__ = ("source_message_id", "missing_parent_id")

    def __init__(self, *, source_message_id: str, missing_parent_id: str) -> None:
        self.source_message_id = source_message_id
        self.missing_parent_id = missing_parent_id

    def to_metadata(self) -> dict[str, str]:
        return {
            "source_message_id": self.source_message_id,
            "missing_parent_id": self.missing_parent_id,
        }


class CopiedMessages:
    """Result tuple — copies + any orphan warnings."""

    __slots__ = ("records", "orphan_warnings", "id_map")

    def __init__(
        self,
        *,
        records: tuple[MessageRecord, ...],
        orphan_warnings: tuple[ForkOrphanWarning, ...],
        id_map: Mapping[str, str],
    ) -> None:
        self.records = records
        self.orphan_warnings = orphan_warnings
        self.id_map = dict(id_map)

    def __len__(self) -> int:
        return len(self.records)


class MessageCopyPlanner:
    """Stateless builder for fork message copies.

    Kept as a class (not a free function) per the AI-backend code
    organization rule that production helpers live inside named
    classes — avoids module-level helper functions.
    """

    @classmethod
    def plan(
        cls,
        *,
        source_messages: Sequence[MessageRecord],
        target_conversation_id: str,
        target_org_id: str,
        now: datetime,
    ) -> CopiedMessages:
        """Build copy records preserving the parent_message_id graph.

        The input must be ordered by ``created_at`` ASC so each message
        sees its parent's new id already in ``id_map`` by the time the
        copy is built. Callers source the rows via
        :meth:`PersistencePort.list_messages`, which already orders
        by ``created_at`` ASC in both adapters.
        """

        id_map: dict[str, str] = {}
        records: list[MessageRecord] = []
        warnings: list[ForkOrphanWarning] = []

        for source in source_messages:
            new_id = uuid4().hex
            id_map[source.message_id] = new_id

            new_parent_id: str | None = None
            if source.parent_message_id is not None:
                new_parent_id = id_map.get(source.parent_message_id)
                if new_parent_id is None:
                    warnings.append(
                        ForkOrphanWarning(
                            source_message_id=source.message_id,
                            missing_parent_id=source.parent_message_id,
                        )
                    )

            metadata: dict[str, Any] = dict(source.metadata or {})
            metadata.update(
                {
                    _MetadataKeys.ORIGINAL_CONVERSATION_ID: source.conversation_id,
                    _MetadataKeys.ORIGINAL_MESSAGE_ID: source.message_id,
                    _MetadataKeys.ORIGINAL_CREATED_AT: source.created_at.isoformat(),
                }
            )

            # ``MessageRecord`` carries no ``user_id`` field — message
            # ownership is implicit via the conversation row's user_id,
            # which we set on the new conversation row in the fork
            # service. The role enum (user / assistant / system) is
            # carried verbatim so the FE renders the same bubbles.

            records.append(
                source.model_copy(
                    update={
                        "message_id": new_id,
                        "conversation_id": target_conversation_id,
                        "org_id": target_org_id,
                        "run_id": None,
                        "source_message_id": None,
                        "branch_id": None,
                        "parent_message_id": new_parent_id,
                        "created_at": now,
                        "edited_at": None,
                        "deleted_at": None,
                        "metadata": metadata,
                    }
                )
            )

        return CopiedMessages(
            records=tuple(records),
            orphan_warnings=tuple(warnings),
            id_map=id_map,
        )
