r"""Unit tests for host-vs-virtual path classification and host-root coverage.

The defect these exist to prevent: a host-looking path that is not recognised as
one gets answered by a virtual backend, which has nothing at that path and
returns an EMPTY LISTING as a SUCCESS. So every shape a real user's path can
arrive in — POSIX and every Windows spelling — must classify, and everything that
is not a resolvable folder must fail CLOSED rather than become a grant request.

Classification is by SHAPE, so the Windows cases below are meaningful on a macOS
test runner: a classifier that only knew POSIX would mis-route all of them.
"""

from __future__ import annotations

import pytest

from agent_runtime.capabilities.desktop.host_path import (
    ClassifiedPath,
    HostPathClassifier,
    HostPathFlavour,
    HostPathKind,
    HostPathMessages,
    HostPathRefusal,
    HostRootIndex,
)


class ClassifyMixin:
    """Shared helpers over :class:`HostPathClassifier`."""

    @staticmethod
    def classify(path: str | None) -> ClassifiedPath:
        return HostPathClassifier.classify(path)

    @classmethod
    def host(cls, path: str) -> ClassifiedPath:
        """Classify and assert the path IS a resolvable host path."""
        classified = cls.classify(path)
        assert classified.kind is HostPathKind.HOST_ABSOLUTE, path
        return classified


class TestPosixClassification(ClassifyMixin):
    """POSIX shapes: absolute host paths, virtual roots, and relative remainders."""

    def test_posix_absolute_is_a_host_path(self) -> None:
        classified = self.host("/Users/parthpahwa/Downloads")
        assert classified.flavour is HostPathFlavour.POSIX
        assert classified.root == "/"
        assert classified.segments == ("Users", "parthpahwa", "Downloads")
        assert classified.folder_name == "Downloads"
        assert classified.display == "/Users/parthpahwa/Downloads"

    @pytest.mark.parametrize(
        "path",
        [
            "/workspace/proj/a.txt",
            "/memories/user/x.json",
            "/policies/tool_use.json",
            "/skills/writer/SKILL.md",
            "/drafts/note.md",
            "/subagents/task-1/trace.jsonl",
            "/large_tool_results/abc123",
        ],
    )
    def test_virtual_roots_are_not_host_paths(self, path: str) -> None:
        assert self.classify(path).kind is HostPathKind.VIRTUAL
        assert HostPathClassifier.is_host_shaped(path) is False

    def test_relative_remainder_stays_virtual(self) -> None:
        # The prefix-stripped form CompositeBackend delivers below a mount.
        classified = self.classify("sub/b.py")
        assert classified.kind is HostPathKind.VIRTUAL
        assert classified.root == ""
        assert classified.segments == ("sub", "b.py")

    def test_bare_root_is_the_addressed_backends_own_root(self) -> None:
        classified = self.classify("/")
        assert classified.kind is HostPathKind.VIRTUAL
        assert classified.segments == ()
        # A volume root is never grantable, so it is nobody's host path.
        assert HostPathClassifier.is_host_shaped("/") is False

    def test_empty_path_is_virtual(self) -> None:
        assert self.classify("").kind is HostPathKind.VIRTUAL
        assert self.classify(None).kind is HostPathKind.VIRTUAL

    def test_backslash_is_a_filename_character_on_posix(self) -> None:
        # A POSIX filename may contain a backslash; reading it as a separator
        # would silently address a different file.
        classified = self.host("/Users/p/we\\ird")
        assert classified.flavour is HostPathFlavour.POSIX
        assert classified.segments == ("Users", "p", "we\\ird")

    def test_dot_segments_and_duplicate_slashes_are_dropped(self) -> None:
        classified = self.host("/Users//p/./Downloads/")
        assert classified.segments == ("Users", "p", "Downloads")

    def test_tilde_is_refused_because_this_process_cannot_expand_it(self) -> None:
        classified = self.classify("~/Downloads")
        assert classified.kind is HostPathKind.HOST_AMBIGUOUS
        assert classified.refusal is HostPathRefusal.HOME_RELATIVE
        # Still host-shaped: it must be claimed so it can be refused out loud.
        assert HostPathClassifier.is_host_shaped("~/Downloads") is True


