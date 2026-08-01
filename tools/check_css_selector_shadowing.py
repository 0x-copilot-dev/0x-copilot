"""CI guard: no host stylesheet silently shadows a shared `packages/*` selector.

`packages/chat-surface` and `packages/design-system` are the single source of
truth for shared surface styling; both hosts (`apps/frontend`, `apps/desktop`)
import those stylesheets and then load their own on top. A class name declared
in BOTH a package sheet and a host sheet has two owners, and at equal
specificity the sheet that loads last wins — silently, with nothing in either
file naming the other.

The shape this guard was written for: `apps/desktop/renderer/firstrun.css`
carried a stale copy of the P0 placeholder chrome and re-declared `.fr`,
`.fr-top`, `.fr-brand`, `.fr-main`, `.fr-foot` — names owned by
`packages/chat-surface/src/onboarding/onboarding.css`. `FirstRunGate.tsx`
imports firstrun.css *after* bootstrap.tsx imports the package sheet, so the
stale copy won: `.fr-main { align-items: flex-start }` shrink-wrapped the
onboarding column and the first-run composer rendered 408px wide inside its
640px column, while the identical composer on the Run cockpit rendered at the
full 640px. Nothing failed; it just looked wrong. The mirror-image failure (a
package sheet whose rules live ONLY in a host sheet, so the other host renders
unstyled) is documented all over `apps/desktop/renderer/bootstrap.tsx` —
composer.css, workspace.css, approvals.css, citations.css each shipped that bug.
Same root cause: one visual contract, two owners.

Rule
----
For every **class-bearing** selector declared in a stylesheet under ``apps/*``,
where the owning app loads at least one ``@0x-copilot/*`` package stylesheet:

    that selector must not also be declared in any stylesheet under
    ``packages/*``.

Selector text is compared after whitespace normalisation, per comma-separated
compound (``.a, .b { }`` declares two selectors). Rules inside ``@media`` /
``@supports`` / ``@layer`` / ``@container`` count — a host media-query rule
overrides the package just as silently as a top-level one.

Deliberately out of scope, each for a reason:

* **Selectors with no class** (``:root``, ``body``, ``h1``, ``*``, ``button``).
  Base and reset styling is every document's own job; both hosts legitimately
  set them and a collision there is not a shared-component name being
  re-owned. The bug class is package-owned *component class names*.
* **Apps that load no package stylesheet at all** (today: ``apps/website``, a
  standalone Astro marketing site depending on no ``@0x-copilot/*`` package).
  A sheet that never shares a document with a package sheet cannot shadow one —
  website's ``.cc`` colliding with approvals.css's ``.cc`` is a coincidence,
  not a defect. Derived from the app's own imports, not a hardcoded name, so
  the day website starts importing shared CSS the gate starts covering it.
* **CSS Modules** (``*.module.css``). The bundler hashes those class names, so
  they cannot collide with a plain sheet.

Escape hatch
------------
Put ``shadow-ok: <reason>`` in a comment immediately above the app-side rule::

    /* shadow-ok: the shared sheet sizes .fr with min-height:100vh, which
     * overflows the desktop window frame. Desktop-only substrate delta. */
    .fr {
      height: 100%;
      min-height: 0;
    }

The marker lives at the offending rule — like ``// orphan-destination-waiver:``
and ``# dark-capability-waiver:`` — so it shows up in the diff that would
otherwise hide the shadow, and a reason is mandatory. Consecutive comments
directly above the rule are all searched, so an existing explanatory block just
gains one line.

Baseline
--------
``tools/css_shadowing_baseline.txt`` records the shadowing that predates this
gate (``apps/frontend/src/styles.css`` carries a private fork of ~136 rules from
composer.css / workspace.css / onboarding.css). Baseline entries are debt, NOT
approval: there is deliberately no ``--update-baseline`` writer, so the only way
to land new shadowing is the diff-visible ``shadow-ok:`` marker. A baseline
entry that no longer collides is a hard error, which makes the file a
monotonically-shrinking counter instead of an untracked estimate.

Usage::

    python tools/check_css_selector_shadowing.py             # gate: exit 1 on any shadow
    python tools/check_css_selector_shadowing.py --print-waivers    # list shadow-ok rules
    python tools/check_css_selector_shadowing.py --print-baseline   # list the debt, exit 0
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

WAIVER_MARKER = "shadow-ok:"
_WAIVER_REASON = re.compile(r"shadow-ok:\s*(?P<reason>\S.*)")

BASELINE_SEPARATOR = " :: "

_SKIP_DIRS = {
    ".astro",
    ".git",
    ".next",
    ".venv",
    ".vite",
    "__pycache__",
    "build",
    "coverage",
    "dist",
    "node_modules",
    "out",
    # The desktop staged runtime (``apps/desktop/resources/``, gitignored via
    # apps/desktop/.gitignore and written by tools/desktop-runtime/stage.mjs).
    # It holds a COPY of the built web assets, so every package selector in the
    # bundle reads as an app-side shadow of itself and the guard fires with
    # hundreds of findings about files that are not source. Staging is a
    # documented step — it is what the desktop journeys in
    # tools/desktop-journeys/ require — so this fired for anyone who ran them
    # and then tried to commit anything at all.
    "resources",
    "storybook-static",
    "venv",
}

# An app "loads shared CSS" if any of its sources pulls in a package stylesheet,
# by ES import (`import "@0x-copilot/chat-surface/src/composer/composer.css"`),
# by `@import`, or by a `<link href=...>`.
_PACKAGE_CSS_REF = re.compile(r"@0x-copilot/[^\"'`\s]+\.css")
_APP_SOURCE_SUFFIXES = (
    ".astro",
    ".css",
    ".html",
    ".js",
    ".jsx",
    ".mjs",
    ".svelte",
    ".ts",
    ".tsx",
    ".vue",
)

# At-rules whose body holds ordinary style rules — a `.fr-main` inside one of
# these shadows just as effectively as a top-level `.fr-main`. Every other
# at-rule (@keyframes, @font-face, @property, @page, @counter-style) has a body
# that is not selectors, so its contents are skipped entirely.
_NESTING_AT_RULES = {
    "media",
    "supports",
    "layer",
    "container",
    "scope",
    "starting-style",
}


@dataclass(frozen=True)
class GuardPaths:
    """The locations the guard reads, plus the root that report and baseline
    paths are written relative to. Parameterised so the unit suite can point
    every root at a synthetic tree; ``default_paths()`` is the real repo layout
    used in CI."""

    apps_dir: Path
    packages_dir: Path
    baseline: Path
    repo_root: Path = REPO_ROOT


def default_paths() -> GuardPaths:
    return GuardPaths(
        apps_dir=REPO_ROOT / "apps",
        packages_dir=REPO_ROOT / "packages",
        baseline=REPO_ROOT / "tools" / "css_shadowing_baseline.txt",
        repo_root=REPO_ROOT,
    )


@dataclass(frozen=True)
class CssRule:
    """One comma-separated selector of one rule, with where it was declared and
    whether the comments directly above it carry the ``shadow-ok:`` marker."""

    selector: str
    file: Path
    lineno: int
    waiver_reason: str | None

    @property
    def waived(self) -> bool:
        return self.waiver_reason is not None


@dataclass(frozen=True)
class Shadow:
    """An app-side selector that a package stylesheet also declares."""

    selector: str
    app_file: Path
    app_lineno: int
    package_file: Path
    package_lineno: int
    root: Path = REPO_ROOT
    reason: str | None = None

    @property
    def app_path(self) -> str:
        return _rel(self.app_file, self.root)

    def key(self) -> str:
        return f"{self.app_path}{BASELINE_SEPARATOR}{self.selector}"

    def render(self) -> str:
        return (
            f"{self.app_path}:{self.app_lineno}: selector {self.selector!r} is "
            f"also declared in {_rel(self.package_file, self.root)}:"
            f"{self.package_lineno}. The package sheet owns that name; whichever "
            "sheet loads last silently wins the cascade. Delete the app-side copy, "
            "rename it, or — if the override is deliberate — put a "
            f"`/* {WAIVER_MARKER} <reason> */` comment immediately above the rule."
        )


def _rel(path: Path, root: Path = REPO_ROOT) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


# ---------------------------------------------------------------------------
# 1. Stylesheet discovery
# ---------------------------------------------------------------------------


def iter_stylesheets(root: Path) -> list[Path]:
    """Every plain ``.css`` file under ``root``, minus build output and CSS
    Modules (whose class names the bundler hashes, so they cannot collide)."""

    if not root.is_dir():
        return []
    out: list[Path] = []
    for path in root.rglob("*.css"):
        if not path.is_file():
            continue
        if any(part in _SKIP_DIRS for part in path.parts):
            continue
        if path.name.endswith(".module.css"):
            continue
        out.append(path)
    return sorted(out)


def app_loads_package_css(app_dir: Path) -> bool:
    """True if any source in this app imports a ``@0x-copilot/*`` stylesheet.

    An app whose documents never contain a package sheet cannot shadow one, so
    it is out of scope. Read from the app's own imports rather than hardcoding
    a name, so scope follows reality."""

    for path in app_dir.rglob("*"):
        if not path.is_file() or path.suffix not in _APP_SOURCE_SUFFIXES:
            continue
        if any(part in _SKIP_DIRS for part in path.parts):
            continue
        text = _read(path)
        if text is not None and _PACKAGE_CSS_REF.search(text):
            return True
    return False


def in_scope_app_stylesheets(apps_dir: Path) -> list[Path]:
    """Stylesheets of every app that loads at least one package stylesheet."""

    if not apps_dir.is_dir():
        return []
    out: list[Path] = []
    for app_dir in sorted(p for p in apps_dir.iterdir() if p.is_dir()):
        if app_dir.name in _SKIP_DIRS:
            continue
        if not app_loads_package_css(app_dir):
            continue
        out.extend(iter_stylesheets(app_dir))
    return out


# ---------------------------------------------------------------------------
# 2. CSS parsing
#
# A ~100-line scanner rather than a dependency: the gate must run in CI with
# nothing but stdlib (like every sibling tools/check_*.py), and the question it
# asks — "which selectors does this file declare, on what line, with what
# comment above" — needs no cascade model, no specificity, no value parsing.
# It tracks strings and comments so a brace inside `content: "}"` cannot
# desynchronise it.
# ---------------------------------------------------------------------------


def _selector_is_class_bearing(selector: str) -> bool:
    """True if the selector targets at least one class.

    Attribute values and strings are blanked first so ``[data-x=".foo"]`` does
    not read as a class."""

    stripped = re.sub(r"\[[^\]]*\]", "[]", selector)
    stripped = re.sub(r"""(["']).*?\1""", '""', stripped)
    return re.search(r"\.[A-Za-z_-]", stripped) is not None


def split_selector_list(prelude: str) -> list[str]:
    """Split a rule prelude on top-level commas.

    Commas inside ``:is(…)`` / ``:not(…)``, inside ``[attr="a,b"]``, or inside a
    string are not separators."""

    parts: list[str] = []
    buf: list[str] = []
    depth = 0
    quote: str | None = None
    i = 0
    while i < len(prelude):
        ch = prelude[i]
        if quote is not None:
            buf.append(ch)
            if ch == "\\" and i + 1 < len(prelude):
                buf.append(prelude[i + 1])
                i += 2
                continue
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
            buf.append(ch)
        elif ch in "([":
            depth += 1
            buf.append(ch)
        elif ch in ")]":
            depth = max(0, depth - 1)
            buf.append(ch)
        elif ch == "," and depth == 0:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
        i += 1
    parts.append("".join(buf))
    return [p for p in (normalize_selector(p) for p in parts) if p]


def normalize_selector(selector: str) -> str:
    """Collapse whitespace and pad top-level combinators, so ``.a>.b`` and
    ``.a > .b`` compare equal. Text inside parens/brackets/strings is left
    alone (``:nth-child(2n+1)`` must not gain spaces)."""

    out: list[str] = []
    depth = 0
    quote: str | None = None
    i = 0
    n = len(selector)
    while i < n:
        ch = selector[i]
        if quote is not None:
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                out.append(selector[i + 1])
                i += 2
                continue
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
            out.append(ch)
        elif ch in "([":
            depth += 1
            out.append(ch)
        elif ch in ")]":
            depth = max(0, depth - 1)
            out.append(ch)
        elif depth == 0 and ch.isspace():
            if out and not out[-1].isspace():
                out.append(" ")
        elif depth == 0 and ch in ">+~":
            while out and out[-1] == " ":
                out.pop()
            # Pushed as three entries, not one " > " string, so the whitespace
            # branch above still sees a single space in `out[-1]` and does not
            # append a second one.
            out.extend((" ", ch, " "))
        else:
            out.append(ch)
        i += 1
    return "".join(out).strip()


def _comment_body(comment: str) -> str:
    """A CSS comment with its delimiters and per-line ``*`` gutter removed, so a
    marker reads the same in ``/* shadow-ok: x */`` and in a boxed block."""

    body = comment.removeprefix("/*").removesuffix("*/")
    return "\n".join(line.strip().lstrip("*").strip() for line in body.splitlines())


def _waiver_reason(comments: list[str]) -> str | None:
    """The ``shadow-ok:`` reason from the comments directly above a rule.

    A marker with no reason after it does not count: the escape hatch has to
    cost a sentence, or it is a rubber stamp."""

    for comment in reversed(comments):
        match = _WAIVER_REASON.search(_comment_body(comment))
        if match is not None:
            reason = match.group("reason").strip()
            if reason:
                return reason
    return None


def parse_rules(text: str, source: Path) -> list[CssRule]:
    """Every class-bearing selector declared at style-rule position in ``text``.

    Rules nested inside ``@media`` and friends are included; ``@keyframes``
    bodies (whose ``from``/``50%`` preludes are not selectors) and other
    body-less-of-selectors at-rules are skipped, as are rules nested inside
    another style rule (CSS nesting — those selectors are not standalone)."""

    rules: list[CssRule] = []
    # Enclosing block preludes, outermost first. "" marks a style rule.
    stack: list[str] = []
    pending_comments: list[str] = []
    buf: list[str] = []
    buf_start: int | None = None
    i = 0
    n = len(text)

    def _lineno(offset: int) -> int:
        return text.count("\n", 0, offset) + 1

    def _skippable() -> bool:
        """True if the current block's contents declare nothing we care about."""
        for prelude in stack:
            if prelude == "":
                # Inside a style rule: any nested rule is a CSS-nesting child,
                # not a standalone selector.
                return True
            # Malformed CSS must degrade to "skip", never crash a CI gate.
            words = prelude[1:].split(None, 1)
            name = words[0].split("(", 1)[0].lower() if words else ""
            if name not in _NESTING_AT_RULES:
                return True
        return False

    while i < n:
        ch = text[i]

        if ch == "/" and text.startswith("/*", i):
            end = text.find("*/", i + 2)
            end = n if end == -1 else end + 2
            if buf_start is None:
                pending_comments.append(text[i:end])
            i = end
            continue

        if ch in "\"'":
            quote = ch
            j = i + 1
            while j < n:
                if text[j] == "\\":
                    j += 2
                    continue
                if text[j] == quote:
                    j += 1
                    break
                j += 1
            buf.append(text[i:j])
            i = j
            continue

        if ch == "{":
            prelude = "".join(buf).strip()
            start = buf_start
            buf, buf_start = [], None
            if not _skippable() and prelude and not prelude.startswith("@"):
                reason = _waiver_reason(pending_comments)
                lineno = _lineno(start if start is not None else i)
                for selector in split_selector_list(prelude):
                    if _selector_is_class_bearing(selector):
                        rules.append(
                            CssRule(
                                selector=selector,
                                file=source,
                                lineno=lineno,
                                waiver_reason=reason,
                            )
                        )
                stack.append("")
            else:
                stack.append(prelude if prelude.startswith("@") else "")
            pending_comments = []
            i += 1
            continue

        if ch == "}":
            if stack:
                stack.pop()
            buf, buf_start = [], None
            pending_comments = []
            i += 1
            continue

        if ch == ";":
            # A declaration or a statement at-rule (@import, @charset) — never a
            # selector, so drop what we accumulated.
            buf, buf_start = [], None
            pending_comments = []
            i += 1
            continue

        if not ch.isspace() and buf_start is None:
            buf_start = i
        buf.append(ch)
        i += 1

    return rules


