"""Unit tests for the LLM-provider-import static checker (TU-1 CI guard)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


HERE = Path(__file__).resolve().parent

sys.path.insert(0, str(HERE))

from check_llm_provider_imports import (  # noqa: E402
    ALLOW_INLINE_MARKER,
    DEFAULT_ROOTS,
    _check_file,
    main,
)


def _write(tmp_path: Path, name: str, body: str) -> Path:
    target = tmp_path / name
    target.write_text(body)
    return target


def test_plain_import_of_anthropic_is_flagged(tmp_path: Path) -> None:
    target = _write(tmp_path, "bad_anthropic.py", "import anthropic\n")
    violations = _check_file(target)
    assert len(violations) == 1
    assert violations[0].module == "anthropic"
    assert violations[0].form == "import"
    assert violations[0].lineno == 1


def test_from_import_of_openai_submodule_is_flagged(tmp_path: Path) -> None:
    target = _write(
        tmp_path,
        "bad_openai.py",
        "from openai.types import ChatCompletion\n",
    )
    violations = _check_file(target)
    assert len(violations) == 1
    assert violations[0].module == "openai.types"
    assert violations[0].form == "from"


def test_langchain_anthropic_is_flagged(tmp_path: Path) -> None:
    target = _write(
        tmp_path,
        "bad_lc_anthropic.py",
        "from langchain_anthropic import ChatAnthropic\n",
    )
    violations = _check_file(target)
    assert len(violations) == 1
    assert violations[0].module == "langchain_anthropic"


def test_google_generativeai_is_flagged(tmp_path: Path) -> None:
    target = _write(
        tmp_path,
        "bad_google.py",
        "import google.generativeai as genai\n",
    )
    violations = _check_file(target)
    assert len(violations) == 1
    assert violations[0].module == "google.generativeai"


def test_inline_allowlist_marker_exempts_one_line(tmp_path: Path) -> None:
    target = _write(
        tmp_path,
        "exempt.py",
        f"import anthropic  {ALLOW_INLINE_MARKER} pricing-table backfill\n",
    )
    assert _check_file(target) == []


def test_marker_must_be_on_same_line(tmp_path: Path) -> None:
    """A marker on the wrong line does NOT exempt an import."""

    target = _write(
        tmp_path,
        "exempt_wrong.py",
        f"# {ALLOW_INLINE_MARKER} bogus\nimport anthropic\n",
    )
    violations = _check_file(target)
    assert len(violations) == 1


def test_allowed_neighbor_imports_are_not_flagged(tmp_path: Path) -> None:
    """Generic stdlib / langchain.chat_models imports must not trip the guard."""

    target = _write(
        tmp_path,
        "ok.py",
        (
            "from __future__ import annotations\n"
            "import logging\n"
            "from langchain.chat_models import init_chat_model\n"
            "from langchain_core.language_models import BaseChatModel\n"
        ),
    )
    assert _check_file(target) == []


def test_partial_name_match_does_not_flag(tmp_path: Path) -> None:
    """``openai_compat`` and ``open_aiohttp`` are NOT in the prefix list."""

    target = _write(
        tmp_path,
        "neighbors.py",
        (
            "import openai_compat\n"
            "from openai_compat.client import OpenAICompatClient\n"
            "import googleapiclient\n"
        ),
    )
    assert _check_file(target) == []


def test_multiple_violations_are_all_reported(tmp_path: Path) -> None:
    target = _write(
        tmp_path,
        "many.py",
        ("import anthropic\nimport openai\nfrom langchain_openai import ChatOpenAI\n"),
    )
    violations = _check_file(target)
    assert len(violations) == 3
    modules = {v.module for v in violations}
    assert modules == {"anthropic", "openai", "langchain_openai"}


def test_canonical_deep_agent_builder_passes() -> None:
    """The canonical entry point exists and passes the guard.

    deep_agent_builder.py uses ``langchain.chat_models.init_chat_model``,
    which is a router (not in the forbidden prefix list), so the file
    should pass even without consulting the explicit allowlist. The
    test pins the file's existence so a future refactor that moves the
    canonical entry point updates this guard too.
    """

    candidate = (
        HERE.parent
        / "services"
        / "ai-backend"
        / "src"
        / "agent_runtime"
        / "execution"
        / "deep_agent_builder.py"
    )
    if not candidate.exists():
        pytest.skip("deep_agent_builder.py not present in this checkout")
    assert _check_file(candidate) == []


def test_full_repo_scan_passes_today() -> None:
    """Sanity baseline: running the guard against the real service trees
    must succeed today. If this fails, a direct provider import has crept
    in and TU-1's single-tracker invariant is broken."""

    failures: list[str] = []
    for root in DEFAULT_ROOTS:
        if not root.exists():
            continue
        # Re-use the public ``main`` entry to keep stdin/stdout matched
        # with what pre-commit will run.
        exit_code = main([str(root)])
        if exit_code != 0:
            failures.append(str(root))
    assert failures == [], f"Forbidden provider imports under: {failures}"


def test_planted_violation_in_real_service_tree_is_caught(tmp_path: Path) -> None:
    """End-to-end: plant a violation in a tempdir and confirm ``main`` flags it.

    Mirrors how pre-commit would invoke the script.
    """

    bad_dir = tmp_path / "fake_service" / "src" / "subpkg"
    bad_dir.mkdir(parents=True)
    (bad_dir / "__init__.py").write_text("")
    (bad_dir / "module.py").write_text(
        "from langchain_anthropic import ChatAnthropic\n"
    )
    exit_code = main([str(tmp_path)])
    assert exit_code != 0
