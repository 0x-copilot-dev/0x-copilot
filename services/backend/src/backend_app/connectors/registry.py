"""The one place that answers "what connectors exist, and what state is each in".

Before this module there were three answers. `mcp_catalog.DEFAULT_CATALOG`
seeded thirteen real MCP servers and fed the chat composer. `catalog.yaml`
advertised nine marketing slugs and fed the Tools destination. The two shared
three entries. `desktop_profiles.yaml` reconciled a slice of the overlap for
desktop. A user comparing the composer to the destination saw two different
products, and they were right to.

This is one reader over the sources that remain, not one record type over
them. That distinction is deliberate: a desktop-verified profile and a
curated MCP seed carry genuinely different evidence, and flattening them
would mean inventing the evidence the weaker one lacks. A profile carries a
human `verified_at` attestation, callback modes, and per-tool risk. A seed
carries a URL and auth mode. Forcing a seed into profile shape would require
writing a verification date for a check nobody performed — the same class of
lie as a consent card captioning an unknown scope "Read-only".

So each source keeps its own record, and :class:`ConnectorRegistry` projects
both into one :class:`ResolvedConnector` whose fields are only ever what the
underlying source can actually support. ``capabilities_declared`` is the
honest marker: false means "nobody has inventoried this connector's tools",
not "this connector has no tools".

Three sources, one reader:

* **profiles** (`desktop_profiles.yaml`) — verified endpoint, callback modes,
  per-tool risk/approval. The strongest evidence; wins any slug collision.
* **seeds** (`mcp_catalog.DEFAULT_CATALOG`) — installable MCP servers with
  brand metadata. No capability inventory.
* **announced** (`catalog.yaml`) — connectors the product advertises with no
  implementation behind them. Always ``installable=False``. The file exists
  so a product promise is explicit and dated rather than an orphan slug that
  renders a Connect button leading nowhere.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from enum import StrEnum
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict

from backend_app.mcp_catalog import DEFAULT_CATALOG, CatalogEntry


class ConnectorLifecycle(StrEnum):
    """How ready a connector is, from the user's side."""

    AVAILABLE = "available"
    PREVIEW = "preview"
    ADMIN_SETUP_REQUIRED = "admin_setup_required"
    COMING_SOON = "coming_soon"


class ConnectorSource(StrEnum):
    """Which body of evidence a resolved row came from."""

    PROFILE = "profile"
    MCP_SEED = "mcp_seed"
    ANNOUNCED = "announced"


class RegistryError(ValueError):
    """The connector registry could not be resolved."""


# Lifecycle → the wire's `availability` vocabulary (api-types
# `ConnectorAvailability`). One axis, not two: "can I connect this, and if not
# why" is a single question, and the desktop catalog already answers it with
# these strings. `coming_soon` exists on the union precisely so an announced
# row can answer it too instead of arriving indistinguishable from a live one.
_AVAILABILITY_BY_LIFECYCLE: Mapping[ConnectorLifecycle, str] = {
    ConnectorLifecycle.AVAILABLE: "available",
    ConnectorLifecycle.PREVIEW: "preview",
    ConnectorLifecycle.ADMIN_SETUP_REQUIRED: "admin_setup_required",
    ConnectorLifecycle.COMING_SOON: "coming_soon",
}


class AnnouncedConnector(BaseModel):
    """A connector the product advertises but cannot install yet.

    Deliberately minimal, and deliberately incapable of claiming otherwise:
    there is no endpoint field to fill in. The moment an implementation
    exists it belongs in a profile or a seed, and the registry will prefer
    it — :meth:`ConnectorRegistry.superseded_announcements` reports the
    leftover row so the file self-cleans.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    slug: str
    display_name: str
    description: str = ""
    icon_hint: str | None = None
    # Why it is not installable, in the user's words. Rendered on the card,
    # so it must read as a status and not an apology.
    note: str = ""


class ResolvedConnector(BaseModel):
    """One connector, projected from whichever source described it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    slug: str
    display_name: str
    description: str
    source: ConnectorSource
    lifecycle: ConnectorLifecycle
    # ``None`` for an announced connector — there is no server to install.
    server_id: str | None = None
    icon_hint: str | None = None
    logo_url: str | None = None
    brand_color: str | None = None
    # False when nobody has inventoried this connector's tools and their
    # risk. NOT a claim that it has none — the absence is the point, so a
    # caller that needs per-tool risk knows to withhold rather than assume.
    capabilities_declared: bool = False
    # Why this row is not connectable, in the user's words. Empty for an
    # available row. Carried from the announced entry's ``note``; preview and
    # admin-setup rows leave it empty and let the client's per-state copy
    # speak, so that wording lives in exactly one place.
    availability_reason: str = ""

    @property
    def installable(self) -> bool:
        """True when some source can actually produce a server record."""

        return self.server_id is not None


