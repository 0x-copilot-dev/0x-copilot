"""A parked `run_command` must reach the client as an approval card.

Same live symptom the filesystem branch was written for, one tool name further
along. `native_tool_approval_payloads` recognised exactly two action names — the
six deepagents filesystem tools and `call_mcp_tool` — so an interrupt raised on
a command hit the `continue`, produced no payload, emitted no
`approval_requested` event, and left the run at `waiting_for_approval` behind a
card that spins forever. Correct backend, silent client, indistinguishable from
a hang.

These tests deliberately assert on the PROJECTED payload wherever a field has to
reach a person. The producer and the client-visible wire are separated by a
per-approval-kind allow-list that deletes what it does not name, and asserting
on the dict the producer returned would pass over every one of those deletions —
which is exactly how `op_class`, `risk_level` and `grant_options` each shipped
green while the card they belong to rendered blank.
"""

from __future__ import annotations

from runtime_api.schemas import RuntimeApiEventType
from runtime_api.schemas.events import RuntimeEventPresentationProjector
from runtime_worker.stream_events import StreamOrchestrator


def _command_interrupt(**args: object) -> dict:
    """The shape LangChain's HumanInTheLoopMiddleware actually emits.

    ``review_configs`` is a SEQUENCE of ``{action_name, allowed_decisions}``
    rows, not a name-keyed mapping — `_review_configs_by_action` refuses
    anything else. A mapping here would silently yield no allowed decisions,
    and the assertion that catches that is one of the few in this file whose
    subject is the fixture rather than the code.
    """

    return {
        "action_requests": [{"name": "run_command", "args": args}],
        "review_configs": [
            {
                "action_name": "run_command",
                "allowed_decisions": ["approve", "reject"],
            }
        ],
    }


class CommandApprovalMixin:
    """Builds a parked command and walks it to the client-visible payload."""

    @staticmethod
    def produce(**args: object) -> dict:
        """The producer's own payload — what `park_for_approval`'s sibling emits."""

        payloads = StreamOrchestrator.native_tool_approval_payloads(
            interrupt_id="int-1",
            interrupt_value=_command_interrupt(**args),
        )
        assert len(payloads) == 1, (
            "a parked command produced no approval payload, so the client is "
            "never told a decision is pending and the card spins forever"
        )
        return dict(payloads[0])

    @classmethod
    def project(cls, **args: object) -> dict:
        """End to end: producer -> the append funnel -> the client-visible dict.

        `payload_for_event` is the funnel every appended row goes through, so a
        key that does not survive it does not exist as far as any client is
        concerned — replay reads envelopes back out and never re-projects.
        """

        return RuntimeEventPresentationProjector.payload_for_event(
            event_type=RuntimeApiEventType.APPROVAL_REQUESTED,
            payload=cls.produce(**args),
        )


class TestCommandInterruptBecomesAnApproval(CommandApprovalMixin):
    def test_a_command_interrupt_produces_an_approval_payload(self) -> None:
        approval = self.produce(command="pytest -q", workspace="my-project")

        assert approval["tool_name"] == "run_command"
        assert approval["command"] == "pytest -q"
        assert approval["workspace_label"] == "my-project"
        assert approval["status"] == "pending"

    def test_it_rides_the_write_gate_s_approval_kind(self) -> None:
        """The branch decision, pinned.

        `ask_a_question` is the write gate's kind and the command lane reuses
        it. A bespoke kind would land on the sibling allow-list (where
        `op_class` is absent), fall through the client's closed
        `mapApprovalKind` switch to `"unknown"`, and lose `decision_scope` on
        resume. Change this line and all three break at once, silently.
        """

        assert self.produce(command="ls")["approval_kind"] == "ask_a_question"

    def test_the_batch_contract_matches_its_sibling_branches(self) -> None:
        """One interrupt is one batch; the id is `<batch_id>:<index>` at any size."""

        approval = self.produce(command="ls")

        assert approval["approval_id"] == "int-1:0"
        assert approval["action_id"] == "int-1:0"
        assert approval["batch_id"] == "int-1"
        assert approval["batch_index"] == 0
        assert approval["allowed_decisions"] == ["approve", "reject"]

    def test_a_command_alongside_an_mcp_call_keeps_both(self) -> None:
        """The third branch must not eat the batch it was added to."""

        payloads = StreamOrchestrator.native_tool_approval_payloads(
            interrupt_id="int-9",
            interrupt_value={
                "action_requests": [
                    {"name": "run_command", "args": {"command": "pytest -q"}},
                    {
                        "name": "call_mcp_tool",
                        "args": {"server_name": "linear", "tool_name": "create_issue"},
                    },
                ],
                "review_configs": {},
            },
        )

        assert [payload["approval_kind"] for payload in payloads] == [
            "ask_a_question",
            "mcp_tool",
        ]
        assert [payload["batch_index"] for payload in payloads] == [0, 1]

    def test_a_command_with_no_text_produces_no_card(self) -> None:
        """A card naming no command cannot honestly be answered.

        The run stays parked, which is bad — but it is logged, where the
        original drop was silent. Rendering an approve button over a blank
        subject would be worse: consent to nothing is not consent.
        """

        assert (
            StreamOrchestrator.native_tool_approval_payloads(
                interrupt_id="int-2",
                interrupt_value=_command_interrupt(workspace="my-project"),
            )
            == ()
        )


