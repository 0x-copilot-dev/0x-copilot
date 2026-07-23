"""Unit tests for the orphan-destination static gate (PRD-13 CI guard)."""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

sys.path.insert(0, str(HERE))

from check_orphan_destinations import (  # noqa: E402
    GuardPaths,
    barrel_value_exports,
    component_modules,
    default_paths,
    find_orphans,
    main,
)


# ---------------------------------------------------------------------------
# Synthetic-tree scaffolding
# ---------------------------------------------------------------------------


def _write(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def _paths(tmp: Path) -> GuardPaths:
    src = tmp / "packages" / "chat-surface" / "src"
    src.mkdir(parents=True, exist_ok=True)
    return GuardPaths(
        barrel=src / "index.ts",
        chat_surface_src=src,
        component_roots=(src / "destinations", src / "shell"),
        host_roots=(
            tmp / "apps" / "frontend" / "src",
            tmp / "apps" / "desktop" / "renderer",
        ),
    )


_WIDGET_TSX = (
    "import type { ReactElement } from 'react';\n"
    "export function Widget(): ReactElement {\n"
    "  return null as unknown as ReactElement;\n"
    "}\n"
)


def _component(paths: GuardPaths, subdir: str = "destinations/widget") -> None:
    _write(paths.chat_surface_src / subdir / "Widget.tsx", _WIDGET_TSX)


def _barrel(paths: GuardPaths, line: str) -> None:
    _write(paths.barrel, line + "\n")


def _orphan_names(paths: GuardPaths) -> list[str]:
    orphans, _ = find_orphans(paths)
    return [o.name for o in orphans]


# ---------------------------------------------------------------------------
# (a) The ChatsDestination shape — orphan detected, non-zero exit
# ---------------------------------------------------------------------------


class TestOrphanDetection:
    def test_exported_component_with_no_reference_is_an_orphan(
        self, tmp_path: Path
    ) -> None:
        # A `.tsx` under destinations/ exporting `function X(): ReactElement`,
        # re-exported from the barrel, referenced by neither host nor any other
        # in-package module — exactly the ChatsDestination shape.
        paths = _paths(tmp_path)
        _component(paths)
        _barrel(paths, 'export { Widget } from "./destinations/widget";')

        orphans, waived = find_orphans(paths)
        assert [o.name for o in orphans] == ["Widget"]
        assert waived == []
        # The orphan anchors to the barrel export line for a diff-visible report.
        assert orphans[0].file == paths.barrel
        assert orphans[0].lineno == 1

    def test_orphan_makes_the_gate_exit_non_zero(self, tmp_path: Path) -> None:
        paths = _paths(tmp_path)
        _component(paths)
        _barrel(paths, 'export { Widget } from "./destinations/widget";')
        assert main([], paths=paths) == 1

    def test_a_type_only_export_is_never_an_orphan(self, tmp_path: Path) -> None:
        # `export type { Widget }` publishes no value — not a mountable component.
        paths = _paths(tmp_path)
        _component(paths)
        _barrel(paths, 'export type { Widget } from "./destinations/widget";')
        assert _orphan_names(paths) == []

    def test_a_non_component_value_export_is_out_of_scope(self, tmp_path: Path) -> None:
        # A PascalCase value that is NOT an `export function …: ReactElement`
        # (here a const) is deliberately outside the narrow rule, even unused.
        paths = _paths(tmp_path)
        _write(
            paths.chat_surface_src / "destinations/widget/constants.ts",
            "export const WidgetWidth = 320;\n",
        )
        _barrel(paths, 'export { WidgetWidth } from "./destinations/widget";')
        assert _orphan_names(paths) == []

    def test_a_component_outside_destinations_or_shell_is_out_of_scope(
        self, tmp_path: Path
    ) -> None:
        # Same component defined under citations/ (not destinations|shell) — the
        # `from` specifier is out of scope, so the rule never reaches it.
        paths = _paths(tmp_path)
        _write(paths.chat_surface_src / "citations/Widget.tsx", _WIDGET_TSX)
        _barrel(paths, 'export { Widget } from "./citations/Widget";')
        assert _orphan_names(paths) == []


# ---------------------------------------------------------------------------
# (b) A host reference clears it
# ---------------------------------------------------------------------------


class TestHostMountClears:
    def test_frontend_mount_clears_the_orphan(self, tmp_path: Path) -> None:
        paths = _paths(tmp_path)
        _component(paths)
        _barrel(paths, 'export { Widget } from "./destinations/widget";')
        _write(
            paths.host_roots[0] / "app" / "App.tsx",
            "import { Widget } from '@0x-copilot/chat-surface';\n"
            "export const M = () => <Widget />;\n",
        )
        assert _orphan_names(paths) == []
        assert main([], paths=paths) == 0

    def test_desktop_renderer_mount_clears_the_orphan(self, tmp_path: Path) -> None:
        paths = _paths(tmp_path)
        _component(paths)
        _barrel(paths, 'export { Widget } from "./destinations/widget";')
        _write(
            paths.host_roots[1] / "destinationBinders.tsx",
            "const bind = () => Widget;\n",
        )
        assert _orphan_names(paths) == []

    def test_a_reference_only_in_a_host_TEST_file_does_not_clear(
        self, tmp_path: Path
    ) -> None:
        paths = _paths(tmp_path)
        _component(paths)
        _barrel(paths, 'export { Widget } from "./destinations/widget";')
        _write(paths.host_roots[0] / "app" / "App.test.tsx", "render(<Widget/>);\n")
        assert _orphan_names(paths) == ["Widget"]


# ---------------------------------------------------------------------------
# (c) An in-package (non-test, non-index) consumer clears it
# ---------------------------------------------------------------------------


class TestInPackageConsumerClears:
    def test_another_chat_surface_module_consuming_it_clears(
        self, tmp_path: Path
    ) -> None:
        paths = _paths(tmp_path)
        _component(paths)
        _barrel(paths, 'export { Widget } from "./destinations/widget";')
        _write(
            paths.chat_surface_src / "destinations/other/Other.tsx",
            "import { Widget } from '../widget/Widget';\n"
            "export const O = () => <Widget />;\n",
        )
        assert _orphan_names(paths) == []

    def test_even_a_comment_mention_counts_as_a_consumer(self, tmp_path: Path) -> None:
        # By design (a floor, not a proof of aliveness): a textual mention in a
        # non-test, non-index module counts. This is why ChatsSidebar was
        # "text-clean" until ChatsDestination.tsx (its comment) was also deleted.
        paths = _paths(tmp_path)
        _component(paths)
        _barrel(paths, 'export { Widget } from "./destinations/widget";')
        _write(
            paths.chat_surface_src / "destinations/other/note.ts",
            "// The legacy Widget stays for now.\n",
        )
        assert _orphan_names(paths) == []

    def test_the_defining_module_itself_never_counts_as_a_consumer(
        self, tmp_path: Path
    ) -> None:
        # The symbol trivially appears in its own definition; that self-reference
        # must not clear the orphan (else nothing could ever be flagged).
        paths = _paths(tmp_path)
        _component(paths)
        _barrel(paths, 'export { Widget } from "./destinations/widget";')
        assert _orphan_names(paths) == ["Widget"]

    def test_an_index_ts_barrel_never_counts_as_a_consumer(
        self, tmp_path: Path
    ) -> None:
        # A directory sub-barrel re-exporting the symbol is not a mount.
        paths = _paths(tmp_path)
        _component(paths)
        _barrel(paths, 'export { Widget } from "./destinations/widget";')
        _write(
            paths.chat_surface_src / "destinations/widget/index.ts",
            'export { Widget } from "./Widget";\n',
        )
        assert _orphan_names(paths) == ["Widget"]

    def test_a_reference_only_in_a_TEST_file_does_not_clear(
        self, tmp_path: Path
    ) -> None:
        paths = _paths(tmp_path)
        _component(paths)
        _barrel(paths, 'export { Widget } from "./destinations/widget";')
        _write(
            paths.chat_surface_src / "destinations/widget/Widget.test.tsx",
            "render(<Widget/>);\n",
        )
        assert _orphan_names(paths) == ["Widget"]


# ---------------------------------------------------------------------------
# (d) An inline waiver on the export line clears it (and is counted)
# ---------------------------------------------------------------------------


class TestWaiver:
    def test_waiver_on_the_export_line_clears_the_orphan(self, tmp_path: Path) -> None:
        paths = _paths(tmp_path)
        _component(paths)
        _barrel(
            paths,
            'export { Widget } from "./destinations/widget";'
            " // orphan-destination-waiver: owner=TEST-1 — folded IA",
        )
        orphans, waived = find_orphans(paths)
        assert orphans == []
        assert [(w.name, w.owner) for w in waived] == [("Widget", "TEST-1")]

    def test_waiver_in_a_multiline_block_clears_the_orphan(
        self, tmp_path: Path
    ) -> None:
        paths = _paths(tmp_path)
        _component(paths)
        _write(
            paths.barrel,
            "export {\n"
            "  Widget, // orphan-destination-waiver: owner=TEST-2 — reason\n"
            '} from "./destinations/widget";\n',
        )
        orphans, waived = find_orphans(paths)
        assert orphans == []
        assert [(w.name, w.owner) for w in waived] == [("Widget", "TEST-2")]

    def test_print_waivers_lists_each_waiver_and_exits_zero(
        self, tmp_path: Path, capsys
    ) -> None:
        paths = _paths(tmp_path)
        _component(paths)
        _barrel(
            paths,
            'export { Widget } from "./destinations/widget";'
            " // orphan-destination-waiver: owner=TEST-1 — folded IA",
        )
        assert main(["--print-waivers"], paths=paths) == 0
        out = capsys.readouterr().out
        assert "Widget owner=TEST-1" in out


# ---------------------------------------------------------------------------
# (f) README G8 — the scope extension to src/shell/ is live
# ---------------------------------------------------------------------------


class TestShellScopeExtension:
    def test_a_shell_component_with_no_reference_is_an_orphan(
        self, tmp_path: Path
    ) -> None:
        # Defining module under src/shell/ (NOT destinations/), barrel `from
        # "./shell"`, no host and no in-package consumer — must be flagged,
        # proving G8's scope extension actually reaches the shell.
        paths = _paths(tmp_path)
        _write(
            paths.chat_surface_src / "shell" / "RailWidget.tsx",
            _WIDGET_TSX.replace("Widget", "RailWidget"),
        )
        _barrel(paths, 'export { RailWidget } from "./shell";')
        assert _orphan_names(paths) == ["RailWidget"]


# ---------------------------------------------------------------------------
# Low-level parser behaviour
# ---------------------------------------------------------------------------


class TestBarrelParser:
    def test_value_exports_are_collected_types_are_skipped(
        self, tmp_path: Path
    ) -> None:
        barrel = _write(
            tmp_path / "index.ts",
            "export {\n"
            "  Widget,\n"
            "  type WidgetProps,\n"
            "  helperFn,\n"
            '} from "./destinations/widget";\n',
        )
        names = [e.name for e in barrel_value_exports(barrel)]
        # PascalCase VALUES only: `Widget` in, the `type` and the camelCase fn out.
        assert names == ["Widget"]

    def test_component_modules_finds_reactelement_functions(
        self, tmp_path: Path
    ) -> None:
        root = tmp_path / "destinations"
        _write(root / "widget" / "Widget.tsx", _WIDGET_TSX)
        _write(
            root / "gen" / "Generic.tsx",
            "import type { ReactElement } from 'react';\n"
            "export function Generic<T>(p: T): ReactElement {\n"
            "  return null as unknown as ReactElement;\n}\n",
        )
        _write(root / "notacomp" / "util.ts", "export const x = 1;\n")
        modules = component_modules((root,))
        assert set(modules) == {"Widget", "Generic"}


# ---------------------------------------------------------------------------
# (e) The real repo tree must be green (standing baseline)
# ---------------------------------------------------------------------------


def test_real_tree_has_no_orphaned_destinations() -> None:
    """Every barrel-exported destination/shell component in the real tree is
    mounted by a host, consumed in-package, or waived. If this fails, a
    component shipped exported-but-mounted-by-nobody — the exact ChatsSidebar
    /ChatsDestination failure mode this guard exists to stop."""

    orphans, _ = find_orphans(default_paths())
    assert [o.name for o in orphans] == [], (
        "orphaned exported destinations: " + ", ".join(o.name for o in orphans)
    )
    assert main([]) == 0


def test_waiver_count_does_not_grow() -> None:
    """The waiver block is the ~95k-LOC dead-code backlog's CI-visible counter.
    Pinning it to an integer literal means a later PR adding a waiver (or an
    orphan that must be waived) fails HERE and has to justify the bump."""

    _, waivers = find_orphans(default_paths())
    # Bumped 12 -> 13 for the Generative Surfaces v2 cockpit chips
    # (PostureChip / PendingCounterChip): exported from the barrel and consumed
    # in-package by RunDestination (the cockpit both hosts mount), but not
    # host-name-referenced, so the shallow guard waives them. Legit in-package
    # consumers, owner-tagged at the export site.
    assert len(waivers) == 13
