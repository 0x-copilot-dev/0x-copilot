"""PRD-A2 D7 — the model-construction seam gate.

AST-walks every ``.py`` under ``src/`` and fails if:

* ``init_chat_model`` / ``init_embeddings`` is referenced anywhere except the sole
  funnel ``agent_runtime/execution/deep_agent_builder.py``, or
* any first-party provider SDK (``langchain_openai`` / ``langchain_anthropic`` /
  ``langchain_google_genai`` / ``anthropic`` / ``openai``) is imported anywhere.

This is the recording-seam's construction half (D1): every model client must be
built through ``build_chat_model`` / ``build_chat_model_from_id`` /
``build_embeddings_model`` so the ``UsageMeter`` cannot be bypassed. Mirrors
``tools/check_llm_provider_imports.py`` — ai-backend has no legitimate
exceptions, so there is no escape marker. A planted-fixture canary keeps the
detector itself honest (it cannot rot into a no-op).

Detection is AST-based on purpose: a bare mention in a comment or docstring (e.g.
``provider_kwargs.py`` explaining ``init_chat_model`` kwargs) is data, not a
reference, and must NOT trip the gate.
"""

from __future__ import annotations

import ast
from pathlib import Path


class TestLlmSeamGate:
    _SRC_ROOT = Path(__file__).resolve().parents[2] / "src"
    _FUNNEL_REL = "agent_runtime/execution/deep_agent_builder.py"
    _INIT_FUNCTIONS = frozenset({"init_chat_model", "init_embeddings"})
    # Banned top-level modules (first path segment). ``openai`` is banned as a
    # top-level module; the internal ``agent_runtime.execution.openai_compat``
    # has first segment ``agent_runtime`` and is unaffected.
    _BANNED_MODULES = frozenset(
        {
            "langchain_openai",
            "langchain_anthropic",
            "langchain_google_genai",
            "anthropic",
            "openai",
        }
    )

    # ------------------------------------------------------------------ helpers
    @classmethod
    def _iter_source_files(cls) -> list[Path]:
        return sorted(cls._SRC_ROOT.glob("**/*.py"))

    @classmethod
    def _rel(cls, path: Path) -> str:
        return path.relative_to(cls._SRC_ROOT).as_posix()

    @classmethod
    def _init_reference_violations(cls, tree: ast.AST, rel_path: str) -> list[str]:
        """Return init_chat_model/init_embeddings references outside the funnel."""

        if rel_path == cls._FUNNEL_REL:
            return []
        found: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id in cls._INIT_FUNCTIONS:
                found.append(f"{rel_path}: references {node.id}()")
            elif isinstance(node, ast.Attribute) and node.attr in cls._INIT_FUNCTIONS:
                found.append(f"{rel_path}: references .{node.attr}()")
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    if alias.name in cls._INIT_FUNCTIONS:
                        found.append(f"{rel_path}: imports {alias.name}")
        return found

    @classmethod
    def _provider_import_violations(cls, tree: ast.AST, rel_path: str) -> list[str]:
        """Return direct provider-SDK imports (banned everywhere in ai-backend)."""

        found: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if cls._top_module(alias.name) in cls._BANNED_MODULES:
                        found.append(f"{rel_path}: import {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                if node.module and cls._top_module(node.module) in cls._BANNED_MODULES:
                    found.append(f"{rel_path}: from {node.module} import ...")
        return found

    @staticmethod
    def _top_module(module: str) -> str:
        return module.split(".", 1)[0]

    # -------------------------------------------------------------------- tests
    def test_init_chat_model_only_in_funnel(self) -> None:
        violations: list[str] = []
        for path in self._iter_source_files():
            rel = self._rel(path)
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            violations.extend(self._init_reference_violations(tree, rel))
        assert violations == [], (
            "init_chat_model/init_embeddings must only be referenced in the "
            f"funnel ({self._FUNNEL_REL}); offenders route model construction "
            "around the UsageMeter seam:\n" + "\n".join(violations)
        )

    def test_no_direct_provider_imports(self) -> None:
        violations: list[str] = []
        for path in self._iter_source_files():
            rel = self._rel(path)
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            violations.extend(self._provider_import_violations(tree, rel))
        assert violations == [], (
            "Direct provider-SDK imports are banned in ai-backend; build models "
            "through deep_agent_builder so usage is metered:\n" + "\n".join(violations)
        )

    def test_funnel_itself_is_the_one_exception(self) -> None:
        # Sanity: the funnel really does reference the guarded names, so the
        # allow-list is meaningful (not pointing at an unrelated file).
        funnel = self._SRC_ROOT / self._FUNNEL_REL
        tree = ast.parse(funnel.read_text(encoding="utf-8"), filename=str(funnel))
        names = {
            node.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Name) and node.id in self._INIT_FUNCTIONS
        }
        assert names == self._INIT_FUNCTIONS

    def test_gate_fails_on_planted_init_reference(self) -> None:
        # Canary: a planted init_chat_model reference in a non-funnel file MUST
        # be caught — proves the detector can't silently rot into a no-op.
        planted = ast.parse(
            "from langchain.chat_models import init_chat_model\n"
            "model = init_chat_model('gpt-5-mini')\n"
        )
        violations = self._init_reference_violations(planted, "some/rogue_file.py")
        assert violations, "detector failed to flag a planted init_chat_model use"

    def test_gate_ignores_docstring_and_comment_mentions(self) -> None:
        # A bare mention in a docstring/comment is NOT a reference — the AST
        # detector must ignore it (this is why the gate is AST-based, not grep).
        benign = ast.parse(
            '"""This module explains init_chat_model and init_embeddings."""\n'
            "# init_chat_model is called only in deep_agent_builder\n"
            "x = 1\n"
        )
        assert self._init_reference_violations(benign, "some/doc_only.py") == []

    def test_gate_fails_on_planted_provider_import(self) -> None:
        planted = ast.parse("import langchain_openai\n")
        assert self._provider_import_violations(planted, "some/rogue.py")
        planted_from = ast.parse("from anthropic import Anthropic\n")
        assert self._provider_import_violations(planted_from, "some/rogue.py")

    def test_gate_allows_internal_openai_compat_module(self) -> None:
        # The internal openai_compat module (first segment agent_runtime) is not
        # the openai SDK and must never be flagged.
        benign = ast.parse(
            "from agent_runtime.execution.openai_compat import "
            "CUSTOM_OPENAI_COMPATIBLE_PROVIDER\n"
        )
        assert self._provider_import_violations(benign, "some/ok.py") == []
