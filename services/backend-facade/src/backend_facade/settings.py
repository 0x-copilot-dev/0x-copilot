"""Environment-backed settings for the backend facade."""

from __future__ import annotations

import os

from pydantic import BaseModel, ConfigDict


class FacadeSettings(BaseModel):
    model_config = ConfigDict(frozen=True)

    backend_url: str = "http://127.0.0.1:8100"
    ai_backend_url: str = "http://127.0.0.1:8000"
    otel_collector_url: str = ""

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
        )
