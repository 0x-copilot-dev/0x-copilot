"""Unit tests for the dark-capabilities static gate (P5 CI guard).

The interesting half of this file is :class:`TestGateIsNotBlindAgain`. The gate
shipped for months unable to see a single flag of the generative-UI subsystem —
``SURFACES_V2`` failed its regex, ``RUNTIME_TIER2_GENERATION`` failed its name
predicate — and that subsystem then went dark in exactly the way this gate
exists to prevent (``docs/audit/generative-ui/FINDINGS.md`` §4.6b). A gate is
only as good as the proof that it can still see; these tests are that proof,
pinned against the real tree so re-narrowing the scan fails here.
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

sys.path.insert(0, str(HERE))

from check_dark_capabilities import (  # noqa: E402
    DEFAULT_SRC_ROOTS,
    REFERENCE_ROOTS,
    WAIVER_MARKER,
    _collect_referenced_names,
    _collect_referenced_symbols,
    _first_declarations,
    _FlagReaderScanner,
    _is_capability_flag,
    main,
)


def _write(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


class FlagSourceMixin:
    """Source fragments for the two flag shapes the gate must recognise."""

    #: The ``Flag.enabled()`` shape — an env name in a class constant, read by a
    #: classmethod. Detector B's whole target; ``SurfacesV2Flag`` verbatim in
    #: miniature, including a non-env default constant that must not be mistaken
    #: for a flag name.
    FLAG_READER = '''
class WidgetsV2Flag:
    """Whether widgets v2 emission is active."""

    ENV_VAR: ClassVar[str] = "WIDGETS_V2"
    _DEFAULT_WHEN_UNSET: ClassVar[str] = "true"

    @classmethod
    def enabled(cls, environ=None) -> bool:
        source = environ if environ is not None else os.environ
        return source.get(cls.ENV_VAR, cls._DEFAULT_WHEN_UNSET) == "true"
'''

    #: The same gate as a staticmethod dereferencing the owning class by name
    #: rather than ``cls`` (``RevisionControlPlaneEnvironment``'s shape).
    STATIC_FLAG_READER = """
class WidgetEnvironment:
    ENABLED = "WIDGETS_CONTROL_PLANE"

    @staticmethod
    def enabled(environ=None) -> bool:
        return (environ or os.environ).get(WidgetEnvironment.ENABLED, "") == "1"
"""

    #: An instance ``@property`` named ``enabled``. Reports resolved object
    #: state, not an environment gate — must stay out of scope.
    PROPERTY_ENABLED = """
class QuotaGuard:
    CEILING = "WIDGET_MAX_BYTES"

    @property
    def enabled(self) -> bool:
        return self._max_bytes > 0
