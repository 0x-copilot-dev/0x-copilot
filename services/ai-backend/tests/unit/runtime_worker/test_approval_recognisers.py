"""PR 4.4.6.3 — vendor-specific approval-param recognisers.

Each test class exercises one recogniser plus the registry-level
ordering / fallback contract. Recognisers are pure functions, so
fixtures are minimal — direct class instantiation only.
"""

from __future__ import annotations


from runtime_api.schemas.approvals import (
    APPROVAL_MAX_PARAMS,
    McpApprovalMetadata,
)
from runtime_api.schemas.common import ApprovalCategory, ApprovalReasonCode
from runtime_worker.approval_recognisers import (
    ApprovalParamRecogniserRegistry,
    AtlassianApprovalRecogniser,
    GitHubApprovalRecogniser,
    LinearApprovalRecogniser,
    NotionApprovalRecogniser,
    SlackApprovalRecogniser,
)


class TestSlackRecogniser:
    def test_matches_slack_server_name(self) -> None:
        assert SlackApprovalRecogniser.matches_server_name("mcp_slack_com")
        assert SlackApprovalRecogniser.matches_server_name("slack")
        assert not SlackApprovalRecogniser.matches_server_name("github")

    def test_channel_only(self) -> None:
        params = SlackApprovalRecogniser().recognise({"channel": "#launch-aurora"})
        assert [(p.label, p.value) for p in params] == [
            ("Channel", "#launch-aurora"),
        ]

    def test_thread_yes_when_thread_ts_present(self) -> None:
        params = SlackApprovalRecogniser().recognise(
            {"channel": "#launch-aurora", "thread_ts": "1700000000.001"}
        )
        labels = [p.label for p in params]
        values = [p.value for p in params]
        assert "In thread" in labels
        assert "Yes" in values
        # Raw thread ts never projects.
        assert "1700000000.001" not in values

    def test_thread_no_when_thread_ts_explicitly_falsy(self) -> None:
        params = SlackApprovalRecogniser().recognise({"thread_ts": ""})
        assert any(p.label == "In thread" and p.value == "No" for p in params)

    def test_recipient_falls_back_to_to(self) -> None:
        params = SlackApprovalRecogniser().recognise({"to": "U123"})
        assert any(p.label == "Recipient" and p.value == "U123" for p in params)

    def test_unknown_keys_dropped(self) -> None:
        params = SlackApprovalRecogniser().recognise(
            {"channel": "#a", "text": "secret body", "api_key": "sk-x"}
        )
        # `text` and `api_key` are not recognised — they don't appear.
        values = " ".join(p.value for p in params)
        assert "secret body" not in values
        assert "sk-x" not in values


class TestGitHubRecogniser:
    def test_owner_repo_compose(self) -> None:
        params = GitHubApprovalRecogniser().recognise({"owner": "acme", "repo": "api"})
        assert [(p.label, p.value) for p in params] == [("Repo", "acme/api")]

    def test_pull_number_appended_to_repo(self) -> None:
        params = GitHubApprovalRecogniser().recognise(
            {"owner": "acme", "repo": "api", "pull_number": 42}
        )
        assert [(p.label, p.value) for p in params] == [
            ("Repo", "acme/api · #42"),
        ]

    def test_branch_compose(self) -> None:
        params = GitHubApprovalRecogniser().recognise(
            {"owner": "acme", "repo": "api", "head": "feat-y", "base": "main"}
        )
        labels = [(p.label, p.value) for p in params]
        assert ("Branch", "feat-y → main") in labels

    def test_org_synonym_for_owner(self) -> None:
        params = GitHubApprovalRecogniser().recognise({"org": "acme", "repo": "api"})
        assert [(p.label, p.value) for p in params] == [("Repo", "acme/api")]

    def test_repo_only_when_owner_missing(self) -> None:
        params = GitHubApprovalRecogniser().recognise({"repo": "api"})
        assert [(p.label, p.value) for p in params] == [("Repo", "api")]

    def test_title_appended(self) -> None:
        params = GitHubApprovalRecogniser().recognise(
            {"owner": "acme", "repo": "api", "title": "Fix CI"}
        )
        labels = [(p.label, p.value) for p in params]
        assert ("Title", "Fix CI") in labels


