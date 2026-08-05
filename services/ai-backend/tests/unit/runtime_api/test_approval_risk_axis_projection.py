"""The risk axis must survive the client-visible projection.

A parked write borrows the ``ask_a_question`` wire shape so it can reuse the
approval resume plumbing, and that path has its own allow-list. It dropped both
fields that say how dangerous the call is, which is why the desktop card's
irreversible lane — no one-click Approve, the "can't be undone" chip — could not
fire for any payload a user was able to provoke. Every client-side test of that
lane passed, on fixtures, over a branch production never took.

These pin the two fields at the boundary that deleted them. They are about the
PROJECTION, not the card: if this file goes red, the client's safety lane has
gone dark again regardless of what the TypeScript suite says.
"""

from __future__ import annotations

from runtime_api.schemas.events import RuntimeEventPresentationProjector as _P


def _ask_payload(**extra: object) -> dict[str, object]:
    return {
        "approval_id": "mcp_write:run_1:call_1",
        "approval_kind": "ask_a_question",
        "header": "Approve write",
        "question": "Allow Linear to run save_issue?",
        **extra,
    }


class TestAskAQuestionRiskAxis:
    def test_op_class_survives_the_projection(self) -> None:
        """The PDP's verdict is the ONLY field that can say ``destructive``."""

        projected = _P._ask_a_question_requested_payload(
            _ask_payload(op_class="destructive")
        )

        assert projected["op_class"] == "destructive"

    def test_risk_level_survives_the_projection(self) -> None:
        """``high`` marks a write that reaches the user's real files.

        The distinction is load-bearing for the copy the card shows: "you can
        undo this from the connector" is true of a Linear issue (``medium``) and
        a lie about a file this app has already written (``high``).
        """

        projected = _P._ask_a_question_requested_payload(
            _ask_payload(risk_level="high")
        )

        assert projected["risk_level"] == "high"

    def test_each_lane_carries_only_its_own_signal(self) -> None:
        """Neither field alone covers both lanes, so neither may be dropped.

        The MCP gate stamps ``op_class`` and no ``risk_level``; the filesystem
        lane stamps ``risk_level`` and no ``op_class``. A projection that keeps
        one and drops the other silently under-protects the other lane.
        """

        gate = _P._ask_a_question_requested_payload(
            _ask_payload(op_class="destructive")
        )
        filesystem = _P._ask_a_question_requested_payload(
            _ask_payload(risk_level="high")
        )

        assert gate["op_class"] == "destructive"
        assert "risk_level" not in gate
        assert filesystem["risk_level"] == "high"
        assert "op_class" not in filesystem

    def test_absent_axis_is_omitted_rather_than_defaulted(self) -> None:
        """An unstated axis stays unstated.

        The client fails OPEN on absence — a missing axis means the ordinary
        one-click Approve — so inventing a value here would be the projection
        deciding a safety question it has no facts about.
        """

        projected = _P._ask_a_question_requested_payload(_ask_payload())

        assert "op_class" not in projected
        assert "risk_level" not in projected

    def test_the_question_payload_is_still_projected(self) -> None:
        """Widening the allow-list must not disturb what it already carried."""

        projected = _P._ask_a_question_requested_payload(
            _ask_payload(op_class="destructive", risk_level="high")
        )

        assert projected["header"] == "Approve write"
        assert projected["question"] == "Allow Linear to run save_issue?"
        assert projected["approval_id"] == "mcp_write:run_1:call_1"

    def test_a_hostile_axis_value_cannot_smuggle_a_payload(self) -> None:
        """Both fields are producer-side enums, but the wire is still a wire.

        They go through the same ``_text`` coercion as every other string on
        this path, so a non-string is dropped rather than forwarded to a client
        that will lower-case and compare it.
        """

        projected = _P._ask_a_question_requested_payload(
            _ask_payload(op_class={"$ne": None}, risk_level=["high"])
        )

        assert "op_class" not in projected
        assert "risk_level" not in projected
