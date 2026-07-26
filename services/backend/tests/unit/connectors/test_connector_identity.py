"""Connector identity is the slug; `server_id` is an installation detail.

Installed rows are keyed by `server_id`, and the two install surfaces mint
different shapes: `seed:<slug>` from the catalog install, `desktop:<vendor>:
<product>` from the desktop OAuth coordinator. Product code that needed to
know *which connector* a row is recovered the answer by parsing that id and
falling back to `name` — and `name` is lossy, because both mint paths write
`slug.replace("-", "_")`.

The consequence, before this change: the same connector resolved to
`cloudflare-bindings` when seed-installed and `cloudflare_bindings` when
profile-installed. Two surfaces computing one connector's identity
differently is the class of bug this phase removes.

The migration is additive, and these tests are mostly about proving that:
nothing is renumbered, and a row written by an older build keeps resolving
exactly as it did. That property is what lets the column ship before the
backfill has run everywhere.
"""

from __future__ import annotations

from backend_app.connectors.service import mcp_connector_slug
from backend_app.contracts import McpAuthMode, McpServerRecord, McpTransport


def _record(**overrides: object) -> McpServerRecord:
    payload: dict[str, object] = {
        "org_id": "org_a",
        "user_id": "usr_1",
        "display_name": "X",
        "url": "https://x.example/mcp",
        "transport": McpTransport.HTTP,
        "auth_mode": McpAuthMode.OAUTH2,
        "name": "x",
    }
    payload.update(overrides)
    return McpServerRecord(**payload)  # type: ignore[arg-type]


class TestHistoricalRowsStillResolve:
    """The additive half: no existing installation is orphaned."""

    def test_a_seed_row_written_before_the_column_existed(self) -> None:
        record = _record(server_id="seed:linear", name="linear", connector_slug=None)
        assert mcp_connector_slug(record) == "linear"

    def test_a_desktop_row_written_before_the_column_existed(self) -> None:
        record = _record(
            server_id="desktop:google:gmail", name="gmail", connector_slug=None
        )
        assert mcp_connector_slug(record) == "gmail"

    def test_a_dashed_seed_slug_survives_the_prefix_strip(self) -> None:
        record = _record(
            server_id="seed:cloudflare-bindings",
            name="cloudflare_bindings",
            connector_slug=None,
        )
        assert mcp_connector_slug(record) == "cloudflare-bindings"

    def test_the_historical_derivation_is_unchanged_for_every_shape(self) -> None:
        """Byte-for-byte the old behaviour when no slug is stored.

        If this drifts, the migration stops being additive and starts
        renaming connectors out from under installed rows.
        """

        for server_id, name, expected in (
            ("seed:notion", "notion", "notion"),
            ("seed:cloudflare-observability", "x", "cloudflare-observability"),
            ("desktop:google:gmail", "gmail", "gmail"),
            ("desktop:microsoft:outlook", "outlook", "outlook"),
            (uuid_like := "9f2c1ab34d", "custom_server", "custom_server"),
        ):
            assert (
                mcp_connector_slug(
                    _record(server_id=server_id, name=name, connector_slug=None)
                )
                == expected
            ), server_id
        assert uuid_like  # the custom-server case is a real shape, not a typo


class TestStatedIdentityWins:
    def test_an_explicit_slug_beats_the_id_derivation(self) -> None:
        record = _record(
            server_id="desktop:cf:bindings",
            name="cloudflare_bindings",
            connector_slug="cloudflare-bindings",
        )
        assert mcp_connector_slug(record) == "cloudflare-bindings"

    def test_both_surfaces_now_agree_on_one_connector(self) -> None:
        """The bug this phase exists to remove.

        Same connector, two install surfaces, two `server_id` shapes — and
        before the stated slug, two different identities.
        """

        seed_installed = _record(
            server_id="seed:cloudflare-bindings",
            name="cloudflare_bindings",
            connector_slug="cloudflare-bindings",
        )
        desktop_installed = _record(
            server_id="desktop:cf:bindings",
            name="cloudflare_bindings",
            connector_slug="cloudflare-bindings",
        )
        assert mcp_connector_slug(seed_installed) == mcp_connector_slug(
            desktop_installed
        )

    def test_the_old_derivation_would_have_disagreed(self) -> None:
        """Pins the defect, so the fix cannot be quietly reverted."""

        seed_only = _record(
            server_id="seed:cloudflare-bindings",
            name="cloudflare_bindings",
            connector_slug=None,
        )
        desktop_only = _record(
            server_id="desktop:cf:bindings",
            name="cloudflare_bindings",
            connector_slug=None,
        )
        assert mcp_connector_slug(seed_only) != mcp_connector_slug(desktop_only)


class TestMintPathsStateTheirSlug:
    def test_catalog_install_states_the_slug(self) -> None:
        from backend_app.mcp_catalog import DEFAULT_CATALOG
        from backend_app.service import McpRegistryService
        from backend_app.store import InMemoryMcpStore
        from backend_app.contracts import InstallMcpServerRequest

        dashed = next(e for e in DEFAULT_CATALOG if "-" in e.slug)
        service = McpRegistryService(store=InMemoryMcpStore())
        installed = service.install_from_catalog(
            InstallMcpServerRequest(org_id="org_a", user_id="usr_1", slug=dashed.slug)
        )
        record = service.store.get_server(org_id="org_a", server_id=installed.server_id)
        assert record is not None
        assert record.connector_slug == dashed.slug
        # And the identity survives the dash that `name` loses.
        assert mcp_connector_slug(record) == dashed.slug
        assert record.name != dashed.slug