# ---------------------------------------------------------------------------
# 3. The rule
# ---------------------------------------------------------------------------


def package_selector_index(packages_dir: Path) -> dict[str, tuple[Path, int]]:
    """Map every class-bearing selector declared under ``packages/*`` to its
    first declaration site."""

    index: dict[str, tuple[Path, int]] = {}
    for path in iter_stylesheets(packages_dir):
        text = _read(path)
        if text is None:
            continue
        for rule in parse_rules(text, path):
            index.setdefault(rule.selector, (rule.file, rule.lineno))
    return index


def load_baseline(baseline: Path) -> list[str]:
    """The recorded pre-existing shadowing, as ``<app path> :: <selector>``."""

    text = _read(baseline)
    if text is None:
        return []
    entries: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        entries.append(line)
    return entries


def find_shadowing(
    paths: GuardPaths | None = None,
) -> tuple[list[Shadow], list[Shadow], list[Shadow], list[str]]:
    """Return (violations, waived, baselined, stale_baseline_entries)."""

    paths = paths or default_paths()
    index = package_selector_index(paths.packages_dir)
    baseline = load_baseline(paths.baseline)
    baseline_set = set(baseline)

    violations: list[Shadow] = []
    waived: list[Shadow] = []
    baselined: list[Shadow] = []
    seen_keys: set[str] = set()

    for app_file in in_scope_app_stylesheets(paths.apps_dir):
        text = _read(app_file)
        if text is None:
            continue
        # One (file, selector) pair reports once. It only counts as waived when
        # EVERY declaration of it in that file carries the marker — otherwise an
        # annotated rule would launder an unannotated duplicate elsewhere.
        by_selector: dict[str, list[CssRule]] = {}
        for rule in parse_rules(text, app_file):
            if rule.selector in index:
                by_selector.setdefault(rule.selector, []).append(rule)

        for selector, occurrences in by_selector.items():
            package_file, package_lineno = index[selector]
            unwaived = [r for r in occurrences if not r.waived]
            anchor = unwaived[0] if unwaived else occurrences[0]
            shadow = Shadow(
                selector=selector,
                app_file=app_file,
                app_lineno=anchor.lineno,
                package_file=package_file,
                package_lineno=package_lineno,
                root=paths.repo_root,
                reason=anchor.waiver_reason,
            )
            seen_keys.add(shadow.key())
            if not unwaived:
                waived.append(shadow)
            elif shadow.key() in baseline_set:
                baselined.append(shadow)
            else:
                violations.append(shadow)

    stale = sorted(entry for entry in baseline_set if entry not in seen_keys)

    violations.sort(key=lambda s: (s.app_path, s.selector))
    waived.sort(key=lambda s: (s.app_path, s.selector))
    baselined.sort(key=lambda s: (s.app_path, s.selector))
    return violations, waived, baselined, stale


