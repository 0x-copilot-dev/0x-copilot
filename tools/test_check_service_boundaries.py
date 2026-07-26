from __future__ import annotations

from pathlib import Path

from check_service_boundaries import (
    ServiceBoundary,
    boundary_violations,
    desktop_ipc_boundary_violations,
)


def _desktop_boundary_fixture(tmp_path: Path) -> Path:
    for path in (
        "apps/desktop/renderer",
        "apps/desktop/preload",
        "apps/desktop/main/capabilities",
        "apps/desktop/main/connectors",
        "apps/desktop/main/services",
        "packages/chat-transport/src",
        "packages/chat-surface/src",
        "packages/api-types/src",
        "packages/design-system/src",
        "packages/surface-renderers/src",
    ):
        (tmp_path / path).mkdir(parents=True)
    return tmp_path / "apps/desktop/renderer"


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
    renderer = _desktop_boundary_fixture(tmp_path)
    (renderer / "rogue.ts").write_text('import { ipcRenderer } from "electron";\n')

    assert desktop_ipc_boundary_violations(tmp_path) == (
        "desktop-renderer:rogue.ts:electron-import:electron",
    )


def test_desktop_ipc_scanner_rejects_resolved_desktop_main_imports_with_or_without_extension(
    tmp_path: Path,
) -> None:
    renderer = _desktop_boundary_fixture(tmp_path)
    main_capabilities = tmp_path / "apps/desktop/main/capabilities"
    (main_capabilities / "broker.ts").write_text("export const broker = {};\n")
    (main_capabilities / "native-workspace-commit-helper.ts").write_text(
        "export const nativeWorkspaceCommitHelper = {};\n"
    )
    (renderer / "broker-with-extension.ts").write_text(
        'import { broker } from "../main/capabilities/broker.ts";\n'
    )
    (renderer / "broker-no-extension.ts").write_text(
        'import { broker } from "../main/capabilities/broker";\n'
    )
    (renderer / "native-helper-with-extension.ts").write_text(
        'import { nativeWorkspaceCommitHelper } from "../main/capabilities/native-workspace-commit-helper.ts";\n'
    )
    (renderer / "native-helper-no-extension.ts").write_text(
        'import { nativeWorkspaceCommitHelper } from "../main/capabilities/native-workspace-commit-helper";\n'
    )

    assert desktop_ipc_boundary_violations(tmp_path) == (
        "desktop-renderer:broker-no-extension.ts:desktop-main-import:../main/capabilities/broker",
        "desktop-renderer:broker-with-extension.ts:desktop-main-import:../main/capabilities/broker.ts",
        "desktop-renderer:native-helper-no-extension.ts:desktop-main-import:../main/capabilities/native-workspace-commit-helper",
        "desktop-renderer:native-helper-with-extension.ts:desktop-main-import:../main/capabilities/native-workspace-commit-helper.ts",
    )


def test_desktop_ipc_scanner_rejects_shared_package_escape_to_desktop_main(
    tmp_path: Path,
) -> None:
    _desktop_boundary_fixture(tmp_path)
    main_capabilities = tmp_path / "apps/desktop/main/capabilities"
    (main_capabilities / "broker.ts").write_text("export const broker = {};\n")
    shared = tmp_path / "packages/chat-surface/src"
    (shared / "rogue.ts").write_text(
        'import { broker } from "../../../apps/desktop/main/capabilities/broker";\n'
    )

    assert desktop_ipc_boundary_violations(tmp_path) == (
        "chat-surface:rogue.ts:desktop-main-import:../../../apps/desktop/main/capabilities/broker",
    )


def test_desktop_ipc_scanner_allows_only_resolved_typed_ipc_contracts(
    tmp_path: Path,
) -> None:
    renderer = _desktop_boundary_fixture(tmp_path)
    channels = tmp_path / "apps/desktop/main/capabilities/channels.ts"
    channels.write_text("export const CAPABILITY_CHANNELS = {};\n")
    (renderer / "typed-contract.ts").write_text(
        'import { CAPABILITY_CHANNELS } from "../main/capabilities/channels";\n'
    )
    (renderer / "typed-contract-extension.ts").write_text(
        'import { CAPABILITY_CHANNELS } from "../main/capabilities/channels.ts";\n'
    )

    assert desktop_ipc_boundary_violations(tmp_path) == ()
