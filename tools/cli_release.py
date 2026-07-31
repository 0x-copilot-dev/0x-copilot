"""Compute the next @0x-copilot/cli version and render its changelog entry.

Driven by .github/workflows/release-cli.yml. Kept as a module rather than a
workflow heredoc so ruff and pytest can see it -- see
tools/apply_branch_protection.py for what that costs when it is not.

VERSIONING POLICY (pre-1.0, deliberate)
--------------------------------------
The package is 0.x, and npm resolves ``^0.1.4`` as ``>=0.1.4 <0.2.0``. While the
leading digit is 0 the MINOR position is what actually breaks consumers, so it
plays the role major will play after 1.0:

    breaking change  -> 0.1.4 -> 0.2.0   (minor)
    feature or fix   -> 0.1.4 -> 0.1.5   (patch)

A release may still be forced to ``major`` by hand, which is how the package
graduates: 0.x -> 1.0.0. After that the same code applies ordinary semver,
because ``_Bump.apply`` keys off whether the current major is 0.

A commit is breaking when it either marks the type with ``!`` (``feat!:``,
``fix(scope)!:``) or carries a ``BREAKING CHANGE:`` footer -- both spellings from
the Conventional Commits spec.

CHANGELOG SCOPE
---------------
Every commit since the previous ``cli-v*`` tag counts, not just those touching
``tools/cli/``. The published tarball assembles a payload from the desktop app at
pack time, so a change anywhere in the product is a change to what the CLI ships.
Scoping the log to the CLI directory would hide almost everything users receive.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from dataclasses import dataclass
from datetime import date as date_type
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_JSON = REPO_ROOT / "tools/cli/package.json"
CHANGELOG = REPO_ROOT / "tools/cli/CHANGELOG.md"
TAG_PREFIX = "cli-v"
REPO_URL = "https://github.com/0x-copilot-dev/0x-copilot"

# Conventional-commit header: type(scope)!: description
_HEADER = re.compile(
    r"^(?P<type>[a-z]+)(?:\((?P<scope>[^)]*)\))?(?P<bang>!)?:\s*(?P<description>.+)$"
)
_BREAKING_FOOTER = re.compile(r"^BREAKING[ -]CHANGE:", re.MULTILINE)
_SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")

# Ordered: how sections appear in the rendered entry.
_SECTIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Features", ("feat",)),
    ("Bug Fixes", ("fix",)),
    ("Performance", ("perf",)),
    ("Reverts", ("revert",)),
    ("Maintenance", ("build", "chore", "ci", "docs", "refactor", "style", "test")),
)


class ReleaseError(RuntimeError):
    """A release cannot be computed from the current repository state."""


@dataclass(frozen=True)
class Commit:
    """One parsed conventional commit."""

    sha: str
    type: str
    scope: str | None
    breaking: bool
    description: str

    @property
    def short_sha(self) -> str:
        return self.sha[:8]


class CommitReader:
    """Reads commits from git. The only component that shells out."""

    @staticmethod
    def _git(*args: str) -> str:
        completed = subprocess.run(
            ["git", "-C", str(REPO_ROOT), *args],
            capture_output=True,
            text=True,
            check=True,
        )
        return completed.stdout.strip()

    @classmethod
    def last_release_tag(cls) -> str | None:
        """Return the most recent ``cli-v*`` tag, or None on a first release."""
        tags = cls._git("tag", "--list", f"{TAG_PREFIX}*", "--sort=-v:refname")
        for tag in tags.splitlines():
            if tag.strip():
                return tag.strip()
        return None

    @classmethod
    def commits_since(cls, tag: str | None) -> tuple[Commit, ...]:
        """Return parsed commits after ``tag`` (or the whole history if None).

        Merge commits are excluded: their subjects are "Merge pull request #N"
        noise, and the work they carry is already present as the merged commits.
        """
        span = f"{tag}..HEAD" if tag else "HEAD"
        raw = cls._git("log", span, "--no-merges", "--format=%H%x1f%B%x1e")
        return CommitParser.parse_log(raw)


class CommitParser:
    """Turns raw ``git log`` output into typed commits."""

    @classmethod
    def parse_log(cls, raw: str) -> tuple[Commit, ...]:
        """Parse the 0x1f/0x1e delimited log produced by ``CommitReader``."""
        commits: list[Commit] = []
        for record in raw.split("\x1e"):
            record = record.strip()
            if not record:
                continue
            sha, _, message = record.partition("\x1f")
            parsed = cls.parse_one(sha.strip(), message)
            if parsed is not None:
                commits.append(parsed)
        return tuple(commits)

    @classmethod
    def parse_one(cls, sha: str, message: str) -> Commit | None:
        """Return a Commit, or None when the subject is not conventional.

        Non-conventional subjects are dropped from the changelog rather than
        guessed at. They still count for nothing in the bump, which is the safe
        direction: an unlabelled commit cannot silently force a minor.
        """
        lines = message.strip().splitlines()
        if not lines:
            return None
        match = _HEADER.match(lines[0].strip())
        if match is None:
            return None
        breaking = bool(match.group("bang")) or bool(_BREAKING_FOOTER.search(message))
        return Commit(
            sha=sha,
            type=match.group("type"),
            scope=match.group("scope") or None,
            breaking=breaking,
            description=match.group("description").strip(),
        )


class _Bump:
    """Version arithmetic under the pre-1.0 policy documented at module level."""

    PATCH = "patch"
    MINOR = "minor"
    MAJOR = "major"
    AUTO = "auto"
    CHOICES = (AUTO, PATCH, MINOR, MAJOR)

    @classmethod
    def decide(cls, commits: tuple[Commit, ...], override: str) -> str:
        """Return the bump to apply, honouring an explicit override."""
        if override != cls.AUTO:
            return override
        if any(commit.breaking for commit in commits):
            return cls.MINOR
        return cls.PATCH

    @classmethod
    def apply(cls, current: str, bump: str) -> str:
        """Return the next version string."""
        match = _SEMVER.match(current)
        if match is None:
            raise ReleaseError(f"version {current!r} is not X.Y.Z")
        major, minor, patch = (int(part) for part in match.groups())
        if bump == cls.MAJOR:
            return f"{major + 1}.0.0"
        if bump == cls.MINOR:
            return f"{major}.{minor + 1}.0"
        return f"{major}.{minor}.{patch + 1}"


class ChangelogRenderer:
    """Renders one Keep a Changelog entry from parsed commits."""

    @classmethod
    def render(
        cls,
        version: str,
        commits: tuple[Commit, ...],
        *,
        released_on: str,
        previous_tag: str | None,
    ) -> str:
        """Return the markdown entry for ``version``."""
        lines = [f"## {version} - {released_on}", ""]

        breaking = [commit for commit in commits if commit.breaking]
        if breaking:
            lines.append("### Breaking Changes")
            lines.append("")
            lines.extend(cls._bullet(commit) for commit in breaking)
            lines.append("")

        for heading, types in _SECTIONS:
            selected = [
                commit
                for commit in commits
                if commit.type in types and not commit.breaking
            ]
            if not selected:
                continue
            lines.append(f"### {heading}")
            lines.append("")
            lines.extend(cls._bullet(commit) for commit in selected)
            lines.append("")

        if len(lines) == 2:
            lines.extend(["_No conventional commits in this range._", ""])

        if previous_tag:
            lines.append(
                f"[Full changelog]({REPO_URL}/compare/{previous_tag}...{TAG_PREFIX}{version})"
            )
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"

    @staticmethod
    def _bullet(commit: Commit) -> str:
        scope = f"**{commit.scope}:** " if commit.scope else ""
        link = f"[`{commit.short_sha}`]({REPO_URL}/commit/{commit.sha})"
        return f"- {scope}{commit.description} ({link})"


class ChangelogFile:
    """Reads and rewrites tools/cli/CHANGELOG.md."""

    HEADER = (
        "# Changelog\n"
        "\n"
        "All notable changes to `@0x-copilot/cli`.\n"
        "\n"
        "Generated by `.github/workflows/release-cli.yml` from Conventional Commits.\n"
        "While the package is 0.x, a breaking change bumps the MINOR digit and\n"
        "everything else bumps PATCH -- npm treats `^0.1.4` as `>=0.1.4 <0.2.0`.\n"
    )

    @classmethod
    def prepend(cls, entry: str, path: Path = CHANGELOG) -> str:
        """Insert ``entry`` beneath the header, returning the full document."""
        existing = path.read_text() if path.exists() else cls.HEADER
        if not existing.startswith("# Changelog"):
            existing = cls.HEADER + "\n" + existing
        header, separator, rest = existing.partition("\n\n")
        # `rest` holds prior entries; the newest goes directly above them.
        body = f"{header}{separator}{entry}\n{rest}".rstrip() + "\n"
        return body


class PackageVersion:
    """Reads and writes the version field of tools/cli/package.json."""

    @staticmethod
    def read(path: Path = PACKAGE_JSON) -> str:
        return str(json.loads(path.read_text())["version"])

    @staticmethod
    def write(version: str, path: Path = PACKAGE_JSON) -> None:
        """Rewrite only the version line, preserving key order and formatting."""
        text = path.read_text()
        updated, count = re.subn(
            r'("version"\s*:\s*")[^"]+(")', rf"\g<1>{version}\g<2>", text, count=1
        )
        if count != 1:
            raise ReleaseError(f"could not rewrite the version field in {path}")
        path.write_text(updated)


def build_plan(override: str, today: str) -> dict[str, object]:
    """Return everything the workflow needs, without mutating the tree."""
    if override not in _Bump.CHOICES:
        raise ReleaseError(f"bump must be one of {_Bump.CHOICES}, got {override!r}")
    previous_tag = CommitReader.last_release_tag()
    commits = CommitReader.commits_since(previous_tag)
    current = PackageVersion.read()
    bump = _Bump.decide(commits, override)
    version = _Bump.apply(current, bump)
    entry = ChangelogRenderer.render(
        version, commits, released_on=today, previous_tag=previous_tag
    )
    return {
        "previous_tag": previous_tag,
        "current_version": current,
        "bump": bump,
        "version": version,
        "tag": f"{TAG_PREFIX}{version}",
        "commit_count": len(commits),
        "breaking_count": sum(1 for commit in commits if commit.breaking),
        "changelog_entry": entry,
    }


def main(argv: list[str] | None = None) -> int:
    """Print the plan, and optionally write the version + changelog."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bump", default=_Bump.AUTO, choices=list(_Bump.CHOICES))
    parser.add_argument(
        "--today", default=date_type.today().isoformat(), help="release date (ISO)"
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="apply the version bump and changelog entry to the working tree",
    )
    parser.add_argument("--output", help="write the plan as JSON to this path")
    args = parser.parse_args(argv)

    plan = build_plan(args.bump, args.today)

    print(f"previous tag   : {plan['previous_tag'] or '(none - first release)'}")
    print(f"current version: {plan['current_version']}")
    print(
        f"commits        : {plan['commit_count']} ({plan['breaking_count']} breaking)"
    )
    print(f"bump           : {plan['bump']}")
    print(f"next version   : {plan['version']}  (tag {plan['tag']})")
    print("\n--- changelog entry ---\n")
    print(plan["changelog_entry"])

    if args.write:
        PackageVersion.write(str(plan["version"]))
        CHANGELOG.write_text(ChangelogFile.prepend(str(plan["changelog_entry"])))
        print(
            f"wrote {PACKAGE_JSON.relative_to(REPO_ROOT)} and "
            f"{CHANGELOG.relative_to(REPO_ROOT)}"
        )

    if args.output:
        Path(args.output).write_text(json.dumps(plan, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
