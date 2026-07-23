"""Authorship-span diff tests (PRD-D1).

Covers insert / replace / delete regions, the multi-edit session invariant (each
new rev diffs against the immediately-previous rev only), the span-cap honest
fallback, and unicode/emoji offset correctness (code points, never bytes).
"""

from __future__ import annotations

from agent_runtime.surfaces_v2.revision_diff import AuthorshipSpan, RevisionDiffer


def _spans(old: str, new: str) -> list[tuple[int, int, str]]:
    return [
        (span.start, span.end, span.author)
        for span in RevisionDiffer.spans(old=old, new=new, author="user")
    ]


class TestRevisionDiffSpans:
    def test_identical_text_has_no_spans(self) -> None:
        assert RevisionDiffer.spans(old="hello", new="hello", author="user") == ()

    def test_agent_author_never_produces_spans(self) -> None:
        # Even with different text, an agent revision is unmarked by construction.
        assert RevisionDiffer.spans(old="a", new="b", author="agent") == ()

    def test_pure_insertion_marks_only_inserted_region(self) -> None:
        spans = _spans("hello world", "hello brave world")
        assert spans == [(6, 12, "user")]
        assert "hello brave world"[6:12] == "brave "

    def test_replacement_marks_the_replaced_region_of_new_text(self) -> None:
        spans = _spans("the quick fox", "the slow fox")
        assert len(spans) == 1
        start, end, author = spans[0]
        assert author == "user"
        assert "the slow fox"[start:end] == "slow"

    def test_prefix_append_marks_the_tail(self) -> None:
        spans = _spans("hi", "hi there")
        assert spans == [(2, 8, "user")]

    def test_pure_deletion_falls_back_to_whole_body_span(self) -> None:
        # No NEW extent changed authorship, but the bodies differ — mark the
        # honest whole new body rather than claiming nothing changed.
        spans = _spans("hello world", "hello")
        assert spans == [(0, 5, "user")]

    def test_full_delete_to_empty_has_no_span(self) -> None:
        assert _spans("hello", "") == []

    def test_multiple_disjoint_edits_each_marked(self) -> None:
        spans = _spans("aaa bbb ccc", "aaa XXX ccc YYY")
        assert len(spans) == 2
        for start, end, author in spans:
            assert author == "user"

    def test_multi_edit_session_diffs_against_previous_rev_only(self) -> None:
        # rev1 (agent) → rev2 (user) → rev3 (user). rev3's spans are computed vs
        # rev2, so an agent region untouched since rev2 stays unmarked.
        rev2 = "Dear team, the launch is Friday."
        rev3 = "Dear team, the launch is Monday now."
        spans = RevisionDiffer.spans(old=rev2, new=rev3, author="user")
        assert spans  # only the edited tail
        marked = "".join(rev3[s.start : s.end] for s in spans)
        assert "Monday" in marked or "now" in marked
        assert "Dear team" not in marked

    def test_span_cap_collapses_to_single_whole_body_span(self) -> None:
        # Force > _MAX_SPANS disjoint regions: alternate chars so every other one
        # differs, producing many tiny replace regions.
        old = "a" * 1000
        new = "".join("a" if i % 2 == 0 else "b" for i in range(1000))
        spans = RevisionDiffer.spans(old=old, new=new, author="user")
        assert len(spans) == 1
        assert spans[0].start == 0 and spans[0].end == len(new)

    def test_unicode_emoji_offsets_are_code_points_not_bytes(self) -> None:
        # A 4-byte emoji is ONE code-point position; the inserted "!" after it
        # must be at index 2, not index 5 (which byte offsets would give).
        spans = _spans("a😀", "a😀!")
        assert spans == [(2, 3, "user")]
        assert "a😀!"[2:3] == "!"

    def test_returns_typed_authorship_span(self) -> None:
        result = RevisionDiffer.spans(old="x", new="xy", author="user")
        assert all(isinstance(span, AuthorshipSpan) for span in result)
