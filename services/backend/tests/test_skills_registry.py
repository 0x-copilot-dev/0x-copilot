from __future__ import annotations

from backend_app.contracts import CreateSkillRequest, UpdateSkillRequest
from backend_app.service import SkillRegistryService
from backend_app.store import InMemorySkillStore


SKILL_MARKDOWN = """---
name: launch-checklist
description: Review launch plans and summarize top risks.
allowed_tools: [doc_search]
---
# Launch Checklist
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

    assert created.name == "launch_checklist"
    assert created.allowed_tools == ("doc_search",)
    assert created.virtual_path.endswith("/launch_checklist/SKILL.md")
    assert updated.enabled is False
    assert all(card.name != "launch_checklist" for card in cards.skills)
    assert [
        event.action
        for event in store.audit_events
        if event.skill_id == created.skill_id
    ] == [
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


def test_skill_registry_seeds_preloaded_skills_as_read_only() -> None:
    service = SkillRegistryService(store=InMemorySkillStore())

    listed = service.list_skills(org_id="org_123", user_id="user_123")
    preloaded = [skill for skill in listed.skills if skill.source_type == "preloaded"]
    status_report = next(
        skill for skill in preloaded if skill.name == "generate_status_report"
    )
    cards = service.list_internal_cards(org_id="org_123", user_id="user_123")

    assert len(preloaded) >= 5
    assert status_report.enabled is True
    assert (
        status_report.virtual_path
        == "/skills/preloaded/generate_status_report/SKILL.md"
    )
    assert any(card.name == "generate_status_report" for card in cards.skills)

    disabled = service.update_skill(
        org_id="org_123",
        user_id="user_123",
        skill_id=status_report.skill_id,
        request=UpdateSkillRequest(enabled=False),
    )

    assert disabled.enabled is False

    try:
        service.update_skill(
            org_id="org_123",
            user_id="user_123",
            skill_id=status_report.skill_id,
            request=UpdateSkillRequest(display_name="Edited"),
        )
    except ValueError as exc:
        assert "Preloaded skills" in str(exc)
    else:
        raise AssertionError("Preloaded Skills should be read-only")


def test_skill_source_type_includes_system_value() -> None:
    """`source_type=system` is a valid wire value the backend can pass-through.

    Backend never persists or seeds system skills (those live on the runtime's
    filesystem), but `SkillResponse` still must accept the value so the facade
    can aggregate ai-backend's payload through this contract surface without
    failing Pydantic validation.
    """

    from datetime import datetime, timezone

    from backend_app.contracts import SkillResponse, SkillScope, SkillSourceType

    assert SkillSourceType.SYSTEM == "system"
    response = SkillResponse(
        skill_id="system:search-subagent-logs",
        name="search-subagent-logs",
        display_name="Search Subagent Logs",
        description="A test description.",
        markdown="---\nname: search-subagent-logs\ndescription: A test description.\n---\n",
        virtual_path="/skills/system/search-subagent-logs/SKILL.md",
        enabled=True,
        scope=SkillScope.USER,
        source_type=SkillSourceType.SYSTEM,
        version=1,
        allowed_tools=("ls", "read_file"),
        compatibility=(),
        metadata={},
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    assert response.source_type is SkillSourceType.SYSTEM
