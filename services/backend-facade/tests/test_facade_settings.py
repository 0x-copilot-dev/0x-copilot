from __future__ import annotations

from backend_facade.settings import FacadeSettings


def test_facade_settings_normalize_service_urls() -> None:
    settings = FacadeSettings(
        backend_url="http://backend.local/",
        ai_backend_url="http://ai.local/",
    )

    assert settings.backend_url == "http://backend.local/"
    assert settings.ai_backend_url == "http://ai.local/"
