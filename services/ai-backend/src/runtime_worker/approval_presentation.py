"""Project a consent card's SHAPE from the real arguments of the tool call being approved.

The design draws three approval cards, and which one is right is a property of the
action: a batch of payouts wants individually decidable rows, a draft post wants the
draft itself, everything else wants a key/value frame.

**Why this reads the tool arguments and not a model-authored description.** A consent
card is the last thing standing between the agent and an irreversible side effect. If
the preview text came from a field the model filled in alongside the call, a model
could show "Posting a thank-you note" while the ``text`` argument said something else
entirely — the card would be a description of the action rather than the action. So the
preview is projected from the exact argument the connector will send, and the rows from
the exact batch it will execute. The model still authors this content (it authors the
call), but it cannot author a second, divergent story about it.

The projection is best-effort decoration: anything it cannot confidently shape falls
back to ``None`` and the caller keeps today's params frame.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import ClassVar

from runtime_api.schemas.approvals import (
    APPROVAL_MAX_ROWS,
    ApprovalPresentation,
    ApprovalPreview,
    ApprovalRow,
)
from runtime_api.schemas.common import ApprovalLayout


class _Limits:
    """Caps applied before the contract's own ``StringConstraints`` see a value."""

    PREVIEW_TEXT_MAX = 2000
    PREVIEW_META_MAX = 96
    ROW_LABEL_MAX = 64
    ROW_VALUE_MAX = 48
    ROW_NOTE_MAX = 40
    ROW_ID_MAX = 64
    INITIALS_MAX = 3
    # Below this a "body" is a title, not content worth previewing — a params
    # row already shows it, and a preview frame around six words reads as noise.
    PREVIEW_MIN_CHARS = 40


class _Verbs:
    """Approve-button verbs, keyed off the real tool name so the promise can't drift."""

    DEFAULT = "Approve"
    SEND = "Approve & send"
    SIGN = "Approve & sign"

    SEND_TOKENS: ClassVar[tuple[str, ...]] = (
        "send",
        "post",
        "publish",
        "tweet",
        "email",
        "reply",
        "broadcast",
    )
    SIGN_TOKENS: ClassVar[tuple[str, ...]] = (
        "sign",
        "transfer",
        "swap",
        "payout",
        "pay",
        "transaction",
        "execute",
        "withdraw",
    )


