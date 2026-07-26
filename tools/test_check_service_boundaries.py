from __future__ import annotations

from pathlib import Path

from check_service_boundaries import (
    ServiceBoundary,
    boundary_violations,
    desktop_ipc_boundary_violations,
)


def test_rejects_sibling_service_import(tmp_path: Path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    (source / "rogue.py").write_text("from backend_app.store import Store\n")

    assert boundary_violations(
        (ServiceBoundary("ai-backend", source, frozenset({"backend_app"})),)
    ) == ("ai-backend:rogue.py:1:backend_app.store",)


def test_allows_contract_or_http_library_imports(tmp_path: Path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    (source / "allowed.py").write_text(
        "from copilot_service_contracts.work_ledger import load_work_ledger_contract\n"
        "import httpx\n"
    )

    assert (
        boundary_violations(
            (ServiceBoundary("ai-backend", source, frozenset({"backend_app"})),)
        )
        == ()
    )


def test_desktop_ipc_scanner_rejects_electron_import_from_renderer(
    tmp_path: Path,
) -> None:
    renderer = tmp_path / "apps/desktop/renderer"
    renderer.mkdir(parents=True)
    (renderer / "rogue.ts").write_text('import { ipcRenderer } from "electron";\n')
    for path in (
        "apps/desktop/preload",
        "packages/chat-transport/src",
        "packages/chat-surface/src",
        "packages/api-types/src",
    ):
        (tmp_path / path).mkdir(parents=True)

    assert desktop_ipc_boundary_violations(tmp_path) == (
        "desktop-renderer:rogue.ts:electron-import:electron",
    )


def test_desktop_ipc_scanner_rejects_renderer_import_of_private_main_broker(
    tmp_path: Path,
) -> None:
    renderer = tmp_path / "apps/desktop/renderer"
    renderer.mkdir(parents=True)
    (renderer / "rogue.ts").write_text(
        'import { broker } from "../main/capabilities/broker";\n'
    )
    for path in (
        "apps/desktop/preload",
        "packages/chat-transport/src",
        "packages/chat-surface/src",
        "packages/api-types/src",
    ):
        (tmp_path / path).mkdir(parents=True)

    assert desktop_ipc_boundary_violations(tmp_path) == (
        "desktop-renderer:rogue.ts:desktop-main-broker-import:../main/capabilities/broker",
    )
