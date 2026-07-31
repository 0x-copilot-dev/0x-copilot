"""A parked filesystem call must reach the client as an approval.

The live symptom this pins: asking for an ungranted folder left the `ls` tool
card spinning forever. The backend was correct — the run really was at
`waiting_for_approval` — but `native_tool_approval_payloads` filtered every
action that was not `call_mcp_tool`, so no `approval_requested` payload was
built, no event was emitted, and the client was never told a decision existed.
Correct backend, silent client, and to the user indistinguishable from a hang.
"""

from __future__ import annotations

import pytest

from runtime_worker.stream_events import StreamOrchestrator


def _interrupt(name: str, args: dict) -> dict:
    """The shape LangChain's HumanInTheLoopMiddleware actually emits."""

    return {
        "action_requests": [{"name": name, "args": args}],
        "review_configs": {name: ["approve", "reject"]},
    }


class TestFilesystemInterruptBecomesAnApproval:
    def test_an_ls_interrupt_produces_an_approval_payload(self) -> None:
        payloads = StreamOrchestrator.native_tool_approval_payloads(
            interrupt_id="int-1",
            interrupt_value=_interrupt("ls", {"path": "/Users/ada/Downloads"}),
        )
        assert len(payloads) == 1, (
            "a parked filesystem call produced no approval payload, so the "
            "client is never told and the tool card spins forever"
        )
        approval = payloads[0]
        assert approval["approval_kind"] == "filesystem_access"
        assert approval["path"] == "/Users/ada/Downloads"
        assert approval["operation"] == "read"
        assert approval["read_only"] is True
        # The card has to be able to NAME the folder, or it asks about nothing.
        assert approval["display_name"] == "Downloads"

    def test_a_write_is_marked_higher_risk_than_a_read(self) -> None:
        read = StreamOrchestrator.native_tool_approval_payloads(
            interrupt_id="i",
            interrupt_value=_interrupt("read_file", {"file_path": "/a/b.txt"}),
        )[0]
        write = StreamOrchestrator.native_tool_approval_payloads(
            interrupt_id="i",
            interrupt_value=_interrupt("write_file", {"file_path": "/a/b.txt"}),
        )[0]
        assert read["risk_level"] == "low"
        assert write["risk_level"] == "high"
        assert write["read_only"] is False

    def test_a_pathless_bulk_call_says_so_rather_than_naming_nothing(self) -> None:
        """deepagents fires a pathless grep unconditionally — it could touch anything."""

        approval = StreamOrchestrator.native_tool_approval_payloads(
            interrupt_id="i", interrupt_value=_interrupt("grep", {"pattern": "x"})
        )[0]
        assert approval["path"] == "anywhere on this computer"

    def test_it_carries_the_same_batch_contract_as_an_mcp_approval(self) -> None:
        """Reuses the existing batch/resume machinery rather than a parallel one."""

        approval = StreamOrchestrator.native_tool_approval_payloads(
            interrupt_id="int-9", interrupt_value=_interrupt("ls", {"path": "/x"})
        )[0]
        assert approval["batch_id"] == "int-9"
        assert approval["batch_index"] == 0
        assert approval["approval_id"] == "int-9:0"
        assert approval["status"] == "pending"
        # The decisions list is normalised by the SAME `_review_configs_by_action`
        # helper the MCP branch uses and is covered there; what this test owns
        # is that the field is present and typed, not its vocabulary.
        assert isinstance(approval["allowed_decisions"], list)

    @pytest.mark.parametrize("name", ["call_mcp_tool", "some_other_tool"])
    def test_non_filesystem_actions_are_untouched(self, name: str) -> None:
        """The MCP branch must keep behaving exactly as before."""

        payloads = StreamOrchestrator.native_tool_approval_payloads(
            interrupt_id="i", interrupt_value=_interrupt(name, {})
        )
        assert all(p.get("approval_kind") != "filesystem_access" for p in payloads)


class TestThePayloadSurvivesTheApiProjection:
    """The producer being right is not enough — the projection must carry it.

    `_approval_requested_payload` is an ALLOW-LIST. A field it does not name is
    silently dropped, which is how `workspace_grant` was undeliverable earlier
    in this program: correct producer, correct parser, field deleted between
    them, and no test on either side could see it.
    """

    def test_path_and_operation_reach_the_client(self) -> None:
        from runtime_api.schemas.events import RuntimeEventPresentationProjector

        approval = StreamOrchestrator.native_tool_approval_payloads(
            interrupt_id="int-1",
            interrupt_value=_interrupt("ls", {"path": "/Users/ada/Downloads"}),
        )[0]
        projected = RuntimeEventPresentationProjector._approval_requested_payload(
            approval
        )
        assert projected.get("path") == "/Users/ada/Downloads", (
            "the card cannot name the folder it is asking about"
        )
        assert projected.get("operation") == "read"
        assert projected.get("approval_kind") == "filesystem_access"
