"""Every advertised connector must be installable.

`DesktopProfileCatalog.reconcile` already checks one direction — a profile
may not reference a marketing slug the catalog does not advertise, so there
are no orphan *profiles*. Nothing checks the inverse, and the inverse is the
one a user sees: the "Available" tab renders every row of `catalog.yaml`
with no cross-check that anything can install it.

Today three slugs advertise a connector that resolves to nothing. Clicking
Connect on one of them cannot succeed, because `install_from_catalog` raises
`Unknown catalog entry` for a slug with no MCP seed and no desktop profile.
The card is a promise the product cannot keep.

This module is the check in the missing direction. It is deliberately
separate from the profile reconciler: that one protects the profile author
("you referenced a card that doesn't exist"), this one protects the user
("we advertised a connector we can't deliver").

A slug is **installable** when either source can produce a server record:

* a desktop profile declares it — the profile carries its own verified
  endpoint and auth metadata; or
* `mcp_catalog.DEFAULT_CATALOG` seeds it — `install_from_catalog` mints
  `seed:<slug>` from that entry.

Neither is a "nice to have": both are the only two code paths that turn a
slug into something with a URL.
"""

from __future__ import annotations

from collections.abc import Iterable

from backend_app.connectors.service import ConnectorCatalogEntry, load_catalog
from backend_app.mcp_catalog import DEFAULT_CATALOG


class ConnectorCatalogConformanceError(ValueError):
    """An advertised connector slug resolves to nothing installable."""


class ConnectorCatalogConformance:
    """Checks that the advertised catalog and the installable sources agree."""

    @staticmethod
    def installable_slugs(profile_slugs: Iterable[str] | None = None) -> frozenset[str]:
        """Return every slug some source can turn into a server record.

        ``profile_slugs`` is injectable so a caller that has already loaded
        the desktop profiles does not pay for a second YAML read; omitted, it
        is loaded here.
        """

        if profile_slugs is None:
            # Imported lazily: the profile catalog imports this module's
            # siblings, and a module-level import would make the two files
            # circular for no benefit.
            from backend_app.connectors.profile_catalog import (  # noqa: PLC0415
                DesktopProfileCatalog,
            )

            profile_slugs = [
                profile.connector_slug
                for profile in DesktopProfileCatalog.load().profiles
            ]
        return frozenset(profile_slugs) | {entry.slug for entry in DEFAULT_CATALOG}

    @classmethod
    def unresolvable_slugs(
        cls,
        *,
        marketing: Iterable[ConnectorCatalogEntry] | None = None,
        profile_slugs: Iterable[str] | None = None,
    ) -> tuple[str, ...]:
        """Return advertised slugs nothing can install, sorted for stable output."""

        advertised = marketing if marketing is not None else load_catalog()
        installable = cls.installable_slugs(profile_slugs)
        return tuple(
            sorted(entry.slug for entry in advertised if entry.slug not in installable)
        )

    @classmethod
    def assert_installable(
        cls,
        *,
        marketing: Iterable[ConnectorCatalogEntry] | None = None,
        profile_slugs: Iterable[str] | None = None,
    ) -> None:
        """Raise when the catalog advertises something that cannot be installed."""

        orphans = cls.unresolvable_slugs(
            marketing=marketing, profile_slugs=profile_slugs
        )
        if not orphans:
            return
        raise ConnectorCatalogConformanceError(
            "connectors/catalog.yaml advertises "
            f"{', '.join(orphans)} but no desktop profile or MCP catalog seed "
            "can install them — a Connect button on those cards cannot succeed. "
            "Give each one a verified endpoint or stop advertising it."
        )


__all__ = (
    "ConnectorCatalogConformance",
    "ConnectorCatalogConformanceError",
)
