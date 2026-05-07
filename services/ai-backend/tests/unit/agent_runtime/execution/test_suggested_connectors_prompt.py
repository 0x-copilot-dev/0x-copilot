"""PR 4.4.7 Phase 2 (Slice B) — system-prompt rendering for catalog
suggestions.

The factory composes the system prompt by chaining
``_instructions_with_*`` helpers. The suggestions section must:

  * Render an empty input as a no-op (no token tax for runs with
    nothing to suggest).
  * Render a non-empty input with one row per suggestion.
  * Use the orchestrator-supplied scope summary when present, falling
    back to the description.
  * Tell the agent to call ``suggest_mcp_connector`` and not to
    pretend the tools are already available.

These tests pin the helper directly so prompt drift from a future
catalog reorder is caught without booting the full graph.
"""

from __future__ import annotations

from agent_runtime.execution.contracts import CatalogSuggestionCard
from agent_runtime.execution.factory import (
    _instructions_with_suggested_connectors,
)


_BASE = "You are a helpful agent."


class TestSuggestedConnectorsPromptSection:
    def test_empty_suggestions_returns_instructions_unchanged(self) -> None:
        rendered = _instructions_with_suggested_connectors(
            instructions=_BASE,
            suggestions=(),
        )
        assert rendered == _BASE
        # No additional copy bleeds into the prompt — keeps the token
        # cost flat for runs that have nothing to surface.
        assert "Suggestable" not in rendered

    def test_non_empty_suggestions_render_one_line_per_card(self) -> None:
        rendered = _instructions_with_suggested_connectors(
            instructions=_BASE,
            suggestions=(
                CatalogSuggestionCard(
                    slug="linear",
                    display_name="Linear",
                    description="Issues, projects, and cycles.",
                    scopes_summary="Read issues, projects, and cycles.",
                ),
                CatalogSuggestionCard(
                    slug="notion",
                    display_name="Notion",
                    description="Workspace pages and databases.",
                ),
            ),
        )
        # Section header + per-card lines.
        assert "Suggestable integrations" in rendered
        assert "linear (Linear)" in rendered
        assert "notion (Notion)" in rendered
        # Scope summary wins when present; description is the fallback.
        assert "Read issues, projects, and cycles." in rendered
        assert "Workspace pages and databases." in rendered
        # PR 4.4.7 — the prompt now carries directive language so the
        # agent stops asking clarifying questions and stops reaching
        # for ``auth_mcp`` against uninstalled connectors. These
        # phrases are load-bearing — losing them sends the agent back
        # to "I see two options, which would you like?" prose.
        lower = rendered.lower()
        assert "suggest_mcp_connector" in rendered
        assert "do not" in lower
        assert "auth_mcp" in rendered
        assert "pretend" in lower

    def test_suggestion_without_summary_or_description_still_renders(
        self,
    ) -> None:
        rendered = _instructions_with_suggested_connectors(
            instructions=_BASE,
            suggestions=(
                CatalogSuggestionCard(
                    slug="example",
                    display_name="Example",
                ),
            ),
        )
        assert "example (Example)" in rendered

    def test_scope_summary_takes_precedence_over_description(self) -> None:
        rendered = _instructions_with_suggested_connectors(
            instructions=_BASE,
            suggestions=(
                CatalogSuggestionCard(
                    slug="linear",
                    display_name="Linear",
                    description="Long verbose description.",
                    scopes_summary="Read issues.",
                ),
            ),
        )
        assert "Read issues." in rendered
        # Description is dropped when scope summary is available so the
        # agent doesn't see two near-identical strings per row.
        assert "Long verbose description." not in rendered

    def test_section_appears_after_base_instructions(self) -> None:
        rendered = _instructions_with_suggested_connectors(
            instructions=_BASE,
            suggestions=(CatalogSuggestionCard(slug="linear", display_name="Linear"),),
        )
        # Base instructions land first so the agent's primary role is
        # locked in before the suggestable section.
        assert rendered.startswith(_BASE)