class TestWindowsClassification(ClassifyMixin):
    """Every Windows root spelling, including the ones that must fail closed."""

    @pytest.mark.parametrize(
        "path",
        [
            "C:\\Users\\parth\\Downloads",
            "C:/Users/parth/Downloads",
            "c:\\Users\\parth\\Downloads",
        ],
    )
    def test_drive_absolute_forms(self, path: str) -> None:
        classified = self.host(path)
        assert classified.flavour is HostPathFlavour.WINDOWS
        assert classified.root == "C:\\"
        assert classified.segments == ("Users", "parth", "Downloads")
        assert classified.display == "C:\\Users\\parth\\Downloads"

    def test_extended_length_drive_prefix_is_unwrapped(self) -> None:
        classified = self.host("\\\\?\\C:\\Users\\parth\\Downloads")
        assert classified.root == "C:\\"
        assert classified.segments == ("Users", "parth", "Downloads")

    def test_unc_share_is_the_root(self) -> None:
        classified = self.host("\\\\fileserver\\team\\reports\\q4.xlsx")
        assert classified.root == "\\\\fileserver\\team"
        assert classified.segments == ("reports", "q4.xlsx")
        assert classified.display == "\\\\fileserver\\team\\reports\\q4.xlsx"

    def test_extended_unc_prefix_is_unwrapped(self) -> None:
        classified = self.host("\\\\?\\UNC\\fileserver\\team\\reports")
        assert classified.root == "\\\\fileserver\\team"
        assert classified.segments == ("reports",)

    def test_bare_volume_root_is_absolute_but_holds_no_segments(self) -> None:
        # ``C:\`` genuinely is absolute; it is the grant flow that refuses to ask
        # for a whole volume, not the classifier that pretends it is malformed.
        classified = self.host("C:\\")
        assert classified.root == "C:\\"
        assert classified.segments == ()

    @pytest.mark.parametrize(
        ("path", "refusal"),
        [
            ("C:Users\\parth", HostPathRefusal.DRIVE_RELATIVE),
            ("C:", HostPathRefusal.DRIVE_RELATIVE),
            ("\\Users\\parth", HostPathRefusal.ROOT_RELATIVE),
            ("\\", HostPathRefusal.ROOT_RELATIVE),
            ("\\\\fileserver", HostPathRefusal.INCOMPLETE_UNC),
            ("\\\\", HostPathRefusal.INCOMPLETE_UNC),
        ],
    )
    def test_underspecified_windows_shapes_are_ambiguous(
        self, path: str, refusal: HostPathRefusal
    ) -> None:
        # Their meaning depends on host state this process cannot see, so there
        # is no single folder we could truthfully ask the user to grant.
        classified = self.classify(path)
        assert classified.kind is HostPathKind.HOST_AMBIGUOUS
        assert classified.refusal is refusal
        assert HostPathClassifier.is_host_shaped(path) is True

    @pytest.mark.parametrize(
        ("path", "refusal"),
        [
            ("\\\\.\\PhysicalDrive0", HostPathRefusal.DEVICE_NAMESPACE),
            ("\\\\?\\GLOBALROOT\\Device\\Foo", HostPathRefusal.DEVICE_NAMESPACE),
            ("\\\\?\\Volume{2eca078d}\\x", HostPathRefusal.DEVICE_NAMESPACE),
            ("C:\\Users\\NUL", HostPathRefusal.RESERVED_NAME),
            ("C:\\Users\\nul.txt", HostPathRefusal.RESERVED_NAME),
            ("C:\\Users\\COM1\\x", HostPathRefusal.RESERVED_NAME),
            ("C:\\Users\\conin$", HostPathRefusal.RESERVED_NAME),
            ("C:\\Users\\parth.", HostPathRefusal.TRAILING_DOT_OR_SPACE),
            ("C:\\Users\\parth ", HostPathRefusal.TRAILING_DOT_OR_SPACE),
        ],
    )
    def test_device_and_reserved_shapes_are_unsafe(
        self, path: str, refusal: HostPathRefusal
    ) -> None:
        classified = self.classify(path)
        assert classified.kind is HostPathKind.UNSAFE
        assert classified.refusal is refusal

    def test_leading_dot_segment_is_a_normal_folder(self) -> None:
        # Only a TRAILING dot is the Windows normalization trap.
        assert self.host("C:\\Users\\parth\\.config").segments[-1] == ".config"


class TestTraversalFailsClosed(ClassifyMixin):
    """A ``..`` segment fails the whole path — it is never normalised away."""

    @pytest.mark.parametrize(
        "path",
        [
            "/Users/parth/../../etc/passwd",
            "/Users/parth/..",
            "C:\\Users\\parth\\..\\..\\Windows",
            "\\\\?\\C:\\Users\\..\\Windows",
            "\\\\srv\\share\\..\\other",
            "/workspace/proj/../../secrets",
            "sub/../../etc",
        ],
    )
    def test_traversal_is_unsafe(self, path: str) -> None:
        classified = self.classify(path)
        assert classified.kind is HostPathKind.UNSAFE
        assert classified.refusal is HostPathRefusal.TRAVERSAL
        # Nothing survives to be resolved or granted.
        assert classified.segments == ()

    @pytest.mark.parametrize("path", ["/Users/p/\x00etc", "C:\\Users\\p\\a\nb"])
    def test_control_characters_are_unsafe(self, path: str) -> None:
        classified = self.classify(path)
        assert classified.kind is HostPathKind.UNSAFE
        assert classified.refusal is HostPathRefusal.CONTROL_CHARACTER

    def test_dotdot_filename_lookalikes_are_still_allowed(self) -> None:
        # ``..foo`` is a legal name; only the exact ``..`` segment is traversal.
        assert self.host("/Users/p/..foo").segments[-1] == "..foo"


