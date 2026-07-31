r"""Unit tests for the blocking host-folder grant request.

Two properties are load-bearing:

1. an ungranted host path ASKS — through the same ``langgraph.types.interrupt``
   seam the MCP auth gate uses — with a payload a UI can render as
   "grant access to <folder>?" and echo back;
2. every uncertain answer fails CLOSED with a safe message, because binding the
   wrong grant would mis-root later reads (returning the *wrong file*, quietly),
   and returning nothing at all is the empty-success lie this program exists to
   kill.
"""

from __future__ import annotations

import pytest

from agent_runtime.capabilities.desktop.broker_client import (
    BrokerUnavailableError,
    DesktopBrokerClient,
)
from agent_runtime.capabilities.desktop.host_path import (
    ClassifiedPath,
    HostPathClassifier,
    HostPathMessages,
)
from agent_runtime.capabilities.desktop.workspace_grant import (
    WorkspaceGrantGate,
    WorkspaceGrantMessages,
    WorkspaceGrantOutcome,
    WorkspaceGrantResume,
    WorkspaceGrantValues,
)
from tests.unit.agent_runtime.capabilities.desktop.fakes import (
    GRANT_BLOCK_KEY,
    FakeBrokerFs,
    RecordingBroker,
    RecordingConsent,
)

DOWNLOADS = "/Users/parthpahwa/Downloads"


class GrantGateMixin:
    """Builds gates over the fake broker with a scripted user decision."""

    @staticmethod
    def folder(path: str = DOWNLOADS) -> ClassifiedPath:
        return HostPathClassifier.classify(path)

    @staticmethod
    def broker(**grants: dict[str, bytes]) -> RecordingBroker:
        return RecordingBroker(
            grants={
                grant_id: FakeBrokerFs(files=files)
                for grant_id, files in grants.items()
            }
        )

    @classmethod
    def gate(
        cls,
        consent: RecordingConsent,
        *,
        broker: RecordingBroker | None = None,
        run_id: str | None = "run-1",
    ) -> WorkspaceGrantGate:
        return WorkspaceGrantGate(
            grants=(broker or cls.broker()).client(),
            interrupt_handler=consent,
            run_id=run_id,
        )

    @staticmethod
    def approve(**fields: object) -> RecordingConsent:
        return RecordingConsent(resume={"decision": "approved", **fields})


