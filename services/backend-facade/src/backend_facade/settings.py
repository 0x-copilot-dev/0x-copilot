"""Environment-backed settings for the backend facade."""

from __future__ import annotations

import os

from pydantic import BaseModel, ConfigDict


class FacadeSettings(BaseModel):
    model_config = ConfigDict(frozen=True)

    backend_url: str = "http://127.0.0.1:8100"
    ai_backend_url: str = "http://127.0.0.1:8000"
    otel_collector_url: str = ""
    # Filesystem dir holding the built frontend `wallet.html` + `assets/`. Set
    # ONLY by the desktop supervisor (FACADE_WEB_DIST_DIR); empty everywhere
    # else, where nginx/Vite serves the wallet page. When set, the facade serves
    # the SIWE wallet page same-origin with the /v1/auth/siwe/* API.
    web_dist_dir: str = ""

    @classmethod
    def load(cls) -> "FacadeSettings":
        return cls(
            backend_url=os.environ.get("BACKEND_URL", "http://127.0.0.1:8100").rstrip(
                "/"
            ),
            ai_backend_url=os.environ.get(
                "AI_BACKEND_URL", "http://127.0.0.1:8000"
            ).rstrip("/"),
            otel_collector_url=os.environ.get("OTEL_COLLECTOR_HTTP_URL", "").rstrip(
                "/"
            ),
            web_dist_dir=os.environ.get("FACADE_WEB_DIST_DIR", "").strip(),
        )
