r"""What a host path actually meets: Deep Agents' tool surface and consent gate.

Judged by driving a REAL ``create_deep_agent`` graph — real permission rules,
real ``HumanInTheLoopMiddleware`` (installed by the ``interrupt`` rule itself),
real ``FilesystemBackend(virtual_mode=False)`` over a real temporary directory,
and a deterministic offline model scripted to call one filesystem tool. Nothing
about the consent decision is simulated here, because the two defects this file
exists for were both invisible from inside the tool:

* a drive-absolute path (``C:\Users\p\Downloads``) never reached the classifier
  or the rules at all — ``validate_path`` rejected it first, so the product's
  Windows half could not address a host folder;
* a ``//``-rooted path — which is what ``validate_path`` REWRITES a UNC path
  into — skipped the consent interrupt for ``ls`` / ``glob`` / ``grep`` and was
  answered from the real disk. Every ``*_without_the_translator`` test below
  drives the same graph with the middleware removed and pins that bypass, so a
  regression cannot be mistaken for "the test never worked".
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from deepagents import create_deep_agent
from deepagents.backends.filesystem import FilesystemBackend
from deepagents.backends.protocol import (
    EditResult,
    GlobResult,
    GrepResult,
    LsResult,
    ReadResult,
    WriteResult,
)
from deepagents.backends.utils import validate_path
from deepagents.middleware._fs_interrupt import (
    _FS_TOOL_PATH_ARGS,
    _build_interrupt_on_from_permissions,
)
from deepagents.middleware.filesystem import FilesystemPermission, _check_fs_permission
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from agent_runtime.capabilities.desktop.host_filesystem import (
    GrantedRoot,
    HostFilesystemRules,
)
from agent_runtime.capabilities.desktop.host_path import (
    HostPathClassifier,
    HostPathMessages,
)
from agent_runtime.capabilities.desktop.host_tool_paths import (
    HostFsToolArgs,
    HostPathToolMiddleware,
    NativeHostPathBackend,
)
from agent_runtime.execution.fake_model import DeterministicFakeChatModel

WINDOWS_DOWNLOADS = "C:\\Users\\p\\Downloads"
UNC_REPORTS = "\\\\server\\share\\reports"


@dataclass(frozen=True)
class ToolOutcome:
    """What one scripted filesystem call produced in a real graph run."""

    #: Arguments the consent card was raised for, or ``None`` if it never was.
    asked_with: dict[str, Any] | None
    #: The tool result, when the call ran instead of parking for consent.
    status: str | None = None
    content: str = ""

    @property
    def asked(self) -> bool:
        """Whether the run parked on a consent request."""

        return self.asked_with is not None


class HostFsGraphMixin:
    """Builds and runs the real desktop filesystem composition."""

    @staticmethod
    def rules(roots: Sequence[GrantedRoot] = ()) -> list[FilesystemPermission]:
        return [
            FilesystemPermission(**rule)  # type: ignore[arg-type]
            for rule in HostFilesystemRules.build(tuple(roots))
        ]

    @classmethod
    def run_tool(
        cls,
        tool: str,
        args: dict[str, Any],
        *,
        roots: Sequence[GrantedRoot] = (),
        translator: bool = True,
        decide: dict[str, Any] | None = None,
    ) -> ToolOutcome:
        """Script one filesystem call through a real deep-agent graph.

        ``decide`` answers a consent card the run parks on (the same
        ``approve`` / ``edit`` decisions the product's approval surface sends),
        so the post-approval half of the lane is exercised rather than assumed.
        """

        agent = create_deep_agent(
            model=DeterministicFakeChatModel(
                tool_calls_before_final=1,
                tool_call_name=tool,
                tool_call_args=dict(args),
                emit_reasoning=False,
            ),
            tools=[],
            middleware=[HostPathToolMiddleware()] if translator else [],
            permissions=cls.rules(roots),
            # Exactly what ``factory._host_default_backend`` composes.
            backend=NativeHostPathBackend(FilesystemBackend(virtual_mode=False)),
            checkpointer=InMemorySaver(),
        )
        config = {"configurable": {"thread_id": str(uuid.uuid4())}}
        state = agent.invoke({"messages": [HumanMessage("do it")]}, config)
        interrupts = state.get("__interrupt__")
        if interrupts and decide is not None:
            state = agent.invoke(Command(resume={"decisions": [decide]}), config)
            interrupts = state.get("__interrupt__")
        if interrupts:
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

    @staticmethod
    def tmp_tree(tmp_path: Path) -> tuple[str, str]:
        """A real directory holding one real file: ``(directory, file)``."""

        secret = tmp_path / "user-secrets.txt"
        secret.write_text("hunter2\n")
        return (str(tmp_path), str(secret))


class TestTheDoubleSlashConsentBypass(HostFsGraphMixin):
    r"""One extra slash used to buy an ungranted read with no consent card.

    ``validate_path`` turns ``\\server\share`` into ``//server/share``, and a
    ``//``-rooted path is not ``PurePosixPath.is_relative_to("/")`` — which is
    the comparison deepagents' bulk-tool interrupt predicate makes against the
    catch-all rule's ``/`` anchor. The interrupt therefore never fired, and the
    tool went on to read the real disk. The model did not need a UNC path to get
    there: it could simply type the extra slash.
    """

    def test_a_doubled_slash_still_asks(self, tmp_path: Path) -> None:
        directory, _ = self.tmp_tree(tmp_path)

        outcome = self.run_tool("ls", {"path": f"/{directory}"})

        assert outcome.asked, (
            "an ungranted host folder was listed with no consent card — the "
            "model reached it by doubling the leading slash"
        )
        assert outcome.asked_with == {"path": directory}

    def test_without_the_translator_the_bypass_is_live(self, tmp_path: Path) -> None:
        directory, _ = self.tmp_tree(tmp_path)

        outcome = self.run_tool("ls", {"path": f"/{directory}"}, translator=False)

        assert not outcome.asked
        assert outcome.status == "success"
        assert "user-secrets.txt" in outcome.content

    def test_the_posix_twin_asked_all_along(self, tmp_path: Path) -> None:
        """The single-slash spelling always asked — only the doubled one leaked."""

        directory, _ = self.tmp_tree(tmp_path)

        outcome = self.run_tool("ls", {"path": directory}, translator=False)

        assert outcome.asked

    def test_a_doubled_slash_grep_cannot_read_file_contents(
        self, tmp_path: Path
    ) -> None:
        """``grep`` returns matching LINES, so the bypass leaked content, not names."""

        directory, _ = self.tmp_tree(tmp_path)

        outcome = self.run_tool("grep", {"pattern": "hunter2", "path": f"/{directory}"})

        assert outcome.asked
        assert "hunter2" not in outcome.content


class TestWindowsPathsReachTheConsentGate(HostFsGraphMixin):
    r"""The product requirement: macOS AND Windows.

    Every shape here used to die at ``validate_path`` or arrive rewritten past
    the consent gate. The assertion is not that the folder was read — this host
    has no ``C:`` drive — but that the call now reaches the same gate its POSIX
    twin reaches, wearing the canonical spelling the rules can match.
    """

    @pytest.mark.parametrize(
        ("supplied", "canonical"),
        [
            (WINDOWS_DOWNLOADS, "/C:/Users/p/Downloads"),
            ("C:/Users/p/Downloads", "/C:/Users/p/Downloads"),
            ("\\\\?\\C:\\Users\\p\\Downloads", "/C:/Users/p/Downloads"),
            (UNC_REPORTS, "/UNC:/server/share/reports"),
            ("\\\\?\\UNC\\server\\share\\reports", "/UNC:/server/share/reports"),
        ],
    )
    def test_an_ungranted_windows_folder_asks(
        self, supplied: str, canonical: str
    ) -> None:
        outcome = self.run_tool("ls", {"path": supplied})

        assert outcome.asked, f"{supplied!r} never reached the consent gate"
        assert outcome.asked_with == {"path": canonical}

    def test_without_the_translator_a_drive_path_is_simply_rejected(self) -> None:
        outcome = self.run_tool("ls", {"path": WINDOWS_DOWNLOADS}, translator=False)

        assert not outcome.asked
        assert outcome.status == "error"
        assert "Windows absolute paths are not supported" in outcome.content

    def test_without_the_translator_a_unc_path_skips_the_gate(self) -> None:
        outcome = self.run_tool("ls", {"path": UNC_REPORTS}, translator=False)

        assert not outcome.asked, (
            "a UNC path was dispatched to the real filesystem without consent"
        )

    def test_a_windows_glob_pattern_is_translated_too(self) -> None:
        """An absolute ``pattern`` redirects the search away from ``path``."""

        outcome = self.run_tool(
            "glob", {"pattern": "C:\\Users\\p\\Downloads\\*.csv", "path": None}
        )

        assert outcome.asked
        assert outcome.asked_with == {
            "pattern": "/C:/Users/p/Downloads/*.csv",
            "path": None,
        }


class TestRefusedShapesNeverBecomeAConsentRequest(HostFsGraphMixin):
    """Traversal, device namespaces and reserved names fail closed, and early.

    "Early" is the load-bearing half: a refusal that arrives after the consent
    card has been raised has already taught the user to approve a shape that
    must never resolve. Each of these is refused with the safe copy from
    ``HostPathMessages`` and never parks the run.
    """

    @pytest.mark.parametrize(
        ("path", "message"),
        [
            ("/Users/p/../etc", HostPathMessages.TRAVERSAL),
            ("C:\\Users\\p\\..\\etc", HostPathMessages.TRAVERSAL),
            ("\\\\.\\PhysicalDrive0", HostPathMessages.DEVICE_NAMESPACE),
            ("\\\\?\\GLOBALROOT\\Device\\Disk0", HostPathMessages.DEVICE_NAMESPACE),
            ("C:\\Users\\p\\NUL", HostPathMessages.RESERVED_NAME),
            ("C:\\Users\\p\\COM1.txt", HostPathMessages.RESERVED_NAME),
            ("C:\\Users\\p\\Downloads.", HostPathMessages.TRAILING_DOT_OR_SPACE),
            ("C:relative", HostPathMessages.DRIVE_RELATIVE),
            ("~/Downloads", HostPathMessages.HOME_RELATIVE),
            ("\\\\server", HostPathMessages.INCOMPLETE_UNC),
        ],
    )
    def test_a_refused_shape_is_answered_not_asked(
        self, path: str, message: str
    ) -> None:
        outcome = self.run_tool("ls", {"path": path})

        assert not outcome.asked, f"{path!r} was turned into a consent request"
        assert outcome.status == "error"
        assert outcome.content == f"Error: {message}"

    def test_a_refused_shape_is_refused_for_reads_of_files_too(self) -> None:
        outcome = self.run_tool("read_file", {"file_path": "\\\\.\\PhysicalDrive0"})

        assert not outcome.asked
        assert outcome.status == "error"
        assert outcome.content == f"Error: {HostPathMessages.DEVICE_NAMESPACE}"

    def test_the_refusal_repeats_nothing_about_the_host(self) -> None:
        """Safe public copy only — never the internal refusal reason."""

        outcome = self.run_tool("ls", {"path": "C:\\Users\\p\\NUL"})

        assert "reserved_name" not in outcome.content
        assert "NUL" not in outcome.content


class TestGrantedFoldersReadWithoutAsking(HostFsGraphMixin):
    """A grant has to actually buy something, in either grammar."""

    def test_a_granted_file_is_read_without_a_consent_card(
        self, tmp_path: Path
    ) -> None:
        directory, secret = self.tmp_tree(tmp_path)
        roots = (GrantedRoot.from_host_path(directory),)

        outcome = self.run_tool("read_file", {"file_path": secret}, roots=roots)

        assert not outcome.asked
        assert outcome.status == "success"
        assert "hunter2" in outcome.content

    def test_the_doubled_spelling_of_a_granted_file_reads_the_same_file(
        self, tmp_path: Path
    ) -> None:
        """Normalising the bypass must not break the path it normalises to."""

        directory, secret = self.tmp_tree(tmp_path)
        roots = (GrantedRoot.from_host_path(directory),)

        outcome = self.run_tool("read_file", {"file_path": f"/{secret}"}, roots=roots)

        assert not outcome.asked
        assert "hunter2" in outcome.content

    def test_an_ungranted_sibling_still_asks(self, tmp_path: Path) -> None:
        directory, _ = self.tmp_tree(tmp_path)
        sibling = tmp_path.parent / f"{tmp_path.name}-other"
        sibling.mkdir()
        roots = (GrantedRoot.from_host_path(directory),)

        outcome = self.run_tool(
            "read_file", {"file_path": str(sibling / "x.txt")}, roots=roots
        )

        assert outcome.asked


class TestAnApprovedCallStillGoesThroughTheTranslator(HostFsGraphMixin):
    """The consent card's ``edit`` decision never re-enters the model node.

    ``HumanInTheLoopMiddleware`` replaces the tool call's arguments with the
    human's and dispatches them straight to the tool node, so ``wrap_model_call``
    — where the translation normally happens — does not run again. That is why
    the tool wrapper screens as well, and it is a security seam rather than
    belt-and-braces: without it an edited argument would reach the backend
    untranslated and unscreened.
    """

    def test_an_approved_read_reaches_the_host_and_returns_content(
        self, tmp_path: Path
    ) -> None:
        _, secret = self.tmp_tree(tmp_path)

        outcome = self.run_tool(
            "read_file", {"file_path": secret}, decide={"type": "approve"}
        )

        assert not outcome.asked
        assert outcome.status == "success"
        assert "hunter2" in outcome.content

    def test_an_edited_doubled_path_is_still_normalised(self, tmp_path: Path) -> None:
        _, secret = self.tmp_tree(tmp_path)

        outcome = self.run_tool(
            "read_file",
            {"file_path": secret},
            decide={
                "type": "edit",
                "edited_action": {
                    "name": "read_file",
                    "args": {"file_path": f"/{secret}"},
                },
            },
        )

        assert not outcome.asked
        assert outcome.status == "success"
        assert "hunter2" in outcome.content

    def test_an_edited_refused_shape_is_still_refused(self, tmp_path: Path) -> None:
        _, secret = self.tmp_tree(tmp_path)

        outcome = self.run_tool(
            "read_file",
            {"file_path": secret},
            decide={
                "type": "edit",
                "edited_action": {
                    "name": "read_file",
                    "args": {"file_path": f"{tmp_path}/../{tmp_path.name}/x"},
                },
            },
        )

        assert not outcome.asked
        assert outcome.status == "error"
        assert outcome.content == f"Error: {HostPathMessages.TRAVERSAL}"

    def test_an_edited_windows_path_reaches_the_host_in_windows_grammar(
        self, tmp_path: Path
    ) -> None:
        """No ``C:`` drive here, so the proof is the spelling in the error."""

        _, secret = self.tmp_tree(tmp_path)

        outcome = self.run_tool(
            "read_file",
            {"file_path": secret},
            decide={
                "type": "edit",
                "edited_action": {
                    "name": "read_file",
                    "args": {"file_path": f"{WINDOWS_DOWNLOADS}\\q4.csv"},
                },
            },
        )

        assert not outcome.asked
        assert outcome.status == "error"
        assert "Windows absolute paths are not supported" not in outcome.content, (
            "an edited Windows path died at validate_path — it never reached "
            "the rules or the backend"
        )
        # It got past the validator AND was decoded back for the real call,
        # which only a real filesystem lookup can report.
        assert "not found" in outcome.content
        assert f"{WINDOWS_DOWNLOADS}\\q4.csv" in outcome.content


class RuleVerdictMixin:
    """Verdicts read from deepagents' OWN matcher and interrupt predicates."""

    @staticmethod
    def _rules(roots: Sequence[GrantedRoot]) -> list[FilesystemPermission]:
        return [
            FilesystemPermission(**rule)  # type: ignore[arg-type]
            for rule in HostFilesystemRules.build(tuple(roots))
        ]

    @classmethod
    def verdict(
        cls,
        tool: str,
        supplied: str,
        *,
        roots: Sequence[GrantedRoot] = (),
        operation: str = "read",
    ) -> tuple[str, bool]:
        """``(rule verdict, does the consent interrupt fire)`` for one call."""

        rules = cls._rules(roots)
        translated = HostPathToolMiddleware._screened(tool, {cls._arg(tool): supplied})
        args = (
            translated if isinstance(translated, dict) else {cls._arg(tool): supplied}
        )
        configs = _build_interrupt_on_from_permissions(rules)
        request = _FakeToolCallRequest(tool, args)
        fires = bool(configs[tool]["when"](request)) if tool in configs else False
        try:
            checked = validate_path(str(args[cls._arg(tool)]))
        except ValueError:
            return ("refused-by-validator", fires)
        return (_check_fs_permission(rules, operation, checked), fires)  # type: ignore[arg-type]

    @staticmethod
    def _arg(tool: str) -> str:
        return _FS_TOOL_PATH_ARGS[tool][1]


@dataclass
class _FakeToolCallRequest:
    """The one field deepagents' interrupt predicates read off a request."""

    name: str
    args: dict[str, Any]

    @property
    def tool_call(self) -> dict[str, Any]:
        return {"name": self.name, "args": self.args, "id": "call-1"}


class TestWindowsAndPosixGetTheSameVerdicts(RuleVerdictMixin):
    """Parity, cell for cell, at both layers that can say no."""

    @pytest.mark.parametrize("tool", ["ls", "read_file"])
    def test_an_ungranted_folder_asks_in_either_grammar(self, tool: str) -> None:
        posix = self.verdict(tool, "/Users/p/Downloads")
        windows = self.verdict(tool, WINDOWS_DOWNLOADS)

        assert posix == windows
        assert posix[0] == "interrupt"

    @pytest.mark.parametrize("tool", ["ls", "read_file"])
    def test_a_granted_folder_allows_in_either_grammar(self, tool: str) -> None:
        posix = self.verdict(
            tool,
            "/Users/p/Downloads/q4.csv",
            roots=(GrantedRoot.from_host_path("/Users/p/Downloads"),),
        )
        windows = self.verdict(
            tool,
            f"{WINDOWS_DOWNLOADS}\\q4.csv",
            roots=(GrantedRoot.from_host_path(WINDOWS_DOWNLOADS),),
        )

        assert posix == windows
        assert posix[0] == "allow"

    def test_a_grant_does_not_widen_to_a_sibling_in_either_grammar(self) -> None:
        posix = self.verdict(
            "read_file",
            "/Users/p/DownloadsSecret/x",
            roots=(GrantedRoot.from_host_path("/Users/p/Downloads"),),
        )
        windows = self.verdict(
            "read_file",
            "C:\\Users\\p\\DownloadsSecret\\x",
            roots=(GrantedRoot.from_host_path(WINDOWS_DOWNLOADS),),
        )

        assert posix == windows
        assert posix[0] == "interrupt"

    def test_a_host_write_is_denied_in_either_grammar(self) -> None:
        """D7: no filesystem interrupt may authorize a host mutation."""

        roots_posix = (GrantedRoot.from_host_path("/Users/p/Downloads"),)
        roots_windows = (GrantedRoot.from_host_path(WINDOWS_DOWNLOADS),)

        posix = self.verdict(
            "write_file",
            "/Users/p/Downloads/out.csv",
            roots=roots_posix,
            operation="write",
        )
        windows = self.verdict(
            "write_file",
            f"{WINDOWS_DOWNLOADS}\\out.csv",
            roots=roots_windows,
            operation="write",
        )

        assert posix == windows
        assert posix[0] == "deny"

    def test_a_windows_grant_never_covers_the_posix_path_of_the_same_name(
        self,
    ) -> None:
        """A ``C:`` grant is not a grant of ``/Users/p/Downloads``."""

        verdict, _ = self.verdict(
            "read_file",
            "/Users/p/Downloads/q4.csv",
            roots=(GrantedRoot.from_host_path(WINDOWS_DOWNLOADS),),
        )

        assert verdict == "interrupt"


class TestPosixCallsAreUnchanged:
    """The regression proof: nothing that worked before is touched.

    Identity, not equality — a call the translator leaves alone must be the very
    object deepagents would have received, so no downstream consumer can observe
    that this middleware ran.
    """

    @pytest.mark.parametrize(
        ("tool", "args"),
        [
            ("ls", {"path": "/memories"}),
            ("read_file", {"file_path": "/memories/user/profile.json"}),
            ("read_file", {"file_path": "/workspace/downloads/q4.csv"}),
            ("write_file", {"file_path": "/drafts/reply.md"}),
            ("ls", {"path": "/Users/p/Downloads"}),
            ("read_file", {"file_path": "/Users/p/Downloads/q4.csv"}),
            ("ls", {"path": "/"}),
            ("ls", {"path": "relative.txt"}),
            ("glob", {"pattern": "*.md", "path": None}),
            ("glob", {"pattern": "**/*.py", "path": "/skills"}),
            ("grep", {"pattern": "needle", "path": None}),
            ("grep", {"pattern": "needle", "path": "/memories", "glob": "*.md"}),
        ],
    )
    def test_an_untouched_call_is_passed_through_verbatim(
        self, tool: str, args: dict[str, Any]
    ) -> None:
        assert HostPathToolMiddleware._screened(tool, args) is None

    @pytest.mark.parametrize(
        "tool", ["task", "web_search", "todo_write", "load_skill", "execute"]
    )
    def test_a_non_filesystem_tool_is_never_inspected(self, tool: str) -> None:
        assert (
            HostPathToolMiddleware._screened(tool, {"path": WINDOWS_DOWNLOADS}) is None
        )

    def test_a_traversal_below_a_relative_root_is_left_to_the_backend(self) -> None:
        """A relative remainder is not host-shaped; refusing it would regress."""

        assert HostPathToolMiddleware._screened("glob", {"pattern": "../*.md"}) is None

    def test_the_tool_argument_map_matches_deepagents(self) -> None:
        """Pins the map this middleware mirrors, so a skew fails loudly here."""

        upstream = {
            tool: path_arg for tool, (_, path_arg, _, _) in _FS_TOOL_PATH_ARGS.items()
        }
        assert HostFsToolArgs.tool_names() == frozenset(upstream)
        for tool, path_arg in upstream.items():
            assert path_arg in HostFsToolArgs.for_tool(tool), tool


class TestCanonicalAndNativeAreInverses:
    """The encoding contract the tool layer and the backend both depend on."""

    HOST_SHAPES = (
        "/Users/p/Downloads",
        "//Users/p/Downloads",
        "/Users/p/./Downloads/",
        WINDOWS_DOWNLOADS,
        "C:/Users/p/Downloads",
        "\\\\?\\C:\\Users\\p\\Downloads",
        "c:\\users\\p\\downloads",
        UNC_REPORTS,
        "\\\\?\\UNC\\server\\share\\reports",
        "C:\\",
        "\\\\server\\share",
    )

    @pytest.mark.parametrize("supplied", HOST_SHAPES)
    def test_the_canonical_form_survives_the_validator_unchanged(
        self, supplied: str
    ) -> None:
        """If it were rewritten, the rules would judge a different string."""

        canonical = HostPathClassifier.classify(supplied).canonical

        assert validate_path(canonical) == canonical

    @pytest.mark.parametrize("supplied", HOST_SHAPES)
    def test_the_canonical_form_has_exactly_one_leading_slash(
        self, supplied: str
    ) -> None:
        """A ``//`` root overlaps no interrupt anchor — that WAS the bypass."""

        canonical = HostPathClassifier.classify(supplied).canonical

        assert canonical.startswith("/")
        assert not canonical.startswith("//")

    @pytest.mark.parametrize("supplied", HOST_SHAPES)
    def test_native_undoes_canonical_exactly(self, supplied: str) -> None:
        classified = HostPathClassifier.classify(supplied)

        assert HostPathClassifier.native(classified.canonical) == classified.display

    @pytest.mark.parametrize(
        "supplied",
        [
            "/Users/p/Downloads",
            "/memories/a.md",
            "/tmp/x",
            "/",
            "relative.txt",
            "*.md",
            "",
        ],
    )
    def test_decoding_never_alters_a_posix_path(self, supplied: str) -> None:
        """The backend wrapper must be inert everywhere but Windows."""

        assert HostPathClassifier.native(supplied) == supplied

    @pytest.mark.parametrize(
        ("supplied", "expected"),
        [
            ("/C:", "C:\\"),
            ("/C:/Users/p", "C:\\Users\\p"),
            ("/UNC:/server/share", "\\\\server\\share"),
            ("/UNC:/server/share/reports", "\\\\server\\share\\reports"),
        ],
    )
    def test_the_windows_decodings_are_spelled_out(
        self, supplied: str, expected: str
    ) -> None:
        assert HostPathClassifier.native(supplied) == expected

    def test_a_bare_unc_marker_is_not_inflated_into_a_share(self) -> None:
        """``/UNC:/server`` names no share; inventing one would be a guess."""

        assert HostPathClassifier.native("/UNC:/server") == "/UNC:/server"

    def test_the_two_spellings_of_one_windows_folder_agree(self) -> None:
        """A model may write either; both must reach one rule verdict."""

        backslash = HostPathClassifier.classify(WINDOWS_DOWNLOADS).canonical
        url_ish = HostPathClassifier.classify("/C:/Users/p/Downloads").canonical

        assert backslash == url_ish


class RecordingBackend:
    """Records the paths it is handed and answers with paths of its own."""

    def __init__(
        self, entries: Iterable[str] = (), matches: Iterable[str] = ()
    ) -> None:
        self.seen: list[Any] = []
        self._entries = list(entries)
        self._matches = list(matches)

    def ls(self, path: str) -> LsResult:
        self.seen.append(path)
        return LsResult(entries=[{"path": entry} for entry in self._entries])

    async def als(self, path: str) -> LsResult:
        return self.ls(path)

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        self.seen.append((file_path, offset, limit))
        return ReadResult(file_data={"content": "x", "encoding": "utf-8"})

    async def aread(
        self, file_path: str, offset: int = 0, limit: int = 2000
    ) -> ReadResult:
        return self.read(file_path, offset, limit)

    def glob(self, pattern: str, path: str | None = None) -> GlobResult:
        self.seen.append((pattern, path))
        return GlobResult(matches=[{"path": match} for match in self._matches])

    async def aglob(self, pattern: str, path: str | None = None) -> GlobResult:
        return self.glob(pattern, path)

    def grep(
        self, pattern: str, path: str | None = None, glob: str | None = None
    ) -> GrepResult:
        self.seen.append((pattern, path, glob))
        return GrepResult(
            matches=[{"path": m, "line": 1, "text": "hit"} for m in self._matches]
        )

    async def agrep(
        self, pattern: str, path: str | None = None, glob: str | None = None
    ) -> GrepResult:
        return self.grep(pattern, path, glob)

    def write(self, file_path: str, content: str) -> WriteResult:
        self.seen.append((file_path, content))
        return WriteResult(path=file_path)

    async def awrite(self, file_path: str, content: str) -> WriteResult:
        return self.write(file_path, content)

    def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,  # noqa: FBT001, FBT002
    ) -> EditResult:
        self.seen.append((file_path, old_string, new_string, replace_all))
        return EditResult(path=file_path, occurrences=1)

    async def aedit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,  # noqa: FBT001, FBT002
    ) -> EditResult:
        return self.edit(file_path, old_string, new_string, replace_all)

    def download_files(self, paths: list[str]) -> list[str]:
        self.seen.append(paths)
        return list(paths)

    @property
    def id(self) -> str:
        return "recording"


