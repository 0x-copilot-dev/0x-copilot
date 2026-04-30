from __future__ import annotations

from fastapi.testclient import TestClient

import backend_facade.app as facade_app
from backend_facade.app import create_app
from backend_facade.settings import FacadeSettings


def test_facade_settings_normalize_service_urls() -> None:
    settings = FacadeSettings(
        backend_url="http://backend.local/",
        ai_backend_url="http://ai.local/",
    )

    assert settings.backend_url == "http://backend.local/"
    assert settings.ai_backend_url == "http://ai.local/"


def test_facade_forwards_skill_list(monkeypatch) -> None:
    async def fake_forward_json(*args, **kwargs):
        assert args[1] == "GET"
        assert args[2] == "/v1/skills"
        return {"skills": []}

    monkeypatch.setattr(facade_app, "forward_json", fake_forward_json)
    client = TestClient(create_app(FacadeSettings()))

    response = client.get("/v1/skills", params={"org_id": "org_123", "user_id": "user_123"})

    assert response.status_code == 200
    assert response.json() == {"skills": []}