def main(argv: list[str] | None = None, *, paths: GuardPaths | None = None) -> int:
    parser = argparse.ArgumentParser(prog="check_css_selector_shadowing")
    parser.add_argument(
        "--print-waivers",
        action="store_true",
        help="List every `shadow-ok:` rule as `<file> :: <selector> — <reason>`, exit 0.",
    )
    parser.add_argument(
        "--print-baseline",
        action="store_true",
        help="List the recorded pre-existing shadowing (the burn-down list), exit 0.",
    )
    args = parser.parse_args(argv)

    paths = paths or default_paths()
    violations, waived, baselined, stale = find_shadowing(paths)

    if args.print_waivers:
        for shadow in waived:
            sys.stdout.write(f"{shadow.key()} — {shadow.reason}\n")
        sys.stdout.write(f"# {len(waived)} deliberate override(s)\n")
        return 0

    if args.print_baseline:
        for shadow in baselined:
            sys.stdout.write(f"{shadow.key()}\n")
        sys.stdout.write(f"# {len(baselined)} baselined shadow(s) to burn down\n")
        return 0

    failed = False

    if violations:
        sys.stderr.write(
            "FAIL: app stylesheets re-declare selectors owned by packages/*\n"
        )
        for shadow in violations:
            sys.stderr.write(f"  {shadow.render()}\n")
        failed = True

    if stale:
        sys.stderr.write(
            "FAIL: stale entries in "
            f"{_rel(paths.baseline, paths.repo_root)} — these selectors no longer "
            "shadow anything.\n"
            "      Delete the listed lines; the baseline must only ever shrink.\n"
        )
        for entry in stale:
            sys.stderr.write(f"  {entry}\n")
        failed = True

    if failed:
        return 1

    sys.stdout.write(
        "OK: no new CSS selector shadowing "
        f"({len(waived)} deliberate override(s), {len(baselined)} baselined "
        "shadow(s) still to burn down)\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
