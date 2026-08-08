"""Mirror of `packages/chat-surface/src/projections/conversationTitle.test.ts`.

The client derives a title at create time and this service fills an empty one at
first message. A divergence is a conversation whose name depends on which side
named it, so these cases are kept identical to the TS suite's.
"""

from __future__ import annotations

from agent_runtime.api.conversation_title import ConversationTitle


class TestConversationTitle:
    def test_keeps_a_short_prompt_verbatim(self) -> None:
        assert (
            ConversationTitle.derive("are there any csv in the folder")
            == "are there any csv in the folder"
        )

    def test_collapses_newlines_and_whitespace_runs(self) -> None:
        # A pasted multi-line prompt otherwise stores its line breaks into a
        # single-line header.
        assert ConversationTitle.derive("fix   the\n\nbuild  please") == (
            "fix the build please"
        )

    def test_falls_back_when_the_prompt_is_empty(self) -> None:
        assert ConversationTitle.derive("   ") == "New chat"
        assert ConversationTitle.derive(None) == "New chat"
        assert ConversationTitle.derive("", "First run") == "First run"

    def test_cuts_on_a_word_boundary_and_marks_the_cut(self) -> None:
        # The reported string: 60 chars, mid-word, unmarked — "…official Py".
        prompt = (
            "Use the web_search tool exactly once to find the official Python "
            "documentation page for math.isqrt."
        )
        title = ConversationTitle.derive(prompt)
        assert title == "Use the web_search tool exactly once to find the official…"
        assert not title.endswith("Py")
        assert title.endswith("…")

    def test_never_exceeds_the_cap(self) -> None:
        assert len(ConversationTitle.derive("x" * 500)) <= 61

    def test_cuts_hard_when_there_is_no_usable_word_boundary(self) -> None:
        # One unbroken token — a path, a URL, a hash. A word-boundary cut would
        # leave almost nothing, so the cap wins and the ellipsis still marks it.
        assert ConversationTitle.derive("a" * 120) == f"{'a' * 60}…"

    def test_leaves_no_dangling_punctuation_before_the_ellipsis(self) -> None:
        prompt = (
            "Please review the deployment checklist, the runbook, and the "
            "rollback plan before Friday."
        )
        assert ",…" not in ConversationTitle.derive(prompt)