class TestNativeHostPathBackend:
    r"""The other half of the encoding: the host is opened in its own grammar.

    The tool layer hands this backend ``/C:/Users/p`` because that is the only
    spelling ``validate_path`` and the permission globs accept. Windows cannot
    open that string, so the wrapper must undo the encoding — and must put it
    back on the way out, because those paths are re-checked against the rules
    and then handed to the model to address again.

    What CANNOT be proven on this host: that the decoded string opens the file
    Windows intends. That needs the windows-latest job.
    """

    async def test_a_windows_path_reaches_the_host_in_windows_grammar(self) -> None:
        inner = RecordingBackend()
        backend = NativeHostPathBackend(inner)

        await backend.als("/C:/Users/p/Downloads")

        assert inner.seen == [WINDOWS_DOWNLOADS]

    async def test_a_unc_path_reaches_the_host_in_unc_grammar(self) -> None:
        inner = RecordingBackend()
        backend = NativeHostPathBackend(inner)

        await backend.aread("/UNC:/server/share/reports/q4.csv")

        assert inner.seen == [(f"{UNC_REPORTS}\\q4.csv", 0, 2000)]

    async def test_listed_paths_come_back_canonical(self) -> None:
        inner = RecordingBackend(entries=[f"{WINDOWS_DOWNLOADS}\\q4.csv"])
        backend = NativeHostPathBackend(inner)

        result = await backend.als("/C:/Users/p/Downloads")

        assert result.entries == [{"path": "/C:/Users/p/Downloads/q4.csv"}]

    async def test_grep_matches_come_back_canonical(self) -> None:
        inner = RecordingBackend(matches=[f"{WINDOWS_DOWNLOADS}\\q4.csv"])
        backend = NativeHostPathBackend(inner)

        result = await backend.agrep("needle", "/C:/Users/p/Downloads")

        assert result.matches is not None
        assert result.matches[0]["path"] == "/C:/Users/p/Downloads/q4.csv"
        assert result.matches[0]["text"] == "hit"

    async def test_glob_translates_the_pattern_and_the_root(self) -> None:
        inner = RecordingBackend()
        backend = NativeHostPathBackend(inner)

        await backend.aglob("/C:/Users/p/Downloads/*.csv", "/C:/Users/p")

        assert inner.seen == [(f"{WINDOWS_DOWNLOADS}\\*.csv", "C:\\Users\\p")]

    async def test_a_missing_search_root_stays_missing(self) -> None:
        inner = RecordingBackend()
        backend = NativeHostPathBackend(inner)

        await backend.aglob("*.md", None)

        assert inner.seen == [("*.md", None)]

    @pytest.mark.parametrize(
        "path", ["/Users/p/Downloads", "/memories/a.md", "/tmp/scratch"]
    )
    async def test_a_posix_path_is_delegated_byte_for_byte(self, path: str) -> None:
        inner = RecordingBackend(entries=[f"{path}/child.txt"])
        backend = NativeHostPathBackend(inner)

        result = await backend.als(path)

        assert inner.seen == [path]
        assert result.entries == [{"path": f"{path}/child.txt"}]

    async def test_a_write_is_translated_and_reported_canonically(self) -> None:
        inner = RecordingBackend()
        backend = NativeHostPathBackend(inner)

        result = await backend.awrite("/C:/Users/p/Downloads/.copilot/notes.json", "{}")

        assert inner.seen == [(f"{WINDOWS_DOWNLOADS}\\.copilot\\notes.json", "{}")]
        assert result.path == "/C:/Users/p/Downloads/.copilot/notes.json"

    def test_an_untranslated_operation_still_reaches_the_host(self) -> None:
        """Delegation stays total for operations this wrapper does not name."""

        inner = RecordingBackend()
        backend = NativeHostPathBackend(inner)

        assert backend.download_files(["/a"]) == ["/a"]
        assert backend.id == "recording"
        assert backend.inner is inner
