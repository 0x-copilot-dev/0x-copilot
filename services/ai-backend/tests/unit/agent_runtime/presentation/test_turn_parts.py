"""Behaviour of the assistant-turn parts fold.

The TypeScript differential proves the two folds AGREE. It cannot prove either
is right -- the "run ends immediately after a tool call" case was wrong in both
implementations simultaneously and the differential passed cleanly. These tests
pin the behaviour by value.
"""

from __future__ import annotations

from agent_runtime.presentation.turn_parts import (
    TurnPartKind,
    TurnPartsProjection,
    TurnPartStatus,
)


def delta(sequence_no: int, text: str) -> dict[str, object]:
    return {
        "sequence_no": sequence_no,
        "event_type": "model_delta",
        "payload": {"delta": text},
    }


def thinking(sequence_no: int, text: str) -> dict[str, object]:
    return {
        "sequence_no": sequence_no,
        "event_type": "reasoning_summary_delta",
        "payload": {"delta": text},
    }


def tool(sequence_no: int) -> dict[str, object]:
    return {
        "sequence_no": sequence_no,
        "event_type": "tool_call_started",
        "payload": {},
    }


def final(sequence_no: int, text: str) -> dict[str, object]:
    return {
        "sequence_no": sequence_no,
        "event_type": "final_response",
        "payload": {"message": text},
    }


def texts(events: list[dict[str, object]]) -> list[str]:
    return [part.text for part in TurnPartsProjection.fold(events)]


class TestOrderedTurn:
    def test_text_after_a_tool_call_opens_a_new_part(self) -> None:
        parts = TurnPartsProjection.fold(
            [delta(1, "Checking."), tool(2), delta(3, "It shipped.")]
        )
        assert [(part.kind, part.text, part.seq) for part in parts] == [
            (TurnPartKind.TEXT, "Checking.", 1),
            (TurnPartKind.TEXT, "It shipped.", 3),
        ]

    def test_final_response_does_not_overwrite_pre_tool_prose(self) -> None:
        assert texts(
            [delta(1, "Let me look."), tool(2), delta(3, "Sh"), final(4, "Shipped.")]
        ) == ["Let me look.", "Shipped."]

    def test_final_response_after_a_card_opens_its_own_part(self) -> None:
        # No text streamed after the tool, so the tail text part is still the
        # PRE-tool sentence. Reconciling into it would destroy it.
        assert texts([delta(1, "Checking."), tool(2), final(3, "Done.")]) == [
            "Checking.",
            "Done.",
        ]

    def test_plain_turn_reconciles_into_its_only_part(self) -> None:
        assert texts([delta(1, "Hi"), final(2, "Hi there.")]) == ["Hi there."]

    def test_every_thinking_span_survives_two_tool_batches(self) -> None:
        parts = TurnPartsProjection.fold(
            [
                thinking(1, "First check CI."),
                tool(2),
                thinking(3, "Now the deploy log."),
                tool(4),
                delta(5, "All good."),
            ]
        )
        assert [(part.kind, part.text) for part in parts] == [
            (TurnPartKind.REASONING, "First check CI."),
            (TurnPartKind.REASONING, "Now the deploy log."),
            (TurnPartKind.TEXT, "All good."),
        ]

    def test_reasoning_cap_lands_on_the_open_span_not_the_first(self) -> None:
        parts = TurnPartsProjection.fold(
            [
                thinking(1, "span one"),
                tool(2),
                thinking(3, "span two"),
                {
                    "sequence_no": 4,
                    "event_type": "reasoning_summary",
                    "payload": {"summary": "span two, capped"},
                },
            ]
        )
        assert [part.text for part in parts] == ["span one", "span two, capped"]

    def test_incidental_frames_do_not_split_a_part(self) -> None:
        # Splitting here would tear a GFM table into two halves that each parse
        # as an invalid document.
        assert texts(
            [
                delta(1, "| a | b |\n"),
                {"sequence_no": 2, "event_type": "todo_list_updated", "payload": {}},
                delta(3, "| - | - |"),
            ]
        ) == ["| a | b |\n| - | - |"]

    def test_orders_by_sequence_no_not_arrival(self) -> None:
        assert texts([delta(5, "second"), tool(3), delta(1, "first")]) == [
            "first",
            "second",
        ]

    def test_subagent_deltas_never_enter_the_main_reply(self) -> None:
        assert texts(
            [
                delta(1, "Delegating."),
                {
                    "sequence_no": 2,
                    "event_type": "model_delta",
                    "subagent_id": "sub-1",
                    "payload": {"delta": "child chatter"},
                },
                delta(3, " Done."),
            ]
        ) == ["Delegating. Done."]

    def test_dedupes_by_event_id_on_replay(self) -> None:
        duplicated = {**delta(1, "once"), "event_id": "e1"}
        assert texts([duplicated, duplicated]) == ["once"]

    def test_open_part_is_running_until_the_turn_finalises(self) -> None:
        parts = TurnPartsProjection.fold(
            [delta(1, "Checking."), tool(2), delta(3, "…")]
        )
        assert parts[0].status is TurnPartStatus.COMPLETE
        assert parts[1].status is TurnPartStatus.RUNNING

    def test_empty_until_the_agent_produces_prose(self) -> None:
        assert TurnPartsProjection.fold([]) == ()
        assert (
            TurnPartsProjection.fold(
                [{"sequence_no": 1, "event_type": "run_started", "payload": {}}]
            )
            == ()
        )


class TestContentBlocks:
    def test_wire_shape_matches_what_the_client_reads(self) -> None:
        blocks = TurnPartsProjection.content_blocks(
            [delta(1, "Checking."), tool(2), final(3, "Done.")]
        )
        assert blocks == (
            {
                "type": "text",
                "text": "Checking.",
                "seq": 1,
                "status": {"type": "complete"},
            },
            {
                "type": "text",
                "text": "Done.",
                "seq": 3,
                "status": {"type": "complete"},
            },
        )
