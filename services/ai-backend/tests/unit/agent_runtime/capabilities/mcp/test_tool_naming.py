"""``mcp__{server}__{tool}`` — the two register-crossing functions.

The properties asserted here are the ones the rest of the MCP stack is allowed
to rely on: :meth:`McpToolName.strip` is the exact inverse of
:meth:`McpToolName.compose`, ``compose`` is idempotent and injective, and every
name it produces is one all three providers will accept.
"""

from __future__ import annotations

import pytest

from agent_runtime.capabilities.mcp.tool_naming import (
    McpToolName,
    ParsedMcpToolName,
)


class NameFixtureMixin:
    """Pairs every property test runs over."""

    #: ``(server, tool)`` pairs spanning the shapes a real connector produces:
    #: plain, single-underscore, hyphenated slug, a separator INSIDE either
    #: component, illegal characters, and leading/trailing noise.
    PAIRS: tuple[tuple[str, str], ...] = (
        ("linear", "search"),
        ("linear", "list_issues"),
        ("github-mcp", "create_pull_request"),
        # ``__`` inside the tool name — the case a naive split gets wrong.
        ("linear", "list__issues"),
        # ``__`` inside the slug.
        ("my__server", "search"),
        # Characters no provider's tool-name validator accepts.
        ("linear.app", "search:issues"),
        ("linear", "  spaced name  "),
        # Already namespaced, but by a DIFFERENT server — an aggregating
        # connector re-advertising an upstream's tool.
        ("proxy", "mcp__github__search"),
    )

    #: A tool name long enough to force the truncation path.
    LONG_TOOL: str = "list_" + ("issue_" * 12) + "records"


class TestSanitize(NameFixtureMixin):
    """One component reduced to the provider-safe, separator-free charset."""

    @pytest.mark.parametrize(("server", "tool"), NameFixtureMixin.PAIRS)
    def test_no_sanitized_component_can_contain_the_delimiter(
        self, server: str, tool: str
    ) -> None:
        # This is what makes ``__`` an unambiguous boundary and therefore what
        # makes ``parse`` the exact inverse of ``compose``.
        assert McpToolName.DELIMITER not in McpToolName.sanitize(server)
        assert McpToolName.DELIMITER not in McpToolName.sanitize(tool)

    def test_a_component_that_sanitizes_away_becomes_a_named_placeholder(self) -> None:
        # Never the empty string: an empty component would let the delimiter
        # abut itself and make the name unparseable.
        assert McpToolName.sanitize("...") == "unknown"
        assert McpToolName.sanitize("") == "unknown"
        assert McpToolName.sanitize(None) == "unknown"

    def test_the_ends_are_trimmed_so_a_component_never_abuts_the_delimiter(
        self,
    ) -> None:
        assert McpToolName.sanitize("_search_") == "search"


class TestCompose(NameFixtureMixin):
    """The model-surface name."""

    def test_the_registered_name_carries_the_connector(self) -> None:
        assert (
            McpToolName.compose(server="linear", tool="list_issues")
            == "mcp__linear__list_issues"
        )

    @pytest.mark.parametrize(("server", "tool"), NameFixtureMixin.PAIRS)
    def test_every_composed_name_is_provider_legal(
        self, server: str, tool: str
    ) -> None:
        # OpenAI's ``^[a-zA-Z0-9_-]{1,64}$`` is the tightest of the three we
        # ship against; a name that fails it is rejected at the provider before
        # the model ever sees the tool.
        name = McpToolName.compose(server=server, tool=tool)
        assert len(name) <= McpToolName.MAX_LENGTH
        assert name
        assert all(char.isalnum() or char in "_-" for char in name)

    @pytest.mark.parametrize(("server", "tool"), NameFixtureMixin.PAIRS)
    def test_composing_twice_is_composing_once(self, server: str, tool: str) -> None:
        # The registration path must be re-runnable without double-prefixing.
        once = McpToolName.compose(server=server, tool=tool)
        assert McpToolName.compose(server=server, tool=once) == once

    def test_an_over_long_pair_is_fitted_and_still_idempotent(self) -> None:
        once = McpToolName.compose(server="linear", tool=self.LONG_TOOL)
        assert len(once) == McpToolName.MAX_LENGTH
        assert McpToolName.compose(server="linear", tool=once) == once

    def test_two_long_tools_sharing_a_prefix_stay_two_names(self) -> None:
        # The digest is taken over the WHOLE pair, so truncation cannot merge
        # two tools whose first 44 characters agree.
        first = McpToolName.compose(server="linear", tool=f"{self.LONG_TOOL}_alpha")
        second = McpToolName.compose(server="linear", tool=f"{self.LONG_TOOL}_beta")
        assert first != second
        assert len(first) == len(second) == McpToolName.MAX_LENGTH

    def test_a_prefix_naming_another_server_is_part_of_the_tool_name(self) -> None:
        # An aggregating connector (mcp-proxy, metamcp) re-advertises its
        # upstreams' tools already prefixed. Absorbing ANY prefix would collapse
        # these two onto one registered name and drop the second with the exact
        # DUPLICATE_DESCRIPTOR_NAME failure the namespace exists to remove.
        github = McpToolName.compose(server="proxy", tool="mcp__github__search")
        gitlab = McpToolName.compose(server="proxy", tool="mcp__gitlab__search")
        assert github != gitlab
        assert McpToolName.parse(github) == ParsedMcpToolName(
            server="proxy", tool="mcp_github_search"
        )

    def test_the_caller_owns_the_attribution_not_the_tool_name(self) -> None:
        # The tool name is untrusted input read off an MCP server: it may not
        # re-attribute itself to a connector it does not belong to.
        name = McpToolName.compose(server="proxy", tool="mcp__github__search")
        parsed = McpToolName.parse(name)
        assert parsed is not None
        assert parsed.server == "proxy"


