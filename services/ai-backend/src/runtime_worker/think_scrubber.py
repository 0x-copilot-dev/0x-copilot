"""Stateful scrubber for inline reasoning tags in streamed assistant text.

Some models have no structured reasoning channel at all. Local runtimes and a
number of open-weight models emit their chain of thought *inline in the visible
text*, wrapped in ``<think>`` … ``</think>``. There is no content-block type and
no sibling field to read — the only signal is the tag.

Two things must happen, and only one of them is optional:

* **The reasoning must not reach the user as answer text.** This is the
  non-negotiable half. Raw chain of thought leaking into a reply is worse than
  showing no thinking at all.
* **The reasoning should be surfaced as reasoning.** This module returns it
  rather than discarding it, so the worker can emit it as the SAME
  ``reasoning_summary_delta`` events a provider's typed blocks produce. Every
  downstream consumer — the ordered-parts fold, persistence, the transcript —
  then treats an inline-tag model identically to Anthropic, with no special
  cases anywhere above this layer.

WHY A STATE MACHINE AND NOT A REGEX
-----------------------------------
A regex over a complete string is correct. Run per-delta it is actively harmful,
because tags split across chunk boundaries::

    delta1 = "<think>"
    delta2 = "Let me check their config"
    delta3 = "</think>"

A per-delta regex erases ``delta1`` (it matches an unterminated open), so no
downstream state machine ever sees the open tag, ``delta2`` is treated as
ordinary content, and the reasoning is published to the user. Holding partial
tags back across deltas is the whole job.

Usage::

    scrubber = StreamingThinkScrubber()
    for delta in stream:
        scrubbed = scrubber.feed(delta)
        if scrubbed.visible:
            emit_text(scrubbed.visible)
        if scrubbed.reasoning:
            emit_reasoning(scrubbed.reasoning)
    tail = scrubber.flush()   # at end of stream

``reset()`` at the top of each turn so an unclosed block from an interrupted
stream cannot swallow the next turn's output.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ScrubbedDelta:
    """One delta, split into what the user sees and what it was thinking."""

    visible: str = ""
    reasoning: str = ""

    def __bool__(self) -> bool:
        return bool(self.visible or self.reasoning)


class StreamingThinkScrubber:
    """Split streamed text into visible prose and inline-tagged reasoning."""

    #: Every spelling seen in the wild, matched case-insensitively. A model that
    #: invents a new one leaks, so this list is the contract — extend it rather
    #: than adding a second parser somewhere else.
    TAGS: tuple[str, ...] = (
        "think",
        "thinking",
        "reasoning",
        "thought",
        "reasoning_scratchpad",
    )

    def __init__(self) -> None:
        self._in_block = False
        #: Held-back tail that may be the prefix of a tag. Never emitted until
        #: the next feed resolves it, or `flush` decides it never was one.
        self._buf = ""
        #: True when nothing has been emitted yet, or the last emission ended on
        #: a newline. Start-of-stream counts as a boundary.
        self._at_line_start = True

    def reset(self) -> None:
        """Drop all state. Call at the top of every turn."""
        self._in_block = False
        self._buf = ""
        self._at_line_start = True

    @property
    def in_block(self) -> bool:
        """True while inside an unclosed reasoning block."""
        return self._in_block

    def feed(self, delta: str) -> ScrubbedDelta:
        """Consume one streamed chunk."""
        if not delta:
            return ScrubbedDelta()
        self._buf += delta
        return self._drain(final=False)

    def flush(self) -> ScrubbedDelta:
        """End of stream: release anything held back.

        A held-back tail that turned out not to be a tag is prose the user is
        owed. An unclosed block is NOT released as visible text — a truncated
        stream inside a reasoning block must not dump the chain of thought into
        the answer — but it is returned as reasoning, which is what it is.
        """
        result = self._drain(final=True)
        tail, self._buf = self._buf, ""
        if not tail:
            return result
        if self._in_block:
            return ScrubbedDelta(result.visible, result.reasoning + tail)
        return ScrubbedDelta(result.visible + tail, result.reasoning)

    # -- internals ---------------------------------------------------------

    def _drain(self, *, final: bool) -> ScrubbedDelta:
        visible: list[str] = []
        reasoning: list[str] = []

        while self._buf:
            if self._in_block:
                index, tag = self._find_close(self._buf)
                if index is None:
                    # Still inside the block. Hold back only as much as could be
                    # the start of a closing tag; the rest is settled reasoning.
                    keep = self._partial_tail(self._buf, closing=True)
                    if keep and not final:
                        settled, self._buf = self._buf[:-keep], self._buf[-keep:]
                    else:
                        settled, self._buf = self._buf, ""
                    if settled:
                        reasoning.append(settled)
                    break
                reasoning.append(self._buf[:index])
                self._buf = self._buf[index + len(tag) :]
                self._in_block = False
                # A close tag is a boundary: whatever follows starts a line as
                # far as open-detection is concerned.
                self._at_line_start = True
                continue

            index, tag = self._find_open(self._buf)
            if index is None:
                keep = self._partial_tail(self._buf, closing=False)
                if keep and not final:
                    settled, self._buf = self._buf[:-keep], self._buf[-keep:]
                else:
                    settled, self._buf = self._buf, ""
                if settled:
                    visible.append(settled)
                    self._track_line_start(settled)
                break

            before = self._buf[:index]
            if before:
                visible.append(before)
                self._track_line_start(before)
            self._buf = self._buf[index + len(tag) :]
            self._in_block = True

        return ScrubbedDelta("".join(visible), "".join(reasoning))

    def _find_open(self, text: str) -> tuple[int | None, str]:
        """Index of the next OPENING tag that is a real block opener.

        An opening tag only counts at the start of the stream, after a newline,
        or when nothing but whitespace precedes it on the current line. Without
        that rule, prose that merely *mentions* a tag — ``use <think> tags`` —
        would suppress everything after it. This is the single most common false
        positive, and the models most likely to emit inline reasoning are also
        the ones most likely to talk about it.
        """
        best: tuple[int | None, str] = (None, "")
        lowered = text.lower()
        for tag in self.TAGS:
            needle = f"<{tag}>"
            start = 0
            while True:
                index = lowered.find(needle, start)
                if index == -1:
                    break
                if self._is_block_boundary(text, index):
                    if best[0] is None or index < best[0]:
                        best = (index, needle)
                    break
                start = index + len(needle)
        return best

    def _find_close(self, text: str) -> tuple[int | None, str]:
        """Index of the next CLOSING tag, whichever variant arrives first.

        Deliberately not matched to the tag that opened the block: a model that
        opens ``<think>`` and closes ``</thinking>`` is malformed, and treating
        the mismatch as "still open" would swallow the entire rest of the reply.
        """
        best: tuple[int | None, str] = (None, "")
        lowered = text.lower()
        for tag in self.TAGS:
            needle = f"</{tag}>"
            index = lowered.find(needle)
            if index != -1 and (best[0] is None or index < best[0]):
                best = (index, needle)
        return best

    def _is_block_boundary(self, text: str, index: int) -> bool:
        if index == 0:
            return self._at_line_start
        preceding = text[:index]
        line = preceding.rsplit("\n", 1)[-1]
        if line.strip():
            return False
        # Only whitespace since the last newline WITHIN this buffer. If the
        # buffer itself has no newline, defer to what was last emitted.
        if "\n" in preceding:
            return True
        return self._at_line_start

    def _partial_tail(self, text: str, *, closing: bool) -> int:
        """How many trailing chars could still become a tag, so must be held.

        Returns 0 when the tail cannot possibly extend into one. Bounded by the
        longest tag, so a stream that never closes cannot grow the buffer.
        """
        prefixes = [f"</{tag}>" for tag in self.TAGS]
        if not closing:
            prefixes += [f"<{tag}>" for tag in self.TAGS]
        longest = max(len(p) for p in prefixes) - 1
        lowered = text.lower()
        for size in range(min(longest, len(lowered)), 0, -1):
            tail = lowered[-size:]
            # A partial tag must START with '<' — otherwise it is just prose.
            if not tail.startswith("<"):
                continue
            if any(prefix.startswith(tail) for prefix in prefixes):
                return size
        return 0

    def _track_line_start(self, emitted: str) -> None:
        self._at_line_start = emitted.endswith("\n")
