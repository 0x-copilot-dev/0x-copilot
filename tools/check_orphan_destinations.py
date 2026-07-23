"""CI guard (PRD-13): no chat-surface destination ships exported-but-mounted-by-nobody.

`packages/chat-surface` is the single-source-of-truth interaction layer; its only
sanctioned contract with the two hosts (`apps/frontend`, `apps/desktop`) is the
package barrel `packages/chat-surface/src/index.ts`. A component that is *exported
from that barrel* but *mounted by no host and consumed by no other module in the
package* is dead weight — worse, a decoy: every future reader looking for "the X
destination" finds the wrong file first. A prior repo-wide audit put dead code at
~95k LOC; the `ChatsSidebar` case shipped a 498-line component that fetched a route
(`/v1/chats/projects`) served by no service in the repo, and `ChatsDestination`
shipped a 48-line forwarder both hosts route around. This guard makes the *next*
such addition fail CI instead of accreting silently.

Rule
----
For every PascalCase **value** export in
``packages/chat-surface/src/index.ts`` whose ``from`` specifier resolves under
``src/destinations/`` **or ``src/shell/``** (README G8: the guard's scope is
extended to the shell) and whose defining module is a ``.tsx`` file under those
roots that exports ``function <Name>(…): ReactElement`` — i.e. a *rendering
component module*, the class where the audited defect actually lives:

    the identifier must appear, as a whole word, in at least one non-test file
    under ``apps/frontend/src/`` or ``apps/desktop/renderer/`` (a host mount),
    **or** in at least one non-test, non-``index.ts`` file inside
    ``packages/chat-surface/src/`` other than its own defining module (an
    in-package consumer).

Barrel re-export files (any ``index.ts``) never count as a consumer — a
re-export is not a mount. The defining module never counts as a consumer of
itself. A plain comment reference *does* count (it is textual proof the symbol
is still spoken about in the package), by design: the guard is a floor, not a
proof of aliveness, and word-boundary text matching keeps it dependency-free and
un-flaky (no TS parsing, no network).

Why not ``knip`` / ``ts-prune`` / an ESLint rule: each either adds a dependency
and reports across the whole monorepo (unsteerable to the "mounted by a host"
question), or cannot see across the package boundary into ``apps/*`` — and
``chat-surface``'s eslint config *bans* importing ``apps/*``, so a lint-side
check would violate the very boundary it protects. A repo-level stdlib script
that *reads* files (never imports them) is the only form that respects the
boundary while checking across it. Mirrors the six existing guards
(``check_dark_capabilities.py`` et al.): a companion ``test_check_*`` suite plus
a paths-filtered workflow.

Waiver
------
Put ``// orphan-destination-waiver: owner=<PRD or issue> — <reason>`` on the
export line in ``src/index.ts``. The waiver lives at the export site so it shows
up in the diff that would otherwise hide the orphan, exactly like
``# dark-capability-waiver:``. Each waiver requires an ``owner=`` so the folded
legacy-IA backlog gets a CI-visible, monotonically-shrinking counter to burn
down instead of an untracked ~95k-LOC estimate.

Usage::

    python tools/check_orphan_destinations.py            # gate: exit 1 on any orphan
    python tools/check_orphan_destinations.py --print-waivers   # list waivers, exit 0

Exits non-zero listing every orphaned exported destination component.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class GuardPaths:
    """The four repo locations the guard reads. Parameterised so the unit suite
    can point every root at a synthetic tree; ``default_paths()`` is the real
    repo layout used in CI."""

    barrel: Path
    chat_surface_src: Path
    # A barrel-exported component's source must live under one of these to be in
    # scope. README G8 extends the original ``destinations`` scope to ``shell``.
    component_roots: tuple[Path, ...]
    # Where a host mounts a component.
    host_roots: tuple[Path, ...]


def default_paths() -> GuardPaths:
    src = REPO_ROOT / "packages" / "chat-surface" / "src"
    return GuardPaths(
        barrel=src / "index.ts",
        chat_surface_src=src,
        component_roots=(src / "destinations", src / "shell"),
        host_roots=(
            REPO_ROOT / "apps" / "frontend" / "src",
            REPO_ROOT / "apps" / "desktop" / "renderer",
        ),
    )


WAIVER_MARKER = "orphan-destination-waiver:"
_WAIVER_OWNER = re.compile(r"orphan-destination-waiver:\s*owner=(?P<owner>\S+)")

# A rendering component module: ``export function <Name>(…): ReactElement``.
# Non-greedy across a possibly-multi-line, possibly-generic signature.
_COMPONENT_FN = re.compile(
    r"export\s+function\s+(?P<name>[A-Z][A-Za-z0-9]*)\b[\s\S]{0,400}?:\s*ReactElement\b"
)

_SKIP_DIRS = {".venv", "venv", "__pycache__", "node_modules", "dist", ".vite", ".git"}


def _is_test_file(path: Path) -> bool:
    name = path.name
    if ".test." in name or ".spec." in name:
        return True
    return any(part in {"__tests__", "__mocks__"} for part in path.parts)


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _iter_source_files(root: Path, suffixes: tuple[str, ...]) -> list[Path]:
    if not root.is_dir():
        return []
    out: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix not in suffixes:
            continue
        if any(part in _SKIP_DIRS for part in path.parts):
            continue
        out.append(path)
    return out


# ---------------------------------------------------------------------------
# 1. Which identifiers are rendering component modules under destinations/shell.
# ---------------------------------------------------------------------------


def component_modules(roots: tuple[Path, ...]) -> dict[str, Path]:
    """Map every ``export function <Name>(…): ReactElement`` identifier defined in a
    non-test ``.tsx`` under the given roots to its defining file."""

    modules: dict[str, Path] = {}
    for root in roots:
        for path in _iter_source_files(root, (".tsx",)):
            if _is_test_file(path):
                continue
            text = _read(path)
            if text is None:
                continue
            for match in _COMPONENT_FN.finditer(text):
                modules.setdefault(match.group("name"), path)
    return modules


# ---------------------------------------------------------------------------
# 2. Which of those identifiers the barrel exports as a value, and under what
#    ``from`` specifier / line (for waiver detection).
# ---------------------------------------------------------------------------


class BarrelExport:
    __slots__ = ("name", "from_spec", "lineno", "line")

    def __init__(self, *, name: str, from_spec: str, lineno: int, line: str) -> None:
        self.name = name
        self.from_spec = from_spec
        self.lineno = lineno
        self.line = line

    @property
    def waived(self) -> bool:
        return WAIVER_MARKER in self.line

    @property
    def owner(self) -> str | None:
        m = _WAIVER_OWNER.search(self.line)
        return m.group("owner") if m else None


_BLOCK_START = re.compile(r"export\s+(?P<type>type\s+)?\{")
# One entry inside a `{ … }` export list: an optional `type ` prefix, then the
# exported identifier (the `as` alias is what a consumer imports).
_ENTRY = re.compile(
    r"(?P<typekw>type\s+)?(?P<orig>[A-Za-z_$][\w$]*)(?:\s+as\s+(?P<alias>[A-Za-z_$][\w$]*))?"
)


def barrel_value_exports(barrel: Path) -> list[BarrelExport]:
    """Parse every ``export { … } from "spec"`` block in the barrel and return the
    PascalCase VALUE (non-``type``) exports, each tagged with the physical line the
    identifier sits on (so an inline waiver on that line is detectable)."""

    text = _read(barrel)
    if text is None:
        return []
    lines = text.splitlines()
    exports: list[BarrelExport] = []

    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        start = _BLOCK_START.search(line)
        if start is None:
            i += 1
            continue
        type_block = start.group("type") is not None
        # Accumulate the block until the closing `} from "spec"`.
        block_lines: list[tuple[int, str]] = [(i, line)]
        j = i
        from_spec: str | None = None
        while j < n:
            m = re.search(r"\}\s*from\s*[\"'](?P<spec>[^\"']+)[\"']", lines[j])
            if m is not None:
                from_spec = m.group("spec")
                break
            j += 1
            if j < n:
                block_lines.append((j, lines[j]))
        if from_spec is None:
            i += 1
            continue

        if not type_block:
            for lineno0, block_line in block_lines:
                # Strip the `export {` / `} from …` scaffolding on the boundary
                # lines so we only read entries.
                content = block_line
                content = re.sub(r"export\s+(type\s+)?\{", "", content)
                content = re.sub(r"\}\s*from\s*[\"'][^\"']+[\"']\s*;?", "", content)
                for chunk in content.split(","):
                    chunk = chunk.strip()
                    if not chunk:
                        continue
                    em = _ENTRY.match(chunk)
                    if em is None or em.group("typekw"):
                        continue
                    name = em.group("alias") or em.group("orig")
                    if not name or not name[0].isupper():
                        continue
                    exports.append(
                        BarrelExport(
                            name=name,
                            from_spec=from_spec,
                            lineno=lineno0 + 1,
                            line=block_line,
                        )
                    )
        i = j + 1
    return exports


def _spec_in_component_roots(from_spec: str) -> bool:
    """True if the barrel ``from`` specifier resolves under destinations/ or shell/."""

    s = from_spec.lstrip("./")
    return (
        s.startswith("destinations/")
        or s.startswith("shell/")
        or s
        in {
            "destinations",
            "shell",
        }
    )


# ---------------------------------------------------------------------------
# 3. Is an in-scope identifier referenced by a host or an in-package consumer?
# ---------------------------------------------------------------------------


def _word_appears(name: str, files: list[Path]) -> bool:
    pattern = re.compile(rf"\b{re.escape(name)}\b")
    for path in files:
        text = _read(path)
        if text is not None and pattern.search(text):
            return True
    return False


def _host_files(host_roots: tuple[Path, ...]) -> list[Path]:
    files: list[Path] = []
    for root in host_roots:
        for path in _iter_source_files(root, (".ts", ".tsx")):
            if not _is_test_file(path):
                files.append(path)
    return files


def _in_package_consumer_files(
    chat_surface_src: Path, defining_file: Path
) -> list[Path]:
    """chat-surface/src files that may count as a consumer: non-test, not any
    ``index.ts`` barrel, and not the symbol's own defining module."""

    files: list[Path] = []
    for path in _iter_source_files(chat_surface_src, (".ts", ".tsx")):
        if _is_test_file(path):
            continue
        if path.name == "index.ts":
            continue
        if path.resolve() == defining_file.resolve():
            continue
        files.append(path)
    return files


