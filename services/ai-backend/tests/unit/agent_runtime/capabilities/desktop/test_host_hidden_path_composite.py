r"""Hidden paths and doubled slashes, judged by the composition the factory builds.

Three lanes landed on this stack independently, and each one's tests could only
see its own half:

* the grant lane proved that an attached folder produces ``allow`` rules and a
  matching :class:`HostFilesystemFloor` — against a floor wrapped directly
  around ``FilesystemBackend``, with no tool-layer translator in the picture;
* the tool-path lane proved that ``C:\Users\p`` reaches the rules and that a
  ``//``-rooted path no longer skips the consent card — against
  ``NativeHostPathBackend(FilesystemBackend(...))``, with no floor in the
  picture.

Neither could ask the question this file exists for, because the answer lives in
the seam BETWEEN them. The rule set is structurally blind to any path with a
dot segment (``wcmatch`` without ``DOTGLOB``: ``/**`` does not match
``~/.ssh/id_rsa``, and unmatched means allow), so for every hidden path the
verdict is decided ENTIRELY by the floor — and the floor compares path against
granted root with :class:`~pathlib.PurePosixPath`, where ``//x`` and ``/x`` are
different roots::

    PurePosixPath("//var/tmp/a/.env").parts  ->  ("//", "var", "tmp", "a", ".env")
    PurePosixPath("/var/tmp/a/.env").parts   ->  ("/",  "var", "tmp", "a", ".env")

So a doubled slash makes a granted hidden file look UNGRANTED to the floor, in
exactly the composition the product ships. The translator is what collapses the
two spellings before either layer judges them — and only a test that holds the
floor, the rules and the translator at once can see that.

Everything below is driven through ``agent_runtime.execution.factory``'s own
composition helpers rather than a hand-built stack, so a change to how the
factory wraps these layers is a failure here, not a silent divergence.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from deepagents import create_deep_agent
from deepagents.middleware.filesystem import _check_fs_permission
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import InMemorySaver

from agent_runtime.capabilities.desktop.broker_client import BrokerGrant
from agent_runtime.capabilities.desktop.host_filesystem import GrantedRoot
from agent_runtime.capabilities.desktop.host_floor import (
    HostFilesystemFloor,
    HostFloorMessages,
)
from agent_runtime.capabilities.desktop.workspace_backend import WorkspaceMountTable
from agent_runtime.execution.factory import (
    _composed_deep_backend,
    _host_filesystem_permissions,
    _host_path_tool_middleware,
)
from agent_runtime.execution.fake_model import DeterministicFakeChatModel

#: The file name every case below reaches for. Hidden, so the rule set cannot
#: see it and the floor is the only thing standing between the model and it.
HIDDEN_NAME = ".env"
SECRET = "OPENAI_API_KEY=sk-not-a-real-key\n"


class EnforceLaneBackend:
    """A workspace backend that cannot name a host root.

    This is the shape the desktop actually ships in ENFORCE mode
    (``WorkspaceGatewayBackend`` / ``WorkspaceTombstoneBackend``): its grant
    projection is path-free by design, so it has no ``granted_roots`` attribute
    at all. Attaching a folder used to buy the user nothing in this lane, which
    is why the roots are now RESOLVED by the worker and threaded in.

    It is deliberately the stand-in for every test here: if the composition
    still works when the workspace object knows nothing about folders, it works
    in the mode the product ships.
    """


class StagingLaneBackend:
    """A workspace backend that does expose the capability, for contrast."""

    def __init__(self, roots: tuple[GrantedRoot, ...] = ()) -> None:
        self.granted_roots = roots


@dataclass(frozen=True)
class ToolOutcome:
    """What one scripted filesystem call produced in a real graph run."""

    #: Arguments the consent card was raised for, or ``None`` if it never was.
    asked_with: dict[str, Any] | None
    status: str | None = None
    content: str = ""

    @property
    def asked(self) -> bool:
        """Whether the run parked on a consent request."""

        return self.asked_with is not None

    @property
    def refused_by_floor(self) -> bool:
        """Whether the floor answered with its hidden-read refusal."""

        return HostFloorMessages.HIDDEN_READ in self.content

    @property
    def verdict(self) -> str:
        """One comparable token per outcome, for twin-vs-twin assertions.

        Twin comparisons are the whole point of this file, and comparing whole
        outcomes would compare the CONTENT too — which differs between spellings
        for uninteresting reasons. This collapses an outcome to the thing a user
        would notice: was I asked, refused, or served.
        """

        if self.asked:
            return "asked"
        if self.refused_by_floor:
            return "refused"
        if self.status == "error":
            return "error"
        return "served"


class MergedCompositionMixin:
    """Builds and drives the stack ``factory`` composes, floor and all."""

    @staticmethod
    def granted_tree(tmp_path: Path, name: str) -> tuple[Path, Path]:
        """A real folder holding one real HIDDEN file: ``(folder, file)``.

        Real on purpose. A refusal over a file that does not exist would be
        indistinguishable from a refusal over one that does, and the second is
        the only one worth pinning.
        """

        folder = tmp_path / name
        folder.mkdir()
        hidden = folder / HIDDEN_NAME
        hidden.write_text(SECRET)
        return folder, hidden

    @classmethod
    def run_tool(
        cls,
        tool: str,
        args: dict[str, Any],
        *,
        roots: Sequence[GrantedRoot] = (),
        translator: bool = True,
        workspace_backend: object | None = None,
        resolve_roots: bool = True,
    ) -> ToolOutcome:
        """Script one filesystem call through the factory's own composition.

        ``resolve_roots=False`` reproduces the state before the worker resolved
        the attach set: the rules and the floor fall back to reading the
        workspace object's capability, which the ENFORCE lane does not have.
        """

        workspace = workspace_backend or EnforceLaneBackend()
        resolved: tuple[object, ...] | None = tuple(roots) if resolve_roots else None
        backend = _composed_deep_backend(
            None, workspace_backend=workspace, granted_host_roots=resolved
        )
        assert backend is not None, "the desktop composition produced no backend"
        agent = create_deep_agent(
            model=DeterministicFakeChatModel(
                tool_calls_before_final=1,
                tool_call_name=tool,
                tool_call_args=dict(args),
                emit_reasoning=False,
            ),
            tools=[],
            middleware=list(
                _host_path_tool_middleware(workspace, granted_host_roots=resolved)
            )
            if translator
            else [],
            permissions=list(
                _host_filesystem_permissions(workspace, granted_host_roots=resolved)
            ),
            backend=backend,
            checkpointer=InMemorySaver(),
        )
        config = {"configurable": {"thread_id": str(uuid.uuid4())}}
        state = agent.invoke({"messages": [HumanMessage("do it")]}, config)
        if interrupts := state.get("__interrupt__"):
            requests = interrupts[0].value["action_requests"]
            return ToolOutcome(asked_with=dict(requests[0]["args"]))
        for message in state["messages"]:
            if message.type == "tool":
                return ToolOutcome(
                    asked_with=None,
                    status=message.status,
                    content=str(message.content),
                )
        pytest.fail("the scripted tool call never ran and never asked")

    @classmethod
    def read(cls, path: str, **kwargs: Any) -> ToolOutcome:
        """``read_file`` — the op the floor actually guards."""

        return cls.run_tool("read_file", {"file_path": path}, **kwargs)

    @classmethod
    def ls(cls, path: str, **kwargs: Any) -> ToolOutcome:
        """``ls`` — a BULK op, which the floor delegates and consent gates."""

        return cls.run_tool("ls", {"path": path}, **kwargs)


class TestAHiddenPathInsideAGrant(MergedCompositionMixin):
    """(a) A dotfile in an attached folder is served, not refused.

    A grant is a statement about a FOLDER. Refusing the hidden files inside it
    would make "attach this folder" mean something different from what the user
    was asked to agree to — and `.env`, `.gitignore`, `.copilot/` are exactly
    what an agent working in a project folder needs to read.
    """

    def test_a_dotfile_inside_a_granted_root_is_read(self, tmp_path: Path) -> None:
        folder, hidden = self.granted_tree(tmp_path, "attached")

        outcome = self.read(str(hidden), roots=(GrantedRoot(path=str(folder)),))

        assert outcome.verdict == "served"
        assert SECRET.strip() in outcome.content

    def test_the_rules_never_saw_it_at_all(self, tmp_path: Path) -> None:
        """Why the read above is NOT evidence that the rules work.

        The grant produces an ``allow`` rule, but ``wcmatch`` without
        ``DOTGLOB`` cannot match a dot segment, so the rule set returns the same
        ``allow`` for a granted and an ungranted hidden path alike — the
        unmatched-means-allow default, not a decision. Everything separating
        those two cases lives in the floor, which is precisely why this file
        exercises the composed stack rather than the rules.
        """

        folder, hidden = self.granted_tree(tmp_path, "attached")
        outside = tmp_path / "elsewhere" / HIDDEN_NAME
        rules = list(
            _host_filesystem_permissions(
                EnforceLaneBackend(),
                granted_host_roots=(GrantedRoot(path=str(folder)),),
            )
        )

        assert _check_fs_permission(rules, "read", str(hidden)) == "allow"
        assert _check_fs_permission(rules, "read", str(outside)) == "allow"


class TestTheSameShapeOutsideEveryGrant(MergedCompositionMixin):
    """(b) The identical file shape, one folder over, is refused."""

    def test_a_dotfile_outside_every_grant_is_refused(self, tmp_path: Path) -> None:
        _, hidden = self.granted_tree(tmp_path, "not-attached")

        outcome = self.read(str(hidden))

        assert outcome.verdict == "refused"
        assert SECRET.strip() not in outcome.content

    def test_the_refusal_is_not_a_consent_card(self, tmp_path: Path) -> None:
        """It is refused OUTRIGHT, and that is the only honest answer.

        A consent card would be a lie about who decided: the rules never
        matched this path, so nothing asked and nothing may be treated as
        approved. The floor is the last layer, and it has no human to consult.
        """

        _, hidden = self.granted_tree(tmp_path, "not-attached")

        outcome = self.read(str(hidden))

        # Both halves, or this passes vacuously the moment the floor is dropped
        # and the file is simply served — which also does not ask.
        assert outcome.verdict == "refused"
        assert outcome.asked is False

    def test_the_same_file_reads_once_its_folder_is_attached(
        self, tmp_path: Path
    ) -> None:
        """The refusal is about the GRANT, not about the file being missing.

        Without this, every assertion in this class would still pass over a
        path that does not exist — which is the failure mode that let
        ``ls ~/Downloads`` return an empty listing and a green tick.
        """

        folder, hidden = self.granted_tree(tmp_path, "later-attached")

        assert self.read(str(hidden)).verdict == "refused"
        assert (
            self.read(str(hidden), roots=(GrantedRoot(path=str(folder)),)).verdict
            == "served"
        )


class TestTheDoubledSlashTwin(MergedCompositionMixin):
    r"""(c) ``//x`` must reach the same verdict as ``/x`` — in BOTH directions.

    The doubled root is not a hypothetical: it is what ``validate_path``
    rewrites a UNC path into, and it survives that validator unchanged
    (``validate_path("//var/tmp/x") == "//var/tmp/x"``).

    It diverges from its twin at two different layers, in opposite directions,
    which is why one test cannot cover it:

    * at the FLOOR, ``//<granted>/.env`` is not within ``/<granted>``, so a
      granted file is wrongly REFUSED;
    * at the CONSENT GATE, ``//<ungranted>`` overlaps no interrupt anchor
      (``//x`` is not ``is_relative_to("/")``), so an ungranted listing is
      wrongly SERVED without asking.

    The translator collapses both spellings before either layer runs, which is
    the only reason the twins agree at all.
    """

    @staticmethod
    def doubled(path: str) -> str:
        """The same path wearing the root that skips the gate."""

        return f"/{path}"

    def test_a_doubled_dotfile_inside_a_grant_reads_like_its_twin(
        self, tmp_path: Path
    ) -> None:
        folder, hidden = self.granted_tree(tmp_path, "attached")
        roots = (GrantedRoot(path=str(folder)),)

        plain = self.read(str(hidden), roots=roots)
        twin = self.read(self.doubled(str(hidden)), roots=roots)

        assert twin.verdict == plain.verdict == "served"
        assert SECRET.strip() in twin.content

    def test_a_doubled_dotfile_outside_every_grant_is_refused_like_its_twin(
        self, tmp_path: Path
    ) -> None:
        _, hidden = self.granted_tree(tmp_path, "not-attached")

        plain = self.read(str(hidden))
        twin = self.read(self.doubled(str(hidden)))

        assert twin.verdict == plain.verdict == "refused"
        assert SECRET.strip() not in twin.content

    def test_without_the_translator_the_granted_twins_disagree(
        self, tmp_path: Path
    ) -> None:
        """The divergence is live in this exact stack without the translator.

        Pinned so a regression cannot be misread as "the doubled spelling never
        worked anyway". Drop ``HostPathToolMiddleware`` and a folder the user
        attached stops being readable through one of its two spellings.
        """

        folder, hidden = self.granted_tree(tmp_path, "attached")
        roots = (GrantedRoot(path=str(folder)),)

        plain = self.read(str(hidden), roots=roots, translator=False)
        twin = self.read(self.doubled(str(hidden)), roots=roots, translator=False)

        assert plain.verdict == "served"
        assert twin.verdict == "refused"

    def test_a_doubled_listing_of_an_ungranted_folder_still_asks(
        self, tmp_path: Path
    ) -> None:
        """The bypass itself, through the merged composite.

        ``ls`` is bulk, so the floor delegates it untouched and the consent gate
        is the whole boundary — the layer the doubled root used to slip past.
        """

        folder, _ = self.granted_tree(tmp_path, "not-attached")

        plain = self.ls(str(folder))
        twin = self.ls(self.doubled(str(folder)))

        assert twin.verdict == plain.verdict == "asked"

    def test_without_the_translator_that_listing_did_not_ask(
        self, tmp_path: Path
    ) -> None:
        """The bypass, reproduced. One extra slash bought a silent read."""

        folder, _ = self.granted_tree(tmp_path, "not-attached")

        plain = self.ls(str(folder), translator=False)
        twin = self.ls(self.doubled(str(folder)), translator=False)

        assert plain.verdict == "asked"
        assert twin.verdict == "served"
        assert HIDDEN_NAME in twin.content


class TestTheFloorFollowsTheRootsNotTheLane(MergedCompositionMixin):
    """The grant lane's fix, seen through the floor the other lane wraps.

    Which folders the user attached is a broker fact. It must not depend on
    which ``/workspace/`` object the run's effect mode happened to build — and
    in ENFORCE mode that object cannot name a host root at all, so reading the
    attach set off it silently produced a floor that admitted nothing.
    """

    @pytest.mark.parametrize("lane", ["enforce", "staging"])
    def test_a_granted_dotfile_reads_in_either_lane(
        self, tmp_path: Path, lane: str
    ) -> None:
        folder, hidden = self.granted_tree(tmp_path, "attached")
        roots = (GrantedRoot(path=str(folder)),)
        workspace: object = (
            EnforceLaneBackend() if lane == "enforce" else StagingLaneBackend(roots)
        )

        outcome = self.read(str(hidden), roots=roots, workspace_backend=workspace)

        assert outcome.verdict == "served"

    def test_unresolved_roots_leave_the_enforce_lane_with_an_empty_floor(
        self, tmp_path: Path
    ) -> None:
        """What the resolution is actually load-bearing for.

        With nothing resolved, the ENFORCE lane's path-free backend yields no
        roots, so the floor admits nothing and the folder the user attached is
        refused. That was the shipped behaviour, and it is pinned here so the
        resolution cannot be quietly dropped as redundant.
        """

        folder, hidden = self.granted_tree(tmp_path, "attached")
        roots = (GrantedRoot(path=str(folder)),)

        assert (
            self.read(str(hidden), roots=roots, resolve_roots=False).verdict
            == "refused"
        )
        assert self.read(str(hidden), roots=roots).verdict == "served"


class TestAWindowsGrantReachesTheFloor:
    r"""The Windows half of (a) — the join no lane could make on its own.

    The grant lane produces the roots; the tool-path lane decides what spelling
    the floor will be handed. Between them sits one conversion, and it lives in
    the ONE production caller: ``WorkspaceMountTable.granted_roots``.

    Judged by verdict rather than by driving a graph, because ``/C:/...`` names
    no folder on the machine running these tests — and that is the point of a
    shape-driven classifier: the Windows encoding is decided identically here
    and on Windows.
    """

    @staticmethod
    def _grant(root: str) -> BrokerGrant:
        return BrokerGrant(
            grantId="g1",
            mode="read_write",
            label="Projects",
            status="active",
            mount="mnt_g1",
            root=root,
        )

    def test_a_windows_grant_admits_the_hidden_files_inside_it(self) -> None:
        """A folder attached on Windows must read like one attached on macOS.

        Every layer between the broker and this verdict is blind to the others:
        the rules cannot see a dot segment at all, and the floor cannot read a
        drive-absolute root. If the conversion is dropped anywhere along the
        way, this is where it shows up as a user-visible difference — the same
        `.env`, in the same attached folder, readable on one platform and not
        the other.
        """

        roots = WorkspaceMountTable.granted_roots(
            WorkspaceMountTable.from_broker_grants(
                [self._grant("C:\\Users\\ada\\Projects")]
            )
        )
        floor = HostFilesystemFloor(object(), roots=roots)  # type: ignore[arg-type]

        assert [r.path for r in roots] == ["/C:/Users/ada/Projects"]  # type: ignore[attr-defined]
        assert floor.permits_read("/C:/Users/ada/Projects/.env") is True

    def test_it_does_not_widen_to_a_sibling_folder(self) -> None:
        """One spelling per folder must not become one spelling per volume."""

        roots = WorkspaceMountTable.granted_roots(
            WorkspaceMountTable.from_broker_grants(
                [self._grant("C:\\Users\\ada\\Projects")]
            )
        )
        floor = HostFilesystemFloor(object(), roots=roots)  # type: ignore[arg-type]

        assert floor.permits_read("/C:/Users/ada/Secrets/.env") is False
        assert floor.permits_read("/C:/Users/ada/ProjectsSecret/.env") is False

    def test_the_posix_path_of_the_same_name_is_a_different_folder(self) -> None:
        """``/Users/ada/Projects`` is not what ``C:\\Users\\ada\\Projects`` granted.

        The canonical encoding is what keeps those two apart. A conversion that
        merely stripped the drive would silently hand a Windows grant authority
        over an unrelated POSIX subtree.
        """

        roots = WorkspaceMountTable.granted_roots(
            WorkspaceMountTable.from_broker_grants(
                [self._grant("C:\\Users\\ada\\Projects")]
            )
        )
        floor = HostFilesystemFloor(object(), roots=roots)  # type: ignore[arg-type]

        assert floor.permits_read("/Users/ada/Projects/.env") is False