class TestCommandFieldsSurviveTheProjection(CommandApprovalMixin):
    """The allow-list is a filter, and it fails by looking like it worked."""

    def test_the_verbatim_command_reaches_the_client(self) -> None:
        """The command block is the card's evidence — stripped, it renders blank."""

        projected = self.project(command="pytest -q tests/unit")

        assert projected["command"] == "pytest -q tests/unit"

    def test_a_multi_line_command_keeps_its_newlines(self) -> None:
        """The block renders `white-space: pre-wrap`; a flattened command is a
        different command from the one that will run."""

        command = "set -e\npytest -q\nruff check ."
        projected = self.project(command=command)

        assert projected["command"] == command

    def test_the_workspace_label_reaches_the_client(self) -> None:
        projected = self.project(command="ls", workspace="my-project")

        assert projected["workspace_label"] == "my-project"

    def test_the_tool_name_reaches_the_client(self) -> None:
        """Without it the card cannot be joined to the call it is holding up.

        The client joins a pending ask to its tool card either exactly, by the
        `mcp_write:<run>:<call_id>` id, or — for every other lane — by tool
        name. This lane has no such id, so the name is the only handle, and a
        stripped name leaves the command's card spinning on the run-wide signal.
        """

        assert self.project(command="ls")["tool_name"] == "run_command"

    def test_the_risk_axis_reaches_the_client(self) -> None:
        """`risk_level: high` is what makes a command un-approvable in one click.

        Carried by risk and NOT by `op_class: destructive`, which would also
        make the server withhold `allow_always` for every command forever.
        """

        projected = self.project(command="rm -rf build")

        assert projected["risk_level"] == "high"
        assert projected["op_class"] == "execute"

    def test_the_scope_offered_is_once_only(self) -> None:
        """An `argv[0]`-keyed always-grant needs a simple-command verdict, and
        this projection has no tokeniser to produce one."""

        assert self.project(command="pytest -q")["grant_options"] == ["allow_once"]

    def test_the_card_gets_a_title_naming_the_command_and_the_label(self) -> None:
        """`gate.purpose` is the only title vehicle this allow-list carries.

        Without it the card falls through its whole title chain to the generic
        "Approve this action" — over a command about to run on the user's
        machine.
        """

        projected = self.project(command="pytest -q", workspace="my-project")

        assert projected["display_title"] == "Run `pytest -q` in my-project"

    def test_the_title_collapses_a_multi_line_command_to_one_line(self) -> None:
        """The title is a label; the verbatim block below it is the evidence."""

        projected = self.project(command="set -e\npytest -q")

        assert projected["display_title"] == "Run `set -e pytest -q`"

    def test_a_command_names_no_connector(self) -> None:
        """There is no vendor behind a local command, and the card must not
        invent one."""

        assert self.project(command="ls")["connector"] is None


class TestTheLabelIsALabelNotAPath(CommandApprovalMixin):
    """`workspace` is an opaque grant label from a closed set — never a path.

    The event contract forbids a host-absolute path on this lane outright, and
    the card promises the user it names an attached folder. A model-supplied
    string wearing a path's shape satisfies neither, so it is refused rather
    than printed.
    """

    def test_an_absolute_path_is_not_shown_as_a_label(self) -> None:
        projected = self.project(command="ls", workspace="/Users/ada/secrets")

        assert "workspace_label" not in projected
        assert projected["display_title"] == "Run `ls`"

    def test_a_home_relative_path_is_not_shown_as_a_label(self) -> None:
        assert "workspace_label" not in self.project(command="ls", workspace="~/proj")

    def test_a_windows_path_is_not_shown_as_a_label(self) -> None:
        assert "workspace_label" not in self.project(
            command="ls", workspace="C:\\Users\\ada"
        )

    def test_an_absent_label_simply_omits_the_clause(self) -> None:
        projected = self.project(command="pytest -q")

        assert "workspace_label" not in projected
        assert projected["display_title"] == "Run `pytest -q`"
