"""Rebase one whole-document rewrite onto a newer revision of the same artifact.

The compare-and-append guard is what stops an agent overwriting a cell the user
just edited by hand, and it stays. But refusing is only half an answer. The
agent holds a COMPLETE document derived from an older revision, and the user's
edit almost always lands somewhere else entirely — a cell in row 3 while the
agent appends row 40. Those two are reconcilable against their common ancestor,
and reconciling them here is what turns "the artifact changed, nothing was
overwritten" from a dead end into a retry the runtime performs itself instead of
hoping the model reads its tool result carefully enough to perform it.

Line-granular and deliberately conservative: overlapping edits are reported as
ambiguous rather than resolved by guesswork, because a wrong merge silently
corrupts a durable object the user believes they edited by hand.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher
from enum import Enum


class ArtifactMergeStatus(str, Enum):
    """Why a rebase did or did not produce content."""

    MERGED = "merged"
    #: Both sides rewrote the same lines. A human owns that decision.
    AMBIGUOUS = "ambiguous"
    #: Not comparable as text at all: undecodable bytes, or too large to diff.
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True)
class ArtifactMergeResult:
    """The merged bytes, or the reason there are none."""

    status: ArtifactMergeStatus
    content: bytes | None = None


@dataclass(frozen=True)
class _Edit:
    """One contiguous rewrite of ``base[start:end]``, expressed in whole lines."""

    start: int
    end: int
    lines: tuple[str, ...]


class ThreeWayTextMerge:
    """Reconcile two independent rewrites of one common ancestor.

    ``base`` is the revision the agent read, ``current`` is the revision that
    beat it to the store, and ``proposed`` is what the agent wants to write.
    Both sides are diffed against ``base``; disjoint edit regions are replayed
    in order, and any collision refuses rather than picks a winner.
    """

    class Limits:
        #: Past this, the differ's quadratic worst case is not worth paying
        #: inside a run loop, and the honest answer is to hand the conflict back.
        MAX_LINES = 20_000

    @classmethod
    def merge(
        cls, *, base: bytes, current: bytes, proposed: bytes
    ) -> ArtifactMergeResult:
        """Rebase ``proposed`` — written against ``base`` — onto ``current``."""

        try:
            base_lines = cls._lines(base)
            current_lines = cls._lines(current)
            proposed_lines = cls._lines(proposed)
        except UnicodeDecodeError:
            return ArtifactMergeResult(ArtifactMergeStatus.UNSUPPORTED)
        if cls._too_large(base_lines, current_lines, proposed_lines):
            return ArtifactMergeResult(ArtifactMergeStatus.UNSUPPORTED)

        # A set, because the same edit made on both sides is ONE edit, not a
        # collision: the agent may already have folded in the change it is
        # being rebased over, and calling that a conflict would refuse a
        # revision that has nothing left to disagree about.
        edits = sorted(
            {
                *cls._edits(base_lines, current_lines),
                *cls._edits(base_lines, proposed_lines),
            },
            key=lambda edit: (edit.start, edit.end),
        )
        if cls._collides(edits):
            return ArtifactMergeResult(ArtifactMergeStatus.AMBIGUOUS)
        return ArtifactMergeResult(
            ArtifactMergeStatus.MERGED,
            cls._apply(base_lines, edits).encode("utf-8"),
        )

    @staticmethod
    def _lines(content: bytes) -> tuple[str, ...]:
        """Split into lines that rejoin byte-identically, or refuse to decode."""

        return tuple(content.decode("utf-8").splitlines(keepends=True))

    @classmethod
    def _too_large(cls, *documents: Sequence[str]) -> bool:
        return max(len(document) for document in documents) > cls.Limits.MAX_LINES

    @staticmethod
    def _edits(base: Sequence[str], other: Sequence[str]) -> tuple[_Edit, ...]:
        """Describe ``other`` as the regions of ``base`` it rewrites.

        ``autojunk`` stays ON, and the reason is measured rather than assumed.
        It treats a line filling more than 1% of a long document as noise, so
        it can widen an edit region — but a widened region only ever refuses a
        merge that a finer diff would have allowed, and a refusal costs the
        agent one retry. Turning it off costs 30 SECONDS on 20k repetitive
        lines (0.3s with it on) and buys nothing on real content, where both
        settings finish in single-digit milliseconds. A stalled run loop is the
        worse failure of the two.
        """

        matcher = SequenceMatcher(None, base, other, autojunk=True)
        return tuple(
            _Edit(start=start, end=end, lines=tuple(other[other_start:other_end]))
            for tag, start, end, other_start, other_end in matcher.get_opcodes()
            if tag != "equal"
        )

    @staticmethod
    def _collides(edits: Sequence[_Edit]) -> bool:
        for earlier, later in zip(edits, edits[1:]):
            if later.start < earlier.end:
                return True
            # Two DIFFERENT insertions at one anchor — duplicates are already
            # gone — so which of them goes first is a guess, and guessing is
            # the one thing a merge must never do.
            if later.start == later.end == earlier.start == earlier.end:
                return True
        return False

    @staticmethod
    def _apply(base: Sequence[str], edits: Sequence[_Edit]) -> str:
        merged: list[str] = []
        cursor = 0
        for edit in edits:
            merged.extend(base[cursor : edit.start])
            merged.extend(edit.lines)
            cursor = edit.end
        merged.extend(base[cursor:])
        return "".join(merged)


__all__ = (
    "ArtifactMergeResult",
    "ArtifactMergeStatus",
    "ThreeWayTextMerge",
)
