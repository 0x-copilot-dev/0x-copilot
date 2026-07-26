"""The connector registry resolves one honest list from every source.

Phase 0 shipped these tests with the strict assertion `xfail`ed: three
advertised slugs (gcal, salesforce, slack) resolved to nothing installable,
so their Connect button could not succeed. Phase 2 removed the way to
express that — `installable` is derived from `server_id`, and only a profile
or a seed can set one — so the marker is gone and the assertion is live.

What the tests cover, in order of what they protect:

* the registry resolves the union of all three sources, deduplicated;
* precedence is by evidence (profile > seed > announced), not file order;
* `capabilities_declared` is honest about what nobody has inventoried;
* the shipped data holds every invariant.
"""

from __future__ import annotations

import pytest

from backend_app.connectors.conformance import (
    ConnectorCatalogConformance,
    ConnectorCatalogConformanceError,
)
from backend_app.connectors.registry import (
    AnnouncedConnector,
    ConnectorLifecycle,
    ConnectorRegistry,
    ConnectorSource,
)
from backend_app.mcp_catalog import CatalogEntry


def _seed(slug: str, **overrides: object) -> CatalogEntry:
    return CatalogEntry(
        slug=slug,
        display_name=overrides.pop("display_name", slug.title()),  # type: ignore[arg-type]
        url=f"https://{slug}.example/mcp",
        **overrides,  # type: ignore[arg-type]
    )


def _announced(slug: str, **overrides: object) -> AnnouncedConnector:
    payload: dict[str, object] = {
        "slug": slug,
        "display_name": slug.title(),
        "description": "",
    }
    payload.update(overrides)
    return AnnouncedConnector.model_validate(payload)


class TestShippedRegistry:
    def test_the_shipped_data_is_conformant(self) -> None:
        """The assertion Phase 0 could only xfail."""

        ConnectorCatalogConformance.assert_conformant()

    def test_no_advertised_connector_is_a_dead_end(self) -> None:
        assert ConnectorCatalogConformance.unresolvable_slugs() == ()

    def test_every_card_has_a_label(self) -> None:
        assert ConnectorCatalogConformance.blank_display_names() == ()

    def test_the_two_surfaces_now_see_one_list(self) -> None:
        """The whole point: composer and destination read the same registry.

        Before this, the destination advertised 9 slugs and the composer
        offered 13, overlapping by 3.
        """

        registry = ConnectorRegistry.load()
        slugs = {row.slug for row in registry}
        # Was destination-only and uninstallable...
        assert {"gcal", "salesforce", "slack"} <= slugs
        # ...and these were composer-only, invisible in Tools.
        assert {"linear", "sentry", "asana"} <= slugs

    def test_the_former_orphans_are_declared_not_installable(self) -> None:
        registry = ConnectorRegistry.load()
        for slug in ("gcal", "salesforce", "slack"):
            row = registry.get(slug)
            assert row is not None, slug
            assert row.installable is False, slug
            assert row.lifecycle is ConnectorLifecycle.COMING_SOON, slug
            assert row.server_id is None, slug

    def test_no_announcement_has_been_overtaken(self) -> None:
        registry = ConnectorRegistry.load()
        announced = ConnectorRegistry.load_announced()
        assert registry.superseded_announcements(announced) == ()


class TestPrecedence:
    def test_a_profile_beats_a_seed_for_the_same_slug(self) -> None:
        """Atlassian ships as both; the profile carries more evidence."""

        registry = ConnectorRegistry.load()
        row = registry.get("atlassian")
        assert row is not None
        assert row.source is ConnectorSource.PROFILE
        assert row.capabilities_declared is True

    def test_a_seed_beats_an_announcement(self) -> None:
        registry = ConnectorRegistry.resolve(
            seeds=[_seed("linear")], announced=[_announced("linear")]
        )
        row = registry.get("linear")
        assert row is not None
        assert row.source is ConnectorSource.MCP_SEED
        assert row.installable is True

    def test_each_slug_resolves_exactly_once(self) -> None:
        registry = ConnectorRegistry.load()
        slugs = [row.slug for row in registry]
        assert len(slugs) == len(set(slugs))


class TestCapabilityHonesty:
    def test_a_seed_does_not_claim_a_tool_inventory(self) -> None:
        """A seed is a URL and an auth mode — nobody inventoried its tools.

        False must read as "unknown", never as "has none": a caller deciding
        per-tool risk has to withhold rather than assume.
        """

        registry = ConnectorRegistry.load()
        row = registry.get("linear")
        assert row is not None
        assert row.source is ConnectorSource.MCP_SEED
        assert row.capabilities_declared is False

    def test_a_profile_with_declared_tools_says_so(self) -> None:
        registry = ConnectorRegistry.load()
        row = registry.get("gmail")
        assert row is not None
        assert row.capabilities_declared is True


class TestInvariantsFailLoudly:
    def test_a_row_that_neither_installs_nor_declares_itself_raises(self) -> None:
        registry = ConnectorRegistry(
            (
                ConnectorRegistry._from_seed(_seed("ok")).model_copy(
                    update={"server_id": None, "source": ConnectorSource.MCP_SEED}
                ),
            )
        )
        with pytest.raises(ConnectorCatalogConformanceError) as caught:
            ConnectorCatalogConformance.assert_conformant(registry, announced=[])
        assert "ok" in str(caught.value)
        # An error that only states the problem sends the reader hunting.
        assert "coming_soon" in str(caught.value)

    def test_a_blank_display_name_raises(self) -> None:
        registry = ConnectorRegistry(
            (
                ConnectorRegistry._from_seed(_seed("ghost")).model_copy(
                    update={"display_name": "  "}
                ),
            )
        )
        with pytest.raises(ConnectorCatalogConformanceError) as caught:
            ConnectorCatalogConformance.assert_conformant(registry, announced=[])
        assert "blank card" in str(caught.value)

    def test_a_superseded_announcement_raises(self) -> None:
        registry = ConnectorRegistry.resolve(seeds=[_seed("linear")])
        with pytest.raises(ConnectorCatalogConformanceError) as caught:
            ConnectorCatalogConformance.assert_conformant(
                registry, announced=[_announced("linear")]
            )
        assert "dead copy" in str(caught.value)


class TestAnnouncedRows:
    def test_an_announcement_cannot_declare_an_endpoint(self) -> None:
        """The file has no field to make a promise installable with."""

        with pytest.raises(Exception):
            AnnouncedConnector.model_validate(
                {
                    "slug": "x",
                    "display_name": "X",
                    "url": "https://x.example/mcp",
                }
            )

    def test_a_missing_announced_file_is_not_an_error(self) -> None:
        from pathlib import Path

        assert ConnectorRegistry.load_announced(Path("/nonexistent.yaml")) == ()