class TestGrantRequestPayload(GrantGateMixin):
    """What the user is shown, and what the approval id is made of."""

    async def test_payload_names_the_folder_and_uses_the_shared_envelope(
        self,
    ) -> None:
        consent = self.approve()
        await self.gate(consent).request(self.folder())
        payload = consent.payload
        # Envelope mirrors the MCP auth interrupt so existing approval plumbing
        # treats this like any other blocking gate.
        assert payload["api_event_type"] == WorkspaceGrantValues.EVENT_TYPE
        assert payload["event_type"] == WorkspaceGrantValues.EVENT_TYPE
        assert payload["approval_kind"] == WorkspaceGrantValues.APPROVAL_KIND
        assert payload["approval_id"] == payload["action_id"]
        assert payload["source_tool"] == WorkspaceGrantValues.SOURCE
        # The block a consent card renders: the folder, by name and full path.
        # ``path`` is the client's REQUIRED field name — see the class docstring.
        assert consent.grant_block["path"] == DOWNLOADS
        assert consent.grant_block["folder_name"] == "Downloads"
        assert consent.grant_block["platform"] == "posix"
        assert consent.grant_block["mode"] == WorkspaceGrantValues.MODE_READ_ONLY
        assert "Downloads" in str(payload["message"])

    async def test_windows_folder_is_rendered_in_windows_spelling(self) -> None:
        consent = self.approve()
        await self.gate(consent).request(self.folder("C:\\Users\\parth\\Downloads"))
        assert consent.grant_block["path"] == "C:\\Users\\parth\\Downloads"
        assert consent.grant_block["platform"] == "windows"

    async def test_the_client_can_actually_parse_the_folder_out_of_the_block(
        self,
    ) -> None:
        """Mirror of ``parseWorkspaceGrantRequest``'s only hard requirement.

        That parser reads ``payload[WORKSPACE_GRANT_PAYLOAD_KEY].path`` and
        returns null — no card, on a parked run — when the block names no
        ``path``. Python cannot import the TS constant, so the two literals are
        asserted here; this test is what fails if either side is renamed alone.
        """

        consent = self.approve()
        await self.gate(consent).request(self.folder())
        block = consent.payload[GRANT_BLOCK_KEY]
        assert isinstance(block, dict)
        assert block.get("path") == DOWNLOADS
        # ``mode`` must be one of the client's three literals or its card
        # withholds the grant button rather than guessing the access.
        assert block.get("mode") in {
            "read_only",
            "read_write_no_delete",
            "read_write",
        }

    def test_event_type_is_one_the_run_projection_recognises(self) -> None:
        """The dead-letter regression.

        ``StreamMessageParser.explicit_api_payloads`` collects a payload only when
        ``api_event_type`` parses as a ``RuntimeApiEventType``. A bespoke name is
        never collected, so the interrupt yields no event, no approval row and no
        batch: the run parks forever and the user is shown nothing. This fails if
        the envelope ever drifts back to an unregistered event name.
        """

        from runtime_api.schemas.common import RuntimeApiEventType
        from runtime_worker.stream_messages import StreamMessageParser

        payload = (
            WorkspaceGrantGate(
                grants=RecordingBroker(grants={}).client(), run_id="run-1"
            )
            ._request(self.folder())
            .interrupt_payload()
        )

        assert RuntimeApiEventType(WorkspaceGrantValues.EVENT_TYPE)
        assert StreamMessageParser.api_event_type(payload) is not None
        # ``approval_requested`` is also one of the two types that actually
        # create the ApprovalRequestRecord + 1-item batch a resume needs.
        assert StreamMessageParser.api_event_type(payload) in {
            RuntimeApiEventType.APPROVAL_REQUESTED,
            RuntimeApiEventType.MCP_AUTH_REQUIRED,
        }

    def test_approval_id_is_deterministic_per_folder_and_run(self) -> None:
        gate = self.gate(RecordingConsent())
        assert gate.approval_id(self.folder()) == gate.approval_id(self.folder())
        assert gate.approval_id(self.folder()) != gate.approval_id(
            self.folder("/Users/parthpahwa/Documents")
        )
        other_run = self.gate(RecordingConsent(), run_id="run-2")
        assert other_run.approval_id(self.folder()) != gate.approval_id(self.folder())

    def test_approval_id_carries_no_host_path(self) -> None:
        # Approval ids reach clients, events and audit rows, all of which stay
        # path-free; the folder is represented by a digest instead.
        approval_id = self.gate(RecordingConsent()).approval_id(self.folder())
        assert "Downloads" not in approval_id
        assert "parthpahwa" not in approval_id
        assert approval_id.startswith("workspace_grant:run-1:")

    def test_windows_approval_id_folds_case_like_the_platform(self) -> None:
        gate = self.gate(RecordingConsent())
        assert gate.approval_id(self.folder("C:\\Users\\P")) == gate.approval_id(
            self.folder("c:/users/p")
        )


