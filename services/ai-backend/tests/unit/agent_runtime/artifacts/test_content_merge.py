"""The rebase that turns a lost compare-and-append into a retry.

The guard that refuses a stale revision is correct and stays. What these pin is
the half that decides whether the user's request quietly does nothing: a hand
edit to row 2 and an agent appending row 40 are not in conflict, and treating
them as one leaves the agent with nothing to do but apologise.

The bar is asymmetric on purpose. A refused merge costs a retry; a WRONG merge
silently corrupts a durable object the user believes they edited by hand. So
every case where the answer is "it depends" must come back ambiguous.
"""

from __future__ import annotations

from agent_runtime.artifacts.content_merge import (
    ArtifactMergeStatus,
    ThreeWayTextMerge,
)

BASE = b"id,name,team\n1,alice,core\n2,bob,core\n3,carol,platform\n"


class MergeMixin:
    @staticmethod
    def merge(base: bytes, current: bytes, proposed: bytes):
        return ThreeWayTextMerge.merge(base=base, current=current, proposed=proposed)

    @staticmethod
    def rows(*lines: str) -> bytes:
        return ("id,name,team\n" + "".join(f"{line}\n" for line in lines)).encode()


class TestDisjointChanges(MergeMixin):
    def test_the_reported_case_a_hand_edit_plus_an_appended_row(self) -> None:
        """AS-6, exactly: the user fixes a cell, then asks for one more row."""

        current = self.rows("1,ALICE,core", "2,bob,core", "3,carol,platform")
        proposed = self.rows(
            "1,alice,core", "2,bob,core", "3,carol,platform", "4,dan,platform"
        )

        result = self.merge(BASE, current, proposed)

        assert result.status is ArtifactMergeStatus.MERGED
        # Both survive: the hand edit is not reverted to what the agent read,
        # and the agent's row is not dropped to protect it.
        assert result.content == self.rows(
            "1,ALICE,core", "2,bob,core", "3,carol,platform", "4,dan,platform"
        )

    def test_edits_to_different_rows_both_land(self) -> None:
        current = self.rows("1,ALICE,core", "2,bob,core", "3,carol,platform")
        proposed = self.rows("1,alice,core", "2,bob,core", "3,carol,DESIGN")

        result = self.merge(BASE, current, proposed)

        assert result.content == self.rows(
            "1,ALICE,core", "2,bob,core", "3,carol,DESIGN"
        )

    def test_a_prepend_and_an_append_do_not_collide(self) -> None:
        current = BASE + b"4,dan,platform\n"
        proposed = b"# generated\n" + BASE

        result = self.merge(BASE, current, proposed)

        assert result.status is ArtifactMergeStatus.MERGED
        assert result.content == b"# generated\n" + BASE + b"4,dan,platform\n"

    def test_a_deletion_by_one_side_and_an_append_by_the_other(self) -> None:
        current = self.rows("1,alice,core", "3,carol,platform")
        proposed = BASE + b"4,dan,platform\n"

        result = self.merge(BASE, current, proposed)

        assert result.content == self.rows(
            "1,alice,core", "3,carol,platform", "4,dan,platform"
        )

    def test_no_trailing_newline_round_trips_byte_exactly(self) -> None:
        """Rejoining lines must reproduce bytes, not normalise them."""

        base = b"a\nb"
        result = self.merge(base, b"A\nb", b"a\nb\nc")

        assert result.content == b"A\nb\nc"

    def test_the_agent_having_already_folded_in_the_edit_is_not_a_conflict(
        self,
    ) -> None:
        """Same change on both sides is one change; refusing it would be absurd."""

        current = self.rows("1,ALICE,core", "2,bob,core", "3,carol,platform")
        proposed = self.rows(
            "1,ALICE,core", "2,bob,core", "3,carol,platform", "4,dan,platform"
        )

        result = self.merge(BASE, current, proposed)

        assert result.status is ArtifactMergeStatus.MERGED
        assert result.content == proposed

    def test_a_side_that_changed_nothing_yields_the_other(self) -> None:
        appended = BASE + b"4,dan,platform\n"

        assert self.merge(BASE, BASE, appended).content == appended
        assert self.merge(BASE, appended, BASE).content == appended


class TestAmbiguousChanges(MergeMixin):
    def test_both_sides_rewriting_one_row_is_refused(self) -> None:
        """Two answers for one line, and no basis in the data to pick either."""

        current = self.rows("1,ALICE,core", "2,bob,core", "3,carol,platform")
        proposed = self.rows("1,alicia,core", "2,bob,core", "3,carol,platform")

        result = self.merge(BASE, current, proposed)

        assert result.status is ArtifactMergeStatus.AMBIGUOUS
        assert result.content is None

    def test_two_different_rows_appended_at_the_same_anchor_are_refused(self) -> None:
        """Nothing in either document says which of the two goes first."""

        result = self.merge(BASE, BASE + b"4,dan,platform\n", BASE + b"4,erin,core\n")

        assert result.status is ArtifactMergeStatus.AMBIGUOUS

    def test_a_deletion_the_other_side_edited_is_refused(self) -> None:
        """Keep-my-edit versus drop-the-row is a decision only a human owns."""

        current = self.rows("1,alice,core", "3,carol,platform")
        proposed = self.rows("1,alice,core", "2,BOB,core", "3,carol,platform")

        result = self.merge(BASE, current, proposed)

        assert result.status is ArtifactMergeStatus.AMBIGUOUS

    def test_a_wholesale_rewrite_over_any_hand_edit_is_refused(self) -> None:
        """The dangerous shape: the agent's document would swallow the edit."""

        current = self.rows("1,ALICE,core", "2,bob,core", "3,carol,platform")
        proposed = b"name\nalice\nbob\ncarol\n"

        result = self.merge(BASE, current, proposed)

        assert result.status is ArtifactMergeStatus.AMBIGUOUS


class TestUnsupportedContent(MergeMixin):
    def test_undecodable_bytes_are_not_a_conflict(self) -> None:
        """A different fact from an overlap, and it must not be reported as one."""

        result = self.merge(b"\xff\xfe\x00binary", BASE, BASE)

        assert result.status is ArtifactMergeStatus.UNSUPPORTED
        assert result.content is None

    def test_a_document_beyond_the_line_cap_is_declined(self) -> None:
        """The differ's quadratic worst case is not paid inside a run loop."""

        huge = b"row\n" * (ThreeWayTextMerge.Limits.MAX_LINES + 1)

        result = self.merge(huge, huge + b"x\n", huge + b"y\n")

        assert result.status is ArtifactMergeStatus.UNSUPPORTED

    def test_the_cap_is_a_ceiling_not_a_refusal_of_large_files(self) -> None:
        """A document that reaches the cap exactly still merges."""

        large = b"row\n" * (ThreeWayTextMerge.Limits.MAX_LINES - 1)

        result = self.merge(large, large, large + b"tail\n")

        assert result.status is ArtifactMergeStatus.MERGED
        assert result.content == large + b"tail\n"
