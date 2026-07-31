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
        # These were composer-only, invisible in Tools.
        assert {"linear", "sentry", "asana"} <= slugs
        # These were destination-only and uninstallable. Unifying the two lists
        # made that visible; the answer was to remove them, not to render them
        # in both places.
        assert {"gcal", "salesforce", "slack"}.isdisjoint(slugs)

    def test_an_announced_row_is_still_declared_not_installable(self) -> None:
        """The announced lane is empty but not gone.

        The three orphans it held are removed; this pins that a row added back
        still resolves as uninstallable rather than quietly gaining a Connect
        button, which is the only reason to keep the file at all.
        """

        registry = ConnectorRegistry.resolve(
            announced=[
                AnnouncedConnector(
                    slug="hypothetical",
                    display_name="Hypothetical",
                    note="Not yet available.",
                )
            ]
        )
        row = registry.get("hypothetical")
        assert row is not None
        assert row.installable is False
        assert row.lifecycle is ConnectorLifecycle.COMING_SOON
        assert row.server_id is None

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


class TestCatalogEntryAvailability:
    """`as_catalog_entries` must carry the state, not just the name.

    The projection's own docstring always promised the lifecycle rode along
    "so the client can render the state instead of offering a Connect button
    that cannot succeed" — but it built entries with four fields, none of them
    the lifecycle, and the route model had nowhere to put it. Every row
    therefore reached the browser looking equally connectable, including the
    announced slugs that resolve to no server at all.
    """

    def test_announced_row_is_coming_soon_and_carries_its_note(self) -> None:
        registry = ConnectorRegistry.resolve(
            announced=[
                AnnouncedConnector(
                    slug="slack",
                    display_name="Slack",
                    description="Channels, DMs, and threads.",
                    note="Not yet available.",
                )
            ],
        )
        (entry,) = registry.as_catalog_entries()
        assert entry.availability == "coming_soon"
        assert entry.availability_reason == "Not yet available."

    def test_seed_row_is_available(self) -> None:
        registry = ConnectorRegistry.resolve(seeds=[_seed("linear")])
        (entry,) = registry.as_catalog_entries()
        assert entry.availability == "available"
        assert entry.availability_reason is None

    def test_preview_profile_is_not_available_until_preview_is_enabled(
        self,
    ) -> None:
        """The exact gate `DesktopMcpOAuthCoordinator._assert_available`
        enforces — the catalog must agree with it, or the button lies."""

        off = ConnectorRegistry.load(preview_enabled=False).as_map()
        on = ConnectorRegistry.load(preview_enabled=True).as_map()
        assert off["gmail"].lifecycle is ConnectorLifecycle.PREVIEW
        assert on["gmail"].lifecycle is ConnectorLifecycle.AVAILABLE

    def test_no_shipped_connector_needs_an_admin(self) -> None:
        """Every row a user can see is one they can finish themselves.

        This replaces an assertion about Outlook specifically. Outlook was the
        only shipped profile that needed a tenant admin (Microsoft Work IQ:
        M365 Copilot licence + an Entra app registration), which is exactly why
        it was removed — a personal account could never complete it. The rule
        that no such row ships is the durable version of that assertion; the
        gate itself is pinned on synthetic data in test_desktop_oauth.py.
        """

        on = ConnectorRegistry.load(preview_enabled=True).as_map()
        assert on
        assert all(
            row.lifecycle is not ConnectorLifecycle.ADMIN_SETUP_REQUIRED
            for row in on.values()
        )

    def test_shipped_catalog_offers_no_coming_soon_row(self) -> None:
        """Nothing ships as `coming_soon` any more — the three slugs that did
        were removed rather than shown as rows the user cannot act on."""

        entries = ConnectorRegistry.load(preview_enabled=False).as_catalog_entries()
        assert entries
        assert all(e.availability != "coming_soon" for e in entries)
