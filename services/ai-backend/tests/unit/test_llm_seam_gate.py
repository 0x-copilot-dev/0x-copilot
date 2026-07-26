"""PRD-A2 D7 canaries for the shared model-construction seam guard."""

from __future__ import annotations

from pathlib import Path

from agent_runtime.observability.llm_seam_conformance import (
    CANONICAL_MODEL_FUNNEL,
    canonical_model_funnel_present,
    llm_seam_violations,
)


_SRC_ROOT = Path(__file__).resolve().parents[2] / "src"


def test_init_chat_model_only_in_funnel_and_no_direct_provider_imports() -> None:
    assert canonical_model_funnel_present(_SRC_ROOT)
    assert llm_seam_violations(_SRC_ROOT) == ()


def test_gate_fails_on_planted_init_reference(tmp_path: Path) -> None:
    rogue = tmp_path / "some" / "rogue_file.py"
    rogue.parent.mkdir(parents=True)
    rogue.write_text(
        "from langchain.chat_models import init_chat_model\n"
        "model = init_chat_model('gpt-5-mini')\n",
        encoding="utf-8",
    )

    assert llm_seam_violations(tmp_path) == (
        "some/rogue_file.py: imports init_chat_model",
        "some/rogue_file.py: references init_chat_model()",
    )


def test_gate_ignores_docstring_and_comment_mentions(tmp_path: Path) -> None:
    source = tmp_path / "doc_only.py"
    source.write_text(
        '"""This module explains init_chat_model and init_embeddings."""\n'
        "# init_chat_model is called only in deep_agent_builder\n"
        "x = 1\n",
        encoding="utf-8",
    )

    assert llm_seam_violations(tmp_path) == ()


def test_gate_fails_on_planted_provider_import(tmp_path: Path) -> None:
    source = tmp_path / "rogue.py"
    source.write_text("import langchain_openai\n", encoding="utf-8")

    assert llm_seam_violations(tmp_path) == ("rogue.py: import langchain_openai",)


def test_gate_allows_internal_openai_compat_module(tmp_path: Path) -> None:
    source = tmp_path / "ok.py"
    source.write_text(
        "from agent_runtime.execution.openai_compat import "
        "CUSTOM_OPENAI_COMPATIBLE_PROVIDER\n",
        encoding="utf-8",
    )

    assert llm_seam_violations(tmp_path) == ()


def test_missing_funnel_is_detected(tmp_path: Path) -> None:
    assert canonical_model_funnel_present(tmp_path) is False
    assert CANONICAL_MODEL_FUNNEL.as_posix().endswith("deep_agent_builder.py")