class ConnectorRegistry:
    """Resolves every source into one ordered, deduplicated connector list."""

    _ANNOUNCED_FILE = "catalog.yaml"

    def __init__(self, resolved: tuple[ResolvedConnector, ...]) -> None:
        self._resolved = resolved

    def __iter__(self):
        return iter(self._resolved)

    def __len__(self) -> int:
        return len(self._resolved)

    @property
    def all(self) -> tuple[ResolvedConnector, ...]:
        return self._resolved

    def installable(self) -> tuple[ResolvedConnector, ...]:
        return tuple(row for row in self._resolved if row.installable)

    def get(self, slug: str) -> ResolvedConnector | None:
        return next((row for row in self._resolved if row.slug == slug), None)

    # -- construction ------------------------------------------------------

    @classmethod
    def load(
        cls,
        *,
        announced_path: Path | None = None,
        preview_enabled: bool = False,
    ) -> "ConnectorRegistry":
        """Build the registry from the shipped profile / seed / announced files."""

        # Late import: profile_catalog imports this module's siblings, and a
        # module-level import would make the pair circular for no benefit.
        from backend_app.connectors.profile_catalog import (  # noqa: PLC0415
            DesktopProfileCatalog,
        )

        return cls.resolve(
            profiles=DesktopProfileCatalog.load().profiles,
            seeds=DEFAULT_CATALOG,
            announced=cls.load_announced(announced_path),
            preview_enabled=preview_enabled,
        )

    @classmethod
    def load_announced(cls, path: Path | None = None) -> tuple[AnnouncedConnector, ...]:
        """Read the advertised-but-unimplemented list; empty when absent."""

        resolved = path or Path(__file__).resolve().parent / cls._ANNOUNCED_FILE
        if not resolved.exists():
            return ()
        with resolved.open("r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
        rows = raw.get("entries") or []
        try:
            return tuple(AnnouncedConnector.model_validate(row) for row in rows)
        except Exception as exc:  # pydantic ValidationError → typed error
            raise RegistryError(f"invalid announced connector: {exc}") from exc

    @classmethod
    def resolve(
        cls,
        *,
        profiles: Iterable[object] = (),
        seeds: Iterable[CatalogEntry] = (),
        announced: Iterable[AnnouncedConnector] = (),
        preview_enabled: bool = False,
    ) -> "ConnectorRegistry":
        """Project every source into one list, strongest evidence winning.

        Precedence is profile > seed > announced, because that is the order
        of how much is actually known about the connector. A slug present in
        two sources resolves once, from the stronger one.
        """

        by_slug: dict[str, ResolvedConnector] = {}
        for entry in announced:
            by_slug[entry.slug] = cls._from_announced(entry)
        for seed in seeds:
            by_slug[seed.slug] = cls._from_seed(seed)
        for profile in profiles:
            row = cls._from_profile(
                profile,
                seed=next((s for s in seeds if s.slug == profile.connector_slug), None),
                announced=next(
                    (a for a in announced if a.slug == profile.connector_slug), None
                ),
                preview_enabled=preview_enabled,
            )
            by_slug[row.slug] = row
        return cls(tuple(sorted(by_slug.values(), key=lambda r: r.display_name)))

    # -- projections -------------------------------------------------------

    @staticmethod
    def _from_announced(entry: AnnouncedConnector) -> ResolvedConnector:
        return ResolvedConnector(
            slug=entry.slug,
            display_name=entry.display_name,
            description=entry.description,
            source=ConnectorSource.ANNOUNCED,
            lifecycle=ConnectorLifecycle.COMING_SOON,
            server_id=None,
            icon_hint=entry.icon_hint,
            capabilities_declared=False,
            availability_reason=entry.note,
        )

    @staticmethod
    def _from_seed(seed: CatalogEntry) -> ResolvedConnector:
        return ResolvedConnector(
            slug=seed.slug,
            display_name=seed.display_name,
            description=seed.description,
            source=ConnectorSource.MCP_SEED,
            lifecycle=ConnectorLifecycle.AVAILABLE,
            server_id=seed.server_id,
            icon_hint=seed.slug,
            logo_url=seed.logo_url,
            brand_color=seed.brand_color,
            # A seed is a URL and an auth mode. Nobody has inventoried its
            # tools, so the registry says so rather than implying none.
            capabilities_declared=False,
        )

    @staticmethod
    def _from_profile(
        profile: object,
        *,
        seed: CatalogEntry | None,
        announced: AnnouncedConnector | None,
        preview_enabled: bool,
    ) -> ResolvedConnector:
        from backend_app.connectors.profile_catalog import (  # noqa: PLC0415
            ConnectorAvailability,
        )

        availability = profile.default_availability(preview_enabled=preview_enabled)
        lifecycle = {
            ConnectorAvailability.PREVIEW: ConnectorLifecycle.PREVIEW,
            ConnectorAvailability.ADMIN_SETUP_REQUIRED: (
                ConnectorLifecycle.ADMIN_SETUP_REQUIRED
            ),
        }.get(availability, ConnectorLifecycle.AVAILABLE)

        # Presentation falls back through the sources that carry it, so a
        # profile never has to restate copy a seed already owns.
        display_name = (
            getattr(profile, "display_name", "")
            or (seed.display_name if seed is not None else "")
            or (announced.display_name if announced is not None else "")
            or profile.connector_slug
        )
        description = (
            getattr(profile, "description", "")
            or (seed.description if seed is not None else "")
            or (announced.description if announced is not None else "")
        )
        return ResolvedConnector(
            slug=profile.connector_slug,
            display_name=display_name,
            description=description,
            source=ConnectorSource.PROFILE,
            lifecycle=lifecycle,
            server_id=profile.server_id,
            icon_hint=profile.connector_slug,
            logo_url=seed.logo_url if seed is not None else None,
            brand_color=seed.brand_color if seed is not None else None,
            capabilities_declared=bool(profile.tools),
        )

    # -- hygiene -----------------------------------------------------------

    def superseded_announcements(
        self, announced: Iterable[AnnouncedConnector]
    ) -> tuple[str, ...]:
        """Announced slugs that now have a real implementation.

        The announcement is dead copy once a profile or seed exists; this
        lets the catalog file self-clean instead of accumulating rows that
        no longer describe anything.
        """

        return tuple(
            sorted(
                entry.slug
                for entry in announced
                if (row := self.get(entry.slug)) is not None
                and row.source is not ConnectorSource.ANNOUNCED
            )
        )

    def as_map(self) -> Mapping[str, ResolvedConnector]:
        return {row.slug: row for row in self._resolved}

    def as_catalog_entries(self) -> tuple[object, ...]:
        """Project to the destination's `available` wire shape.

        The Available tab renders every row, installable or not — a
        `coming_soon` card is honest and a missing one is a silent lie of
        omission. The lifecycle rides along so the client can render the
        state instead of offering a Connect button that cannot succeed.

        That last sentence described an intent, not the code: the projection
        dropped `lifecycle` on the floor and the wire model had nowhere to put
        it, so every row reached the browser indistinguishable from every
        other and the destination rendered an unconditional Connect over all
        of them — including the three announced slugs that resolve to no
        server at all. The lifecycle now actually rides along.
        """

        from backend_app.connectors.service import (  # noqa: PLC0415
            ConnectorCatalogEntry,
        )

        return tuple(
            ConnectorCatalogEntry(
                slug=row.slug,
                display_name=row.display_name,
                description=row.description,
                icon_hint=row.icon_hint,
                availability=_AVAILABILITY_BY_LIFECYCLE[row.lifecycle],
                availability_reason=row.availability_reason or None,
            )
            for row in self._resolved
        )


__all__ = (
    "AnnouncedConnector",
    "ConnectorLifecycle",
    "ConnectorRegistry",
    "ConnectorSource",
    "RegistryError",
    "ResolvedConnector",
)
