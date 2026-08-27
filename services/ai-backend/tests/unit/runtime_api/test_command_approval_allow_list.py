"""The command lane's keys must survive the allow-list that actually runs.

There are two approval allow-lists and `approval_kind` picks between them.
`_approval_requested_payload` is the one you find first, and for a parked
command it never reads its own key tuple: it early-returns into
`_ask_a_question_requested_payload`, because the command lane reuses the write
gate's `approval_kind` (the only shape whose resume carries `decision_scope`,
and the only branch the client's closed `mapApprovalKind` switch already
accepts).

So adding `command` / `workspace_label` to the first tuple would have compiled,
passed review, ticked the checklist item — and changed nothing, with the card
still rendering blank. That is not a hypothetical failure mode: `op_class`,
`risk_level` and `grant_options` were each dropped by exactly this split and had
to be added to the ask branch afterwards, and `category` / `vendor` are recorded
on the client as still missing for the same reason.

These tests pin the branch, not the card. If this file goes red, a producer that
is still perfectly correct is talking to a client that hears nothing.
"""

from __future__ import annotations

from runtime_api.schemas.events import RuntimeEventPresentationProjector as _P


def _command_payload(**extra: object) -> dict[str, object]:
    """A parked command as `_CommandApproval` emits it, trimmed to essentials."""

    return {
        "approval_id": "int-1:0",
        "approval_kind": "ask_a_question",
        "tool_name": "run_command",
        "command": "pytest -q",
        "workspace_label": "my-project",
        "op_class": "execute",
        "risk_level": "high",
        "message": "Run `pytest -q` in my-project",
        "question": "Run `pytest -q` in my-project",
        "status": "pending",
        "grant_options": ["allow_once"],
        **extra,
    }


class TestTheCommandLaneTakesTheAskBranch:
    def test_a_command_entering_the_sibling_projection_is_rerouted(self) -> None:
        """Entering at `_approval_requested_payload` still lands on the ask list.

        This is the whole trap in one assertion: the caller dispatches on event
        type alone, so every approval enters here, and the kind is what decides
        which tuple filters it.
        """

        projected = _P._approval_requested_payload(_command_payload())

        assert projected["command"] == "pytest -q"
        assert projected["workspace_label"] == "my-project"

    def test_the_sibling_tuple_is_demonstrably_not_the_one_that_ran(self) -> None:
        """`path` is on the sibling list and only there.

        If it survives, the early-return did not happen and every conclusion in
        this file is about a branch production does not take.
        """

        projected = _P._approval_requested_payload(_command_payload(path="/tmp"))

        assert "path" not in projected


class TestCommandKeysSurviveTheAskProjection:
    def test_the_command_survives(self) -> None:
        """The verbatim block is the card's evidence and its approve unlock."""

        assert (
            _P._ask_a_question_requested_payload(_command_payload())["command"]
            == "pytest -q"
        )

    def test_the_workspace_label_survives(self) -> None:
        assert (
            _P._ask_a_question_requested_payload(_command_payload())["workspace_label"]
            == "my-project"
        )

    def test_the_tool_name_survives(self) -> None:
        """The only handle a non-`mcp_write:` lane has for naming its own call."""

        assert (
            _P._ask_a_question_requested_payload(_command_payload())["tool_name"]
            == "run_command"
        )

    def test_a_non_string_command_is_dropped_rather_than_coerced(self) -> None:
        """`_text` refuses anything that is not a non-empty string.

        A card that printed `{'cmd': ...}` where a command belongs would be
        showing the reader something no shell will ever run.
        """

        projected = _P._ask_a_question_requested_payload(
            _command_payload(command={"cmd": "rm -rf /"}, workspace_label=17)
        )

        assert "command" not in projected
        assert "workspace_label" not in projected


class TestTheOtherTwoLanesAreUnchanged:
    """Three lanes ride this branch; two of them predate the command.

    Neither the `ask_a_question` tool nor `ToolAccessGate` stamps any of the
    three new keys, so this widening is inert for them — which is the reason it
    can land in a phase that ships dark.
    """

    def test_a_plain_question_gains_no_command_keys(self) -> None:
        projected = _P._ask_a_question_requested_payload(
            {
                "approval_id": "ask_a_question:run_1:abcd",
                "approval_kind": "ask_a_question",
                "header": "Which environment?",
                "question": "Which environment should I deploy to?",
                "options": ["staging", "production"],
            }
        )

        assert projected["question"] == "Which environment should I deploy to?"
        assert projected["options"] == [{"label": "staging"}, {"label": "production"}]
        assert not {"command", "workspace_label", "tool_name"} & set(projected)

    def test_a_parked_write_keeps_its_own_shape(self) -> None:
        projected = _P._ask_a_question_requested_payload(
            {
                "approval_id": "mcp_write:run_1:call_1",
                "approval_kind": "ask_a_question",
                "server_name": "linear",
                "question": "Allow Linear to run create_issue?",
                "grant_options": ["allow_once", "allow_always"],
                "gate": {"v": 1, "purpose": "Linear · create_issue"},
            }
        )

        assert projected["display_title"] == "Linear · create_issue"
        assert projected["connector"] == "linear"
        assert projected["grant_options"] == ["allow_once", "allow_always"]
        assert "command" not in projected
