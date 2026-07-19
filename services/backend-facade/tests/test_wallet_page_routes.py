"""The facade serves the built SIWE wallet page when FACADE_WEB_DIST_DIR is set.

Desktop-only behavior (PRD-2): the packaged app opens `{facade}/wallet.html`, so
the supervised facade must serve the built frontend dist same-origin with the
SIWE API. Env-gated: unset (web/self-host) → no route, byte-unchanged.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from backend_facade.app import create_app
from backend_facade.settings import FacadeSettings


def _client(web_dist_dir: str) -> TestClient:
    return TestClient(
        create_app(
            FacadeSettings(
                backend_url="http://backend.local",
                web_dist_dir=web_dist_dir,
            ),
            configure_logging_on_create=False,
            configure_telemetry_on_create=False,
        )
    )


def _seed_dist(tmp_path: Path) -> Path:
    (tmp_path / "wallet.html").write_text("<!doctype html><title>wallet</title>")
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "wallet-abc.js").write_text("export const x = 1;\n")
    return tmp_path


class TestWalletPageServed:
    def test_serves_wallet_html_and_assets_when_configured(self, tmp_path) -> None:
        _seed_dist(tmp_path)
        client = _client(str(tmp_path))

        page = client.get("/wallet.html")
        assert page.status_code == 200
        assert "text/html" in page.headers["content-type"]
        assert "wallet" in page.text

        asset = client.get("/assets/wallet-abc.js")
        assert asset.status_code == 200
        assert "javascript" in asset.headers["content-type"]

    def test_no_route_when_unset(self) -> None:
        # Web/self-host default: nginx serves the page, the facade must not.
        client = _client("")
        assert client.get("/wallet.html").status_code == 404

    def test_fails_closed_when_dir_has_no_wallet_html(self, tmp_path) -> None:
        # Misconfigured dir must not 500 — it degrades to the prior 404.
        (tmp_path / "assets").mkdir()
        client = _client(str(tmp_path))
        assert client.get("/wallet.html").status_code == 404
