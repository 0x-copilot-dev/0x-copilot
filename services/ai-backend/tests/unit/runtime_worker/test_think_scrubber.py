"""Behaviour of the inline reasoning-tag scrubber.

The failure this guards is not a cosmetic one: a leaked ``<think>`` block puts
the model's raw chain of thought into the user's answer. Every case below is a
way that has actually happened to someone.
"""

from __future__ import annotations

from runtime_worker.think_scrubber import StreamingThinkScrubber


def run(deltas: list[str]) -> tuple[str, str]:
    """Feed a whole stream, returning (visible, reasoning)."""
    scrubber = StreamingThinkScrubber()
    visible: list[str] = []
    reasoning: list[str] = []
    for delta in deltas:
        out = scrubber.feed(delta)
        visible.append(out.visible)
        reasoning.append(out.reasoning)
    tail = scrubber.flush()
    visible.append(tail.visible)
    reasoning.append(tail.reasoning)
    return "".join(visible), "".join(reasoning)


class TestTagSplitAcrossDeltas:
    def test_the_case_that_motivates_the_state_machine(self) -> None:
        # A per-delta regex erases delta 0, so the open tag is never seen and
        # the reasoning is published as answer text.
        visible, reasoning = run(
            ["<think>", "Let me check their config", "</think>", "Done."]
        )
        assert visible == "Done."
        assert reasoning == "Let me check their config"

    def test_tag_split_mid_token(self) -> None:
        visible, reasoning = run(["<th", "ink>", "hidden", "</thi", "nk>", "shown"])
        assert visible == "shown"
        assert reasoning == "hidden"

    def test_single_character_deltas(self) -> None:
        stream = "<think>abc</think>xyz"
        visible, reasoning = run(list(stream))
        assert visible == "xyz"
        assert reasoning == "abc"


class TestFalsePositives:
    def test_prose_mentioning_the_tag_is_not_suppressed(self) -> None:
        # The single most common false positive: the model talks ABOUT the tag.
        visible, reasoning = run(["Please use <think> tags here, then continue."])
        assert visible == "Please use <think> tags here, then continue."
        assert reasoning == ""

    def test_a_tag_mid_sentence_does_not_eat_the_rest_of_the_reply(self) -> None:
        visible, _ = run(["The answer is <thinking> in that sense the best one."])
        assert visible.endswith("the best one.")

    def test_a_lone_angle_bracket_is_prose(self) -> None:
        visible, reasoning = run(["5 < 7 and 8 > 2"])
        assert visible == "5 < 7 and 8 > 2"
        assert reasoning == ""

    def test_partial_tag_that_never_completes_is_released(self) -> None:
        # Held back mid-stream, then flushed as the prose it turned out to be.
        visible, reasoning = run(["all good <th"])
        assert visible == "all good <th"
        assert reasoning == ""


class TestBoundaryRule:
    def test_tag_after_a_newline_opens_a_block(self) -> None:
        visible, reasoning = run(["Intro.\n<think>hidden</think>tail"])
        assert visible == "Intro.\ntail"
        assert reasoning == "hidden"

    def test_tag_after_only_whitespace_on_the_line_opens_a_block(self) -> None:
        visible, reasoning = run(["Intro.\n   <think>hidden</think>"])
        assert reasoning == "hidden"
        assert "hidden" not in visible


class TestVariantsAndMalformed:
    def test_every_declared_tag_variant(self) -> None:
        for tag in StreamingThinkScrubber.TAGS:
            visible, reasoning = run([f"<{tag}>secret</{tag}>answer"])
            assert reasoning == "secret", tag
            assert visible == "answer", tag

    def test_tags_are_case_insensitive(self) -> None:
        visible, reasoning = run(["<THINK>secret</Think>answer"])
        assert reasoning == "secret"
        assert visible == "answer"

    def test_mismatched_close_still_closes_the_block(self) -> None:
        # Malformed, but treating it as still-open would swallow the whole reply.
        visible, reasoning = run(["<think>secret</thinking>answer"])
        assert visible == "answer"
        assert reasoning == "secret"

    def test_unclosed_block_never_leaks_as_visible_text(self) -> None:
        # A truncated stream inside a block must not dump the chain of thought.
        visible, reasoning = run(["<think>never closed and the stream died"])
        assert visible == ""
        assert reasoning == "never closed and the stream died"

    def test_multiple_blocks_in_one_stream(self) -> None:
        visible, reasoning = run(["<think>one</think>A\n<think>two</think>B"])
        assert visible == "A\nB"
        assert reasoning == "onetwo"


class TestPassThrough:
    def test_text_with_no_tags_is_untouched(self) -> None:
        text = "Just a normal answer, with punctuation < and > too."
        visible, reasoning = run([text])
        assert visible == text
        assert reasoning == ""

    def test_empty_deltas_are_inert(self) -> None:
        assert not StreamingThinkScrubber().feed("")

    def test_reset_clears_a_hung_block(self) -> None:
        scrubber = StreamingThinkScrubber()
        scrubber.feed("<think>interrupted")
        assert scrubber.in_block
        scrubber.reset()
        assert not scrubber.in_block
        # The next turn must not be swallowed by the previous turn's open block.
        assert scrubber.feed("fresh answer").visible == "fresh answer"

    def test_buffer_cannot_grow_without_bound_inside_a_block(self) -> None:
        scrubber = StreamingThinkScrubber()
        scrubber.feed("<think>")
        for _ in range(500):
            scrubber.feed("more reasoning text ")
        # Only a possible partial close tag may ever be held back.
        assert len(scrubber._buf) < 32