class ApprovalPresentationProjector:
    """Derive an :class:`ApprovalPresentation` from a tool call's arguments."""

    # Argument keys holding free text that leaves the workspace. Ordered by
    # specificity: a call carrying both ``text`` and ``description`` is a post
    # with a description, not a description with a body.
    _PREVIEW_KEYS: ClassVar[tuple[str, ...]] = (
        "text",
        "body",
        "message",
        "content",
        "markdown",
        "comment",
        "html",
        "description",
    )
    # Argument keys holding a batch of line items.
    _ROWS_KEYS: ClassVar[tuple[str, ...]] = (
        "payouts",
        "transfers",
        "transactions",
        "recipients",
        "items",
        "entries",
        "rows",
        "batch",
        "records",
        "lines",
    )
    _ROW_LABEL_KEYS: ClassVar[tuple[str, ...]] = (
        "name",
        "label",
        "recipient",
        "to",
        "handle",
        "email",
        "address",
        "title",
        "who",
    )
    _ROW_VALUE_KEYS: ClassVar[tuple[str, ...]] = (
        "amount",
        "value",
        "total",
        "quantity",
        "qty",
        "price",
        "sum",
    )
    _ROW_NOTE_KEYS: ClassVar[tuple[str, ...]] = (
        "role",
        "note",
        "memo",
        "category",
        "type",
        "reason",
    )
    _ROW_ID_KEYS: ClassVar[tuple[str, ...]] = (
        "id",
        "row_id",
        "item_id",
        "key",
        "address",
        "email",
        "handle",
    )

    @classmethod
    def project(
        cls,
        *,
        tool_name: str,
        arguments: Mapping[str, object],
    ) -> ApprovalPresentation | None:
        """Return the card shape for this call, or ``None`` to keep the params frame."""
        approve_label = cls._approve_label(tool_name)
        rows = cls._project_rows(arguments)
        if rows:
            return ApprovalPresentation(
                layout=ApprovalLayout.ROWS,
                approve_label=cls._batch_label(approve_label),
                rows=rows,
            )
        preview = cls._project_preview(arguments)
        if preview is not None:
            return ApprovalPresentation(
                layout=ApprovalLayout.PREVIEW,
                approve_label=approve_label,
                preview=preview,
            )
        # Nothing shape-worthy found. Still worth returning a presentation when
        # the verb is more specific than the neutral default — the params card
        # benefits from "Approve & sign" too.
        if approve_label != _Verbs.DEFAULT:
            return ApprovalPresentation(
                layout=ApprovalLayout.PARAMS, approve_label=approve_label
            )
        return None

    @classmethod
    def _approve_label(cls, tool_name: str) -> str:
        """Pick the approve verb from the tool name. Signing outranks sending."""
        normalized = tool_name.strip().lower()
        if any(token in normalized for token in _Verbs.SIGN_TOKENS):
            return _Verbs.SIGN
        if any(token in normalized for token in _Verbs.SEND_TOKENS):
            return _Verbs.SEND
        return _Verbs.DEFAULT

    @staticmethod
    def _batch_label(approve_label: str) -> str:
        """Qualify the verb for a batch so "Approve" can't read as "approve this one row"."""
        if approve_label == _Verbs.DEFAULT:
            return "Approve all"
        return approve_label

    @classmethod
    def _project_preview(
        cls, arguments: Mapping[str, object]
    ) -> ApprovalPreview | None:
        """Return the first substantial free-text argument as the card's preview."""
        for key in cls._PREVIEW_KEYS:
            raw = arguments.get(key)
            if not isinstance(raw, str):
                continue
            text = raw.strip()
            if len(text) < _Limits.PREVIEW_MIN_CHARS:
                continue
            return ApprovalPreview(
                text=text[: _Limits.PREVIEW_TEXT_MAX],
                meta=cls._preview_meta(text),
            )
        return None

    @staticmethod
    def _preview_meta(text: str) -> str | None:
        """Describe the volume of what's being sent, so a truncated preview stays honest."""
        chars = len(text)
        words = len(text.split())
        return f"{words:,} words · {chars:,} characters"[: _Limits.PREVIEW_META_MAX]

    @classmethod
    def _project_rows(cls, arguments: Mapping[str, object]) -> tuple[ApprovalRow, ...]:
        """Return decidable line items from the first batch-shaped argument."""
        for key in cls._ROWS_KEYS:
            raw = arguments.get(key)
            if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
                continue
            rows = cls._rows_from_sequence(raw)
            if rows:
                return rows
        return ()

    @classmethod
    def _rows_from_sequence(cls, entries: Sequence[object]) -> tuple[ApprovalRow, ...]:
        """Build rows from a sequence of mappings; skip entries missing a label or value."""
        rows: list[ApprovalRow] = []
        for entry in entries[:APPROVAL_MAX_ROWS]:
            if not isinstance(entry, Mapping):
                continue
            label = cls._first_string(entry, cls._ROW_LABEL_KEYS, _Limits.ROW_LABEL_MAX)
            value = cls._first_scalar(entry, cls._ROW_VALUE_KEYS, _Limits.ROW_VALUE_MAX)
            if label is None or value is None:
                # A row that can't say who and how much isn't a row the user can
                # act on. Drop it rather than rendering a blank line — and if
                # every entry drops, the caller falls back to the params frame.
                continue
            row_id = cls._first_string(entry, cls._ROW_ID_KEYS, _Limits.ROW_ID_MAX)
            rows.append(
                ApprovalRow(
                    label=label,
                    value=value,
                    note=cls._first_string(
                        entry, cls._ROW_NOTE_KEYS, _Limits.ROW_NOTE_MAX
                    ),
                    initials=cls._initials(label),
                    row_id=row_id,
                    # Only a row with a stable id can carry its own decision;
                    # the contract downgrades the rest to display-only anyway.
                    decidable=row_id is not None,
                )
            )
        return tuple(rows)

    @staticmethod
    def _first_string(
        entry: Mapping[str, object], keys: tuple[str, ...], cap: int
    ) -> str | None:
        """Return the first non-empty string value among ``keys``."""
        for key in keys:
            raw = entry.get(key)
            if isinstance(raw, str) and raw.strip():
                return raw.strip()[:cap]
        return None

    @staticmethod
    def _first_scalar(
        entry: Mapping[str, object], keys: tuple[str, ...], cap: int
    ) -> str | None:
        """Return the first scalar value among ``keys``, rendered as a string.

        Numbers are kept verbatim rather than formatted: the consent card must
        show the amount the connector receives, not a prettier approximation of
        it. ``bool`` is excluded because ``True`` is not an amount.
        """
        for key in keys:
            raw = entry.get(key)
            if isinstance(raw, bool):
                continue
            if isinstance(raw, (int, float)):
                return str(raw)[:cap]
            if isinstance(raw, str) and raw.strip():
                return raw.strip()[:cap]
        return None

    @staticmethod
    def _initials(label: str) -> str | None:
        """Derive an avatar monogram from a row label.

        Handles the two shapes these labels actually take: a human name
        ("Mira Patel" → "MP") and an opaque handle or address
        ("mira.eth" / "0x8f42…" → "MI" / "0X"). Returns ``None`` when the
        label has no alphanumerics to work with.
        """
        words = [word for word in label.replace("_", " ").split() if word]
        if len(words) >= 2:
            monogram = "".join(word[0] for word in words[:2])
        elif words:
            monogram = words[0][: _Limits.INITIALS_MAX - 1]
        else:
            return None
        cleaned = "".join(char for char in monogram if char.isalnum())
        return cleaned.upper()[: _Limits.INITIALS_MAX] or None
