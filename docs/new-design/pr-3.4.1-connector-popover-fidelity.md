# PR 3.4.1 — ConnectorPopover brand fidelity, scope subtitles, slider toggle, server-supplied metadata

> **Status:** Draft (PRD + Spec + Architecture)
> **Plan reference:** Wave 3 follow‑up to PR 3.4
> **Owner:** backend (1 migration + 1 brand‑seed catalog + 4 fields on `McpServerResponse`) · backend‑facade (zero — proxy) · api‑types (5 fields on `McpServer`) · frontend (popover row redesign + `<AppIcon logoUrl>` variant + `<Switch>` row + scope subtitle) · design‑system (one variant on `<AppIcon>`)
> **Size:** **M.** Pure additive: 5 nullable columns + 4 nullable API fields + 1 `<AppIcon>` variant + 1 popover row rewrite. No streaming change. No agent‑harness change. No new endpoint. No new event type.
> **Depends on:**
>
> - ✅ PR 1.2 (per‑chat connector scope persistence + `useConversationConnectors`)
> - ✅ PR 1.2.1 (multi‑tab reconciliation)
> - ✅ PR 2.1 (`<ConnectorsPill>` topbar trigger)
> - ✅ PR 3.4 (`<ConnectorPopover>` structural shell + four‑state vocabulary + `projectConnectors` projection)
> - ✅ PR 4.4 (Settings → Connectors detail; provides the admin surface where brand metadata can be edited if/when we add UI for it)
>
> **Reads alongside:**
>
> - [`pr-3.4-connector-popover.md`](pr-3.4-connector-popover.md) — the structural PR; this PR is the visual + data‑model fidelity follow‑up
> - [`pr-1-2-per-chat-connector-scope.md`](pr-1-2-per-chat-connector-scope.md) — endpoint, frozen‑at‑run‑start contract, audit chain
> - [`apps/frontend/CLAUDE.md`](../../apps/frontend/CLAUDE.md), [`packages/design-system/CLAUDE.md`](../../packages/design-system/CLAUDE.md), [`services/backend/CLAUDE.md`](../../services/backend/CLAUDE.md)

---

## 0 · TL;DR