class TestContainmentAndRelative(ClassifyMixin):
    """Coverage is segment-wise, flavour-exact, and case-correct per platform."""

    def test_root_contains_descendant_and_itself(self) -> None:
        root = self.host("/Users/p/Downloads")
        assert root.contains(root)
        assert root.contains(self.host("/Users/p/Downloads/reports/q4.csv"))

    def test_sibling_prefix_is_not_covered(self) -> None:
        root = self.host("/Users/p/Downloads")
        assert root.contains(self.host("/Users/p/Downloads2/x")) is False

    def test_relative_is_posix_even_for_a_windows_root(self) -> None:
        root = self.host("C:\\Users\\parth\\Downloads")
        target = self.host("C:\\Users\\parth\\Downloads\\reports\\q4.xlsx")
        # The broker only ever accepts POSIX, root-relative paths.
        assert target.relative_to(root) == "reports/q4.xlsx"
        assert root.relative_to(root) == ""

    def test_windows_coverage_folds_case(self) -> None:
        root = self.host("C:\\USERS\\Parth")
        assert root.contains(self.host("c:/users/parth/Downloads"))

    def test_posix_coverage_preserves_case(self) -> None:
        root = self.host("/Users/p")
        assert root.contains(self.host("/users/p/x")) is False

    def test_cross_flavour_never_covers(self) -> None:
        assert self.host("/Users/p").contains(self.host("C:\\Users\\p\\x")) is False

    def test_relative_to_an_uncovering_root_is_a_programming_error(self) -> None:
        with pytest.raises(ValueError, match="not covered"):
            self.host("/etc/passwd").relative_to(self.host("/Users/p"))

    def test_parent_of_a_file_is_its_folder(self) -> None:
        parent = self.host("/Users/p/Downloads/q4.csv").parent()
        assert parent.kind is HostPathKind.HOST_ABSOLUTE
        assert parent.display == "/Users/p/Downloads"

    def test_parent_of_a_top_level_entry_is_refused_not_a_volume_grant(self) -> None:
        # "grant me the whole drive" must never be a request we can make.
        parent = self.host("/q4.csv").parent()
        assert parent.kind is HostPathKind.UNSAFE
        assert parent.refusal is HostPathRefusal.VOLUME_ROOT
        assert self.host("C:\\q4.csv").parent().refusal is HostPathRefusal.VOLUME_ROOT


class TestHostRootIndex(ClassifyMixin):
    """The local translation table from granted host roots to mount names."""

    @classmethod
    def index(cls, **roots: str) -> HostRootIndex:
        return HostRootIndex({key: cls.classify(root) for key, root in roots.items()})

    def test_covers_and_computes_the_relative_path(self) -> None:
        match = self.index(downloads="/Users/p/Downloads").cover(
            self.host("/Users/p/Downloads/reports/q4.csv")
        )
        assert match is not None
        assert match.key == "downloads"
        assert match.relative == "reports/q4.csv"

    def test_longest_root_wins(self) -> None:
        index = self.index(home="/Users/p", downloads="/Users/p/Downloads")
        match = index.cover(self.host("/Users/p/Downloads/q4.csv"))
        assert match is not None
        assert match.key == "downloads"
        assert match.relative == "q4.csv"

    def test_uncovered_path_has_no_match(self) -> None:
        index = self.index(downloads="/Users/p/Downloads")
        assert index.cover(self.host("/Users/p/Documents/x")) is None

    def test_non_host_roots_are_ignored(self) -> None:
        # A mount with no known root (the broker projection is path-free) cannot
        # answer a host path — it must not accidentally match one either.
        index = self.index(unknown="", ambiguous="C:rel", traversal="/Users/../etc")
        assert index.cover(self.host("/Users/p/x")) is None

    def test_root_itself_resolves_to_the_mount_root(self) -> None:
        match = self.index(downloads="/Users/p/Downloads").cover(
            self.host("/Users/p/Downloads")
        )
        assert match is not None
        assert match.relative == ""

    def test_a_virtual_target_is_never_covered(self) -> None:
        index = self.index(downloads="/Users/p/Downloads")
        assert index.cover(self.classify("/workspace/downloads/x")) is None


class TestRefusalMessages:
    """Every refusal has safe, actionable copy — no silence, no host detail."""

    @pytest.mark.parametrize("refusal", list(HostPathRefusal))
    def test_every_refusal_has_dedicated_copy(self, refusal: HostPathRefusal) -> None:
        message = HostPathMessages.for_refusal(refusal)
        assert message != HostPathMessages.GENERIC
        assert message.endswith(".")

    def test_missing_refusal_falls_back_to_generic(self) -> None:
        assert HostPathMessages.for_refusal(None) == HostPathMessages.GENERIC
