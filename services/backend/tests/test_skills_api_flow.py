from __future__ import annotations

from fastapi.testclient import TestClient

from enterprise_service_contracts.headers import ORG_HEADER, SERVICE_TOKEN_HEADER, USER_HEADER
from backend_app.app import create_app
from backend_app.service import SkillRegistryService
from backend_app.store import InMemorySkillStore


SKILL_MARKDOWN = """---
name: incident-review
description: Summarize incidents with timeline and owners.
---
# Incident Review
Use for incident postmortems.
"""


def test_public_and_internal_skill_flow() -> None:
    app = create_app(skill_service=SkillRegistryService(store=InMemorySkillStore()))
    client = TestClient(app)

    created = client.post(
        "/v1/skills",
        json={
            "org_id": "org_123",
            "user_id": "user_123",
            "markdown": SKILL_MARKDOWN,
        },
    ).json()
    skill_id = created["skill_id"]
    listed = client.get(
        "/v1/skills",
        params={"org_id": "org_123", "user_id": "user_123"},
    ).json()
    cards = client.get(
        "/internal/v1/skills/cards",
        params={"org_id": "org_123", "user_id": "user_123"},
    ).json()
    bundle = client.get(
        "/internal/v1/skills/by-name/incident_review",
        params={"org_id": "org_123", "user_id": "user_123"},
    ).json()
    updated = client.put(
        f"/v1/skills/{skill_id}",
        params={"org_id": "org_123", "user_id": "user_123"},
        json={"enabled": False},
    ).json()
    disabled_cards = client.get(
        "/internal/v1/skills/cards",
        params={"org_id": "org_123", "user_id": "user_123"},
    ).json()

    assert listed["skills"][0]["skill_id"] == skill_id
    assert cards["skills"][0]["name"] == "incident_review"
    assert bundle["markdown"] == SKILL_MARKDOWN
    assert updated["enabled"] is False
    assert disabled_cards["skills"] == []


def test_internal_skill_routes_use_service_header_scope_when_token_is_configured(monkeypatch) -> None:
    monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", "service-token")
    app = create_app(skill_service=SkillRegistryService(store=InMemorySkillStore()))
    client = TestClient(app)
    headers = {
        SERVICE_TOKEN_HEADER: "service-token",
        ORG_HEADER: "org_123",
        USER_HEADER: "user_123",
    }

    created = client.post(
        "/v1/skills",
        headers=headers,
        json={
            "org_id": "forged_org",
            "user_id": "forged_user",
            "markdown": SKILL_MARKDOWN,
        },
    ).json()
    cards = client.get(
        "/internal/v1/skills/cards",
        headers=headers,
        params={"org_id": "forged_org", "user_id": "forged_user"},
    ).json()

    assert created["skill_id"] == cards["skills"][0]["skill_id"]
