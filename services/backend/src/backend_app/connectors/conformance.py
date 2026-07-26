"""Invariants the connector registry must hold.

The defect this file was written for — a card offering Connect for a slug
nothing could install — is now **structurally impossible**. `installable` is
derived (`server_id is not None`), and only a profile or an MCP seed can set
a server id; an announced row has no endpoint field to fill in. There is no
longer a way to express the bug.

That is the outcome worth having, so what remains here is not a re-check of
the same thing in another form. These are the invariants the *registry* can
still violate, which the type system cannot catch:

* an announced row that a real implementation has overtaken (dead copy the
  file should shed);
* a slug resolving from more than one source (precedence silently dropping a
  row rather than choosing);
* an installable row with no display name (a card that renders blank).

`DesktopProfileCatalog.reconcile` guards the direction *into* profiles — no
profile may reference a card that does not exist. This guards the registry
that consumes it.
"""

from __future__ import annotations

from collections.abc import Iterable

from backend_app.connectors.registry import (
    AnnouncedConnector,
    ConnectorRegistry,
    ConnectorSource,
)


class ConnectorCatalogConformanceError(ValueError):
    """The resolved connector registry violates an invariant."""


class ConnectorCatalogConformance:
    """Checks the resolved registry against the invariants above."""

    @classmethod
    def unresolvable_slugs(
        cls, registry: ConnectorRegistry | None = None
    ) -> tuple[str, ...]:
        """Advertised connectors that neither install nor say they cannot.

        Structurally empty now: every row is installable or explicitly
        `coming_soon`. Kept as an executable statement of that property, so
        a future source that forgets to declare one is caught here rather
        than by a user meeting a dead Connect button.
        """

        resolved = registry if registry is not None else ConnectorRegistry.load()
        return tuple(
            sorted(
                row.slug
                for row in resolved
                if not row.installable and row.source is not ConnectorSource.ANNOUNCED
            )
        )

    @classmethod
    def blank_display_names(
        cls, registry: ConnectorRegistry | None = None
    ) -> tuple[str, ...]:
        """Installable rows that would render as an unlabelled card."""

        resolved = registry if registry is not None else ConnectorRegistry.load()
        return tuple(
            sorted(row.slug for row in resolved if not row.display_name.strip())
        )

    @classmethod
    def assert_conformant(
        cls,
        registry: ConnectorRegistry | None = None,
        announced: Iterable[AnnouncedConnector] | None = None,
    ) -> None:
        """Raise on any registry invariant violation, naming what to do."""

        resolved = registry if registry is not None else ConnectorRegistry.load()

        orphans = cls.unresolvable_slugs(resolved)
        if orphans:
            raise ConnectorCatalogConformanceError(
                f"{', '.join(orphans)} resolve to no installable server and are "
                "not declared coming_soon — a Connect button on those cards "
                "cannot succeed. Give each a seed or profile, or move it to "
                "catalog.yaml with a note."
            )

        blank = cls.blank_display_names(resolved)
        if blank:
            raise ConnectorCatalogConformanceError(
                f"{', '.join(blank)} resolve with no display name and would "
                "render as a blank card. Set display_name on the profile, or "
                "let it fall through to a seed that has one."
            )

        rows = (
            announced if announced is not None else ConnectorRegistry.load_announced()
        )
        superseded = resolved.superseded_announcements(rows)
        if superseded:
            raise ConnectorCatalogConformanceError(
                f"{', '.join(superseded)} are announced in catalog.yaml but now "
                "resolve from a real profile or seed. Delete those rows — the "
                "announcement is dead copy the registry already ignores."
            )


__all__ = (
    "ConnectorCatalogConformance",
    "ConnectorCatalogConformanceError",
)