class TestGrantApproval(GrantGateMixin):
    """An approval binds to a live broker grant, or it does not bind at all."""

    async def test_echoed_grant_id_and_root_bind_the_mount(self) -> None:
        broker = self.broker(**{"grant-dl": {"a.csv": b"x"}})
        consent = self.approve(grant_id="grant-dl", root=DOWNLOADS)
        outcome = await self.gate(consent, broker=broker).request(self.folder())
        assert outcome.approved
        assert outcome.grant is not None
        assert outcome.grant.grant_id == "grant-dl"
        assert outcome.granted_root is not None
        assert outcome.granted_root.display == DOWNLOADS

    async def test_ancestor_root_is_accepted(self) -> None:
        # The native picker may hand back a parent of the folder we asked about.
        broker = self.broker(**{"grant-home": {"Downloads/a.csv": b"x"}})
        consent = self.approve(grant_id="grant-home", root="/Users/parthpahwa")
        outcome = await self.gate(consent, broker=broker).request(self.folder())
        assert outcome.approved
        assert outcome.granted_root is not None
        assert outcome.granted_root.display == "/Users/parthpahwa"

    async def test_new_grant_is_adopted_when_no_id_is_echoed(self) -> None:
        broker = self.broker(**{"grant-old": {"x": b"1"}})
        consent = RecordingConsent(
            resume={"decision": "approved", "root": DOWNLOADS},
            on_ask=lambda _payload: broker.add_grant(
                "grant-new", {"a.csv": b"x"}, label="Downloads"
            ),
        )
        outcome = await self.gate(consent, broker=broker).request(
            self.folder(), bound_grant_ids=frozenset({"grant-old"})
        )
        assert outcome.approved
        assert outcome.grant is not None
        assert outcome.grant.grant_id == "grant-new"

    async def test_resume_fields_may_arrive_inside_the_grant_block(self) -> None:
        # A host that echoes the block it was given, rather than flattening it —
        # under the same key the block was stamped with.
        broker = self.broker(**{"grant-dl": {"a.csv": b"x"}})
        consent = RecordingConsent(
            resume={
                "decision": "approved",
                GRANT_BLOCK_KEY: {"grant_id": "grant-dl", "root": DOWNLOADS},
            }
        )
        outcome = await self.gate(consent, broker=broker).request(self.folder())
        assert outcome.approved

    async def test_the_production_decisions_batch_shape_is_an_approval(self) -> None:
        """The shape ``_resume_payload`` sends for this kind TODAY.

        It branches on ``approval_kind`` and has no ``workspace_grant`` branch, so
        a folder decision falls to the MCP-tool default:
        ``{"decisions": [{"type": "approve"}]}``. Reading only ``decision`` scored
        that as a REFUSAL — the user's yes rendered as a no.
        """

        broker = self.broker(**{"grant-dl": {"a.csv": b"x"}})
        consent = RecordingConsent(
            resume={
                "decisions": [{"type": "approve"}],
                # A resume branch for this kind would echo these two; the batch
                # shape cannot, which is the gap recorded in the module docstring.
                "grant_id": "grant-dl",
                "root": DOWNLOADS,
            }
        )
        outcome = await self.gate(consent, broker=broker).request(self.folder())
        assert outcome.approved

    @pytest.mark.parametrize(
        "decisions",
        [
            [{"type": "reject"}],
            [{"type": "approve"}, {"type": "reject"}],
            [],
            [{}],
            ["approve"],
            "approve",
        ],
    )
    async def test_non_approving_batches_are_not_approvals(
        self, decisions: object
    ) -> None:
        consent = RecordingConsent(resume={"decisions": decisions, "root": DOWNLOADS})
        outcome = await self.gate(consent).request(self.folder())
        assert outcome.approved is False
        assert outcome.message == WorkspaceGrantMessages.DECLINED

    @pytest.mark.parametrize("decision", ["approve", "approve_with_edits", "granted"])
    async def test_approval_synonyms(self, decision: str) -> None:
        broker = self.broker(**{"grant-dl": {"a.csv": b"x"}})
        consent = RecordingConsent(
            resume={"decision": decision, "grant_id": "grant-dl", "root": DOWNLOADS}
        )
        outcome = await self.gate(consent, broker=broker).request(self.folder())
        assert outcome.approved


