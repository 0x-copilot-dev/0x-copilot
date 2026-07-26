"""Every advertised connector must resolve to something installable.

The profile catalog already guards one direction — no profile may reference a
marketing slug the catalog does not advertise. This guards the inverse, which
is the direction a user experiences: a card on the "Available" tab whose
Connect button cannot succeed, because `install_from_catalog` raises
`Unknown catalog entry` for a slug with no MCP seed and no desktop profile.

Two tests, doing different jobs:

* the strict one is the goal, and is `xfail` until the orphans are resolved —
  it flips to a hard failure the moment someone fixes them, which is the
  signal to delete the marker;
* the pinning one is the guard that works *today*: it fails if a fourth
  orphan is ever added, so the defect cannot grow while the fix is pending.
"""

from __future__ import annotations

import pytest

from backend_app.connectors.conformance import (
    ConnectorCatalogConformance,
    ConnectorCatalogConformanceError,
)
from backend_app.connectors.service import ConnectorCatalogEntry

# Advertised in `connectors/catalog.yaml` with no endpoint behind them. Each
# renders a Connect button that resolves to nothing.
KNOWN_ORPHANS = ("gcal", "salesforce", "slack")


def _entry(slug: str) -> ConnectorCatalogEntry:
    return ConnectorCatalogEntry(
        slug=slug,
        display_name=slug.title(),
        description="",
        icon_hint=slug,
    )


class TestShippedCatalog:
    @pytest.mark.xfail(
        strict=True,
        reason=(
            "catalog.yaml advertises gcal, salesforce and slack with no MCP seed "
            "and no desktop profile. Resolving them (a verified endpoint, or "
            "retiring the card) is the fix; delete this marker when it lands."
        ),
    )
    def test_every_advertised_slug_is_installable(self) -> None:
        ConnectorCatalogConformance.assert_installable()

    def test_the_orphan_set_has_not_grown(self) -> None:
        """The defect is known and bounded — a fourth orphan is a regression."""

        assert ConnectorCatalogConformance.unresolvable_slugs() == KNOWN_ORPHANS

    def test_installable_slugs_span_both_sources(self) -> None:
        """Profiles and MCP seeds both count; neither alone is the catalog."""

        installable = ConnectorCatalogConformance.installable_slugs()
        # `gmail` exists only as a desktop profile...
        assert "gmail" in installable
        # ...and `linear` only as an MCP catalog seed.
        assert "linear" in installable


class TestConformanceRule:
    def test_a_slug_with_a_profile_resolves(self) -> None:
        orphans = ConnectorCatalogConformance.unresolvable_slugs(
            marketing=[_entry("acme")], profile_slugs=["acme"]
        )
        assert orphans == ()

    def test_a_slug_with_only_an_mcp_seed_resolves(self) -> None:
        # `linear` is in DEFAULT_CATALOG and has no desktop profile.
        orphans = ConnectorCatalogConformance.unresolvable_slugs(
            marketing=[_entry("linear")], profile_slugs=[]
        )
        assert orphans == ()

    def test_a_slug_with_neither_is_an_orphan(self) -> None:
        orphans = ConnectorCatalogConformance.unresolvable_slugs(
            marketing=[_entry("nowhere")], profile_slugs=[]
        )
        assert orphans == ("nowhere",)

    def test_orphans_are_reported_sorted(self) -> None:
        """Stable output so the failure message doesn't churn between runs."""

        orphans = ConnectorCatalogConformance.unresolvable_slugs(
            marketing=[_entry("zeta"), _entry("alpha")], profile_slugs=[]
        )
        assert orphans == ("alpha", "zeta")

    def test_the_error_names_every_orphan_and_says_what_to_do(self) -> None:
        with pytest.raises(ConnectorCatalogConformanceError) as caught:
            ConnectorCatalogConformance.assert_installable(
                marketing=[_entry("ghost"), _entry("phantom")], profile_slugs=[]
            )
        message = str(caught.value)
        assert "ghost" in message
        assert "phantom" in message
        # An error that only states the problem makes the reader go hunting.
        assert "verified endpoint" in message

    def test_an_empty_catalog_is_trivially_conformant(self) -> None:
        ConnectorCatalogConformance.assert_installable(marketing=[], profile_slugs=[])
