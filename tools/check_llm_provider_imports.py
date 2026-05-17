"""CI guard: every LLM provider client must funnel through one entry point.

TU-1 (Token Usage Tracking) requires that every LLM provider invocation in
the monorepo is observable by the single usage-recording pipeline. The
runtime composes LangChain chat models via ``init_chat_model`` (see
``services/ai-backend/src/agent_runtime/execution/deep_agent_builder.py``)
and persists token usage via the ``UsageRecorder`` boundary in
``services/ai-backend/src/agent_runtime/observability/usage_recorder.py``.

Multiple call sites that import provider SDKs (``anthropic``, ``openai``,
``google.generativeai``, ``langchain_anthropic``, ``langchain_openai``,
``langchain_google_genai``) directly bypass that pipeline. This static
check walks the AST of every Python source file under ``services/`` and
fails if any non-allowlisted file imports those modules.

Allowlist:
- ``services/ai-backend/src/agent_runtime/execution/deep_agent_builder.py``
  is the canonical bootstrap path (it uses ``langchain.chat_models.init_chat_model``
  — a router rather than a provider-direct import — but we keep it explicitly
  named so future provider-direct imports there are still considered).
- Any file containing the line marker ``# allow-direct-llm-import: <reason>``
  on the same line as the import is exempted. Reasons must be human-readable
  so reviewers can audit the exception in the PR diff.

Usage::

    python tools/check_llm_provider_imports.py             # default scan: services/
    python tools/check_llm_provider_imports.py path1 path2  # explicit roots

Exits non-zero on the first violation; prints every violation.
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


class _Forbidden:
    """Names of the provider modules whose direct import is forbidden.

    The check matches both ``import X`` and ``from X[.sub] import ...``
    forms; a prefix match on a dot-segmented module path catches submodule
    imports (e.g. ``from anthropic.types import ...``).
    """

    PREFIXES: tuple[str, ...] = (
        "anthropic",
        "openai",
        "google.generativeai",
        "google.genai",
        "langchain_anthropic",
        "langchain_openai",
        "langchain_google_genai",
        "langchain_google_vertexai",
    )


# Files that may legitimately import the forbidden modules. Keep this list
# small and load-bearing -- every entry adds blast radius to the guard.
ALLOWLIST: frozenset[Path] = frozenset(
    {
        # The single canonical entry point: deep_agent_builder.py uses
        # ``langchain.chat_models.init_chat_model``, which dispatches to
        # provider-specific LangChain integrations. We list it here so
        # ``init_chat_model`` always lives at this address; if a future
        # change in deep_agent_builder.py adds a direct provider import,
        # that change is reviewed against the allowlist.
        REPO_ROOT
        / "services"
        / "ai-backend"
        / "src"
        / "agent_runtime"
        / "execution"
        / "deep_agent_builder.py",
    }
)

ALLOW_INLINE_MARKER = "# allow-direct-llm-import:"

# Default scan roots: every service's source tree. ``tools/`` itself is
# excluded because this very file references the forbidden module names
# in string literals (not imports).
DEFAULT_ROOTS: tuple[Path, ...] = (
    REPO_ROOT / "services" / "ai-backend" / "src",
    REPO_ROOT / "services" / "backend" / "src",
    REPO_ROOT / "services" / "backend-facade" / "src",
)


class _Violation:
    __slots__ = ("file", "lineno", "module", "form")

    def __init__(self, *, file: Path, lineno: int, module: str, form: str) -> None:
        self.file = file
        self.lineno = lineno
        self.module = module
        self.form = form

    def render(self) -> str:
        try:
            rel = self.file.relative_to(REPO_ROOT)
        except ValueError:
            rel = self.file
        return (
            f"{rel}:{self.lineno}: direct provider import "
            f"({self.form} {self.module!r}); route LLM calls through "
            "the canonical ``build_chat_model`` / ``init_chat_model`` path "
            "in deep_agent_builder.py, or add a "
            f"``{ALLOW_INLINE_MARKER} <reason>`` comment to the import line."
        )


class _ImportChecker:
    """Walk one file's AST and collect forbidden imports."""

    def __init__(self, *, file: Path, source: str) -> None:
        self._file = file
        self._lines = source.splitlines()
        self.violations: list[_Violation] = []

    @classmethod
    def _is_forbidden(cls, module: str) -> bool:
        for prefix in _Forbidden.PREFIXES:
            if module == prefix or module.startswith(prefix + "."):
                return True
        return False

    def _line_is_allowlisted(self, lineno: int) -> bool:
        """Check the source line for the per-line opt-out marker."""

        if lineno <= 0 or lineno > len(self._lines):
            return False
        return ALLOW_INLINE_MARKER in self._lines[lineno - 1]

    def visit(self, tree: ast.AST) -> None:
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if self._is_forbidden(alias.name) and not self._line_is_allowlisted(
                        node.lineno
                    ):
                        self.violations.append(
                            _Violation(
                                file=self._file,
                                lineno=node.lineno,
                                module=alias.name,
                                form="import",
                            )
                        )
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if self._is_forbidden(module) and not self._line_is_allowlisted(
                    node.lineno
                ):
                    self.violations.append(
                        _Violation(
                            file=self._file,
                            lineno=node.lineno,
                            module=module,
                            form="from",
                        )
                    )


def _check_file(path: Path) -> list[_Violation]:
    """Parse one file and return the violations it contains."""

    if path in ALLOWLIST:
        return []
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        # Pre-commit may run before a syntax fix; let ruff/pytest catch it.
        return []
    checker = _ImportChecker(file=path, source=source)
    checker.visit(tree)
    return checker.violations


def _iter_python_files(root: Path) -> list[Path]:
    """Walk ``root`` and yield every ``.py`` file, skipping caches and venvs."""

    if root.is_file():
        return [root] if root.suffix == ".py" else []
    results: list[Path] = []
    if not root.is_dir():
        return results
    skip_dirs = {".venv", "venv", "__pycache__", "node_modules", "dist", ".vite"}
    for path in root.rglob("*.py"):
        if any(part in skip_dirs for part in path.parts):
            continue
        results.append(path)
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="check_llm_provider_imports")
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        help="Files or directories to scan (default: service src trees).",
    )
    args = parser.parse_args(argv)

    roots: tuple[Path, ...] = tuple(args.paths) if args.paths else DEFAULT_ROOTS

    failures: list[_Violation] = []
    file_count = 0
    for root in roots:
        for file in _iter_python_files(root):
            file_count += 1
            failures.extend(_check_file(file))

    if not failures:
        sys.stdout.write(
            f"OK: LLM provider import check passed ({file_count} file(s) scanned)\n"
        )
        return 0

    sys.stderr.write("FAIL: LLM provider import check\n")
    for violation in failures:
        sys.stderr.write(f"  {violation.render()}\n")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
