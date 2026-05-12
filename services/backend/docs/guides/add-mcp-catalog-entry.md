# Guide — Add a New MCP Catalog Entry

How to add a curated MCP server to the static catalog served at `GET /v1/mcp/catalog`.

See also:

- [features/mcp-registry.md](../features/mcp-registry.md) — catalog architecture
- [architecture/02-contracts.md](../architecture/02-contracts.md) — `McpCatalogEntryResponse`

---

## What a catalog entry is

The catalog is a static list of verified MCP servers that users can install in one click.
Catalog entries are compiled into the backend binary (no DB row). When a user installs a
catalog entry, a `McpServerRecord` is created with `server_id = "seed:<slug>"` (idempotent).

---

## Step 1 — Add to `mcp_catalog.py`

`backend_app/mcp_catalog.py` — add a new `CatalogEntry` to `DEFAULT_CATALOG`:

```python
CatalogEntry(
    slug="github",                          # stable lowercase slug; becomes server_id prefix
    display_name="GitHub",
    url="https://api.github.com/mcp",       # public HTTPS only; validated on install
    transport=McpTransport.HTTP,
    auth_mode=McpAuthMode.OAUTH2,
    description="Browse repositories, issues, and PRs.",
    logo_url="https://cdn.example.com/github-logo.png",   # optional
    brand_color="#24292e",                  # optional hex color
    scopes_summary="Read-only access to your repos",      # short user-facing description
    default_scopes=("repo",),              # scopes to request during OAuth
    requires_pre_registered_client=False,   # True if vendor doesn't support DCR
    discoverable=True,                     # phase-2 progressive-discovery hint
    verified=True,
),
```

**Slug rules:**

- Must be `[a-z0-9][a-z0-9_-]*` (SLUG_PATTERN).
- Must be unique across the catalog.
- Never change a slug after launch — existing installs use `seed:<slug>` as their `server_id`.

---

## Step 2 — Set `requires_pre_registered_client`

If the MCP server's OAuth provider doesn't support Dynamic Client Registration (DCR) —
i.e., the user must register an OAuth app themselves and supply `client_id`/`client_secret` —
set `requires_pre_registered_client=True`.

When `True`:

- The frontend shows a credentials form before initiating the install.
- `InstallMcpServerRequest.oauth_client` must be non-None.
- The service validates and encrypts the pre-registered client config.

---

## Step 3 — Test the catalog entry

```bash
# Start the dev stack
make dev

# Install the catalog entry
export TOKEN=$(make dev-bearer)
curl -X POST http://127.0.0.1:8200/v1/mcp/servers/install \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"slug": "github"}'

# Verify the server record was created
curl http://127.0.0.1:8200/v1/mcp/servers \
  -H "Authorization: Bearer $TOKEN"
```

---

## Step 4 — Add unit tests

`tests/unit/backend_app/test_mcp_catalog.py` — add a test verifying:

- The slug is present in `DEFAULT_CATALOG`.
- The `url` passes `Validators.validate_public_mcp_url()`.
- `requires_pre_registered_client` is consistent with the vendor's DCR support.
- The catalog response shape is valid `McpCatalogEntryResponse`.

---

## Checklist

- [ ] `slug` is unique and lowercase-alphanumeric+`-_`
- [ ] `url` is HTTPS and publicly reachable
- [ ] `auth_mode` matches the server's actual auth requirement
- [ ] `requires_pre_registered_client` is set correctly
- [ ] Brand metadata (`logo_url`, `brand_color`, `scopes_summary`) is provided
- [ ] Unit test added
- [ ] Catalog endpoint tested end-to-end in dev stack