class TestGrantFailsClosed(GrantGateMixin):
    """Every uncertain or negative answer denies — with a message, never silence."""

    @pytest.mark.parametrize(
        "resume",
        [
            {"decision": "rejected"},
            {"decision": "skipped"},
            {"decision": ""},
            {},
            None,
            False,
            "approved",
            ["approved"],
        ],
    )
    async def test_non_approval_resumes_are_declined(self, resume: object) -> None:
        consent = RecordingConsent(resume=resume)
        outcome = await self.gate(consent).request(self.folder())
        assert outcome.approved is False
        assert outcome.message == WorkspaceGrantMessages.DECLINED

    async def test_bare_true_approval_without_a_root_is_unbound(self) -> None:
        # Nothing to bind the mount to: guessing the root would let a later read
        # resolve against a different folder and return the wrong file.
        outcome = await self.gate(RecordingConsent(resume=True)).request(self.folder())
        assert outcome.approved is False
        assert outcome.message == WorkspaceGrantMessages.UNBOUND

    async def test_approval_without_a_root_is_unbound(self) -> None:
        broker = self.broker(**{"grant-dl": {"a.csv": b"x"}})
        consent = self.approve(grant_id="grant-dl")
        outcome = await self.gate(consent, broker=broker).request(self.folder())
        assert outcome.approved is False
        assert outcome.message == WorkspaceGrantMessages.UNBOUND

    @pytest.mark.parametrize(
        "root",
        [
            "/Users/parthpahwa/Documents",  # unrelated tree
            "/Users/parthpahwa/Downloads/reports",  # narrower than requested
            "C:\\Users\\parthpahwa\\Downloads",  # wrong platform
            "relative/downloads",  # not host-absolute
            "/Users/parthpahwa/../etc",  # traversal
        ],
    )
    async def test_root_that_does_not_contain_the_asked_folder_is_unbound(
        self, root: str
    ) -> None:
        broker = self.broker(**{"grant-dl": {"a.csv": b"x"}})
        consent = self.approve(grant_id="grant-dl", root=root)
        outcome = await self.gate(consent, broker=broker).request(self.folder())
        assert outcome.approved is False
        assert outcome.message == WorkspaceGrantMessages.UNBOUND

    async def test_grant_id_absent_from_the_snapshot_is_unbound(self) -> None:
        # The broker snapshot is the only authority on which grants exist.
        broker = self.broker(**{"grant-dl": {"a.csv": b"x"}})
        consent = self.approve(grant_id="grant-invented", root=DOWNLOADS)
        outcome = await self.gate(consent, broker=broker).request(self.folder())
        assert outcome.approved is False
        assert outcome.message == WorkspaceGrantMessages.UNBOUND

    async def test_revoked_grant_is_not_bindable(self) -> None:
        broker = self.broker(**{"grant-dl": {"a.csv": b"x"}})
        broker.grant_meta["grant-dl"] = {"status": "revoked"}
        consent = self.approve(grant_id="grant-dl", root=DOWNLOADS)
        outcome = await self.gate(consent, broker=broker).request(self.folder())
        assert outcome.approved is False

    async def test_ambiguous_new_grants_are_unbound(self) -> None:
        # Two folders appeared and nothing says which one the user picked;
        # binding the wrong one would mis-root every later read.
        broker = self.broker()
        consent = RecordingConsent(
            resume={"decision": "approved", "root": DOWNLOADS},
            on_ask=lambda _p: (
                broker.add_grant("g1", {"a": b"1"}),
                broker.add_grant("g2", {"b": b"2"}),
            ),
        )
        outcome = await self.gate(consent, broker=broker).request(self.folder())
        assert outcome.approved is False
        assert outcome.message == WorkspaceGrantMessages.UNBOUND

    async def test_no_new_grant_at_all_is_unbound(self) -> None:
        consent = self.approve(root=DOWNLOADS)
        outcome = await self.gate(consent).request(self.folder())
        assert outcome.approved is False
        assert outcome.message == WorkspaceGrantMessages.UNBOUND

    async def test_unreachable_broker_denies_with_a_safe_message(self) -> None:
        class UnavailableGrants:
            async def grants_snapshot(self) -> object:
                raise BrokerUnavailableError

        gate = WorkspaceGrantGate(
            grants=UnavailableGrants(),  # type: ignore[arg-type]
            interrupt_handler=self.approve(grant_id="g", root=DOWNLOADS),
            run_id="run-1",
        )
        outcome = await gate.request(self.folder())
        assert outcome.approved is False
        assert outcome.message == WorkspaceGrantMessages.UNAVAILABLE

    async def test_a_volume_root_is_never_asked_for(self) -> None:
        consent = self.approve(root="/")
        outcome = await self.gate(consent).request(self.folder("/"))
        assert outcome.approved is False
        assert outcome.message == HostPathMessages.VOLUME_ROOT
        assert consent.asked is False  # the user is not even shown the card

    async def test_a_refused_path_is_never_asked_for(self) -> None:
        consent = self.approve()
        outcome = await self.gate(consent).request(
            self.folder("/Users/parthpahwa/../etc")
        )
        assert outcome.approved is False
        assert consent.asked is False