"""


# ---------------------------------------------------------------------------
# Detector A — the capability-flag name predicate
# ---------------------------------------------------------------------------


class TestIsCapabilityFlag:
    def test_backend_selector_is_a_capability(self) -> None:
        assert _is_capability_flag("RUNTIME_STORE_BACKEND")
        assert _is_capability_flag("RUNTIME_EVENT_BUS_BACKEND")

    def test_backend_selector_without_the_runtime_prefix_is_a_capability(self) -> None:
        # The dropped prefix requirement. ``SURFACE_SPEC_STORE_BACKEND`` is a
        # textbook implementation selector that the gate could not see purely
        # because of how it is spelled.
        assert _is_capability_flag("SURFACE_SPEC_STORE_BACKEND")

    def test_enable_infix_is_a_capability(self) -> None:
        assert _is_capability_flag("RUNTIME_ENABLE_LOCAL_MODELS")
        assert _is_capability_flag("RUNTIME_ENABLE_REMOTE_SANDBOX")

    def test_enabled_suffix_tuning_boolean_is_not_a_capability(self) -> None:
        # Tunes an always-present subsystem; the default path is exercised, so it
        # is not the off-by-default dark shape. A genuine gate spelled this way
        # is caught by detector B instead — see TestFlagReaderScanner.
        assert not _is_capability_flag("RUNTIME_DEFAULT_REASONING_ENABLED")
        assert not _is_capability_flag("RUNTIME_APPROVAL_EXPIRY_SWEEP_ENABLED")

    def test_plain_setting_is_not_a_capability(self) -> None:
        assert not _is_capability_flag("RUNTIME_DEFAULT_MODEL")
        assert not _is_capability_flag("RUNTIME_MAX_RETRIES")

    def test_generative_ui_flags_are_invisible_to_this_detector_alone(self) -> None:
        # Recorded, not lamented: these are precisely why detector B exists.
        # Name shape can never be the only rule, because a convention is not a
        # mechanism.
        assert not _is_capability_flag("SURFACES_V2")
        assert not _is_capability_flag("RUNTIME_TIER2_GENERATION")


# ---------------------------------------------------------------------------
# Detector B — flags read through a ``Flag.enabled()``-shaped classmethod
# ---------------------------------------------------------------------------


class TestFlagReaderScanner(FlagSourceMixin):
    @staticmethod
    def _scan(source: str) -> list:
        return _FlagReaderScanner(file=Path("x.py"), source=source).declarations()

    def test_classmethod_gate_declares_its_env_flag(self) -> None:
        found = self._scan(self.FLAG_READER)
        assert [declaration.name for declaration in found] == ["WIDGETS_V2"]

    def test_classmethod_gate_records_the_symbol_a_test_would_name(self) -> None:
        (declaration,) = self._scan(self.FLAG_READER)
        assert declaration.aliases == frozenset({"WidgetsV2Flag.ENV_VAR"})

    def test_non_env_class_constant_is_not_mistaken_for_a_flag(self) -> None:
        # ``_DEFAULT_WHEN_UNSET = "true"`` is read by the gate but is a default,
        # not an env key.
        assert {d.name for d in self._scan(self.FLAG_READER)} == {"WIDGETS_V2"}

    def test_staticmethod_gate_dereferencing_the_class_by_name(self) -> None:
        (declaration,) = self._scan(self.STATIC_FLAG_READER)
        assert declaration.name == "WIDGETS_CONTROL_PLANE"
        assert declaration.aliases == frozenset({"WidgetEnvironment.ENABLED"})

    def test_instance_property_named_enabled_is_out_of_scope(self) -> None:
        assert self._scan(self.PROPERTY_ENABLED) == []

    def test_inline_literal_read_inside_the_gate_is_found(self) -> None:
        source = (
            "class InlineFlag:\n"
            "    @classmethod\n"
            "    def enabled(cls) -> bool:\n"
            '        return os.environ.get("WIDGETS_INLINE") == "1"\n'
        )
        (declaration,) = self._scan(source)
        assert declaration.name == "WIDGETS_INLINE"
        # Nothing owns it as a named constant, so there is no symbol to name.
        assert declaration.aliases == frozenset()

    def test_sibling_constants_the_gate_never_reads_stay_out_of_scope(self) -> None:
        # ``ArtifactCleanupExecutionEnv`` keeps eleven env keys beside its gate;
        # only the one ``enabled()`` actually reads is a capability flag. The
        # other ten are tuning knobs and flagging them is pure noise.
        source = (
            "class CleanupEnv:\n"
            '    ENABLED = "WIDGETS_CLEANUP_ENABLED"\n'
            '    INTERVAL_SECONDS = "WIDGETS_CLEANUP_INTERVAL_SECONDS"\n'
            "\n"
            "    @classmethod\n"
            "    def enabled(cls) -> bool:\n"
            "        return os.environ.get(cls.ENABLED) == '1'\n"
        )
        assert [d.name for d in self._scan(source)] == ["WIDGETS_CLEANUP_ENABLED"]

    def test_waiver_on_the_declaration_line_exempts_the_flag(self) -> None:
        source = (
            "class WaivedFlag:\n"
            f'    ENV_VAR = "WIDGETS_WAIVED"  {WAIVER_MARKER} spike, not shippable\n'
            "\n"
            "    @classmethod\n"
            "    def enabled(cls) -> bool:\n"
            "        return os.environ.get(cls.ENV_VAR) == '1'\n"
        )
        assert self._scan(source) == []

    def test_unparseable_source_is_skipped_rather_than_raising(self) -> None:
        # A static scan must stay total: a file that cannot be parsed is the type
        # checker's problem, not a crash in the gate.
        assert self._scan("class Broken(:\n") == []


# ---------------------------------------------------------------------------
# Declaration + reference scanning on synthetic trees
# ---------------------------------------------------------------------------


class TestScanning(FlagSourceMixin):
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

    def test_flag_reader_declaration_is_collected_from_a_tree(
        self, tmp_path: Path
    ) -> None:
        src = tmp_path / "src"
        _write(src / "config.py", self.FLAG_READER)
        assert "WIDGETS_V2" in _first_declarations((src,))

    def test_reference_via_quoted_literal_is_detected(self, tmp_path: Path) -> None:
        tests = tmp_path / "tests"
        _write(tests / "test_x.py", 'env = {"RUNTIME_WIDGET_BACKEND": "alt"}\n')
        assert "RUNTIME_WIDGET_BACKEND" in _collect_referenced_names((tests,))

    def test_reference_to_a_non_name_shaped_flag_is_detected(
        self, tmp_path: Path
    ) -> None:
        # The coupling that made the gate self-defeating: the reference side used
        # to be filtered by detector A's name predicate, so a flag detector B put
        # in scope read as unreferenced no matter how many tests drove it.
        tests = tmp_path / "tests"
        _write(tests / "test_x.py", 'monkeypatch.setenv("SURFACES_V2", "true")\n')
        assert "SURFACES_V2" in _collect_referenced_names((tests,))

    def test_reference_via_bare_token_in_mjs_harness_is_detected(
        self, tmp_path: Path
    ) -> None:
        # The Tier B harness (run-local.mjs) sets keys without Python quoting;
        # a bare mention still counts as an exercised path.
        harness = tmp_path / "desktop-runtime"
        _write(harness / "run-local.mjs", "const k = RUNTIME_ENABLE_LOCAL_MODELS;\n")
        assert "RUNTIME_ENABLE_LOCAL_MODELS" in _collect_referenced_names((harness,))

    def test_symbolic_reference_to_the_owning_constant_is_detected(
        self, tmp_path: Path
    ) -> None:
        # How a test that actually flips a flag writes it. Nothing in the file
        # spells the env name, so a literal-only scan reads this as dark.
        tests = tmp_path / "tests"
        _write(
            tests / "test_x.py",
            'monkeypatch.setenv(WidgetEnvironment.ENABLED, "1")\n',
        )
        assert "WidgetEnvironment.ENABLED" in _collect_referenced_symbols((tests,))


# ---------------------------------------------------------------------------
# End-to-end main() on synthetic trees (mirrors how CI invokes the guard)
# ---------------------------------------------------------------------------


class TestMain(FlagSourceMixin):
    def test_dark_flag_with_no_reference_fails(self, tmp_path: Path, capsys) -> None:
        src = tmp_path / "src"
        _write(src / "settings.py", 'FLAG = "RUNTIME_SHADOW_BACKEND"\n')
        # No reference roots exist under tmp -> the flag is dark.
        exit_code = main([str(src)])
        assert exit_code == 1
        err = capsys.readouterr().err
        assert "RUNTIME_SHADOW_BACKEND" in err
        assert "ships DARK" in err

    def test_dark_flag_reader_with_no_reference_fails(
        self, tmp_path: Path, capsys
    ) -> None:
        # The generative-UI escape, reproduced: a flag named by no convention,
        # gated behind an ``enabled()`` classmethod, driven by no test. Under the
        # pre-widening gate this was silent at any severity.
        src = tmp_path / "src"
        _write(src / "config.py", self.FLAG_READER)
        assert main([str(src)]) == 1
        err = capsys.readouterr().err
        assert "WIDGETS_V2" in err
        # The message names the symbol a test would flip, not only the env key.
        assert "WidgetsV2Flag.ENV_VAR" in err

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


class TestGateIsNotBlindAgain:
    """Pin the scope on the real tree, so re-narrowing the scan fails here.

    ``test_real_ai_backend_tree_has_no_dark_capabilities`` passed throughout the
    period the gate could not see the generative-UI subsystem — a green gate
    proves nothing about what it declined to look at. These assert the flags are
    *in scope*, which is the property that was actually missing.
    """

    #: Flags detector B brought into scope. Neither satisfies the old
    #: ``RUNTIME_*`` prefix AND ``_BACKEND``/``_ENABLE_*`` name test, so both
    #: were invisible, and each probes a *different* half of that old test:
    #: ``SURFACES_V2`` fails the prefix, ``RUNTIME_PROPAGATE_QUEUE_TRACE`` fails
    #: the name. A third such flag, ``SURFACE_SPEC_STORE_BACKEND``, is pinned by
    #: the next test because it came back into scope by the dropped prefix.
    #:
    #: The name half was probed by ``RUNTIME_TIER2_GENERATION`` until 2026-08-06,
    #: when the tier-2 render-adapter generator was deleted as an orphan and took
    #: its flag with it. ``RUNTIME_PROPAGATE_QUEUE_TRACE`` replaces it because it
    #: is the only live flag left with that exact shape — ``RUNTIME_``-prefixed,
    #: no ``_BACKEND`` suffix, no ``RUNTIME_ENABLE_`` prefix. If it too is ever
    #: removed, substitute another rather than dropping the half: a one-flag
    #: tuple would let the name-test blindness return unnoticed, which is the
    #: whole failure FINDINGS.md §4.6b recorded.
    GENERATIVE_UI_FLAGS = ("SURFACES_V2", "RUNTIME_PROPAGATE_QUEUE_TRACE")

    @staticmethod
    def _declared() -> dict:
        return _first_declarations(DEFAULT_SRC_ROOTS)

    def test_generative_ui_flags_are_in_scope(self) -> None:
        declared = self._declared()
        missing = [flag for flag in self.GENERATIVE_UI_FLAGS if flag not in declared]
        assert not missing, (
            f"the dark-capabilities gate cannot see {missing} — this is the "
            "exact blindness FINDINGS.md §4.6b recorded, returning"
        )

    def test_the_surface_spec_store_selector_is_in_scope(self) -> None:
        # Caught only because the ``RUNTIME_`` prefix requirement was dropped.
        assert "SURFACE_SPEC_STORE_BACKEND" in self._declared()

    def test_symbolically_referenced_flags_are_not_false_positives(self) -> None:
        """Two real flags pass ONLY through the ``Owner.CONSTANT`` alias.

        Their tests flip them via ``monkeypatch.setenv(SomeEnv.ENABLED, ...)``
        and never spell the env string, so a literal-only reference scan reports
        both dark. Widening the declaration side without widening the reference
        side would have turned two genuinely exercised capabilities into
        permanent CI noise — which is how gates get deleted.
        """

        declared = self._declared()
        symbols = _collect_referenced_symbols(REFERENCE_ROOTS)
        for flag in ("ARTIFACT_CLEANUP_EXECUTION_ENABLED", "REPAIR_EXECUTION_ENABLED"):
            assert declared[flag].aliases & symbols, f"{flag} lost its symbolic path"

    def test_the_reference_scan_answers_for_both_spellings_by_itself(self) -> None:
        """A caller must not be able to assemble a different answer than the gate.

        Regression test for a bug this gate caused rather than caught.
        ``agent_runtime/release/e2_final_conformance.py`` re-derives the verdict
        as ``_first_declarations() - _collect_referenced_names()``, calling
        neither :func:`_is_referenced` nor ``main``. While the symbolic channel
        was a second set the caller had to know to intersect, that consumer did
        not know, so the two flags below — driven ON by real tests, via the
        constant — were reported dark and the E2 release gate went red.

        The lesson is the audit's own: an answer that several layers reassemble
        will eventually be reassembled wrong. So the reference scan now returns
        the finished set, and this test pins that property at the seam the other
        consumer actually uses — the literal-only call, with no alias argument.
        """

        referenced = _collect_referenced_names(REFERENCE_ROOTS)

        for flag in ("ARTIFACT_CLEANUP_EXECUTION_ENABLED", "REPAIR_EXECUTION_ENABLED"):
            assert flag in referenced, (
                f"{flag} is referenced only as Owner.CONSTANT; a caller that "
                "scans literals alone must still see it, or it will report a "
                "tested capability dark"
            )

    def test_no_declared_flag_is_dark_by_either_route(self) -> None:
        """The gate's verdict and a naive caller's must agree, flag for flag."""

        declared = self._declared()
        referenced = _collect_referenced_names(REFERENCE_ROOTS, declarations=declared)
        naive = _collect_referenced_names(REFERENCE_ROOTS)

        assert {name for name in declared if name not in referenced} == set()
        assert {name for name in declared if name not in naive} == set()
