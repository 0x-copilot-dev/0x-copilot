"""Static catalog of well-known remote MCP servers.

Pre-seeded into a user's connector list (disabled by default) on the
first ``list_servers`` call so the chat agent never auto-recommends an
empty connector set, while keeping the user in control: every entry is
``enabled=False`` until the user toggles it on.

The catalog ships with **stable** ``server_id`` values (``seed:<slug>``)
so re-running the seed against an existing user is idempotent — entries
the user has explicitly removed stay removed (we only seed when the
user has zero servers; the deterministic IDs let an explicit
``reset_catalog`` call also be safe).

URLs were verified against vendor documentation as of 2026-05. Entries
where the vendor's MCP URL was not publicly verifiable at seed time are
marked with ``verified=False`` so a follow-up can refresh them without
re-touching the seeding logic.

PR 3.4.1 — each entry now carries brand metadata (``logo_url``,
``brand_color``, ``scopes_summary``, ``default_scopes``). New seeds pick
these up automatically; the migration backfills existing rows in place.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .contracts import McpAuthMode, McpTransport


@dataclass(frozen=True)
class CatalogEntry:
    """One pre-seeded connector. ``slug`` is the stable id suffix."""

    slug: str
    display_name: str
    url: str
    transport: McpTransport = McpTransport.HTTP
    auth_mode: McpAuthMode = McpAuthMode.OAUTH2
    description: str = ""
    # ``verified`` marks whether the URL was confirmed against the
    # vendor's official docs at seed-list time. Unverified entries are
    # still seeded but we keep the marker so a follow-up audit can grep
    # for them.
    verified: bool = True
    # Brand metadata. Frontend renders ``logo_url`` as the row favicon
    # (with letter-glyph fallback on 404), ``brand_color`` as the chip
    # background, ``scopes_summary`` as the popover row subtitle, and
    # ``default_scopes`` as the resume-from-paused payload that PR 1.2
    # round-trips through ``PATCH /v1/agent/conversations/{id}/connectors``.
    logo_url: str | None = None
    brand_color: str | None = None
    scopes_summary: str | None = None
    default_scopes: tuple[str, ...] = field(default_factory=tuple)
    # PR 4.4.6 — when True, install requires a pre-registered OAuth
    # client (the vendor doesn't expose RFC 8414 metadata or RFC 7591
    # dynamic client registration). The frontend prompts for
    # ``client_id`` / ``client_secret`` before calling install.
    requires_pre_registered_client: bool = False

    @property
    def server_id(self) -> str:
        """Stable seed ID — ``seed:<slug>``."""

        return f"seed:{self.slug}"


# Order matters for the UI: alphabetical so the disabled list reads
# predictably. Verified URLs come from vendor MCP docs; check the
# ``verified`` flag for entries to revisit.
DEFAULT_CATALOG: tuple[CatalogEntry, ...] = (
    CatalogEntry(
        slug="asana",
        display_name="Asana",
        url="https://mcp.asana.com/v2/mcp",
        description="Tasks, projects, and portfolios.",
        logo_url="https://cdn.atlas.local/brand/asana.svg",
        brand_color="#F06A6A",
        scopes_summary="Read tasks, comment, no delete",
        default_scopes=("read", "comment"),
    ),
    CatalogEntry(
        slug="atlassian",
        display_name="Atlassian (Jira + Confluence)",
        url="https://mcp.atlassian.com/v1/mcp/authv2",
        description="Jira issues, Confluence pages.",
        logo_url="https://cdn.atlas.local/brand/atlassian.svg",
        brand_color="#2684FF",
        scopes_summary="Read issues and Confluence pages",
        default_scopes=("read",),
        requires_pre_registered_client=True,
    ),
    CatalogEntry(
        slug="cloudflare-bindings",
        display_name="Cloudflare Bindings",
        url="https://bindings.mcp.cloudflare.com/sse",
        transport=McpTransport.SSE,
        description="Cloudflare Workers bindings and config.",
        verified=False,
        logo_url="https://cdn.atlas.local/brand/cloudflare.svg",
        brand_color="#F38020",
        scopes_summary="Read Workers bindings",
        default_scopes=("read",),
    ),
    CatalogEntry(
        slug="cloudflare-observability",
        display_name="Cloudflare Observability",
        url="https://observability.mcp.cloudflare.com/sse",
        transport=McpTransport.SSE,
        description="Cloudflare logs, traces, and metrics.",
        verified=False,
        logo_url="https://cdn.atlas.local/brand/cloudflare.svg",
        brand_color="#F38020",
        scopes_summary="Read logs, traces, and metrics",
        default_scopes=("read",),
    ),
    CatalogEntry(
        slug="github",
        display_name="GitHub",
        url="https://api.githubcopilot.com/mcp",
        description="Repos, issues, pull requests.",
        verified=False,
        logo_url="https://cdn.atlas.local/brand/github.svg",
        brand_color="#0D1117",
        scopes_summary="Read repos, no write",
        default_scopes=("read",),
        requires_pre_registered_client=True,
    ),
    CatalogEntry(
        slug="intercom",
        display_name="Intercom",
        url="https://mcp.intercom.com/sse",
        transport=McpTransport.SSE,
        description="Conversations and contacts.",
        verified=False,
        logo_url="https://cdn.atlas.local/brand/intercom.svg",
        brand_color="#1F8DED",
        scopes_summary="Read conversations and contacts",
        default_scopes=("read",),
        requires_pre_registered_client=True,
    ),
    CatalogEntry(
        slug="linear",
        display_name="Linear",
        url="https://mcp.linear.app/mcp",
        description="Issues, projects, and cycles.",
        logo_url="https://cdn.atlas.local/brand/linear.svg",
        brand_color="#5E6AD2",
        scopes_summary="Read issues, projects, cycles",
        default_scopes=("read",),
    ),
    CatalogEntry(
        slug="notion",
        display_name="Notion",
        url="https://mcp.notion.com/mcp",
        description="Workspace pages and databases.",
        logo_url="https://cdn.atlas.local/brand/notion.svg",
        brand_color="#000000",
        scopes_summary="Read all pages, write to /Drafts",
        default_scopes=("read", "write_drafts"),
    ),
    CatalogEntry(
        slug="paypal",
        display_name="PayPal",
        url="https://mcp.paypal.com/sse",
        transport=McpTransport.SSE,
        description="Payments, invoices, and disputes.",
        verified=False,
        logo_url="https://cdn.atlas.local/brand/paypal.svg",
        brand_color="#003087",
        scopes_summary="Read payments and invoices",
        default_scopes=("read",),
        requires_pre_registered_client=True,
    ),
    CatalogEntry(
        slug="plaid",
        display_name="Plaid",
        url="https://api.dashboard.plaid.com/mcp/sse",
        transport=McpTransport.SSE,
        description="Financial account data and transactions.",
        verified=False,
        logo_url="https://cdn.atlas.local/brand/plaid.svg",
        brand_color="#111111",
        scopes_summary="Read accounts and transactions",
        default_scopes=("read",),
        requires_pre_registered_client=True,
    ),
    CatalogEntry(
        slug="sentry",
        display_name="Sentry",
        url="https://mcp.sentry.dev/mcp",
        description="Issues, releases, and stack traces.",
        verified=False,
        logo_url="https://cdn.atlas.local/brand/sentry.svg",
        brand_color="#362D59",
        scopes_summary="Read issues and stack traces",
        default_scopes=("read",),
    ),
    CatalogEntry(
        slug="square",
        display_name="Square",
        url="https://mcp.squareup.com/sse",
        transport=McpTransport.SSE,
        description="Payments, orders, and inventory.",
        verified=False,
        logo_url="https://cdn.atlas.local/brand/square.svg",
        brand_color="#000000",
        scopes_summary="Read payments, orders, inventory",
        default_scopes=("read",),
        requires_pre_registered_client=True,
    ),
    CatalogEntry(
        slug="zapier",
        display_name="Zapier",
        url="https://mcp.zapier.com/api/mcp/mcp",
        description="Cross-app automations across the Zapier directory.",
        logo_url="https://cdn.atlas.local/brand/zapier.svg",
        brand_color="#FF4A00",
        scopes_summary="Run cross-app automations",
        default_scopes=("read", "trigger"),
    ),
)


def catalog_by_slug() -> dict[str, CatalogEntry]:
    """Lookup helper for service-layer reset/seed flows."""

    return {entry.slug: entry for entry in DEFAULT_CATALOG}
