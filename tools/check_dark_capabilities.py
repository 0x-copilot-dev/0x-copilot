"""CI guard (P5): no capability ships off-by-default without an e2e/test path.

This is the standing gate for the failure mode that shipped both the AC2b
worker-gate bug and the file-store citation data-loss bug: a capability built
correct in isolation, selected by an env flag that defaults OFF, that *no
automated test ever turns ON*. Every unit test, the typecheck, and adversarial
review passed because they all ran the default (OFF) path; the ON path was dark
until a human flipped it in production. (See
``docs/plan/verification/02-no-dark-capabilities-gate.md``.)

The check is deliberately a *floor*, not a proof. It flags any runtime
capability-selecting env flag declared in the ai-backend source whose **name is
never referenced by any test or e2e harness** — the unambiguous signature of a
dark capability (if no test even mentions the flag, its non-default path is
certainly unexercised). It cannot prove the ON path is *asserted* (a test could
reference the flag only to assert it stays off); that deeper obligation lives in
the reviewer checklist in the policy doc. What it *does* enforce mechanically:
you cannot add a new ``RUNTIME_*_BACKEND`` selector or ``RUNTIME_ENABLE_*``
toggle without either wiring it into a test/e2e harness or writing an explicit,
human-readable waiver in the diff.

Capability flags (scanned in ``services/ai-backend/src``):
- ``RUNTIME_*_BACKEND`` — selects one of several implementations; every
  non-default value ships dark unless a test drives it.
- ``RUNTIME_ENABLE_*`` — opt-in capability toggles, OFF by default (the naming
  convention new opt-in capabilities should adopt to stay in scope of this gate;
  plain ``<subsystem>_ENABLED`` tuning booleans are intentionally out of scope).

A flag "has a path" if its exact name appears in any file under a reference root:
- ``services/ai-backend/tests`` (unit/integration, incl. the hermetic run→stream
  Tier A tests),
- ``tools/desktop-runtime`` (the Tier B supervised-boot harness ``run-local.mjs``),
- ``tools/cli-testing`` (the live-smoke Electron driver).

Waiver: put ``# dark-capability-waiver: <reason>`` on the flag's declaration line
to exempt it (the reason is reviewed in the PR diff). Keep waivers rare — each is
a capability whose alternate path is, by admission, unverified.

Usage::

    python tools/check_dark_capabilities.py            # default: ai-backend
    python tools/check_dark_capabilities.py <src_root>  # explicit root(s)

Exits non-zero listing every dark capability flag.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# Any ``RUNTIME_*`` env-var token. Only ``RUNTIME_*`` env flags are in scope —
# the runtime-capability surface where both incidents occurred — so an unrelated
# ``FOO_BACKEND`` literal in another subsystem never trips the guard. Extend the
# prefix here as other services grow implementation-selecting flags. Whether a
# matched token is a *capability* flag is decided by ``_is_capability_flag``.
_RUNTIME_TOKEN = re.compile(r"RUNTIME_[A-Z0-9_]+")
# The same token when it appears as a quoted string literal (a declaration).
_RUNTIME_LITERAL = re.compile(r"""["'](?P<name>RUNTIME_[A-Z0-9_]+)["']""")


def _is_capability_flag(name: str) -> bool:
    """A backend/implementation selector or an opt-in *capability* toggle.

    Two surfaces, matching the codebase's own conventions:

    * ``*_BACKEND`` — selects one of several implementations (``RUNTIME_STORE_
      BACKEND``, ``RUNTIME_EVENT_BUS_BACKEND``, ``RUNTIME_KMS_BACKEND``); every
      non-default value ships dark unless a test drives it.
    * ``RUNTIME_ENABLE_*`` — the established naming convention for an *opt-in
      capability* that is OFF by default (``RUNTIME_ENABLE_LOCAL_MODELS``,
      ``RUNTIME_ENABLE_REMOTE_SANDBOX``, …). A whole feature hangs off it.

    Deliberately NOT matched: ``<subsystem>_ENABLED`` suffix booleans (e.g.
    ``RUNTIME_DEFAULT_REASONING_ENABLED``). Those tune an always-present
    subsystem rather than gate a separable capability behind an off-default
    implementation path, and the default path is exercised — so they are not the
    dark-capability shape and folding them in only adds false positives. New
    opt-in capabilities should adopt the ``RUNTIME_ENABLE_*`` name to stay in
    scope of this gate (reinforced by the policy doc's reviewer checklist).
    """

    return name.endswith("_BACKEND") or "_ENABLE_" in name


WAIVER_MARKER = "# dark-capability-waiver:"

DEFAULT_SRC_ROOTS: tuple[Path, ...] = (REPO_ROOT / "services" / "ai-backend" / "src",)

# Where a flag's ON/alternate path may be exercised. Not limited to .py — the
# Tier B harness is .mjs — so these roots are scanned as plain text.
REFERENCE_ROOTS: tuple[Path, ...] = (
    REPO_ROOT / "services" / "ai-backend" / "tests",
    REPO_ROOT / "tools" / "desktop-runtime",
    REPO_ROOT / "tools" / "cli-testing",
)

_SKIP_DIRS = {".venv", "venv", "__pycache__", "node_modules", "dist", ".vite", ".git"}


class DarkCapability:
    """A capability flag declared in source with no referencing test/e2e path."""

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
            f"{rel}:{self.lineno}: capability flag {self.name!r} is never "
            "referenced by any test or e2e harness — its non-default path ships "
            "DARK. Add a test that drives it ON (e.g. a hermetic run→stream over "
            "the file/postgres store, or the Tier B supervised-boot harness), or "
            f"add a `{WAIVER_MARKER} <reason>` comment to the declaration line."
        )