class TestProductionResumeCannotBindYet(GrantGateMixin):
    """The honest end state of the lane as production wires it today.

    Not an aspiration: this is what happens right now. The user approves, the
    resume arrives as a bare ``decisions[]`` batch carrying no root, and the gate
    refuses — out loud, with a message the model can act on. The test exists so
    the gap is visible in the suite instead of being discovered live, and so
    nobody closes it by ASSUMING the asked folder is the granted root (which would
    return the wrong file whenever the host granted a parent).
    """

    async def test_an_approval_with_no_echoed_root_refuses_rather_than_guessing(
        self,
    ) -> None:
        broker = self.broker(**{"grant-dl": {"a.csv": b"x"}})
        consent = RecordingConsent(resume={"decisions": [{"type": "approve"}]})
        outcome = await self.gate(consent, broker=broker).request(self.folder())
        assert consent.asked is True  # the user WAS shown the card
        assert outcome.approved is False
        assert outcome.message == WorkspaceGrantMessages.UNBOUND
        # Not silence, and not a wrong answer: a refusal that names the next step.
        assert "grant" in outcome.message.lower()


class TestResumeParsing:
    """The resume is untrusted input, exactly like model output."""

    @pytest.mark.parametrize(
        "grant_id",
        ["../etc/passwd", "grant id", "g" * 200, "", None, 7, {"a": 1}],
    )
    def test_malformed_grant_ids_are_dropped(self, grant_id: object) -> None:
        parsed = WorkspaceGrantResume.parse(
            {"decision": "approved", "grant_id": grant_id}
        )
        assert parsed.approved is True
        assert parsed.grant_id is None  # falls back to snapshot adoption

    def test_oversized_root_is_dropped(self) -> None:
        parsed = WorkspaceGrantResume.parse(
            {"decision": "approved", "root": "/" + "a" * 2000}
        )
        assert parsed.root is None

    def test_decision_is_case_and_space_insensitive(self) -> None:
        assert WorkspaceGrantResume.parse({"decision": " Approved "}).approved is True

    def test_non_mapping_resume_is_not_an_approval(self) -> None:
        assert WorkspaceGrantResume.parse(object()).approved is False


class TestOutcomeFactories:
    """The outcome always carries a message the model can act on."""

    def test_denied_carries_the_message_and_no_binding(self) -> None:
        outcome = WorkspaceGrantOutcome.denied(WorkspaceGrantMessages.DECLINED)
        assert outcome.approved is False
        assert outcome.grant is None
        assert outcome.granted_root is None
        assert outcome.message

    def test_default_gate_uses_the_langgraph_interrupt_seam(self) -> None:
        # The production default must be the same blocking mechanism auth_mcp
        # uses, not a no-op that would silently continue.
        from langgraph.types import interrupt as langgraph_interrupt

        gate = WorkspaceGrantGate(grants=RecordingBroker(grants={}).client())
        assert gate.interrupt_handler is langgraph_interrupt
        assert isinstance(gate.grants, DesktopBrokerClient)
