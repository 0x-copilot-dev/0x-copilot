from __future__ import annotations

from fastapi.testclient import TestClient

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
