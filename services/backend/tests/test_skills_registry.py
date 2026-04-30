from __future__ import annotations

from backend_app.contracts import CreateSkillRequest, UpdateSkillRequest
from backend_app.service import SkillRegistryService
from backend_app.store import InMemorySkillStore


SKILL_MARKDOWN = """---
name: launch-risk-review
description: Review launch plans and summarize top risks.
allowed_tools: [doc_search]
---
# Launch Risk Review
Use when the user asks about launch readiness.
"""


def test_skill_registry_create_update_internal_cards_and_audit() -> None:
    store = InMemorySkillStore()
    service = SkillRegistryService(store=store)

    created = service.create_skill(
        CreateSkillRequest(
            org_id="org_123",
            user_id="user_123",
            markdown=SKILL_MARKDOWN,
        )
    )
    updated = service.update_skill(
        org_id="org_123",
        user_id="user_123",
        skill_id=created.skill_id,
        request=UpdateSkillRequest(enabled=False),
    )
    cards = service.list_internal_cards(org_id="org_123", user_id="user_123")

    assert created.name == "launch_risk_review"
    assert created.allowed_tools == ("doc_search",)
    assert created.virtual_path.endswith("/launch_risk_review/SKILL.md")
    assert updated.enabled is False
    assert cards.skills == ()
    assert [event.action for event in store.audit_events] == [
        "skill_created",
        "skill_updated",
    ]


def test_skill_registry_rejects_duplicate_and_malformed_skills() -> None:
    service = SkillRegistryService(store=InMemorySkillStore())
    request = CreateSkillRequest(
        org_id="org_123",
        user_id="user_123",
        markdown=SKILL_MARKDOWN,
    )
    service.create_skill(request)

    try:
        service.create_skill(request)
    except ValueError as exc:
        assert "already exists" in str(exc)
    else:
        raise AssertionError("Duplicate Skill names should be rejected")

    try:
        service.create_skill(
            CreateSkillRequest(
                org_id="org_123",
                user_id="user_123",
                markdown="# Missing frontmatter",
            )
        )
    except ValueError as exc:
        assert "frontmatter" in str(exc)
    else:
        raise AssertionError("Malformed Skill markdown should be rejected")


def test_skill_registry_enforces_scope_visibility() -> None:
    service = SkillRegistryService(store=InMemorySkillStore())
    created = service.create_skill(
        CreateSkillRequest(
            org_id="org_123",
            user_id="user_123",
            markdown=SKILL_MARKDOWN,
        )
    )

    try:
        service.get_skill(
            org_id="org_123", user_id="other_user", skill_id=created.skill_id
        )
    except ValueError as exc:
        assert "not found" in str(exc)
    else:
        raise AssertionError("User-scoped Skills should not be visible to other users")
