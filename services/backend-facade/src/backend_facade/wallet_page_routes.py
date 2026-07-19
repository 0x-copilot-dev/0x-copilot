"""Serve the standalone SIWE wallet sign-in page in the packaged desktop stack.

The desktop opens ``{facade}/wallet.html`` in the system browser
(``apps/desktop/main/auth/wallet-login.ts``). That page fetches
``/v1/auth/siwe/{nonce,verify}`` as SAME-ORIGIN relative paths and derives the
SIWE message domain from ``window.location`` — so it MUST be served by the
facade (the origin that also answers the SIWE API), not the backend.

Single source of truth: the page + its assets are the *built* ``apps/frontend``
artifact (``wallet.html`` + ``assets/``). This module never re-implements the
page or the SIWE template; it hosts the already-built, already-public files from
a directory the desktop supervisor points at via ``FACADE_WEB_DIST_DIR``.

Env-gated: registered ONLY when ``settings.web_dist_dir`` is set (the supervised
desktop path). On the web/self-host stack the field is empty and nginx keeps
serving the page — the facade adds no route, so that path is byte-unchanged.
"""

from __future__ import annotations

import logging
import os

from fastapi import FastAPI
from starlette.responses import FileResponse
from starlette.staticfiles import StaticFiles

from backend_facade.settings import FacadeSettings

logger = logging.getLogger("backend-facade")

_WALLET_PAGE = "wallet.html"
_ASSETS_DIR = "assets"


def register_wallet_page_routes(app: FastAPI) -> None:
    """Serve /wallet.html + /assets from the built frontend dist, if configured.

    No-op unless ``FacadeSettings.web_dist_dir`` points at a real directory that
    contains ``wallet.html`` — so a misconfigured/absent dir fails closed to the
    prior behavior (no route) rather than 500ing at request time.
    """

    settings = app.state.settings
    if not isinstance(settings, FacadeSettings):  # defensive; create_app sets it
        return
    dist_dir = settings.web_dist_dir
    if not dist_dir:
        return

    page_path = os.path.join(dist_dir, _WALLET_PAGE)
    if not os.path.isfile(page_path):
        logger.warning(
            "FACADE_WEB_DIST_DIR set (%s) but %s is missing — wallet page not served",
            dist_dir,
            _WALLET_PAGE,
        )
        return

    @app.get("/wallet.html", include_in_schema=False)
    async def wallet_page() -> FileResponse:
        # Public, unauthenticated: the page itself carries no secrets and drives
        # the nonce -> personal_sign -> verify handshake against the SIWE API.
        return FileResponse(page_path, media_type="text/html")

    assets_dir = os.path.join(dist_dir, _ASSETS_DIR)
    if os.path.isdir(assets_dir):
        # StaticFiles handles content-types + path-traversal safety for the
        # hashed JS/CSS chunks wallet.html references at /assets/*.
        app.mount("/assets", StaticFiles(directory=assets_dir), name="wallet-assets")

    logger.info("serving SIWE wallet page from %s", dist_dir)