class Orphan:
    __slots__ = ("name", "file", "lineno")

    def __init__(self, *, name: str, file: Path, lineno: int) -> None:
        self.name = name
        self.file = file
        self.lineno = lineno

    def render(self) -> str:
        try:
            rel = self.file.relative_to(REPO_ROOT)
        except ValueError:
            rel = self.file
        return (
            f"{rel}:{self.lineno}: destination component {self.name!r} is exported "
            "from the chat-surface barrel but MOUNTED BY NO HOST and consumed by no "
            "other chat-surface module — it is dead weight (or a decoy). Mount it in "
            "a host (apps/frontend/src or apps/desktop/renderer), consume it inside "
            "the package, delete it, or add a "
            f"`// {WAIVER_MARKER} owner=<PRD or issue> — <reason>` comment to its "
            "export line in src/index.ts."
        )


def find_orphans(
    paths: GuardPaths | None = None,
) -> tuple[list[Orphan], list[BarrelExport]]:
    """Return (orphans, waived) — the two lists the gate reports on."""

    paths = paths or default_paths()
    modules = component_modules(paths.component_roots)
    host_files = _host_files(paths.host_roots)

    orphans: list[Orphan] = []
    waived: list[BarrelExport] = []

    for export in barrel_value_exports(paths.barrel):
        if not _spec_in_component_roots(export.from_spec):
            continue
        defining = modules.get(export.name)
        if defining is None:
            # Not a rendering component module (a hook, constant, class …) — out
            # of this guard's deliberately-narrow scope.
            continue
        if export.waived:
            waived.append(export)
            continue
        if _word_appears(export.name, host_files):
            continue
        consumers = _in_package_consumer_files(paths.chat_surface_src, defining)
        if _word_appears(export.name, consumers):
            continue
        orphans.append(
            Orphan(name=export.name, file=paths.barrel, lineno=export.lineno)
        )

    orphans.sort(key=lambda o: o.name)
    waived.sort(key=lambda w: w.name)
    return orphans, waived


def main(argv: list[str] | None = None, *, paths: GuardPaths | None = None) -> int:
    parser = argparse.ArgumentParser(prog="check_orphan_destinations")
    parser.add_argument(
        "--print-waivers",
        action="store_true",
        help="List every waived orphan as `<Identifier> owner=<value>` and exit 0.",
    )
    args = parser.parse_args(argv)

    orphans, waived = find_orphans(paths)

    if args.print_waivers:
        for w in waived:
            sys.stdout.write(f"{w.name} owner={w.owner or '?'}\n")
        sys.stdout.write(f"# {len(waived)} waiver(s)\n")
        return 0

    if not orphans:
        sys.stdout.write(
            "OK: no orphaned exported destinations "
            f"({len(waived)} waived; every other barrel-exported destination "
            "component is mounted by a host or consumed in-package)\n"
        )
        return 0

    sys.stderr.write(
        "FAIL: chat-surface exports destination components that no host mounts\n"
    )
    for orphan in orphans:
        sys.stderr.write(f"  {orphan.render()}\n")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
