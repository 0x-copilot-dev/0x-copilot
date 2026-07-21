"""Unit tests for the dark-capabilities static gate (P5 CI guard)."""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

sys.path.insert(0, str(HERE))

from check_dark_capabilities import (  # noqa: E402
    DEFAULT_SRC_ROOTS,
    WAIVER_MARKER,
    _collect_referenced_names,
    _first_declarations,
    _is_capability_flag,
    main,
)


# ---------------------------------------------------------------------------
# The capability-flag predicate
# ---------------------------------------------------------------------------


class TestIsCapabilityFlag:
    def test_backend_selector_is_a_capability(self) -> None:
        assert _is_capability_flag("RUNTIME_STORE_BACKEND")
        assert _is_capability_flag("RUNTIME_EVENT_BUS_BACKEND")

    def test_enable_prefix_is_a_capability(self) -> None:
        assert _is_capability_flag("RUNTIME_ENABLE_LOCAL_MODELS")
        assert _is_capability_flag("RUNTIME_ENABLE_REMOTE_SANDBOX")

    def test_enabled_suffix_tuning_boolean_is_not_a_capability(self) -> None:
        # Tunes an always-present subsystem; the default path is exercised, so it
        # is not the off-by-default dark shape. Folding it in = false positives.
        assert not _is_capability_flag("RUNTIME_DEFAULT_REASONING_ENABLED")
        assert not _is_capability_flag("RUNTIME_APPROVAL_EXPIRY_SWEEP_ENABLED")

    def test_plain_setting_is_not_a_capability(self) -> None:
        assert not _is_capability_flag("RUNTIME_DEFAULT_MODEL")
        assert not _is_capability_flag("RUNTIME_MAX_RETRIES")


# ---------------------------------------------------------------------------
# Declaration + reference scanning on synthetic trees
# ---------------------------------------------------------------------------


def _write(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


class TestScanning:
    def test_declaration_of_a_backend_flag_is_found(self, tmp_path: Path) -> None:
        src = tmp_path / "src"
        _write(src / "settings.py", 'FOO = "RUNTIME_WIDGET_BACKEND"\n')
        decls = _first_declarations((src,))
        assert "RUNTIME_WIDGET_BACKEND" in decls

    def test_waived_declaration_is_skipped(self, tmp_path: Path) -> None:
        src = tmp_path / "src"
        _write(
            src / "settings.py",
            f'FOO = "RUNTIME_WIDGET_BACKEND"  {WAIVER_MARKER} experimental, tracked in #999\n',
        )
        decls = _first_declarations((src,))
        assert "RUNTIME_WIDGET_BACKEND" not in decls

    def test_tuning_boolean_is_not_collected_as_a_declaration(
        self, tmp_path: Path
    ) -> None:
        src = tmp_path / "src"
        _write(src / "settings.py", 'X = "RUNTIME_DEFAULT_REASONING_ENABLED"\n')
        assert _first_declarations((src,)) == {}

    def test_reference_via_quoted_literal_is_detected(self, tmp_path: Path) -> None:
        tests = tmp_path / "tests"
        _write(tests / "test_x.py", 'env = {"RUNTIME_WIDGET_BACKEND": "alt"}\n')
        assert "RUNTIME_WIDGET_BACKEND" in _collect_referenced_names((tests,))

    def test_reference_via_bare_token_in_mjs_harness_is_detected(
        self, tmp_path: Path
    ) -> None:
        # The Tier B harness (run-local.mjs) sets keys without Python quoting;
        # a bare mention still counts as an exercised path.
        harness = tmp_path / "desktop-runtime"
        _write(harness / "run-local.mjs", "const k = RUNTIME_ENABLE_LOCAL_MODELS;\n")
        assert "RUNTIME_ENABLE_LOCAL_MODELS" in _collect_referenced_names((harness,))


# ---------------------------------------------------------------------------
# End-to-end main() on synthetic trees (mirrors how CI invokes the guard)
# ---------------------------------------------------------------------------


class TestMain:
    def test_dark_flag_with_no_reference_fails(self, tmp_path: Path, capsys) -> None:
        src = tmp_path / "src"
        _write(src / "settings.py", 'FLAG = "RUNTIME_SHADOW_BACKEND"\n')
        # No reference roots exist under tmp -> the flag is dark.
        exit_code = main([str(src)])
        assert exit_code == 1
        err = capsys.readouterr().err
        assert "RUNTIME_SHADOW_BACKEND" in err
        assert "ships DARK" in err

    def test_referenced_flag_passes(self, tmp_path: Path) -> None:
        # A declaration whose name also appears in the real reference roots
        # (services/ai-backend/tests etc.) passes. RUNTIME_STORE_BACKEND is
        # exercised by the hermetic run→stream tests, so declaring it passes.
        src = tmp_path / "src"
        _write(src / "settings.py", 'STORE = "RUNTIME_STORE_BACKEND"\n')
        assert main([str(src)]) == 0

    def test_waiver_suppresses_the_failure(self, tmp_path: Path) -> None:
        src = tmp_path / "src"
        _write(
            src / "settings.py",
            f'FLAG = "RUNTIME_SHADOW_BACKEND"  {WAIVER_MARKER} spike, not shippable\n',
        )
        assert main([str(src)]) == 0


# ---------------------------------------------------------------------------
# The real tree must be green (this is the standing baseline)
# ---------------------------------------------------------------------------


def test_real_ai_backend_tree_has_no_dark_capabilities() -> None:
    """Every capability flag declared in ai-backend src is referenced by a
    test/e2e path today. If this fails, a capability shipped off-by-default with
    no path turning it on — the exact AC2b/citation failure mode."""

    for root in DEFAULT_SRC_ROOTS:
        if not root.exists():
            continue
        assert main([str(root)]) == 0
