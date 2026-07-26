from __future__ import annotations

from pathlib import Path

from check_service_boundaries import ServiceBoundary, boundary_violations


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