class TestStripIsTheExactInverse(NameFixtureMixin):
    """``strip`` undoes ``compose`` — the property every display seam leans on."""

    @pytest.mark.parametrize(("server", "tool"), NameFixtureMixin.PAIRS)
    def test_strip_returns_the_sanitized_connector_register_name(
        self, server: str, tool: str
    ) -> None:
        name = McpToolName.compose(server=server, tool=tool)
        assert McpToolName.strip(name) == McpToolName.sanitize(tool)

    def test_strip_is_the_identity_on_a_native_tool_name(self) -> None:
        # So a seam serving both registers needs no branch.
        for native in ("write_todos", "read_file", "task", "call_mcp_tool"):
            assert McpToolName.strip(native) == native

    def test_strip_is_idempotent(self) -> None:
        name = McpToolName.compose(server="linear", tool="list_issues")
        assert McpToolName.strip(McpToolName.strip(name)) == "list_issues"

    def test_strip_tolerates_a_non_string(self) -> None:
        assert McpToolName.strip(None) == ""


class TestParse(NameFixtureMixin):
    """Splitting a name, and refusing to split one that is not ours."""

    def test_a_native_name_does_not_parse(self) -> None:
        # Which is why every caller can run this over any tool name without
        # first knowing the tool's origin.
        for native in ("write_todos", "mcp_thing", "mcp__", "mcp__only", ""):
            assert McpToolName.parse(native) is None

    def test_a_non_string_does_not_parse(self) -> None:
        assert McpToolName.parse(None) is None
        assert McpToolName.parse(17) is None

    @pytest.mark.parametrize(("server", "tool"), NameFixtureMixin.PAIRS)
    def test_parse_recovers_both_components(self, server: str, tool: str) -> None:
        parsed = McpToolName.parse(McpToolName.compose(server=server, tool=tool))
        assert parsed is not None
        assert parsed.server == McpToolName.sanitize(server)
        assert parsed.tool == McpToolName.sanitize(tool)


class TestInjectivity(NameFixtureMixin):
    """Two distinct sanitized pairs never compose to one registered name."""

    def test_every_distinct_sanitized_pair_composes_to_its_own_name(self) -> None:
        # Injectivity is over the SANITIZED pair — sanitization is lossy by
        # construction (the provider charset is smaller than MCP's), so the
        # honest claim is that nothing is lost after that point.
        by_pair = {
            (McpToolName.sanitize(server), McpToolName.sanitize(tool)): (
                McpToolName.compose(server=server, tool=tool)
            )
            for server, tool in self.PAIRS
        }
        assert len(set(by_pair.values())) == len(by_pair)

    def test_sanitization_is_where_the_only_loss_happens(self) -> None:
        # ``list__issues`` and ``list_issues`` are one name to every provider,
        # so they are one name here. Recorded rather than asserted away: this is
        # the single case where two distinct MCP tool names can still collide,
        # and it lands on the source's typed DUPLICATE_DESCRIPTOR_NAME failure
        # instead of silently registering one over the other.
        assert McpToolName.compose(server="linear", tool="list__issues") == (
            McpToolName.compose(server="linear", tool="list_issues")
        )

    def test_the_same_tool_on_two_connectors_is_two_names(self) -> None:
        # The whole point: ``search`` on Linear and ``search`` on GitHub are two
        # callable tools, not one registration and one dropped connector.
        assert McpToolName.compose(
            server="linear", tool="search"
        ) != McpToolName.compose(server="github", tool="search")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
