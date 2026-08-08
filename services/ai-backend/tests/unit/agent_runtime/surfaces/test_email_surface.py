"""The ``email://`` rung, driven by the local-mailbox fixture's own bytes.

Two halves, and the split matters.

The first half is the pure producer
(:class:`~agent_runtime.capabilities.surfaces.email_surface.EmailDraftSurface`)
over hand-written payloads: what counts as a draft, what does not, and what the
four slots are allowed to be filled from. These run everywhere.

The second half takes the payloads from
``tools/desktop-journeys/local-mailbox/fixture_mcp.py`` — the same file the live
desktop journey registers as an MCP connector — wraps them in the envelope
``langchain-mcp-adapters`` + ``McpPresentedTool._output_of`` actually build, and
runs the real :class:`SurfaceProjector`. That is the whole chain minus the
network and the app shell, so a fixture edit that breaks the surface fails HERE,
in a 1.5-second unit run, rather than in a 20-minute packaged journey.

The fixture is loaded by path with a skip guard rather than imported, because
``tools/`` is repo tooling and not a package on ``ai-backend``'s path. A missing
fixture skips these four; it never turns the suite red for a file this service
does not own.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

from agent_runtime.capabilities.surfaces.email_surface import EmailDraftSurface
from agent_runtime.capabilities.surfaces.projector import SurfaceProjector
from agent_runtime.capabilities.surfaces.spec_models import (
    SurfaceArchetype,
    SurfaceSpecRung,
)

# ---------------------------------------------------------------------------
# The wire envelope
# ---------------------------------------------------------------------------


class _Wire:
    """Rebuild what the runtime actually receives from an MCP text result.

    ``_convert_call_tool_result`` turns a server's content into a LIST of
    ``{"type": "text", "text": …}`` blocks and leaves the artifact ``None``
    unless the server sent ``structuredContent`` (which this fixture never
    does). ``McpPresentedTool._output_of`` then wraps that non-dict payload as
    ``{"result": …}``. Spelling it out here rather than passing a bare document
    is the difference between testing the pipeline and testing a fixture — the
    ``surface-floor`` journey passed 10/10 for months by skipping exactly this
    step.
    """

    KEY = "result"

    @classmethod
    def of(cls, *texts: str) -> dict[str, object]:
        return {cls.KEY: [{"type": "text", "text": text} for text in texts]}


# ---------------------------------------------------------------------------
# The fixture, loaded by path
# ---------------------------------------------------------------------------

_FIXTURE_RELATIVE = Path("tools/desktop-journeys/local-mailbox/fixture_mcp.py")


def _fixture_path() -> Path | None:
    """Find the fixture by walking up to the repo root.

    Ascended rather than counted with ``parents[n]``: this file sits six levels
    below the root today, and a miscount is a silent five-test SKIP that reads
    exactly like "the fixture is missing". It also has to work from a git
    worktree, where the root is not the one the main checkout has.
    """

    for directory in Path(__file__).resolve().parents:
        candidate = directory / _FIXTURE_RELATIVE
        if candidate.exists():
            return candidate
    return None


def _load_fixture() -> ModuleType:
    """Import the local-mailbox fixture, or skip.

    Loaded under a private module name so it cannot collide with the
    same-named ``surface-floor`` fixture if both are ever loaded in one session.
    """

    path = _fixture_path()
    if path is None:
        pytest.skip(f"local-mailbox fixture not found: {_FIXTURE_RELATIVE}")
    name = "_local_mailbox_fixture"
    cached = sys.modules.get(name)
    if cached is not None:
        return cached
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        pytest.skip(f"local-mailbox fixture is not importable: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(name="mailbox")
def _mailbox() -> ModuleType:
    return _load_fixture()


# ---------------------------------------------------------------------------
# The producer, in isolation
# ---------------------------------------------------------------------------


class TestEmailDraftRecognition:
    """What is, and is not, an email draft."""

    def test_matches_a_draft_and_normalises_its_field_names(self) -> None:
        match = EmailDraftSurface.match(
            {
                "to": ["jordan@acme.example"],
                "cc": [],
                "subject_line": "Re: Renewal",
                "body_text": "Hi Jordan,",
            }
        )

        assert match is not None
        assert match.state == {
            "to": "jordan@acme.example",
            "cc": "",
            "subject": "Re: Renewal",
            "body": "Hi Jordan,",
        }

    def test_peels_one_draft_wrapper(self) -> None:
        wrapped = {
            "draft": {"to": "a@b.c", "subject": "s", "body": "b"},
        }
        assert EmailDraftSurface.match(wrapped) is not None

    @pytest.mark.parametrize(
        ("name", "payload"),
        [
            ("no body", {"to": "a@b.c", "subject": "s"}),
            ("no subject", {"to": "a@b.c", "body": "b"}),
            ("no recipient", {"subject": "s", "body": "b"}),
            ("empty recipient", {"to": "", "subject": "s", "body": "b"}),
            # An issue with a title and a description is the closest real
            # neighbour, and drawing it as a composer would put a Send button
            # over something nobody meant to send.
            (
                "an issue, not a message",
                {"id": 4471, "title": "Facade 5xx", "description": "high"},
            ),
            ("a table payload", {"messages": [{"id": 1}, {"id": 2}]}),
            ("not a mapping", ["a@b.c", "s", "b"]),
            ("empty", {}),
        ],
    )
    def test_declines_everything_that_is_not_a_draft(
        self, name: str, payload: object
    ) -> None:
        assert EmailDraftSurface.match(payload) is None, name

    def test_cc_is_never_back_filled_from_another_field(self) -> None:
        """The whole point of the sibling task, stated as a test.

        An over-scoped payload here is a ``Cc`` the user never saw. The only
        key that may fill ``cc`` is a cc-named one — not ``reply_to``, not
        ``bcc``, not the recipient list.
        """

        match = EmailDraftSurface.match(
            {
                "to": "a@b.c",
                "reply_to": "legal@acme.example",
                "bcc": "audit@acme.example",
                "subject": "s",
                "body": "b",
            }
        )

        assert match is not None
        assert match.state["cc"] == ""

    def test_reads_a_recipient_object_by_its_address(self) -> None:
        match = EmailDraftSurface.match(
            {
                "to": [{"name": "Jordan", "email": "jordan@acme.example"}],
                "cc": [f"user{index}@acme.example" for index in range(8)],
                "subject": "s",
                "body": "b",
            }
        )

        assert match is not None
        assert match.state["to"] == "jordan@acme.example"
        assert match.state["cc"].count("@") == 8

    def test_a_list_longer_than_the_cap_refuses_rather_than_summarising(self) -> None:
        # It used to render ``user0@…, … user7@… +4 more``: four recipients
        # absent from the ONE screen a human reviews them on, and — because the
        # write lane sources ``cc`` from the row as rendered — that literal
        # summary string was what a save would DISPATCH. Neither the real
        # recipient set nor anything the user could have typed. No composer at
        # all is the honest answer.
        match = EmailDraftSurface.match(
            {
                "to": "jordan@acme.example",
                "cc": [f"user{index}@acme.example" for index in range(12)],
                "subject": "s",
                "body": "b",
            }
        )

        assert match is None

    def test_a_display_name_never_stands_in_for_an_address(self) -> None:
        # A ``name`` is not a recipient. Letting it stand in put connector-
        # authored prose into a field the write lane dispatches as a To.
        match = EmailDraftSurface.match(
            {
                "to": [{"name": "Jordan Reyes"}],
                "subject": "s",
                "body": "b",
            }
        )

        assert match is None

    def test_a_recipient_carrying_the_separator_is_refused(self) -> None:
        # ``_addresses_of`` joins with ", ", so a display name containing a
        # comma injected a second address into one To value — and the composer
        # would have rendered and dispatched both.
        match = EmailDraftSurface.match(
            {
                "to": [
                    {
                        "name": (
                            "Jordan Reyes <jordan@acme.example>, mallory@evil.example"
                        )
                    }
                ],
                "subject": "s",
                "body": "b",
            }
        )

        assert match is None

    def test_a_bare_recipient_string_carrying_the_separator_is_refused(self) -> None:
        """The string branch's own guard, which the object case never reaches.

        ``{"to": "jordan@…, mallory@…"}`` is the cheapest smuggle there is: no
        object, no list, no display name — one string that the composer draws as
        one To row and that the write lane then dispatches verbatim, delivering
        to a second address the reviewer read as part of the first. Deleting the
        guard in :meth:`_recipients`' ``isinstance(value, str)`` branch leaves
        every other separator test green, because they all refuse for a
        different reason (an unparseable address).
        """

        assert (
            EmailDraftSurface.match(
                {
                    "to": "jordan@acme.example, mallory@evil.example",
                    "subject": "s",
                    "body": "b",
                }
            )
            is None
        )

    def test_a_recipient_objects_address_carrying_the_separator_is_refused(
        self,
    ) -> None:
        """The mapping branch refuses on the ADDRESS, not only on its absence.

        Its sibling test smuggles through a ``name`` with no ``email``, so
        ``_address_of`` answers ``None`` and the refusal is already earned by
        the ``address is None`` half of the guard. This one supplies a perfectly
        parseable ``email`` whose VALUE carries the separator — the only payload
        that exercises the second half. A well-formed object is also the shape a
        real mail connector emits, so this is the realistic version of the
        attack, not the exotic one.
        """

        assert (
            EmailDraftSurface.match(
                {
                    "to": [
                        {
                            "name": "Jordan Reyes",
                            "email": "jordan@acme.example, mallory@evil.example",
                        }
                    ],
                    "subject": "s",
                    "body": "b",
                }
            )
            is None
        )

    @pytest.mark.parametrize(
        ("name", "cc"),
        [
            ("a bare scalar", 42),
            ("a scalar inside the list", ["legal@acme.example", 42]),
        ],
    )
    def test_a_recipient_of_an_unreadable_TYPE_is_refused_not_dropped(
        self, name: str, cc: object
    ) -> None:
        """:meth:`_recipients`' final fall-through must REFUSE, not read empty.

        The two absences are not the same, and this is the branch where
        collapsing them is silent. Answering a bare ``_RecipientRead()`` for a
        value of an unreadable type says "the document carried no cc", so ``cc``
        defaults to ``""`` and the composer opens over a draft whose Cc the user
        is told is empty when it was not — and in the list form, the readable
        recipients still render, so the row looks complete while one addressee
        has been dropped out of it. Both then flow to the write lane as the Cc
        the reviewer approved.

        Neither case reaches the mapping branch, which is why the existing
        ``{"team": "legal"}`` test leaves this one uncovered.
        """

        assert (
            EmailDraftSurface.match(
                {"to": "jordan@acme.example", "cc": cc, "subject": "s", "body": "b"}
            )
            is None
        ), name

    def test_a_present_but_unreadable_cc_fails_the_match(self) -> None:
        # The asymmetry that matters: an ABSENT cc is the empty string, a cc
        # that is there and cannot be shown whole is a refusal. Defaulting the
        # second to "" is a recipient the user never saw.
        match = EmailDraftSurface.match(
            {
                "to": "jordan@acme.example",
                "cc": {"team": "legal"},
                "subject": "s",
                "body": "b",
            }
        )

        assert match is None

    def test_the_spec_binds_the_normalised_state(self) -> None:
        """The paths must resolve against the value actually shipped.

        A spec whose dot-paths and whose ``state.data`` are two different values
        is FINDINGS §3.4 — the defect that made a hand-authored, correct spec
        render nothing.
        """

        match = EmailDraftSurface.match({"to": "a@b.c", "subject": "s", "body": "b"})

        assert match is not None
        assert match.spec.archetype is SurfaceArchetype.RECORD
        assert match.spec.title_path == "subject"
        bound = {field.path for field in match.spec.fields or ()}
        assert bound == set(match.state)


# ---------------------------------------------------------------------------
# The fixture, through the real projector
# ---------------------------------------------------------------------------


class TestLocalMailboxThroughTheProjector:
    """The fixture's own bytes, end to end minus the network."""

    SERVER = "local-mailbox"

    def test_list_messages_draws_a_table_of_the_mailbox(
        self, mailbox: ModuleType
    ) -> None:
        output = _Wire.of(mailbox.list_messages().text)

        envelope = SurfaceProjector().resolve(self.SERVER, "list_messages", output)

        assert envelope is not None
        assert envelope.archetype is SurfaceArchetype.TABLE
        assert envelope.surface_uri.startswith("table://local-mailbox/list_messages/")
        spec = envelope.state.spec
        assert spec is not None
        assert spec.items_path == "messages"
        # Bound to the CONNECTOR's vocabulary, not the transport's. `id` / `type`
        # / `text` among the columns would mean the surface tabulated the MCP
        # envelope instead of the mailbox — the failure mode that renders three
        # rows and looks, from across the room, like three messages.
        columns = {column.path for column in spec.columns or ()}
        assert {"subject", "from"} <= columns
        assert not columns & {"type", "text"}
        rows = envelope.state.data["messages"]  # type: ignore[index,call-overload]
        assert len(rows) == len(mailbox._MESSAGES)

    def test_draft_reply_mints_the_email_surface(self, mailbox: ModuleType) -> None:
        output = _Wire.of(mailbox.draft_reply("m-1041").text)

        envelope = SurfaceProjector().resolve(self.SERVER, "draft_reply", output)

        assert envelope is not None
        # The identity: ONE value, and its scheme is what routes the renderer.
        assert envelope.surface_uri.startswith("email://local-mailbox/draft_reply/")
        assert envelope.surface_uri.count("://") == 1
        # The archetype stays truthful — the scheme is a presentation override,
        # not a tenth archetype smuggled into a frozen contract.
        assert envelope.archetype is SurfaceArchetype.RECORD
        assert envelope.spec_rung is SurfaceSpecRung.SHAPE_MATCH
        # The state IS `EmailState`: exactly the four keys EmailRenderer reads.
        state = envelope.state.data
        assert isinstance(state, dict)
        assert set(state) == {"to", "cc", "subject", "body"}
        assert state["to"] == "jordan.reyes@acme.example"
        assert state["cc"] == ""
        assert state["subject"].startswith("Re: ")
        assert "Confirming the locked-price block" in state["body"]
        assert envelope.state.source is not None
        assert envelope.state.source.server == self.SERVER

    def test_the_same_draft_re_read_keeps_the_same_tab(
        self, mailbox: ModuleType
    ) -> None:
        """The id segment comes from the draft, not from the call.

        A fresh hash per call would open a second canvas tab every time the
        agent looked at the same draft.
        """

        first = SurfaceProjector().resolve(
            self.SERVER,
            "draft_reply",
            _Wire.of(mailbox.draft_reply("m-1041").text),
            call_id="call-1",
        )
        second = SurfaceProjector().resolve(
            self.SERVER,
            "draft_reply",
            _Wire.of(mailbox.draft_reply("m-1041").text),
            call_id="call-2",
        )

        assert first is not None and second is not None
        assert first.surface_uri == second.surface_uri
        assert first.surface_uri.endswith("/draft-m-1041")

    def test_the_draft_rung_costs_no_model_call(self, mailbox: ModuleType) -> None:
        """``SHAPE_MATCH`` does not invite refinement, and must not.

        The composer does not read a spec, so a nano model re-authoring one for
        it is spend that buys nothing.
        """

        scheduled: list[str] = []

        class _Scheduler:
            def maybe_schedule(self, **kwargs: object) -> None:
                scheduled.append(str(kwargs.get("surface_uri")))

        projector = SurfaceProjector(scheduler=_Scheduler())  # type: ignore[arg-type]
        projector.resolve(
            self.SERVER, "draft_reply", _Wire.of(mailbox.draft_reply("m-1041").text)
        )

        assert scheduled == []

    def test_the_send_tool_is_not_annotated_read_only(
        self, mailbox: ModuleType
    ) -> None:
        """The gate depends on this, so the fixture must not drift out of it.

        ``CapabilityDescriptorFactory.action_for`` reads
        ``annotations.read_only_hint``: ``True`` classifies READ and the call
        dispatches unapproved. ``send_reply`` must never carry it — with no hint
        and no catalog entry the action fails closed to WRITE, the PDP returns
        GATE under the default ``write=ask``, and
        ``PolicyStageMcpTool._authorize`` parks on
        ``ToolAccessGate.park_for_approval`` before dispatch.
        """

        by_tool = {entry["tool"]: entry for entry in mailbox.TOOLS}
        assert by_tool["send_reply"]["class"] == "write"
        assert by_tool["list_messages"]["class"] == "read"
        assert by_tool["draft_reply"]["class"] == "read"

        annotations = json.loads(
            json.dumps(
                {
                    name: getattr(
                        getattr(tool, "annotations", None), "readOnlyHint", None
                    )
                    for name, tool in _tools_by_name(mailbox).items()
                }
            )
        )
        assert annotations["list_messages"] is True
        assert annotations["draft_reply"] is True
        # Not False — ABSENT. The un-annotated case is the one a real server
        # most often ships, and the one whose safety depends on the runtime's
        # fail-closed default rather than on the server's honesty.
        assert annotations["send_reply"] is None


def _tools_by_name(mailbox: ModuleType) -> dict[str, object]:
    """The fixture's registered MCP tools, keyed by name.

    Read off the FastMCP registry rather than off the module's functions: the
    annotations are what the policy layer sees, and they live on the
    registration, not on the Python object.
    """

    manager = mailbox.mcp._tool_manager  # noqa: SLF001 — the fixture's own registry
    return {tool.name: tool for tool in manager.list_tools()}
