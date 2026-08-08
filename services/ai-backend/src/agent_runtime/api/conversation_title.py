"""Derive a conversation's display title from its first user message.

WHY THIS EXISTS
---------------
Nothing generated a title. A conversation got one only from an explicit PATCH,
so the ordinary path — open the cockpit, type, send — left ``title`` unset and
the Run header fell through to the literal string ``"Untitled run"``. Reported
from the live desktop app, where a substantive five-exchange thread was headed
"Untitled run".

The hosts each derived one at CREATE time, which covers only the flows that
create a conversation from a prompt (desktop first-run). Deriving here covers
every client at once, including the ones that create the conversation before
there is anything to name it after.

MIRRORS THE CLIENT RULES
------------------------
``packages/chat-surface/src/projections/conversationTitle.ts`` implements the
same rules, and the two test suites pin the same cases. A conversation must not
be named differently depending on which side named it — change both together,
exactly as the SIWE message template requires.
"""

from __future__ import annotations

import re
from typing import Final

#: Longest title we keep. Past this the tail carries no information in a header
#: that is itself ellipsized by CSS.
MAX_TITLE_LENGTH: Final = 60

#: Below this a word-boundary cut removes more than it saves, so we cut hard and
#: let the ellipsis do the talking.
MIN_WORD_BOUNDARY: Final = 24


class ConversationTitle:
    """Derivation of a conversation's display title. Pure; no IO."""

    #: Longest title we keep (see the module constant it mirrors).
    MAX_LENGTH: Final = MAX_TITLE_LENGTH
    #: Shortest acceptable word-boundary cut.
    MIN_WORD_BOUNDARY: Final = MIN_WORD_BOUNDARY

    _WHITESPACE_RUN: Final = re.compile(r"\s+")
    _TRAILING_PUNCTUATION: Final = re.compile(r"[\s,;:.!?-]+$")

    #: Used when the prompt is empty (an attachment-only send).
    DEFAULT_FALLBACK: Final = "New chat"

    @classmethod
    def derive(cls, prompt: str | None, fallback: str | None = None) -> str:
        """Return the display title for a conversation opened with ``prompt``.

        :param prompt: Raw user input. Newlines and runs of whitespace collapse
            — a pasted multi-line prompt otherwise stores its line breaks into a
            single-line header.
        :param fallback: Overrides :attr:`DEFAULT_FALLBACK` for an empty prompt.
        """

        normalized = cls._WHITESPACE_RUN.sub(" ", prompt or "").strip()
        if not normalized:
            return fallback if fallback is not None else cls.DEFAULT_FALLBACK
        if len(normalized) <= cls.MAX_LENGTH:
            return normalized
        # Cut on a word boundary so the title ends on a word rather than
        # mid-token, then mark it — an unmarked cut reads as a rendering bug,
        # which is exactly how the client-side one was reported ("…official Py").
        clipped = normalized[: cls.MAX_LENGTH]
        last_space = clipped.rfind(" ")
        body = clipped[:last_space] if last_space >= cls.MIN_WORD_BOUNDARY else clipped
        # Trailing punctuation before an ellipsis reads as a typo ("official,…").
        return f"{cls._TRAILING_PUNCTUATION.sub('', body)}…"
