"""Authorship-span diff for staged-write revisions (PRD-D1).

When a user free-form edits a staged draft, the server (never the client) diffs
the user's whole-body revision against the agent's immediately-previous revision
and derives the ``authorship_spans``: the char ranges of the NEW text that the
user actually changed. The renderer highlights exactly those ranges as "edited
by you" — honest, because the server computed them from the two snapshots, not
from a client claim.

Deterministic, stdlib-only (``difflib.SequenceMatcher``), and unicode-safe: it
diffs Python ``str`` (code points), so offsets index characters, never bytes —
an emoji is one span position, not four. Beyond :data:`RevisionDiffer._MAX_SPANS`
distinct edited regions the differ collapses to a single whole-body user span
(the honest fallback: "you rewrote it" rather than a thousand slivers).
"""

from __future__ import annotations

from difflib import SequenceMatcher
from typing import ClassVar, Literal

from pydantic import NonNegativeInt

from agent_runtime.execution.contracts import RuntimeContract


class AuthorshipSpan(RuntimeContract):
    """A half-open ``[start, end)`` char range of the NEW text and its author.

    Offsets index into the *new* revision's text (code points). ``author`` is
    ``"user"`` for a range the user inserted/replaced and ``"agent"`` for the
    untouched agent regions (D1 emits only ``"user"`` spans — agent regions stay
    unmarked, per FR-C4 — but the field carries the full literal for D3/forward
    use).
    """

    start: NonNegativeInt
    end: NonNegativeInt
    author: Literal["agent", "user"]


class RevisionDiffer:
    """Derives ``AuthorshipSpan``s for a user revision against its predecessor."""

    # Cap on distinct edited regions before the honest whole-body fallback.
    _MAX_SPANS: ClassVar[int] = 200

    _AUTHOR_USER: ClassVar[str] = "user"

    @classmethod
    def spans(cls, *, old: str, new: str, author: str) -> tuple[AuthorshipSpan, ...]:
        """Return the user-authored ranges of ``new`` vs ``old``.

        Only ``author == "user"`` produces spans (D1 marks user edits; an agent
        revision is unmarked by construction, so it returns ``()``). Uses
        ``SequenceMatcher`` opcodes: ``replace`` and ``insert`` regions of the
        NEW text are the user's; ``equal`` (unchanged) and ``delete`` (removed,
        no NEW extent) contribute nothing. Adjacent user ranges are merged so a
        single continuous edit is one span. Beyond ``_MAX_SPANS`` regions the
        whole new body collapses to one user span (honest fallback).
        """

        if author != cls._AUTHOR_USER:
            return ()
        if new == old:
            return ()

        matcher = SequenceMatcher(a=old, b=new, autojunk=False)
        ranges: list[tuple[int, int]] = []
        for tag, _a1, _a2, b1, b2 in matcher.get_opcodes():
            if tag in ("replace", "insert") and b2 > b1:
                ranges.append((b1, b2))

        if not ranges:
            # Pure deletion(s): no NEW extent changed authorship. Nothing to mark
            # — but the bodies differ, so fall back to the honest whole-body span
            # only when there is a body to mark.
            return (
                (AuthorshipSpan(start=0, end=len(new), author=cls._AUTHOR_USER),)
                if len(new) > 0
                else ()
            )

        merged = cls._merge_adjacent(ranges)
        if len(merged) > cls._MAX_SPANS:
            return (AuthorshipSpan(start=0, end=len(new), author=cls._AUTHOR_USER),)
        return tuple(
            AuthorshipSpan(start=start, end=end, author=cls._AUTHOR_USER)
            for start, end in merged
        )

    @staticmethod
    def _merge_adjacent(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
        """Coalesce touching/overlapping ranges (already in ascending order)."""

        merged: list[tuple[int, int]] = []
        for start, end in ranges:
            if merged and start <= merged[-1][1]:
                prev_start, prev_end = merged[-1]
                merged[-1] = (prev_start, max(prev_end, end))
            else:
                merged.append((start, end))
        return merged


__all__ = ["AuthorshipSpan", "RevisionDiffer"]
