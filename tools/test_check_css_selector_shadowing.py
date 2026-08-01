"""Unit tests for the CSS selector shadowing gate.

The gate exists because `apps/desktop/renderer/firstrun.css` re-declared `.fr`,
`.fr-top`, `.fr-main` … — names owned by
`packages/chat-surface/src/onboarding/onboarding.css` — and, being imported
after the package sheet, silently won the cascade: the first-run composer
rendered 408px wide inside its 640px column. These tests pin the detection, the
`shadow-ok:` escape hatch, the deliberately-narrow scope, and the real tree.
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

sys.path.insert(0, str(HERE))

from check_css_selector_shadowing import (  # noqa: E402
    GuardPaths,
    default_paths,
    find_shadowing,
    main,
    normalize_selector,
    parse_rules,
    split_selector_list,
)


# ---------------------------------------------------------------------------
# Synthetic-tree scaffolding
# ---------------------------------------------------------------------------


def _write(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def _paths(tmp: Path) -> GuardPaths:
    return GuardPaths(
        apps_dir=tmp / "apps",
        packages_dir=tmp / "packages",
        baseline=tmp / "baseline.txt",
        repo_root=tmp,
    )


def _package(paths: GuardPaths, css: str, name: str = "surface.css") -> Path:
    return _write(paths.packages_dir / "chat-surface" / "src" / name, css)


def _app(
    paths: GuardPaths,
    css: str,
    *,
    name: str = "host.css",
    app: str = "desktop",
    loads_package_css: bool = True,
) -> Path:
    app_dir = paths.apps_dir / app
    if loads_package_css:
        # Only an app that actually pulls a package sheet into its document can
        # shadow one, so every in-scope fixture needs a real import site.
        _write(
            app_dir / "renderer" / "bootstrap.tsx",
            'import "@0x-copilot/chat-surface/src/surface.css";\n',
        )
    else:
        _write(app_dir / "renderer" / "bootstrap.tsx", "export const x = 1;\n")
    return _write(app_dir / "renderer" / name, css)


def _baseline(paths: GuardPaths, *entries: str) -> None:
    _write(paths.baseline, "# header\n" + "".join(e + "\n" for e in entries))


def _selectors(paths: GuardPaths) -> list[str]:
    violations, _, _, _ = find_shadowing(paths)
    return [v.selector for v in violations]


# ---------------------------------------------------------------------------
# (a) The firstrun.css shape — shadow detected, non-zero exit
# ---------------------------------------------------------------------------


class TestShadowDetection:
    def test_app_redeclaring_a_package_selector_is_a_shadow(
        self, tmp_path: Path
    ) -> None:
        # Exactly the shipped bug: the package owns `.fr-main`, the host sheet
        # declares it again, and the host sheet loads last.
        paths = _paths(tmp_path)
        _package(paths, ".fr-main {\n  align-items: center;\n}\n")
        _app(paths, ".fr-main {\n  align-items: flex-start;\n}\n")

        violations, waived, baselined, stale = find_shadowing(paths)
        assert [v.selector for v in violations] == [".fr-main"]
        assert (waived, baselined, stale) == ([], [], [])
        # Anchored at the app-side rule, which is the line to delete.
        assert violations[0].app_lineno == 1
        assert violations[0].package_file.name == "surface.css"

    def test_a_shadow_makes_the_gate_exit_non_zero(self, tmp_path: Path) -> None:
        paths = _paths(tmp_path)
        _package(paths, ".fr-main {\n  align-items: center;\n}\n")
        _app(paths, ".fr-main {\n  align-items: flex-start;\n}\n")
        assert main([], paths=paths) == 1

    def test_a_selector_the_package_never_declares_is_fine(
        self, tmp_path: Path
    ) -> None:
        paths = _paths(tmp_path)
        _package(paths, ".fr-main {\n  align-items: center;\n}\n")
        _app(paths, ".fr-boot {\n  display: grid;\n}\n")
        assert _selectors(paths) == []
        assert main([], paths=paths) == 0

    def test_every_selector_in_a_comma_list_is_checked(self, tmp_path: Path) -> None:
        # `.a, .b { }` declares two names; shadowing either one is the bug.
        paths = _paths(tmp_path)
        _package(paths, ".fr-top,\n.fr-foot {\n  display: flex;\n}\n")
        _app(paths, ".fr-brand,\n.fr-foot {\n  display: block;\n}\n")
        assert _selectors(paths) == [".fr-foot"]

    def test_shadowing_inside_a_media_query_still_counts(self, tmp_path: Path) -> None:
        # A host media-query rule overrides the package just as silently.
        paths = _paths(tmp_path)
        _package(paths, ".fr-main {\n  gap: 12px;\n}\n")
        _app(
            paths,
            "@media (max-width: 700px) {\n  .fr-main {\n    gap: 0;\n  }\n}\n",
        )
        assert _selectors(paths) == [".fr-main"]

    def test_shadowing_is_reported_once_per_file_and_selector(
        self, tmp_path: Path
    ) -> None:
        paths = _paths(tmp_path)
        _package(paths, ".fr-main {\n  gap: 12px;\n}\n")
        _app(paths, ".fr-main {\n  gap: 0;\n}\n.fr-main {\n  gap: 4px;\n}\n")
        assert _selectors(paths) == [".fr-main"]


# ---------------------------------------------------------------------------
# (b) The `shadow-ok:` escape hatch
# ---------------------------------------------------------------------------


class TestWaiver:
    def test_shadow_ok_comment_above_the_rule_clears_it(self, tmp_path: Path) -> None:
        paths = _paths(tmp_path)
        _package(paths, ".fr {\n  min-height: 100vh;\n}\n")
        _app(
            paths,
            "/* shadow-ok: desktop window frame is shorter than the viewport */\n"
            ".fr {\n  height: 100%;\n}\n",
        )
        violations, waived, _, _ = find_shadowing(paths)
        assert violations == []
        assert [w.selector for w in waived] == [".fr"]
        assert waived[0].reason == ("desktop window frame is shorter than the viewport")
        assert main([], paths=paths) == 0

    def test_marker_inside_a_long_comment_block_is_found(self, tmp_path: Path) -> None:
        # The real firstrun.css case: an existing explanatory block gains one
        # line rather than growing a second comment.
        paths = _paths(tmp_path)
        _package(paths, ".fr {\n  min-height: 100vh;\n}\n")
        _app(
            paths,
            "/* shadow-ok: substrate delta, re-bases the shared height.\n"
            " * The shared sheet sizes `.fr` for a scrolling web document.\n"
            " */\n.fr {\n  height: 100%;\n}\n",
        )
        violations, waived, _, _ = find_shadowing(paths)
        assert violations == []
        assert waived[0].reason.startswith("substrate delta")

    def test_a_reasonless_marker_does_not_clear_the_shadow(
        self, tmp_path: Path
    ) -> None:
        # A bare `shadow-ok:` is a rubber stamp; the hatch must cost a sentence.
        paths = _paths(tmp_path)
        _package(paths, ".fr {\n  min-height: 100vh;\n}\n")
        _app(paths, "/* shadow-ok: */\n.fr {\n  height: 100%;\n}\n")
        assert _selectors(paths) == [".fr"]

    def test_a_marker_that_is_not_immediately_above_does_not_carry(
        self, tmp_path: Path
    ) -> None:
        # The annotation must sit on the rule it excuses, or one waiver would
        # silently cover every rule after it.
        paths = _paths(tmp_path)
        _package(paths, ".fr {\n  min-height: 100vh;\n}\n.fr-main {\n  gap: 1px;\n}\n")
        _app(
            paths,
            "/* shadow-ok: only the height override is deliberate */\n"
            ".fr {\n  height: 100%;\n}\n"
            ".fr-main {\n  align-items: flex-start;\n}\n",
        )
        violations, waived, _, _ = find_shadowing(paths)
        assert [v.selector for v in violations] == [".fr-main"]
        assert [w.selector for w in waived] == [".fr"]

    def test_one_annotated_copy_does_not_launder_an_unannotated_duplicate(
        self, tmp_path: Path
    ) -> None:
        paths = _paths(tmp_path)
        _package(paths, ".fr {\n  min-height: 100vh;\n}\n")
        _app(
            paths,
            "/* shadow-ok: deliberate */\n.fr {\n  height: 100%;\n}\n"
            ".fr {\n  color: red;\n}\n",
        )
        violations, waived, _, _ = find_shadowing(paths)
        assert [v.selector for v in violations] == [".fr"]
        assert waived == []
        # Reported at the UNannotated copy — the line that has to be justified.
        assert violations[0].app_lineno == 5

    def test_print_waivers_lists_each_override_and_exits_zero(
        self, tmp_path: Path, capsys
    ) -> None:
        paths = _paths(tmp_path)
        _package(paths, ".fr {\n  min-height: 100vh;\n}\n")
        _app(paths, "/* shadow-ok: substrate delta */\n.fr {\n  height: 100%;\n}\n")
        assert main(["--print-waivers"], paths=paths) == 0
        out = capsys.readouterr().out
        assert ":: .fr — substrate delta" in out
        assert "# 1 deliberate override(s)" in out


# ---------------------------------------------------------------------------
# (c) Deliberate scope limits — each keeps a whole class of false positive out
# ---------------------------------------------------------------------------


class TestScope:
    def test_selectors_with_no_class_are_out_of_scope(self, tmp_path: Path) -> None:
        # `:root`, `body`, `h1`, `*` are base/reset styling — every document's
        # own job, and both hosts legitimately set them.
        paths = _paths(tmp_path)
        _package(
            paths,
            ":root {\n  --a: 1;\n}\nbody {\n  margin: 0;\n}\nh1 {\n  font-size: 2rem;\n}\n"
            "* {\n  box-sizing: border-box;\n}\n",
        )
        _app(
            paths,
            ":root {\n  --a: 2;\n}\nbody {\n  margin: 1px;\n}\nh1 {\n  font-size: 1rem;\n}\n"
            "* {\n  box-sizing: content-box;\n}\n",
        )
        assert _selectors(paths) == []

    def test_an_app_that_loads_no_package_stylesheet_is_out_of_scope(
        self, tmp_path: Path
    ) -> None:
        # apps/website: a standalone Astro site depending on no @0x-copilot/*
        # package. Its `.cc` colliding with approvals.css's `.cc` is a
        # coincidence — the two sheets never share a document.
        paths = _paths(tmp_path)
        _package(paths, ".cc {\n  display: flex;\n}\n")
        _app(
            paths,
            ".cc {\n  border-radius: 18px;\n}\n",
            app="website",
            loads_package_css=False,
        )
        assert _selectors(paths) == []

    def test_the_same_sheet_is_in_scope_once_the_app_loads_package_css(
        self, tmp_path: Path
    ) -> None:
        # The inverse of the test above, so the exclusion is proven to be about
        # the import and not about the app's name.
        paths = _paths(tmp_path)
        _package(paths, ".cc {\n  display: flex;\n}\n")
        _app(paths, ".cc {\n  border-radius: 18px;\n}\n", app="website")
        assert _selectors(paths) == [".cc"]

    def test_css_modules_are_out_of_scope(self, tmp_path: Path) -> None:
        # The bundler hashes these class names, so they cannot collide.
        paths = _paths(tmp_path)
        _package(paths, ".fr-main {\n  gap: 12px;\n}\n")
        _app(paths, ".fr-main {\n  gap: 0;\n}\n", name="host.module.css")
        assert _selectors(paths) == []

    def test_build_output_is_not_scanned(self, tmp_path: Path) -> None:
        paths = _paths(tmp_path)
        _package(paths, ".fr-main {\n  gap: 12px;\n}\n")
        _app(paths, ".fr-boot {\n  display: grid;\n}\n")
        _write(
            paths.apps_dir / "desktop" / "dist" / "bundle.css",
            ".fr-main {\n  gap: 0;\n}\n",
        )
        _write(
            paths.apps_dir / "desktop" / "node_modules" / "dep" / "d.css",
            ".fr-main {\n  gap: 0;\n}\n",
        )
        assert _selectors(paths) == []

    def test_the_desktop_staged_runtime_is_not_scanned(self, tmp_path: Path) -> None:
        # `apps/desktop/resources/` is written by tools/desktop-runtime/stage.mjs
        # and holds a COPY of the built web assets, so every package selector in
        # the bundle looks like an app-side shadow of itself. Staging is a
        # documented step — the desktop journeys require it — so before this was
        # skipped, running them made the gate reject EVERY later commit with
        # hundreds of findings about files that are not source.
        paths = _paths(tmp_path)
        _package(paths, ".fr-main {\n  gap: 12px;\n}\n")
        _app(paths, ".fr-boot {\n  display: grid;\n}\n")
        _write(
            paths.apps_dir / "desktop" / "resources" / "web" / "assets" / "b.css",
            ".fr-main {\n  gap: 0;\n}\n",
        )
        assert _selectors(paths) == []


# ---------------------------------------------------------------------------
# (d) The baseline ratchet
# ---------------------------------------------------------------------------


class TestBaseline:
    def test_a_baselined_shadow_does_not_fail_the_gate(self, tmp_path: Path) -> None:
        paths = _paths(tmp_path)
        _package(paths, ".aui-composer {\n  gap: 12px;\n}\n")
        _app(paths, ".aui-composer {\n  gap: 0;\n}\n")
        _baseline(paths, "apps/desktop/renderer/host.css :: .aui-composer")

        violations, _, baselined, stale = find_shadowing(paths)
        assert violations == []
        assert [b.selector for b in baselined] == [".aui-composer"]
        assert stale == []
        assert main([], paths=paths) == 0

    def test_a_new_shadow_alongside_a_baselined_one_still_fails(
        self, tmp_path: Path
    ) -> None:
        paths = _paths(tmp_path)
        _package(
            paths, ".aui-composer {\n  gap: 12px;\n}\n.fr-main {\n  gap: 1px;\n}\n"
        )
        _app(paths, ".aui-composer {\n  gap: 0;\n}\n.fr-main {\n  gap: 2px;\n}\n")
        _baseline(paths, "apps/desktop/renderer/host.css :: .aui-composer")
        assert _selectors(paths) == [".fr-main"]
        assert main([], paths=paths) == 1

    def test_a_baseline_entry_that_no_longer_collides_is_a_hard_error(
        self, tmp_path: Path
    ) -> None:
        # Without this the baseline would be a floor instead of a ratchet:
        # deleting an app-side copy would leave a line that silently re-permits
        # the same shadow the next time someone adds it back.
        paths = _paths(tmp_path)
        _package(paths, ".aui-composer {\n  gap: 12px;\n}\n")
        _app(paths, ".fr-boot {\n  display: grid;\n}\n")
        _baseline(paths, "apps/desktop/renderer/host.css :: .aui-composer")

        violations, _, _, stale = find_shadowing(paths)
        assert violations == []
        assert stale == ["apps/desktop/renderer/host.css :: .aui-composer"]
        assert main([], paths=paths) == 1

    def test_print_baseline_lists_the_debt_and_exits_zero(
        self, tmp_path: Path, capsys
    ) -> None:
        paths = _paths(tmp_path)
        _package(paths, ".aui-composer {\n  gap: 12px;\n}\n")
        _app(paths, ".aui-composer {\n  gap: 0;\n}\n")
        _baseline(paths, "apps/desktop/renderer/host.css :: .aui-composer")
        assert main(["--print-baseline"], paths=paths) == 0
        out = capsys.readouterr().out
        assert "apps/desktop/renderer/host.css :: .aui-composer" in out
        assert "# 1 baselined shadow(s) to burn down" in out


# ---------------------------------------------------------------------------
# (e) Low-level CSS parsing
# ---------------------------------------------------------------------------


class TestParser:
    def test_keyframe_steps_are_not_selectors(self, tmp_path: Path) -> None:
        css = "@keyframes fr-spin {\n  from {\n    opacity: 0;\n  }\n  .5 {\n    x: 1;\n  }\n}\n"
        assert parse_rules(css, tmp_path / "a.css") == []

    def test_rules_inside_media_and_supports_are_selectors(
        self, tmp_path: Path
    ) -> None:
        css = (
            "@media (min-width: 40rem) {\n"
            "  @supports (display: grid) {\n"
            "    .fr-main {\n      gap: 0;\n    }\n"
            "  }\n"
            "}\n"
        )
        assert [r.selector for r in parse_rules(css, tmp_path / "a.css")] == [
            ".fr-main"
        ]

    def test_font_face_and_property_bodies_declare_nothing(
        self, tmp_path: Path
    ) -> None:
        css = (
            "@font-face {\n  font-family: X;\n}\n"
            "@property --p {\n  syntax: '<color>';\n}\n"
            ".fr-main {\n  gap: 0;\n}\n"
        )
        assert [r.selector for r in parse_rules(css, tmp_path / "a.css")] == [
            ".fr-main"
        ]

    def test_a_brace_inside_a_string_does_not_desynchronise_the_scanner(
        self, tmp_path: Path
    ) -> None:
        css = '.fr-foot::after {\n  content: "}";\n}\n.fr-main {\n  gap: 0;\n}\n'
        assert [r.selector for r in parse_rules(css, tmp_path / "a.css")] == [
            ".fr-foot::after",
            ".fr-main",
        ]

    def test_nested_rules_inside_a_style_rule_are_not_standalone_selectors(
        self, tmp_path: Path
    ) -> None:
        css = ".fr-main {\n  gap: 0;\n  &:hover {\n    gap: 1px;\n  }\n}\n"
        assert [r.selector for r in parse_rules(css, tmp_path / "a.css")] == [
            ".fr-main"
        ]

    def test_line_numbers_point_at_the_selector(self, tmp_path: Path) -> None:
        css = "/* note */\n\n\n.fr-main {\n  gap: 0;\n}\n"
        assert parse_rules(css, tmp_path / "a.css")[0].lineno == 4

    def test_malformed_css_degrades_instead_of_crashing(self, tmp_path: Path) -> None:
        # A parse error in one stylesheet must never take the whole gate down:
        # a crashing CI check gets disabled, and then it protects nothing.
        css = (
            "@ {\n  .a {\n    gap: 0;\n  }\n}\n"  # at-rule with no name
            ".fr-main {\n  gap: 0;\n"  # unclosed rule
            "/* unterminated comment\n"
        )
        assert [r.selector for r in parse_rules(css, tmp_path / "a.css")] == [
            ".fr-main"
        ]

    def test_comma_splitting_ignores_commas_inside_is_and_attributes(self) -> None:
        assert split_selector_list(':is(.a, .b) .c, [data-x="p,q"].d') == [
            ":is(.a, .b) .c",
            '[data-x="p,q"].d',
        ]

    def test_normalisation_makes_whitespace_and_combinators_compare_equal(
        self,
    ) -> None:
        assert normalize_selector(".a>.b") == normalize_selector(".a  >  .b")
        assert normalize_selector(".a\n  .b") == ".a .b"
        # A `+` inside a functional pseudo-class must survive untouched.
        assert normalize_selector(".a:nth-child(2n+1)") == ".a:nth-child(2n+1)"

    def test_normalised_variants_of_one_selector_collide(self, tmp_path: Path) -> None:
        paths = _paths(tmp_path)
        _package(paths, ".fr-main > .fr-col {\n  gap: 12px;\n}\n")
        _app(paths, ".fr-main>.fr-col {\n  gap: 0;\n}\n")
        assert _selectors(paths) == [".fr-main > .fr-col"]


# ---------------------------------------------------------------------------
# (f) The real repo tree must be green (standing baseline)
# ---------------------------------------------------------------------------


def test_real_tree_has_no_new_css_shadowing() -> None:
    """Every app-side selector that a package sheet also owns is either
    annotated `shadow-ok:` or recorded in the baseline. If this fails, a host
    sheet just took over a shared class name — the firstrun.css `.fr-main`
    failure mode this guard exists to stop."""

    violations, _, _, stale = find_shadowing(default_paths())
    assert [v.render() for v in violations] == []
    assert stale == []
    assert main([]) == 0


def test_the_firstrun_height_override_is_the_only_deliberate_override() -> None:
    """`apps/desktop/renderer/firstrun.css`'s `.fr` height override is the one
    legitimate shadow in the repo: the shared sheet sizes `.fr` for a scrolling
    web document, and the desktop window frame is one titlebar inset shorter
    than the viewport. Pinning the set means the next `shadow-ok:` fails HERE
    and has to be argued for."""

    _, waived, _, _ = find_shadowing(default_paths())
    assert [w.key() for w in waived] == [
        "apps/desktop/renderer/firstrun.css :: .fr",
    ]


def test_baseline_debt_does_not_grow() -> None:
    """The baseline is the burn-down counter for the one pre-existing defect:
    `apps/frontend/src/styles.css` carries a private fork of rules owned by
    packages/chat-surface (composer.css, workspace.css, onboarding.css). Pinning
    it to an integer literal means a PR that adds to the fork fails HERE instead
    of quietly appending a line."""

    _, _, baselined, _ = find_shadowing(default_paths())
    assert len(baselined) <= 136, (
        "CSS shadowing debt grew; new shadowing needs a `shadow-ok:` reason at "
        "the rule, not a baseline line"
    )
