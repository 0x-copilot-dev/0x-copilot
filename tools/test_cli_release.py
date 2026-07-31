"""Tests for the CLI release planner.

The versioning policy is the part worth pinning: while the package is 0.x a
breaking change moves the MINOR digit, because npm resolves `^0.1.4` as
`>=0.1.4 <0.2.0` and the minor is therefore what actually breaks consumers.
Getting that backwards ships a breaking change as a patch to everyone.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cli_release import (
    ChangelogFile,
    ChangelogRenderer,
    Commit,
    CommitParser,
    PackageVersion,
    ReleaseError,
    _Bump,
)


def _log(*records: tuple[str, str]) -> str:
    return "".join(f"{sha}\x1f{message}\x1e" for sha, message in records)


# --- parsing ---------------------------------------------------------------


def test_parses_type_scope_and_description() -> None:
    (commit,) = CommitParser.parse_log(_log(("a" * 40, "feat(cli): add a flag\n")))
    assert (commit.type, commit.scope, commit.description) == (
        "feat",
        "cli",
        "add a flag",
    )
    assert commit.breaking is False


def test_bang_marks_breaking() -> None:
    (commit,) = CommitParser.parse_log(
        _log(("a" * 40, "feat(cli)!: drop the old flag\n"))
    )
    assert commit.breaking is True


def test_breaking_change_footer_marks_breaking() -> None:
    message = (
        "fix(desktop): change the payload layout\n\nBREAKING CHANGE: paths moved\n"
    )
    (commit,) = CommitParser.parse_log(_log(("a" * 40, message)))
    assert commit.breaking is True


def test_hyphenated_breaking_change_footer_is_also_recognised() -> None:
    message = "fix: rework\n\nBREAKING-CHANGE: it moved\n"
    (commit,) = CommitParser.parse_log(_log(("a" * 40, message)))
    assert commit.breaking is True


def test_non_conventional_subjects_are_dropped_not_guessed() -> None:
    """An unlabelled commit must not silently influence the bump."""
    commits = CommitParser.parse_log(
        _log(("a" * 40, "wip\n"), ("b" * 40, "feat: real one\n"))
    )
    assert [commit.description for commit in commits] == ["real one"]


# --- the 0.x policy --------------------------------------------------------


def test_breaking_bumps_minor_while_pre_1_0() -> None:
    commits = (Commit("a" * 40, "feat", None, True, "x"),)
    assert _Bump.decide(commits, "auto") == "minor"
    assert _Bump.apply("0.1.4", "minor") == "0.2.0"


def test_feature_and_fix_bump_patch_while_pre_1_0() -> None:
    commits = (
        Commit("a" * 40, "feat", None, False, "x"),
        Commit("b" * 40, "fix", None, False, "y"),
    )
    assert _Bump.decide(commits, "auto") == "patch"
    assert _Bump.apply("0.1.4", "patch") == "0.1.5"


def test_an_explicit_major_is_how_the_package_graduates_to_1_0() -> None:
    assert _Bump.apply("0.1.4", "major") == "1.0.0"


def test_after_1_0_the_same_arithmetic_is_ordinary_semver() -> None:
    assert _Bump.apply("1.4.2", "major") == "2.0.0"
    assert _Bump.apply("1.4.2", "minor") == "1.5.0"
    assert _Bump.apply("1.4.2", "patch") == "1.4.3"


def test_override_wins_over_the_computed_bump() -> None:
    commits = (Commit("a" * 40, "feat", None, True, "x"),)  # would auto to minor
    assert _Bump.decide(commits, "patch") == "patch"


def test_no_commits_still_produces_a_patch_not_a_crash() -> None:
    assert _Bump.decide((), "auto") == "patch"


def test_a_malformed_current_version_is_rejected() -> None:
    with pytest.raises(ReleaseError, match="X.Y.Z"):
        _Bump.apply("0.1", "patch")


# --- changelog -------------------------------------------------------------


def test_entry_groups_by_section_and_hoists_breaking() -> None:
    commits = (
        Commit("a" * 40, "feat", "cli", False, "add a flag"),
        Commit("b" * 40, "fix", None, False, "stop crashing"),
        Commit("c" * 40, "feat", "api", True, "rename the field"),
        Commit("d" * 40, "chore", None, False, "bump deps"),
    )
    entry = ChangelogRenderer.render(
        "0.2.0", commits, released_on="2026-07-31", previous_tag="cli-v0.1.4"
    )
    assert entry.startswith("## 0.2.0 - 2026-07-31")
    # Breaking is its own section and comes first.
    assert entry.index("### Breaking Changes") < entry.index("### Features")
    assert "rename the field" in entry.split("### Features")[0]
    assert "**cli:** add a flag" in entry
    assert "### Maintenance" in entry
    assert "compare/cli-v0.1.4...cli-v0.2.0" in entry


def test_a_breaking_commit_is_not_also_listed_in_its_type_section() -> None:
    commits = (Commit("c" * 40, "feat", None, True, "rename the field"),)
    entry = ChangelogRenderer.render(
        "0.2.0", commits, released_on="2026-07-31", previous_tag=None
    )
    assert entry.count("rename the field") == 1


def test_first_release_omits_the_compare_link() -> None:
    entry = ChangelogRenderer.render(
        "0.1.5",
        (Commit("a" * 40, "fix", None, False, "x"),),
        released_on="2026-07-31",
        previous_tag=None,
    )
    assert "compare/" not in entry


def test_an_empty_range_renders_a_placeholder_rather_than_a_bare_heading() -> None:
    entry = ChangelogRenderer.render(
        "0.1.5", (), released_on="2026-07-31", previous_tag="cli-v0.1.4"
    )
    assert "_No conventional commits in this range._" in entry


def test_newest_entry_lands_directly_under_the_header(tmp_path: Path) -> None:
    path = tmp_path / "CHANGELOG.md"
    path.write_text(ChangelogFile.HEADER + "\n## 0.1.4 - 2026-01-01\n\n- older\n")
    document = ChangelogFile.prepend("## 0.1.5 - 2026-07-31\n\n- newer\n", path)
    assert document.index("## 0.1.5") < document.index("## 0.1.4")
    assert document.startswith("# Changelog")


def test_prepend_creates_the_file_with_a_header_when_absent(tmp_path: Path) -> None:
    document = ChangelogFile.prepend(
        "## 0.1.5 - 2026-07-31\n\n- first\n", tmp_path / "n.md"
    )
    assert document.startswith("# Changelog")
    assert "## 0.1.5" in document


# --- package.json ----------------------------------------------------------


def test_version_write_touches_only_the_version_and_keeps_key_order(
    tmp_path: Path,
) -> None:
    path = tmp_path / "package.json"
    original = json.dumps(
        {"name": "@0x-copilot/cli", "version": "0.1.4", "bin": {"copilot": "x"}},
        indent=2,
    )
    path.write_text(original + "\n")
    PackageVersion.write("0.2.0", path)
    written = path.read_text()
    assert json.loads(written)["version"] == "0.2.0"
    # Order and formatting survive: only the one field changed.
    assert written == original.replace('"0.1.4"', '"0.2.0"') + "\n"


def test_the_real_package_version_is_readable_and_semver() -> None:
    assert _Bump.apply(PackageVersion.read(), "patch")
