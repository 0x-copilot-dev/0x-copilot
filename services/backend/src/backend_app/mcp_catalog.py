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
"""

from __future__ import annotations

from dataclasses import dataclass

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
    ),
    CatalogEntry(
        slug="atlassian",
        display_name="Atlassian (Jira + Confluence)",
        url="https://mcp.atlassian.com/v1/sse",
        transport=McpTransport.SSE,
        description="Jira issues, Confluence pages.",
        verified=False,
    ),
    CatalogEntry(
        slug="cloudflare-bindings",
        display_name="Cloudflare Bindings",
        url="https://bindings.mcp.cloudflare.com/sse",
        transport=McpTransport.SSE,
        description="Cloudflare Workers bindings and config.",
        verified=False,
    ),
    CatalogEntry(
        slug="cloudflare-observability",
        display_name="Cloudflare Observability",
        url="https://observability.mcp.cloudflare.com/sse",
        transport=McpTransport.SSE,
        description="Cloudflare logs, traces, and metrics.",
        verified=False,
    ),
    CatalogEntry(
        slug="github",
        display_name="GitHub",
        url="https://api.githubcopilot.com/mcp",
        description="Repos, issues, pull requests.",
        verified=False,
    ),
    CatalogEntry(
        slug="intercom",
        display_name="Intercom",
        url="https://mcp.intercom.com/sse",
        transport=McpTransport.SSE,
        description="Conversations and contacts.",
        verified=False,
    ),
    CatalogEntry(
        slug="linear",
        display_name="Linear",
        url="https://mcp.linear.app/mcp",
        description="Issues, projects, and cycles.",
    ),
    CatalogEntry(
        slug="notion",
        display_name="Notion",
        url="https://mcp.notion.com/mcp",
        description="Workspace pages and databases.",
    ),
    CatalogEntry(
        slug="paypal",
        display_name="PayPal",
        url="https://mcp.paypal.com/sse",
        transport=McpTransport.SSE,
        description="Payments, invoices, and disputes.",
        verified=False,
    ),
    CatalogEntry(
        slug="plaid",
        display_name="Plaid",
        url="https://api.dashboard.plaid.com/mcp/sse",
        transport=McpTransport.SSE,
        description="Financial account data and transactions.",
        verified=False,
    ),
    CatalogEntry(
        slug="sentry",
        display_name="Sentry",
        url="https://mcp.sentry.dev/mcp",
        description="Issues, releases, and stack traces.",
        verified=False,
    ),
    CatalogEntry(
        slug="square",
        display_name="Square",
        url="https://mcp.squareup.com/sse",
        transport=McpTransport.SSE,
        description="Payments, orders, and inventory.",
        verified=False,
    ),
    CatalogEntry(
        slug="zapier",
        display_name="Zapier",
        url="https://mcp.zapier.com/api/mcp/mcp",
        description="Cross-app automations across the Zapier directory.",
    ),
)


def catalog_by_slug() -> dict[str, CatalogEntry]:
    """Lookup helper for service-layer reset/seed flows."""

    return {entry.slug: entry for entry in DEFAULT_CATALOG}
