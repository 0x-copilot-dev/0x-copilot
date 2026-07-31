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

    def test_both_grant_options_and_their_scope_reach_the_client(self) -> None:
        """Advertising `allow_always` without shipping its scope is the bug.

        A client that receives the option but not the folder cannot name what it
        is about to attach, so it either hides the control or attaches something
        it guessed. Both halves have to survive the allow-list together.
        """

        from runtime_api.schemas.events import RuntimeEventPresentationProjector

        approval = StreamOrchestrator.native_tool_approval_payloads(
            interrupt_id="int-1",
            interrupt_value=_interrupt("ls", {"path": "/Users/ada/Downloads"}),
        )[0]
        projected = RuntimeEventPresentationProjector._approval_requested_payload(
            approval
        )
        assert projected.get("grant_options") == ["allow_once", "allow_always"]
        assert projected.get("grant_scope") == {
            "path": "/Users/ada/Downloads",
            "folder_name": "Downloads",
            "platform": "posix",
            "mode": "read_only",
        }


class TestOnceAndAlwaysAreDifferentPromises:
    """The card must be able to offer both, and must not conflate them.

    Approving used to persist nothing, so the same folder asked again on the
    next run. Persisting on every approve would be the opposite error: one click
    silently becoming durable access. So `allow_always` is a SEPARATE option, and
    the folder it would attach is named explicitly rather than re-derived by
    whoever reads the card.
    """

    @staticmethod
    def _payload(name: str, args: dict) -> dict:
        return StreamOrchestrator.native_tool_approval_payloads(
            interrupt_id="i", interrupt_value=_interrupt(name, args)
        )[0]

    def test_a_directory_read_offers_both_options(self) -> None:
        approval = self._payload("ls", {"path": "/Users/ada/Reports"})
        assert approval["grant_options"] == ["allow_once", "allow_always"]

    def test_the_durable_scope_is_the_folder_on_the_card(self) -> None:
        approval = self._payload("ls", {"path": "/Users/ada/Reports"})
        assert approval["grant_scope"]["path"] == approval["path"]

    def test_a_file_read_scopes_to_its_container_and_no_further(self) -> None:
        """A grant covers a folder, so a file read attaches its directory.

        And ONLY its directory: `/a/b/reports/q3.csv` must never become a grant
        on `/a/b`, which is a different and much larger promise.
        """

        approval = self._payload("read_file", {"file_path": "/a/b/reports/q3.csv"})
        assert approval["grant_scope"]["path"] == "/a/b/reports"
        assert approval["grant_scope"]["folder_name"] == "reports"

    def test_a_windows_path_keeps_its_own_grammar(self) -> None:
        approval = self._payload("read_file", {"file_path": "C:\\Users\\ada\\q3.csv"})
        assert approval["grant_scope"]["path"] == "C:\\Users\\ada"
        assert approval["grant_scope"]["platform"] == "windows"

    def test_the_durable_option_only_ever_asks_for_read_access(self) -> None:
        """This lane widens READS. Host writes stay on the staged C2 lane."""

        approval = self._payload("ls", {"path": "/Users/ada/Reports"})
        assert approval["grant_scope"]["mode"] == "read_only"


class TestWhereTheDurableOptionIsWithheld:
    """Every refusal degrades to "ask again next time", never to a wider grant."""

    @staticmethod
    def _payload(name: str, args: dict) -> dict:
        return StreamOrchestrator.native_tool_approval_payloads(
            interrupt_id="i", interrupt_value=_interrupt(name, args)
        )[0]

    def test_a_pathless_bulk_call_cannot_be_made_permanent(self) -> None:
        """ "Anywhere on this computer" is not a folder anyone can attach."""

        approval = self._payload("grep", {"pattern": "x"})
        assert approval["grant_options"] == ["allow_once"]
        assert "grant_scope" not in approval

    def test_a_volume_root_listing_cannot_be_made_permanent(self) -> None:
        approval = self._payload("ls", {"path": "/"})
        assert approval["grant_options"] == ["allow_once"]
        assert "grant_scope" not in approval

    def test_a_top_level_file_read_never_widens_to_the_whole_drive(self) -> None:
        """`read_file("/etc")`'s container is `/` — refuse rather than offer it."""

        approval = self._payload("read_file", {"file_path": "/etc"})
        assert approval["grant_options"] == ["allow_once"]
        assert "grant_scope" not in approval

    def test_a_write_never_offers_a_durable_grant(self) -> None:
        """Rule 5 denies host writes outright; a write must not mint authority."""

        approval = self._payload("write_file", {"file_path": "/a/b/report.csv"})
        assert approval["grant_options"] == ["allow_once"]
        assert "grant_scope" not in approval

    def test_a_virtual_namespace_path_is_not_a_host_folder(self) -> None:
        approval = self._payload("ls", {"path": "/memories/notes"})
        assert approval["grant_options"] == ["allow_once"]
        assert "grant_scope" not in approval

    def test_a_relative_path_is_not_grantable(self) -> None:
        """Nothing here resolves a relative path, so nothing may attach one."""

        approval = self._payload("ls", {"path": "reports"})
        assert approval["grant_options"] == ["allow_once"]
        assert "grant_scope" not in approval

    def test_a_traversal_is_refused_rather_than_normalised(self) -> None:
        approval = self._payload("ls", {"path": "/Users/ada/../../etc"})
        assert approval["grant_options"] == ["allow_once"]
        assert "grant_scope" not in approval

    def test_a_path_the_card_has_to_truncate_is_not_offered(self) -> None:
        """The user would be consenting to an ellipsis, not to a folder."""

        deep = "/Users/ada/" + ("x" * 600)
        approval = self._payload("ls", {"path": deep})
        assert approval["path"].endswith("x")  # truncated for display
        assert len(approval["path"]) < len(deep)
        assert approval["grant_options"] == ["allow_once"]
        assert "grant_scope" not in approval