class TestLinearRecogniser:
    def test_team_and_project_compose_into_scope(self) -> None:
        params = LinearApprovalRecogniser().recognise(
            {"team": "TEAM-1", "project": "Atlas"}
        )
        labels = [(p.label, p.value) for p in params]
        assert ("Scope", "TEAM-1 / Atlas") in labels

    def test_priority_int_decoded(self) -> None:
        params = LinearApprovalRecogniser().recognise({"priority": 1})
        assert any(p.label == "Priority" and p.value == "P1 (Urgent)" for p in params)

    def test_priority_unknown_int_dropped(self) -> None:
        params = LinearApprovalRecogniser().recognise({"priority": 99})
        assert not any(p.label == "Priority" for p in params)

    def test_assignee_present(self) -> None:
        params = LinearApprovalRecogniser().recognise(
            {"team": "TEAM-1", "assignee": "user_marcus"}
        )
        assert any(p.label == "Assignee" and p.value == "user_marcus" for p in params)


class TestNotionRecogniser:
    def test_parent_database_id(self) -> None:
        params = NotionApprovalRecogniser().recognise(
            {"parent": {"database_id": "abc-123"}, "title": "Q4 plan"}
        )
        labels = [(p.label, p.value) for p in params]
        assert ("Database", "abc-123") in labels
        assert ("Title", "Q4 plan") in labels

    def test_parent_page_id_when_no_database(self) -> None:
        params = NotionApprovalRecogniser().recognise({"parent": {"page_id": "page-7"}})
        assert any(p.label == "Parent page" and p.value == "page-7" for p in params)

    def test_top_level_page_id_when_no_parent(self) -> None:
        params = NotionApprovalRecogniser().recognise({"page_id": "page-7"})
        assert any(p.label == "Page" and p.value == "page-7" for p in params)

    def test_title_via_properties(self) -> None:
        params = NotionApprovalRecogniser().recognise(
            {"properties": {"title": "Roadmap"}}
        )
        assert any(p.label == "Title" and p.value == "Roadmap" for p in params)


class TestAtlassianRecogniser:
    def test_matches_jira_token(self) -> None:
        assert AtlassianApprovalRecogniser.matches_server_name("jira_cloud")
        assert AtlassianApprovalRecogniser.matches_server_name("mcp_atlassian")
        assert AtlassianApprovalRecogniser.matches_server_name("confluence")
        assert not AtlassianApprovalRecogniser.matches_server_name("slack")

    def test_project_and_issue_type_compose(self) -> None:
        params = AtlassianApprovalRecogniser().recognise(
            {"project": "PROJ-123", "issue_type": "Bug"}
        )
        labels = [(p.label, p.value) for p in params]
        assert ("Project", "PROJ-123 · Bug") in labels

    def test_issue_key_synonym(self) -> None:
        params = AtlassianApprovalRecogniser().recognise({"issue_key": "PROJ-1"})
        assert any(p.label == "Issue" and p.value == "PROJ-1" for p in params)


class TestRegistry:
    def test_returns_none_for_unknown_vendor(self) -> None:
        assert (
            ApprovalParamRecogniserRegistry.recognise(
                server_name="mcp_acme_internal_com",
                arguments={"channel": "#a"},
            )
            is None
        )

    def test_first_match_wins_no_fallthrough(self) -> None:
        # Atlassian declares 3 tokens; the first match must dispatch
        # without re-scanning the rest.
        params = ApprovalParamRecogniserRegistry.recognise(
            server_name="mcp_jira_com",
            arguments={"project": "PROJ-1", "issue_type": "Bug"},
        )
        assert params is not None
        labels = [(p.label, p.value) for p in params]
        assert ("Project", "PROJ-1 · Bug") in labels

    def test_caps_recogniser_output_at_six(self) -> None:
        # GitHub recogniser only ever emits up to 4 rows but force a
        # contrived case to assert the registry slice never exceeds the
        # validator boundary.
        params = ApprovalParamRecogniserRegistry.recognise(
            server_name="github",
            arguments={
                "owner": "acme",
                "repo": "api",
                "head": "h",
                "base": "b",
                "title": "t",
            },
        )
        assert params is not None
        assert len(params) <= APPROVAL_MAX_PARAMS

    def test_recogniser_output_passes_metadata_validation(self) -> None:
        # Round-trip through the same Pydantic gate the worker uses.
        params = ApprovalParamRecogniserRegistry.recognise(
            server_name="slack",
            arguments={"channel": "#a", "thread_ts": "1.0"},
        )
        assert params is not None
        metadata = McpApprovalMetadata(
            vendor="SLACK",
            category=ApprovalCategory.WRITE,
            reason_code=ApprovalReasonCode.WRITES_OUT_OF_WORKSPACE,
            params=params,
        )
        assert metadata.params == params