PR 3.4 shipped the popover skeleton but with **text actions** ("Pause / Resume") and **letter glyphs** (`AppIcon` falls through to `name.charAt(0)` for anything outside a 13‑entry hard‑coded map at [`design-system/src/index.tsx:316`](../../packages/design-system/src/index.tsx#L316)). The Atlas design wants pill **sliders**, brand **favicons**, and a per‑row **scope subtitle** that explains in one human sentence what each connector is allowed to do. Today none of that survives because the contract carrying it doesn't exist.

This PR is purely additive:

| Surface                                                                                                                                                              | Today                                                                                                                                                                                                            | After this PR                                                                                                                                                                 |
| -------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `mcp_servers` schema                                                                                                                                                 | `server_id, display_name, url, transport, auth_*, enabled, required_scopes, …` ([`services/backend/migrations/0001_initial_mcp_skills.sql:8`](../../services/backend/migrations/0001_initial_mcp_skills.sql#L8)) | + `logo_url TEXT NULL` · `brand_color TEXT NULL` · `scopes_summary TEXT NULL` · `default_scopes JSONB NOT NULL DEFAULT '[]'` · `admin_managed BOOLEAN NOT NULL DEFAULT false` |
| `McpServer` API type ([`packages/api-types/src/index.ts:16`](../../packages/api-types/src/index.ts#L16))                                                             | 12 fields, no brand metadata                                                                                                                                                                                     | + `logo_url?` · `brand_color?` · `scopes_summary?` · `default_scopes` · `admin_managed`                                                                                       |
| `<AppIcon>` ([`packages/design-system/src/index.tsx:348`](../../packages/design-system/src/index.tsx#L348))                                                          | `name`/`color` props; renders `BRAND_GLYPHS[slug]` or letter fallback                                                                                                                                            | + `logoUrl?` prop. Render order: `logoUrl` → `BRAND_GLYPHS[slug]` → letter. **Fallback chain unchanged for existing call‑sites.**                                             |
| `<ConnectorPopover>` row ([`apps/frontend/src/features/connectors/ConnectorPopover.tsx:223`](../../apps/frontend/src/features/connectors/ConnectorPopover.tsx#L223)) | `[icon] [name] [stateLabel] [actionLabel]` — text only                                                                                                                                                           | `[favicon] [name (+ inline reason badge)] [scope subtitle] [<Switch> ⏐ <Button>Connect/Enable</Button>]`                                                                      |
| Popover header                                                                                                                                                       | "Connectors" / "Active for this chat" / footer Manage button                                                                                                                                                     | "Searching this chat" / "{active} of {total} connectors active" / **inline `Manage ↗` top‑right**                                                                             |
| Resume target                                                                                                                                                        | `default_scopes` field on `McpServer` (missing → `RESUME_DEFAULT = []` at [`projectConnectors.ts:37`](../../apps/frontend/src/features/connectors/projectConnectors.ts#L37))                                     | server‑supplied `default_scopes` is real; `RESUME_DEFAULT` deleted                                                                                                            |
| Brand assets for catalog connectors                                                                                                                                  | 13‑entry hard‑coded `BRAND_GLYPHS` (letters with brand colours)                                                                                                                                                  | seeded into `mcp_servers` rows via a one‑time **brand catalog backfill** at migration time. `BRAND_GLYPHS` retained as a build‑time fallback only.                            |

LoC estimate: **backend ≈ 110** (1 migration + 1 backfill catalog + 4 fields wired through `McpServerRecord`/`McpServerResponse` + tests) · **backend‑facade ≈ 0** · **api‑types ≈ 30** · **frontend ≈ 180** (`<AppIcon logoUrl>` variant + popover row rewrite + projection update + scope humanizer + tests) · **design‑system ≈ 30** (`<AppIcon>` `logoUrl` variant + img a11y).

The four runtime / agent‑harness invariants from PR 1.2 and PR 3.4 are **explicitly preserved**:

1. **Frozen at run‑start.** `AgentRuntimeContext.connector_scopes` is materialized at run‑create and never mutated mid‑run ([`agent_runtime/execution/contracts.py:258`](../../services/ai-backend/src/agent_runtime/execution/contracts.py#L258)). Toggling in the popover during an active run only affects the _next_ run.
2. **Binary at runtime.** Presence in `connector_scopes` ⇒ loaded into MCP prompt; absence ⇒ skipped. Paused, disconnected, and workspace‑off all collapse to absence — exactly the user's three‑state model. The popover renders four reasons‑for‑off because the _action to fix each_ differs (toggle vs. OAuth vs. ask‑admin).
3. **No new event type.** The mid‑run `mcp_auth_required` flow ([`agent_runtime/capabilities/mcp/middleware/auth_mcp.py:67`](../../services/ai-backend/src/agent_runtime/capabilities/mcp/middleware/auth_mcp.py#L67)) is untouched; users can still add a connector mid‑conversation through the contextual auth card.
4. **Single PATCH endpoint.** `PATCH /v1/agent/conversations/{id}/connectors` (RFC 7396 merge‑patch) is the only write path; the audit chain entry on every toggle ([`agent_runtime/api/service.py:563`](../../services/ai-backend/src/agent_runtime/api/service.py#L563)) is untouched.

---

## 1 · PRD

### 1.1 Problem

Three observable gaps between the design and the shipped popover:

1. **Letter glyphs everywhere.** `AppIcon` checks a 13‑name map at [`design-system/src/index.tsx:316`](../../packages/design-system/src/index.tsx#L316); for everything outside it (custom MCP servers added by admins; connectors that don't happen to be in the map; any future connector) the user sees a coloured circle with the first letter. The Atlas design uses real brand visuals for _every_ row, not just for the curated dozen. The contract carrying brand metadata to the frontend doesn't exist — `McpServerResponse` ([`services/backend/src/backend_app/contracts.py:506`](../../services/backend/src/backend_app/contracts.py#L506)) has no `logo_url` or `brand_color` field.
2. **No scope subtitle.** The popover row reads `Notion · Active · Pause`. The design reads `Notion / Read all pages, write to /Drafts` — a one‑line natural‑language summary of what the connector is allowed to do for this user in this workspace. Today the only thing the frontend has is the raw `required_scopes` JSON ([`McpServerRecord` line 257](../../services/backend/src/backend_app/contracts.py#L257)), which is fine for a permission check but useless as UI copy.
3. **Slider missing; resume target wrong.** Toggling Active → Paused issues `patch({ [server_id]: null })`. Resuming back issues `patch({ [server_id]: row.default_scopes })` ([`ConnectorPopover.tsx:215`](../../apps/frontend/src/features/connectors/ConnectorPopover.tsx#L215)). But `default_scopes` is not on `McpServer`, so `projectConnectors` invents `RESUME_DEFAULT = []` ([`projectConnectors.ts:37`](../../apps/frontend/src/features/connectors/projectConnectors.ts#L37)). Resume from a paused state therefore re‑activates the connector with **no tool scopes** — the connector is "loaded" into the MCP prompt but has no usable tools. PR 1.2's run‑start materializer just produces an empty frozenset for that connector. **This is a real defect**, not just a fidelity miss.

The popover header copy ("Connectors / Active for this chat" vs. design "Searching this chat / 4 of 6 connectors active") is the smallest of the three gaps but worth fixing in the same PR — the copy ties the per‑chat scope to a verb the user understands.

### 1.2 Goals

1. **Brand favicon on every row.** `<AppIcon>` accepts a `logoUrl?: string` prop. When set, the icon renders `<img>`; when unset, the existing `BRAND_GLYPHS` map serves the curated 13; when neither matches, the existing letter‑glyph fallback. Single render path; one extra branch.
2. **Brand metadata on the wire.** `mcp_servers` carries `logo_url TEXT NULL`, `brand_color TEXT NULL`, `scopes_summary TEXT NULL`, `default_scopes JSONB NOT NULL DEFAULT '[]'`, `admin_managed BOOLEAN NOT NULL DEFAULT false`. `McpServerResponse` exposes them. `McpServer` (api‑types) mirrors them. Backfill the curated 13 once via a `brand_catalog.py` constant + an idempotent `UPDATE … WHERE logo_url IS NULL`. Custom MCP servers fall back to letter glyphs until an admin uploads/edits.
3. **Slider visual on toggleable rows.** Use the design‑system `<Switch>` ([`packages/design-system/src/index.tsx:205`](../../packages/design-system/src/index.tsx#L205)) for the Active ↔ Paused row. Disconnected and workspace‑off rows continue to render a `<Button>` (Connect / Enable) — only one of {`<Switch>`, `<Button>`} renders per row, never both.
4. **Scope subtitle on every row.** Below the connector name, render `row.scopes_summary` if present, else a state‑specific fallback ("Not connected — Atlas can't read this app yet." for disconnected; "Disabled by your workspace admin." for workspace‑off; nothing for active/paused with `null` summary).
5. **Resume to last‑known‑good scopes.** `ConnectorRow.default_scopes` is server‑supplied (no client invention). When a row toggles from Paused to Active, the patch payload is `{ [server_id]: row.default_scopes }` and the connector is loaded next run with real tool access.
6. **Header copy + Manage anchor.** "Searching this chat" + "{active} of {total} connectors active" stacked on the left; `Manage ↗` link inline on the right (routes to `/settings#connectors`, existing helper). Footer Manage button is removed.
7. **Three‑state runtime, four‑state UI — explicit.** This PR documents and tests the relationship: at the agent harness boundary, every connector is binary (loaded or not). At the popover boundary, four reasons (active / paused / disconnected / workspace‑off) drive four user actions. PR 3.4 already enforces this in `projectConnectors`; this PR adds an explicit unit test that paused, disconnected, and workspace‑off all collapse to "not in `runtime_connector_scopes()`" output (verified at [`runtime_api/schemas/conversations.py:140`](../../services/ai-backend/src/runtime_api/schemas/conversations.py#L140)).
8. **Zero new dependency.** No `simple-icons`, no `react-icons`, no Iconify. Brand assets live in the database; the UI consumes URLs. We don't need a 5–8 MB icon bundle to ship 13 logos.

### 1.3 Non‑goals

- **Admin UI to edit brand metadata.** Settings → Connectors detail (PR 4.4) owns connector CRUD. v1 of this PR seeds the catalog at migration time and accepts the current `PATCH /v1/mcp/servers/{server_id}` for admin overrides — no new admin form.
- **Auto‑favicon discovery for custom MCP servers.** A workspace admin who adds a custom MCP server URL doesn't get an auto‑fetched favicon in v1. They get the letter fallback until they (or we, in a follow‑up) populate `logo_url`. Fetching `/favicon.ico` server‑side touches the egress / sandbox story and isn't worth the complexity for this PR.
- **Per‑tool scope toggles in the popover.** Still PR 4.4. v1 row toggles the whole connector with `default_scopes`.
- **Disconnect (revoke OAuth) from the popover.** Lives in Settings → Connectors detail. The popover only flips per‑chat scope and triggers the existing OAuth start path.
- **Scope summary auto‑generation from `required_scopes`.** v1 ships a hand‑written summary per catalog connector. A summarizer that consumes the OAuth scope strings and emits English is worth doing later but is its own PR (touches localization).
- **Promotion of `<ConnectorPopover>` into design‑system.** Same rationale as PR 3.4 §1.3 — feature composition stays in `apps/frontend`.

### 1.4 Success criteria

- ✅ Migration `0017_mcp_server_brand_metadata.sql` adds five columns; backfill seeds the 13 catalog connectors with `logo_url`, `brand_color`, `scopes_summary`. `make test` green; `services/backend` pytest green.
- ✅ `McpServerResponse` and `McpServer` (api‑types) carry the new fields; both old and new clients tolerate missing values (`Optional` everywhere).
- ✅ `<AppIcon logoUrl="...">` renders `<img>` with `alt={name}`, `loading="lazy"`, `decoding="async"`, and an `onError` fallback to the existing brand glyph / letter chain.
- ✅ `ConnectorPopover` row renders favicon (or fallback) + name + scope subtitle + (Switch | Button), in that order. Switch toggles call `onToggle(server_id, isActive ? null : row.default_scopes)`. Button renders only for `disconnected` / `workspace_off`.
- ✅ Resume after Pause uses server‑supplied `default_scopes`; an integration test asserts the next run's `connector_scopes` for that server is non‑empty.
- ✅ Header copy is "Searching this chat" + "{n} of {N} connectors active" + inline `Manage ↗`.
- ✅ Custom MCP server (not in catalog) renders letter glyph and "No description available." subtitle — explicit fallback, no `undefined` in the DOM.
- ✅ Streaming handshake unchanged: `runtime_events` schema diff is empty; `RuntimeEventEnvelope` is byte‑identical pre/post merge; `make test` green; ai‑backend pytest green.
- ✅ `npm run typecheck --workspace @enterprise-search/frontend` and `npm run build --workspace @enterprise-search/frontend` pass.
- ✅ A11y: row label includes the scope subtitle ("Notion — currently active. Read all pages, write to /Drafts. Press Space to pause."). Reduced motion preference disables the Switch knob slide.
- ✅ Bundle delta < 4 KB gz (one extra prop branch + a string subtitle).

### 1.5 User stories

| #    | Persona                      | Story                                                                                                                                                                                                                                                      |
| ---- | ---------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| US‑1 | Sarah · Marketing Ops        | I open the connectors popover from the topbar. I see real Notion / Drive / Slack favicons (not initials), each with a one‑line description ("Read all pages, write to /Drafts"). Slack is on; I tap the slider; it slides off; the pill count drops 4 → 3. |
| US‑2 | Sarah · resume after pause   | I paused Notion an hour ago to keep the chat focused on Drive. I tap the slider back on. My next prompt sees Notion's full scopes (`["read","write_drafts"]`), not an empty `[]` like before this PR.                                                      |
| US‑3 | Sarah                        | Salesforce hasn't been connected yet. I see the brand cloud icon, "Not connected — Atlas can't read this app yet.", and a `Connect` pill. Tap → existing OAuth path opens. Popover stays open.                                                             |
| US‑4 | Marcus · workspace member    | GitHub is workspace‑off. I see the brand mark, "Disabled by your workspace admin.", and an `Enable` pill that opens a tooltip "Ask an admin to enable GitHub" — the pill is non‑actionable for non‑admins.                                                 |
| US‑5 | Sarah · admin                | Same GitHub row; tapping `Enable` routes me to `/settings#connectors`.                                                                                                                                                                                     |
| US‑6 | Workspace admin · custom MCP | I added a private MCP server `https://internal-tools.acme/`. The popover shows it with a letter glyph "I" and "No description available." It's still fully functional — slider, OAuth flow, scope toggle all work.                                         |
| US‑7 | Compliance auditor           | I'm reviewing the audit log. Each per‑chat connector toggle still produces one audit row with before/after diff (untouched from PR 1.2). Brand metadata changes (admin edits to `logo_url`) produce a separate audit row keyed on `mcp_server.update`.     |
| US‑8 | Reduced‑motion user          | The Switch knob does not animate; the row state still flips correctly.                                                                                                                                                                                     |

---

## 2 · Spec

### 2.1 Wire — `mcp_servers` and `McpServerResponse`

```sql
-- services/backend/migrations/0017_mcp_server_brand_metadata.sql
BEGIN;

ALTER TABLE mcp_servers
  ADD COLUMN logo_url        TEXT NULL,
  ADD COLUMN brand_color     TEXT NULL,                    -- '#hex' or 'oklch(...)'
  ADD COLUMN scopes_summary  TEXT NULL,
  ADD COLUMN default_scopes  JSONB NOT NULL DEFAULT '[]'::jsonb,
  ADD COLUMN admin_managed   BOOLEAN NOT NULL DEFAULT false;

-- Backfill the curated 13 from the brand catalog. Idempotent: only updates
-- rows where logo_url IS NULL so re-runs don't clobber admin overrides.
UPDATE mcp_servers SET
  logo_url        = COALESCE(logo_url,        c.logo_url),
  brand_color     = COALESCE(brand_color,     c.brand_color),
  scopes_summary  = COALESCE(scopes_summary,  c.scopes_summary),
  default_scopes  = CASE
                      WHEN default_scopes = '[]'::jsonb THEN c.default_scopes
                      ELSE default_scopes
                    END,
  admin_managed   = COALESCE(admin_managed,   c.admin_managed)
FROM (
  -- 13 rows seeded from services/backend/src/backend_app/brand_catalog.py
  -- via a SQL file generated at build time, so the catalog has exactly one source of truth.
  SELECT * FROM (VALUES
    ('notion',     'https://cdn.atlas.local/brand/notion.svg',     '#FFFFFF', 'Read all pages, write to /Drafts',          '["read","write_drafts"]'::jsonb,   false),
    ('drive',      'https://cdn.atlas.local/brand/drive.svg',      '#4285F4', 'Read, comment, no delete',                  '["read","comment"]'::jsonb,        false),
    ('slack',      'https://cdn.atlas.local/brand/slack.svg',      '#4A154B', 'Read public channels, DM with approval',    '["read","dm"]'::jsonb,             false),
    ('salesforce', 'https://cdn.atlas.local/brand/salesforce.svg', '#00A1E0', null,                                        '["read"]'::jsonb,                  false),
    ('confluence', 'https://cdn.atlas.local/brand/confluence.svg', '#172B4D', 'Read all spaces',                           '["read"]'::jsonb,                  false),
    ('github',     'https://cdn.atlas.local/brand/github.svg',     '#0D1117', 'Read repos, no write',                      '["read"]'::jsonb,                  false),
    ('linear',     'https://cdn.atlas.local/brand/linear.svg',     '#5E6AD2', null,                                        '["read"]'::jsonb,                  false),
    ('figma',      'https://cdn.atlas.local/brand/figma.svg',      '#0D0D0D', null,                                        '["read"]'::jsonb,                  false),
    ('snowflake',  'https://cdn.atlas.local/brand/snowflake.svg',  '#29B5E8', null,                                        '["read"]'::jsonb,                  false),
    ('datadog',    'https://cdn.atlas.local/brand/datadog.svg',    '#632CA6', null,                                        '["read"]'::jsonb,                  false),
    ('intercom',   'https://cdn.atlas.local/brand/intercom.svg',   '#1F8DED', null,                                        '["read"]'::jsonb,                  false),
    ('pagerduty',  'https://cdn.atlas.local/brand/pagerduty.svg',  '#06AC38', null,                                        '["read"]'::jsonb,                  false),
    ('web',        null,                                           '#0F172A', null,                                        '["search"]'::jsonb,                false)
  ) AS t(slug, logo_url, brand_color, scopes_summary, default_scopes, admin_managed)
) AS c
WHERE mcp_servers.name = c.slug;

COMMIT;
```

`McpServerResponse` ([`services/backend/src/backend_app/contracts.py:506`](../../services/backend/src/backend_app/contracts.py#L506)) — additive only:

```python
class McpServerResponse(BaseModel):
    # … existing 12 fields unchanged …
    logo_url: str | None = None
    brand_color: str | None = None
    scopes_summary: str | None = None
    default_scopes: tuple[str, ...] = ()
    admin_managed: bool = False
```

`McpServer` ([`packages/api-types/src/index.ts:16`](../../packages/api-types/src/index.ts#L16)) — same fields, mirrored:

```ts
export interface McpServer {
  // … existing 12 fields unchanged …
  logo_url?: string | null;
  brand_color?: string | null;
  scopes_summary?: string | null;
  default_scopes: readonly string[]; // never undefined; defaults to []
  admin_managed: boolean; // defaults to false
}
```

### 2.2 Wire — explicit zero on the run/streaming side

| Surface                                                                                                                                                                   | Touched?                                                                                                      |
| ------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------- |
| `PATCH /v1/agent/conversations/{id}/connectors`                                                                                                                           | **No.** Same endpoint, same RFC 7396 semantics, same audit chain.                                             |
| `Conversation.enabled_connectors`                                                                                                                                         | **No.**                                                                                                       |
| `runtime_events` schema, `RuntimeEventEnvelope`, SSE handshake                                                                                                            | **No.**                                                                                                       |
| `mcp_auth_required` event                                                                                                                                                 | **No.** Mid‑run discovery / blocking auth path is byte‑identical.                                             |
| `runtime_worker` job loop, `chatModel/eventReducer.ts`                                                                                                                    | **No.**                                                                                                       |
| `AgentRuntimeContext.connector_scopes`                                                                                                                                    | **No.** Already binary; PR 1.2 contract unchanged.                                                            |
| `runtime_connector_scopes()` projection ([`schemas/conversations.py:140`](../../services/ai-backend/src/runtime_api/schemas/conversations.py#L140))                       | **No.** Already drops `null` (paused) entries; this PR adds an explicit unit test for the workspace‑off case. |
| `ToolPermissionChecker._is_connector_scope_authorized` ([`tools/permissions.py:200`](../../services/ai-backend/src/agent_runtime/capabilities/tools/permissions.py#L200)) | **No.**                                                                                                       |

The only write is the migration. The only API additions are five nullable fields. The only frontend addition is one prop on `<AppIcon>` and one row layout change.

### 2.3 Components — what we add, what we reuse

| Component                                      | Source                                                                                                                                    | Notes                                                                                                                                                               |
| ---------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------ |
| `<AppIcon logoUrl?>` (extend)                  | [`packages/design-system/src/index.tsx:348`](../../packages/design-system/src/index.tsx#L348)                                             | Add `logoUrl?: string` prop. Render order: `logoUrl` (img) → `BRAND_GLYPHS[slug]` (existing) → letter (existing). `onError` falls through.                          |
| `<Switch>` (existing)                          | [`packages/design-system/src/index.tsx:205`](../../packages/design-system/src/index.tsx#L205)                                             | Used by toggleable rows in the popover. **No design‑system change.**                                                                                                |
| `<ConnectorPopover>` row layout (rewrite)      | [`apps/frontend/src/features/connectors/ConnectorPopover.tsx:223`](../../apps/frontend/src/features/connectors/ConnectorPopover.tsx#L223) | Replace `[icon][name][stateLabel][actionLabel]` with `[favicon][name + reason][subtitle]Switch                                                                      | Button]`.                                        |
| `projectConnectors` (extend)                   | [`apps/frontend/src/features/connectors/projectConnectors.ts:1`](../../apps/frontend/src/features/connectors/projectConnectors.ts#L1)     | Add `logo_url`, `brand_color`, `scopes_summary` to `ConnectorRow`. Consume server‑supplied `default_scopes`; **delete `RESUME_DEFAULT`**.                           |
| `brand_catalog.py` (NEW; backend)              | `services/backend/src/backend_app/brand_catalog.py`                                                                                       | 13 rows, one source of truth. Used by the migration generator and by `create_mcp_server` to seed catalog defaults when an admin installs from the catalog. ~80 LOC. |
| `<ConnectorsPill>` (existing PR 2.1)           | unchanged                                                                                                                                 | Stack of mini favicons reads from `row.logo_url ?? glyph` via `<AppIcon>`.                                                                                          |
| `<ComposerConnectorsButton>` (existing PR 3.4) | unchanged                                                                                                                                 | Same.                                                                                                                                                               |
| `useConversationConnectors` (existing PR 1.2)  | unchanged                                                                                                                                 | The hook already round‑trips `Record<string, readonly string[]                                                                                                      | null>` — we're just supplying real defaults now. |

### 2.4 Frontend — `<AppIcon logoUrl>` semantics

```tsx
// packages/design-system/src/index.tsx
export function AppIcon({
  name,
  color,
  logoUrl,
  size = "sm",
  className,
  ...props
}: HTMLAttributes<HTMLSpanElement> & {
  name: string;
  color?: string;
  logoUrl?: string | null; // NEW
  size?: "sm" | "lg";
}): ReactElement {
  const slug = name.toLowerCase();
  const brand = !color ? BRAND_GLYPHS[slug] : undefined;

  // Render order: logoUrl → BRAND_GLYPHS → letter
  if (logoUrl) {
    return (
      <span
        className={classNames(
          "ui-app-icon",
          "ui-app-icon--img",
          size === "lg" && "ui-app-icon--lg",
          className,
        )}
        style={brand?.bg ? { background: brand.bg } : undefined}
        aria-label={name}
        {...props}
      >
        <img
          src={logoUrl}
          alt="" // decorative; aria-label on parent
          loading="lazy"
          decoding="async"
          referrerPolicy="no-referrer"
          onError={(e) => {
            // Fall through to letter — preserve existing visual on broken URL.
            (e.currentTarget as HTMLImageElement).style.display = "none";
            const fallback = e.currentTarget.parentElement;
            if (fallback) fallback.classList.add("ui-app-icon--img-failed");
          }}
        />
      </span>
    );
  }
  if (brand) {
    /* existing branch — unchanged */
  }
  /* existing letter fallback — unchanged */
}
```

Existing call‑sites (`<AppIcon name="notion" />`) are byte‑identical because `logoUrl` is undefined.

### 2.5 Frontend — popover row layout

```tsx
// ConnectorPopover.tsx — Row component (rewritten)
function Row({
  row,
  readOnly,
  onToggle,
  onConnect,
  onEnableInSettings,
}: RowProps) {
  const isToggle = row.state === "active" || row.state === "paused";
  const isActive = row.state === "active";
  const isDisconnected = row.state === "disconnected";

  const onActivate = () => {
    if (readOnly) return;
    if (isToggle) onToggle(row.server_id, isActive ? null : row.default_scopes);
    else if (isDisconnected) onConnect(row.server_id);
    else onEnableInSettings(row.server_id);
  };

  const subtitle =
    row.scopes_summary ?? FALLBACK_SUBTITLE_BY_STATE[row.state] ?? null;

  return (
    <div
      data-row="true"
      data-state={row.state}
      data-disabled={readOnly || undefined}
      className="atlas-connector-row"
      role={isToggle ? "menuitemcheckbox" : "menuitem"}
      aria-checked={isToggle ? isActive : undefined}
      aria-label={
        `${row.display_name} — ${LABEL_BY_STATE[row.state]}.` +
        (subtitle ? ` ${subtitle}` : "") +
        (isToggle ? ` Press Space to ${isActive ? "pause" : "resume"}.` : "")
      }
      tabIndex={-1}
      onKeyDown={(e) => {
        if (e.key === " " || e.key === "Enter") {
          e.preventDefault();
          onActivate();
        }
      }}
    >
      <AppIcon
        name={row.display_name}
        logoUrl={row.logo_url ?? undefined}
        className="atlas-connector-row__glyph"
      />
      <div className="atlas-connector-row__col">
        <div className="atlas-connector-row__title">
          <span className="atlas-connector-row__name">{row.display_name}</span>
          {row.state === "disconnected" && (
            <span className="atlas-connector-row__badge">Not connected</span>
          )}
          {row.state === "workspace_off" && (
            <span className="atlas-connector-row__badge">Off · Workspace</span>
          )}
        </div>
        {subtitle && (
          <div className="atlas-connector-row__subtitle">{subtitle}</div>
        )}
      </div>
      {isToggle ? (
        <Switch
          checked={isActive}
          onChange={onActivate}
          aria-label={isActive ? "Pause connector" : "Resume connector"}
          disabled={readOnly}
        />
      ) : (
        <Button
          variant={isDisconnected ? "primary" : "ghost"}
          size="sm"
          onClick={onActivate}
          disabled={
            readOnly ||
            (row.state === "workspace_off" && !row.workspace_admin_managed)
          }
        >
          {isDisconnected ? "Connect" : "Enable"}
        </Button>
      )}
    </div>
  );
}

const FALLBACK_SUBTITLE_BY_STATE: Record<ConnectorRow["state"], string | null> =
  {
    active: null,
    paused: null,
    disconnected: "Not connected — Atlas can't read this app yet.",
    workspace_off: "Disabled by your workspace admin.",
  };
```

The `onClick` was removed from the outer `<div>` because the row is no longer a single‑button surface — keyboard activation goes through `onKeyDown`; mouse activation is on the Switch / Button directly. Per WAI‑ARIA, a `menuitemcheckbox` doesn't need to be a `<button>` element; the role is what matters.

### 2.6 Header copy + Manage anchor

```tsx
<div className="atlas-connector-popover__head">
  <div>
    <span className="atlas-connector-popover__title">Searching this chat</span>
    <span className="atlas-connector-popover__sub">
      {activeCount(rows)} of {rows.length} connectors active
    </span>
  </div>
  <Button
    type="button"
    variant="ghost"
    size="sm"
    className="atlas-connector-popover__manage"
    onClick={() => {
      onManage();
      onClose();
    }}
  >
    Manage <CaretIcon size={12} />
  </Button>
</div>
```

The footer `Manage in Settings →` button is removed. The header is the only `Manage` affordance.

### 2.7 Three‑state runtime, four‑state UI — explicit contract

The user's three‑state proposal (Disabled / Authenticated / Not Authenticated) is **identical to the current runtime contract**. The popover renders four because users need four different actions:

| Reason a connector isn't loaded | UI state        | User action available         | Runtime view ([`runtime_connector_scopes()`](../../services/ai-backend/src/runtime_api/schemas/conversations.py#L140)) |
| ------------------------------- | --------------- | ----------------------------- | ---------------------------------------------------------------------------------------------------------------------- |
| User paused for this chat       | `paused`        | toggle Switch back on         | absent (filtered: `null` value)                                                                                        |
| User hasn't OAuthed yet         | `disconnected`  | tap `Connect` → OAuth         | absent (filtered: server `auth_state !== "authenticated"`)                                                             |
| Admin disabled workspace‑wide   | `workspace_off` | ask admin / route to settings | absent (filtered: server `enabled === false`)                                                                          |
| —                               | `active`        | toggle Switch off             | present (scopes from `enabled_connectors[server_id]`)                                                                  |

A unit test at [`runtime_api/schemas/conversations.test.py`](../../services/ai-backend/tests/unit/runtime_api/schemas/test_conversations.py) (NEW) asserts all three "absent" cases collapse identically.

### 2.8 Toggle semantics — change from PR 3.4

| Click on …             | PR 3.4 behaviour                                                               | PR 3.4.1 behaviour                                                                                                             |
| ---------------------- | ------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------ |
| Switch on Active row   | (button click) `patch({ [id]: null })` → Paused.                               | Same patch payload; visual is now Switch knob slide. ARIA flips `checked`/`aria-checked`.                                      |
| Switch on Paused row   | `patch({ [id]: row.default_scopes })` → Active. **`default_scopes` was `[]`.** | `patch({ [id]: row.default_scopes })` → Active. **`default_scopes` is server‑supplied; resume actually restores tool access.** |
| Disconnected → Connect | `connectors.authenticate(server_id)`. Popover stays open.                      | Identical.                                                                                                                     |
| Workspace‑off → Enable | Admin: route to `/settings#connectors`. Member: tooltip "ask admin".           | Identical, plus `<Button disabled>` state for non‑admins (tooltip still works).                                                |
| Manage link            | Footer button.                                                                 | Header link, top‑right.                                                                                                        |

### 2.9 Streaming impact — explicit zero (re‑stated)

Per PR 3.4 §2.7: **"Toggles affect the next run only."** This PR does not change that. The active run's `connector_scopes` snapshot was captured at run‑create from `enabled_connectors`; PR 3.4.1 changes neither the snapshot nor the projection. There is no code path in this PR that emits, consumes, or schemas a streaming event.

Re‑stated for the audit:

| Subsystem                              | Touched?                                                                                                                             |
| -------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------ |
| `runtime_events` schema                | **No.**                                                                                                                              |
| `RuntimeEventEnvelope` (Pydantic + TS) | **No.**                                                                                                                              |
| SSE handshake                          | **No.**                                                                                                                              |
| `runtime_worker` claim/dispatch loop   | **No.**                                                                                                                              |
| `chatModel/eventReducer.ts`            | **No.**                                                                                                                              |
| Capabilities middleware                | **No.**                                                                                                                              |
| `mcp_auth_required` payload            | **No.** Optional `logo_url` could ride here in a follow‑up so the in‑thread auth card has the brand mark — out of scope for this PR. |
| Audit chain                            | **No.**                                                                                                                              |

### 2.10 Permissions

Same matrix as PR 3.4 §2.8. New `admin_managed` field is read by the popover to decide whether the "Enable" button is even active for non‑admins (today the popover shows the tooltip; this PR also disables the button for non‑admins of admin‑managed servers).

### 2.11 Error semantics

Same matrix as PR 3.4 §2.9, plus:

| Condition                              | Behaviour                                                                                                                                                                                                |
| -------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `logo_url` 404 / network error         | `<img onError>` falls through to brand glyph or letter; row continues to function. No console spam (suppressed by the fallback class transition).                                                        |
| `logo_url` is on a CSP‑blocked origin  | Same as above — `onError` fires.                                                                                                                                                                         |
| `default_scopes` is empty for a server | Resume re‑activates the connector with `[]` scopes — same as today's behaviour but documented now: this is a misconfiguration the admin must fix in Settings → Connectors. The frontend does not invent. |
| `scopes_summary` is null               | Subtitle line is hidden for `active` / `paused`; falls back to state copy for `disconnected` / `workspace_off`. No empty `<div>`s.                                                                       |
| Brand catalog backfill fails partially | Migration is idempotent + transactional. A failed UPDATE rolls back the whole migration; the column adds (which are non‑destructive) succeed and rows show letter glyphs until re‑run.                   |

### 2.12 Accessibility

- Switch carries `aria-label` ("Pause connector" / "Resume connector"); design‑system Switch already exposes the right ARIA contract.
- Connect / Enable buttons carry the explicit verb in their text — no icon‑only buttons.
- Image is `alt=""` (decorative); the connector name is on the parent label. Screen readers announce "Notion. Active. Read all pages, write to /Drafts. Press Space to pause."
- `prefers-reduced-motion` disables the Switch knob slide and any popover open/close transition (already enforced by design‑system).
- The 4‑reason visual encoding is **never** the only signal — every off row has subtitle copy or a button label.

### 2.13 What we explicitly do NOT add

- **No `simple-icons` or `react-icons`.** The catalog is 13 connectors. Bundling 3,000 SVGs (or even tree‑shaking 13 of them) costs more in build complexity than serving 13 SVGs from the CDN already configured by the workspace admin.
- **No `@iconify/react`.** Same.
- **No favicon‑auto‑discovery service.** Custom MCP servers default to letter glyphs; admins paste a `logo_url` if they want one (Settings → Connectors detail surface, owned by PR 4.4).
- **No client‑side caching of `mcp_servers`.** Same as PR 3.4 §2.11 — the list is small and refreshed by `useConnectors()` on mount.
- **No new design‑system primitive.** `<AppIcon>` gains one prop; that's it.
- **No new endpoint.** Brand metadata rides existing `GET /v1/mcp/servers` and existing `PATCH /v1/mcp/servers/{server_id}` (admin update path).

---

## 3 · Architecture

### 3.1 Data flow (post‑merge)

```
mcp_servers table  (5 new columns)
       │
       │ GET /v1/mcp/servers   (existing route)
       ▼
McpServerResponse  (5 new optional fields)
       │
       │ backend-facade proxy  (no change)
       ▼
McpServer          (5 new fields, api-types)
       │
       │ useConnectors()       (existing hook)
       ▼
projectConnectors(servers, scopes, viewer)
       │  consumes server-supplied default_scopes (no more RESUME_DEFAULT)
       │  copies logo_url / brand_color / scopes_summary onto ConnectorRow
       ▼
ConnectorRow[]
       │
       ▼
<ConnectorPopover>
       │
       ├─ <AppIcon logoUrl={row.logo_url} name={row.display_name} />
       │   └─ <img onError → BRAND_GLYPHS[slug] → letter>
       │
       ├─ row.scopes_summary  →  subtitle line
       │
       └─ <Switch> (active/paused) ⏐ <Button>Connect/Enable</Button>
              │
              ▼
       onToggle / onConnect / onEnableInSettings
              │
              ▼
       useConversationConnectors.patch(...)        (PR 1.2; unchanged)
              │
              ▼
       PATCH /v1/agent/conversations/{id}/connectors  (PR 1.2; unchanged)
              │
              ▼
       conversation.enabled_connectors             (PR 1.2; unchanged)
              │  ← frozen at next run-create
              ▼
       AgentRuntimeContext.connector_scopes        (PR 1.2; unchanged)
              │
              ▼
       MCP loader filters tools by scopes          (existing; unchanged)
```

### 3.2 The four invariants — visualized

```
┌──────────────────────────── runtime ────────────────────────────┐
│                                                                  │
│   AgentRuntimeContext.connector_scopes:                          │
│     {"notion": ["read","write_drafts"], "drive": ["read"]}       │
│                                                                  │
│   ─ binary: present ⇒ loaded; absent ⇒ skipped                   │
│   ─ frozen at run-create from conversation.enabled_connectors    │
│   ─ paused / disconnected / workspace_off all collapse here      │
│                                                                  │
└──────────────────────────────────┬───────────────────────────────┘
                                   │ projection (PR 1.2 + PR 3.4)
                                   ▼
┌────────────────────────────── popover ──────────────────────────┐
│                                                                  │
│   four reasons-for-off, four user actions:                       │
│                                                                  │
│   active          Switch on   ──  toggle off                     │
│   paused          Switch off  ──  toggle on                      │
│   disconnected    Button      ──  Connect (OAuth)                │
│   workspace_off   Button      ──  Enable (admin) / tooltip       │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
```

### 3.3 Sequence — pause then resume Notion (post‑merge)

```
Sarah                  Switch                  Popover                 PR 1.2 hook            ai-backend             agent harness
  │                       │                       │                        │                        │                         │
  │ click Switch (on→off) │                       │                        │                        │                         │
  │ ────────────────────► │                       │                        │                        │                         │
  │                       │ onChange()            │                        │                        │                         │
  │                       │ ─────────────────────►│                        │                        │                         │
  │                       │                       │ onToggle("notion",null)│                        │                         │
  │                       │                       │ ──────────────────────►│ patch({notion: null})  │                         │
  │                       │                       │                        │ ──────────────────────►│ PATCH /…/connectors     │
  │                       │                       │                        │                        │ enabled_connectors      │
  │                       │                       │                        │                        │   = {drive: ["read"]}   │
  │                       │                       │                        │                        │ audit row written       │
  │                       │                       │                        │                        │                         │
  │ click Switch (off→on) │                       │                        │                        │                         │
  │ ────────────────────► │                       │                        │                        │                         │
  │                       │                       │ onToggle("notion",     │                        │                         │
  │                       │                       │   ["read","write_drafts"]) ── server-supplied   │                         │
  │                       │                       │   default_scopes (NEW: no longer empty)         │                         │
  │                       │                       │ ──────────────────────►│ patch({notion: [...]}) │                         │
  │                       │                       │                        │ ──────────────────────►│ PATCH /…/connectors     │
  │                       │                       │                        │                        │ enabled_connectors      │
  │                       │                       │                        │                        │ = {drive: [...],        │
  │                       │                       │                        │                        │    notion: [...]}       │
  │ send next message     │                       │                        │                        │                         │
  │ ────────────────────────────────────────────────────────────────────────────────────────────────►│ run created;            │
  │                                                                                                  │ context.connector_scopes│
  │                                                                                                  │   = {drive, notion}     │
  │                                                                                                  │ Notion tools loaded;    │
  │                                                                                                  │ next prompt has access  │
```

The post‑merge "send next message" path includes Notion tools because `default_scopes` was real, not `[]`.

### 3.4 DRY — what's reused vs. what's added

| Concern                    | Reuse                                                       | Add                                                                                                               |
| -------------------------- | ----------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------- |
| Persistence                | PR 1.2 endpoint, audit chain                                | —                                                                                                                 |
| Hook                       | `useConversationConnectors` (PR 1.2)                        | —                                                                                                                 |
| Projection                 | `projectConnectors` (PR 3.4)                                | extend with `logo_url`, `brand_color`, `scopes_summary`; consume server `default_scopes`; delete `RESUME_DEFAULT` |
| Popover shell              | `<Menu>` (design‑system)                                    | —                                                                                                                 |
| Toggle visual              | `<Switch>` (design‑system, existing)                        | —                                                                                                                 |
| Connector glyph            | `<AppIcon>` (design‑system, existing)                       | one new prop (`logoUrl`) + one new branch                                                                         |
| OAuth path                 | `connectors.authenticate(serverId)`                         | —                                                                                                                 |
| Settings routing           | `applyAppRoute('settings', 'connectors')`                   | —                                                                                                                 |
| Brand metadata persistence | —                                                           | 5 columns on `mcp_servers` + 5 fields on `McpServerResponse` + 5 fields on `McpServer`                            |
| Brand catalog              | —                                                           | `brand_catalog.py` + SQL backfill (one source of truth)                                                           |
| Streaming / harness        | runtime context, MCP loader, MCP middleware (all unchanged) | —                                                                                                                 |

Net new code: **backend ≈ 110 LOC · api‑types ≈ 30 LOC · frontend ≈ 180 LOC · design‑system ≈ 30 LOC**.

### 3.5 Dependency survey

- **`simple-icons`** (npm, ~7 MB tree‑shakable to ~13 KB for our 13 brands, CC0). **Considered. Rejected.** The brand assets need to live in the database anyway (custom MCP servers; admin overrides; localized variants). Bundling icons into JS pushes the source‑of‑truth into the wrong layer. Serving 13 SVGs from a CDN is the right shape.
- **`react-icons`** (npm, similar; mixed licenses). Same rejection rationale; license review for every icon adds friction.
- **`@iconify/react`** (npm, lazy‑loads from a CDN). The lazy fetch is identical to `<img src=logo_url>`; we don't need an extra abstraction.
- **`@radix-ui/react-popover`** (already in `apps/frontend/package.json`). Used by design‑system `<Menu>`; no change.
- **`@floating-ui/react`** — same rejection as PR 3.4 §3.5.
- **Self‑hosted SVGs.** Recommended. The 13 catalog connectors get SVGs at `cdn.atlas.local/brand/{slug}.svg` — admins of self‑hosted deploys point their CDN URL via `BRAND_CDN_BASE` env var (already used by branding for the workspace logo). License: each brand provides downloadable SVGs under their press kit terms; Anthropic Brand Studio holds the curated set under an internal tracking ticket.

We add **nothing** from npm.

### 3.6 Edge cases

| Case                                                                                                              | Behaviour                                                                                                                                                                                                              |
| ----------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Admin uploads invalid `logo_url` (404, CSP‑blocked, malformed)                                                    | `<img onError>` falls through to glyph chain. No layout jank because `<span class="ui-app-icon">` is the same width regardless.                                                                                        |
| Admin updates `scopes_summary` mid‑popover                                                                        | Multi‑tab reconciliation (PR 1.2.1) refetches conversation but **not** mcp_servers. Refresh of `useConnectors()` fires on mount; subtitle stays stale until next mount. Acceptable for v1 — not a security concern.    |
| Custom MCP server (`mcp_servers.name` not in catalog)                                                             | `logo_url=NULL`, `scopes_summary=NULL`, `default_scopes=[]`. UI: letter glyph + "No description available." + Switch / Connect button. Functional.                                                                     |
| `default_scopes` is `["read"]` but the server actually requires `["read","write"]` for the tools the user invokes | Server enforces — tool fails with permission error mid‑run; user sees an in‑thread error card. Pre‑existing behaviour; no change.                                                                                      |
| Workspace admin disables a previously‑active connector                                                            | On next conversation refetch, `server.enabled=false`; row re‑projects to `workspace_off`; toggle becomes "Enable in Settings"; existing per‑chat scope is preserved in `enabled_connectors` (re‑enabling restores it). |
| User on slow connection, Switch click → patch in flight, second click                                             | Optimistic UI flips immediately on first click; PR 1.2 hook serializes patches by version. Second click queues; final state is consistent.                                                                             |
| `prefers-color-scheme: light`                                                                                     | `brand_color` value applied as background; brand SVGs are mono‑colour or transparent‑bg; renders correctly. CSS uses tokens for foreground; the brand color is the chip background only.                               |

### 3.7 Test plan

**Backend**

- `tests/unit/backend_app/migrations/test_0017_brand_metadata.py` — fresh DB: columns added, defaults sane. Existing rows: idempotent UPDATE applies catalog values; subsequent run is a no‑op. Admin override (`logo_url` set manually) is preserved across re‑runs.
- `tests/unit/backend_app/test_mcp_server_responses.py` — `McpServerResponse` round‑trips the 5 new fields; null values serialize as `null`, not omitted.
- `tests/unit/backend_app/test_brand_catalog.py` — the catalog has exactly 13 entries; each has `logo_url` (or explicit `null` for `web`), `brand_color`, `default_scopes`. Schema validation per row.

**Frontend**

- `projectConnectors.test.ts` — extend the table tests to cover all `logo_url` × `scopes_summary` × `default_scopes` × `admin_managed` permutations. Specifically: paused with non‑empty `default_scopes` projects to a row whose Switch‑resume payload is the server‑supplied list, not `[]`.
- `AppIcon.test.tsx` — three branches: `logoUrl` set ⇒ `<img>`; `logoUrl` unset + name in `BRAND_GLYPHS` ⇒ glyph; neither ⇒ letter. `onError` toggles the failure class.
- `ConnectorPopover.test.tsx` — extend PR 3.4's tests:
  - Switch renders for `active`/`paused`; Button renders for `disconnected`/`workspace_off`.
  - Subtitle renders `scopes_summary` if present; falls back per state.
  - Header copy is "Searching this chat" + "{n} of {N} connectors active" + Manage link top‑right.
  - Toggling a paused row with `default_scopes=["read","write"]` calls `onToggle(id, ["read","write"])`.
- `runtime_connector_scopes` parity — assert paused, disconnected, workspace‑off all produce identical "absent from `connector_scopes` dict" output.

**Cross‑service smoke**

- `make test` extension: pause Notion in conversation A; send next prompt; assert run's `runtime_context_json.connector_scopes` does **not** contain `notion`. Resume; send next prompt; assert it does — and assert the scope set equals `default_scopes` from the server, not `[]`.

### 3.8 Rollout

- **Migration first.** PR ships in two commits: (a) backend migration + brand catalog + 5 fields; (b) frontend popover row redesign + `<AppIcon logoUrl>` variant. The second commit is purely additive on top of the first; reverting only the second keeps brand metadata in the DB without harm.
- **Backout.** Revert PR. Brand columns remain in `mcp_servers` (additive only; no data lost). `<AppIcon>` reverts to glyph/letter chain. `RESUME_DEFAULT = []` returns. PR 3.4 popover continues to function with text actions.
- **Compat.** Servers running old code talk to backend with new fields without issue (Pydantic ignores unknown fields on response; clients handle nulls). Clients running old code see new backend fields and discard them.
- **No flag.** The user‑visible change is incremental and the failure modes are graceful (a missing `logo_url` falls through to the existing glyph). A feature flag would gate the entire popover row redesign on something the user doesn't notice.

### 3.9 Open questions

1. **Should `mcp_auth_required` events carry `logo_url` so the in‑thread auth card has the brand mark too?** Worth doing — same database row, same projection — but it's a separate PR (touches event serialization). Tracked as a follow‑up; explicitly out of scope here.
2. **Auto‑favicon discovery for custom MCP servers.** A separate PR (touches egress / sandbox / caching). v1 admins paste a URL.
3. **Admin UI to edit brand metadata.** Already PR 4.4's territory (Settings → Connectors detail). v1 of _this_ PR ships only the backfill catalog; admin overrides ride the existing `PATCH /v1/mcp/servers/{server_id}`.
4. **Localized `scopes_summary`.** Hand‑written English in v1. Localization rides PR 4.1's `locale` work once that's wired into the backend.
5. **Should we add a search input to the popover for workspaces with > 12 connectors?** Same as PR 3.4 §3.9 — defer until needed.

---

## 4 · Acceptance checklist

- [ ] `services/backend/migrations/0017_mcp_server_brand_metadata.sql` ships — 5 nullable / defaulted columns + idempotent backfill from `brand_catalog.py`.
- [ ] `services/backend/src/backend_app/brand_catalog.py` ships with 13 catalog entries; tested.
- [ ] `McpServerResponse` exposes `logo_url`, `brand_color`, `scopes_summary`, `default_scopes`, `admin_managed`.
- [ ] `packages/api-types/src/index.ts` `McpServer` mirrors the 5 fields.
- [ ] `<AppIcon logoUrl>` ships in design‑system; render order `logoUrl` → glyph → letter; `onError` falls through; existing call‑sites unchanged.
- [ ] `apps/frontend/src/features/connectors/projectConnectors.ts` consumes server `default_scopes`; `RESUME_DEFAULT` deleted; `ConnectorRow` carries `logo_url`, `brand_color`, `scopes_summary`.
- [ ] `apps/frontend/src/features/connectors/ConnectorPopover.tsx` rewritten:
  - row layout `[favicon] [name + reason badge] [scope subtitle] [Switch | Button]`
  - header `Searching this chat / {n} of {N} connectors active / Manage ↗`
  - footer Manage button removed
  - keyboard contract preserved (Space toggles Switch; Enter activates Button)
- [ ] No new `RuntimeApiEventType`. Pydantic schemas of streaming events unchanged. `RuntimeEventEnvelope` byte‑identical pre/post merge.
- [ ] No new endpoint. Facade route table unchanged.
- [ ] No new design‑system primitive (one new prop on existing `<AppIcon>`).
- [ ] No npm dependency added.
- [ ] `make test` green; `services/backend` pytest green; `services/ai-backend` pytest green; `npm run typecheck --workspace @enterprise-search/frontend` and `npm run build --workspace @enterprise-search/frontend` pass.
- [ ] Streaming integration test asserts paused / disconnected / workspace‑off all collapse to "absent from `connector_scopes`" at run‑create.
- [ ] Resume‑after‑pause integration test asserts the next run's `connector_scopes[server_id]` equals server `default_scopes`, not `[]`.

---

## 5 · References

- [`apps/frontend/src/features/connectors/ConnectorPopover.tsx`](../../apps/frontend/src/features/connectors/ConnectorPopover.tsx) — current popover; row rewritten by this PR.
- [`apps/frontend/src/features/connectors/projectConnectors.ts`](../../apps/frontend/src/features/connectors/projectConnectors.ts) — projection; consumes new fields; deletes `RESUME_DEFAULT`.
- [`apps/frontend/src/features/connectors/useConversationConnectors.ts`](../../apps/frontend/src/features/connectors/useConversationConnectors.ts) — PR 1.2 hook; unchanged.
- [`packages/design-system/src/index.tsx`](../../packages/design-system/src/index.tsx) — `<AppIcon>` (line 348), `<Switch>` (line 205), `<Menu>` (line 512), `BRAND_GLYPHS` (line 316).
- [`packages/api-types/src/index.ts`](../../packages/api-types/src/index.ts) — `McpServer` (line 16); 5 new fields.
- [`services/backend/src/backend_app/contracts.py`](../../services/backend/src/backend_app/contracts.py) — `McpServerRecord` (line 257), `McpServerResponse` (line 506); 5 new fields.
- [`services/backend/migrations/0001_initial_mcp_skills.sql`](../../services/backend/migrations/0001_initial_mcp_skills.sql) — `mcp_servers` table; this PR adds migration `0017`.
- [`services/ai-backend/src/agent_runtime/execution/contracts.py`](../../services/ai-backend/src/agent_runtime/execution/contracts.py) — `AgentRuntimeContext.connector_scopes` (line 258); unchanged.
- [`services/ai-backend/src/runtime_api/schemas/conversations.py`](../../services/ai-backend/src/runtime_api/schemas/conversations.py) — `runtime_connector_scopes()` (line 140); unchanged; new explicit unit test.
- [`services/ai-backend/src/agent_runtime/capabilities/mcp/middleware/auth_mcp.py`](../../services/ai-backend/src/agent_runtime/capabilities/mcp/middleware/auth_mcp.py) — `mcp_auth_required` event (line 67); unchanged.
- [`docs/new-design/pr-3.4-connector-popover.md`](pr-3.4-connector-popover.md) — structural PR; this is its visual + data‑model fidelity follow‑up.
- [`docs/new-design/pr-1-2-per-chat-connector-scope.md`](pr-1-2-per-chat-connector-scope.md) — endpoint, frozen‑at‑run‑start contract, audit chain.
- Atlas Design Doc — §"ConnectorPopover" + screenshot (per‑chat connector toggles with sliders + brand favicons + scope subtitles).
- WAI‑ARIA — [Menu pattern](https://www.w3.org/WAI/ARIA/apg/patterns/menubar/), [Switch pattern](https://www.w3.org/WAI/ARIA/apg/patterns/switch/).