def _iter_text_files(root: Path) -> list[Path]:
    if root.is_file():
        return [root]
    if not root.is_dir():
        return []
    results: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in _SKIP_DIRS for part in path.parts):
            continue
        results.append(path)
    return results


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _collect_referenced_names(roots: tuple[Path, ...]) -> set[str]:
    """Every capability-flag name that appears anywhere under the ref roots."""

    referenced: set[str] = set()
    for root in roots:
        for path in _iter_text_files(root):
            text = _read(path)
            if text is None:
                continue
            # Any mention (quoted literal OR a bare env-key reference — a harness
            # may build the key by name without quotes) counts as a path.
            for token in _RUNTIME_TOKEN.findall(text):
                if _is_capability_flag(token):
                    referenced.add(token)
    return referenced


class _DeclScanner:
    """Collect capability-flag declarations from one source file."""

    def __init__(self, *, file: Path, source: str) -> None:
        self._file = file
        self._lines = source.splitlines()

    def declarations(self) -> list[tuple[str, int]]:
        """Return ``(flag_name, lineno)`` for each non-waived declaration.

        A flag counts as "declared" where its quoted literal appears in source
        (canonically its ``settings.py`` constant). A line carrying the waiver
        marker is skipped so an explicit exception never becomes a violation.
        """

        found: list[tuple[str, int]] = []
        for lineno, line in enumerate(self._lines, start=1):
            if WAIVER_MARKER in line:
                continue
            for match in _RUNTIME_LITERAL.finditer(line):
                name = match.group("name")
                if _is_capability_flag(name):
                    found.append((name, lineno))
        return found


def _first_declarations(roots: tuple[Path, ...]) -> dict[str, tuple[Path, int]]:
    """Map each non-waived capability flag to its first declaration site."""

    declarations: dict[str, tuple[Path, int]] = {}
    for root in roots:
        files = _iter_text_files(root) if root.is_dir() else [root]
        for path in sorted(files):
            if path.suffix != ".py":
                continue
            source = _read(path)
            if source is None:
                continue
            for name, lineno in _DeclScanner(file=path, source=source).declarations():
                declarations.setdefault(name, (path, lineno))
    return declarations


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="check_dark_capabilities")
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        help="Source root(s) to scan for capability flags (default: ai-backend src).",
    )
    args = parser.parse_args(argv)

    src_roots: tuple[Path, ...] = tuple(args.paths) if args.paths else DEFAULT_SRC_ROOTS

    referenced = _collect_referenced_names(REFERENCE_ROOTS)
    declarations = _first_declarations(src_roots)

    dark: list[DarkCapability] = [
        DarkCapability(name=name, file=path, lineno=lineno)
        for name, (path, lineno) in sorted(declarations.items())
        if name not in referenced
    ]

    if not dark:
        sys.stdout.write(
            "OK: no dark capabilities "
            f"({len(declarations)} capability flag(s) scanned, all referenced by "
            "a test/e2e path or waived)\n"
        )
        return 0

    sys.stderr.write("FAIL: dark capability flags (off-by-default, no e2e path)\n")
    for cap in dark:
        sys.stderr.write(f"  {cap.render()}\n")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
