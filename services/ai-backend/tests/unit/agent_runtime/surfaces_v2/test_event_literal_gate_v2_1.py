"""Repository gate: v2.1 event values are referenced through contract enums."""

from __future__ import annotations

from pathlib import Path
import re

from copilot_service_contracts.work_ledger import LEDGER_EVENT_TYPES


_REPO_ROOT = Path(__file__).resolve().parents[6]
_COMPONENT_COLLECTIONS = ("services", "packages", "apps")
_COMPONENT_SOURCE_DIR_NAMES = ("src", "scripts", "migrations")
_DESKTOP_SOURCE_DIR_NAMES = ("main", "preload", "renderer", "build", "native")
_ALLOWED_MIRROR_PATHS = (
    Path("services/ai-backend/src/agent_runtime/surfaces_v2/ledger_models.py"),
    Path("packages/api-types/src/ledger.ts"),
)
_SOURCE_SUFFIXES = {".py", ".ts", ".tsx", ".js", ".mjs"}


def _component_roots(repo_root: Path) -> tuple[Path, ...]:
    roots: list[Path] = []
    for collection in _COMPONENT_COLLECTIONS:
        collection_root = repo_root / collection
        if not collection_root.is_dir():
            continue
        roots.extend(
            sorted(path for path in collection_root.iterdir() if path.is_dir())
        )
    return tuple(roots)


def _source_locations(repo_root: Path) -> tuple[Path, ...]:
    """Return authored code locations without traversing generated app output."""
    locations: list[Path] = []
    desktop_root = repo_root / "apps" / "desktop"
    for component_root in _component_roots(repo_root):
        locations.extend(
            path
            for path in component_root.iterdir()
            if path.is_file() and path.suffix in _SOURCE_SUFFIXES
        )

        source_dir_names = _COMPONENT_SOURCE_DIR_NAMES
        if component_root == desktop_root:
            source_dir_names += _DESKTOP_SOURCE_DIR_NAMES
        for source_dir_name in source_dir_names:
            source_dir = component_root / source_dir_name
            if source_dir.is_dir():
                locations.append(source_dir)

    return tuple(sorted(set(locations)))


def _source_files(repo_root: Path) -> tuple[Path, ...]:
    files: list[Path] = []
    for location in _source_locations(repo_root):
        paths = (location,) if location.is_file() else sorted(location.rglob("*"))
        files.extend(
            path for path in paths if path.is_file() and path.suffix in _SOURCE_SUFFIXES
        )
    return tuple(files)


def _find_inline_event_literals(repo_root: Path) -> list[str]:
    new_events = tuple(LEDGER_EVENT_TYPES[15:])
    pattern = re.compile(
        r"""(?P<quote>["'])(?:%s)(?P=quote)"""
        % "|".join(re.escape(value) for value in new_events)
    )
    allowed_mirrors = {repo_root / path for path in _ALLOWED_MIRROR_PATHS}
    violations: list[str] = []
    for path in _source_files(repo_root):
        if path in allowed_mirrors or "tests" in path.parts or ".test." in path.name:
            continue
        text = path.read_text(encoding="utf-8")
        for match in pattern.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            violations.append(f"{path.relative_to(repo_root)}:{line}: {match.group(0)}")
    return violations


def test_new_event_values_are_not_redeclared_outside_contract_mirrors() -> None:
    violations = _find_inline_event_literals(_REPO_ROOT)

    assert violations == [], (
        "new Work Ledger event values must come from the SSOT/mirror enum; "
        f"inline duplicates found: {violations}"
    )


def test_literal_gate_scans_authored_sources_not_generated_app_output(
    tmp_path: Path,
) -> None:
    event_type = LEDGER_EVENT_TYPES[15]
    paths = (
        tmp_path / "apps/desktop/main/source.ts",
        tmp_path / "apps/frontend/src/source.ts",
        tmp_path / "apps/desktop/out/main/index.js",
        tmp_path / "apps/frontend/dist/assets/index.js",
    )
    for path in paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f'const eventType = "{event_type}";\n', encoding="utf-8")

    violations = _find_inline_event_literals(tmp_path)

    assert set(violations) == {
        f'apps/desktop/main/source.ts:1: "{event_type}"',
        f'apps/frontend/src/source.ts:1: "{event_type}"',
    }
