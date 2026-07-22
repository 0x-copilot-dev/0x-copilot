# PRD-11 — Tools surface: identity tile, desktop connect flow, row-list layout

## Problem

The Tools destination is the one screen where a user answers "what can this agent reach,
and how far can it act?". Today it answers badly.

Every connected tool renders as an **anonymous card**. There is no logo, no coloured
initial, nothing — just bold text. Gmail, Safe{Wallet} and a custom MCP server are
visually identical, so scanning six connectors takes six reads instead of one glance.
The design specifies a 30×30 identity tile on every row; the live surface renders none,
on either host.

The six tools are laid out as a **3-up card grid** — fat bordered tiles, one status pill
each reading "CONNECTED" on a list where every row is connected, and an "Agent access"
label repeated six times. The design is a single hairline row list: one bordered card,
six 61px rows, a 9.5px mono `Connected · 6` eyebrow. The live surface instead grew a
22px `Tools` page title plus a Connected/Available/Custom tab strip — and on web that tab
strip is rendered **twice**, once in a 240px aside and once above the grid.

The access control lies about itself. The design's segmented control signals selection
with a quiet background shift only; live draws an **accent ring** around the selected
segment and bumps its font weight, so "Read & act" — the most permissive mode — looks
like a highlighted call-to-action rather than a state.

And on the desktop app the primary CTA is a dead end. "Connect a tool" does not open a
connect dialog; it flips a tab. There is no catalog picker, no permission step, and no
way at all to add a custom MCP server — that capability exists only on web, even though
desktop already owns a working system-browser OAuth broker in Electron main.

## Evidence

Every row below was opened and read at the cited line. Where the brief and the code
disagree, the code is recorded and the claim is marked **DISPUTED**.

| Claim                                                                                 | File:line                                                                                                                                                                                                                                         | What the code actually does                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| ------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| The identity-tile primitive already exists and is production-grade                    | `packages/design-system/src/index.tsx:492-581`                                                                                                                                                                                                    | CONFIRMED. `AppIcon` = `logoUrl` `<img>` with `onError` → brand glyph map (`BRAND_GLYPHS`, `:~400-490`) → `name.charAt(0)` letter fallback. Already imported _inside_ chat-surface at `packages/chat-surface/src/citations/SourceFavicon.tsx:14` and `citations/MessageSourcesStrip.tsx:19`, so there is no package-boundary obstacle.                                                                                                                                                                                                                                            |
| …but it is a **circle**, not the design's squircle tile                               | `packages/design-system/src/styles.css:854-868`                                                                                                                                                                                                   | `.ui-app-icon { border-radius: 50%; height: 1.25rem; width: 1.25rem }`. There is no square/tile variant. The web app hand-patches one locally: `apps/frontend/src/features/connectors/mcp/mcp-wizard.css:362-366` `.mcp-card__icon.ui-app-icon { border-radius: var(--radius-sm); height: 2rem; width: 2rem }`.                                                                                                                                                                                                                                                                   |
| `ConnectorCard`'s icon slot has no tile chrome                                        | `packages/chat-surface/src/destinations/connectors/ConnectorCard.tsx:200-203`                                                                                                                                                                     | CONFIRMED verbatim: `const iconStyle = { display: "inline-flex", flexShrink: 0 }`. No width/height/radius/background.                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| `renderIcon` is bound by neither host                                                 | `packages/chat-surface/src/destinations/connectors/ConnectorsDestination.tsx:134,295,333`                                                                                                                                                         | CONFIRMED. `grep -rn renderIcon apps/ packages/` returns **7 hits, all inside `ConnectorsDestination.tsx`**. Zero host bindings. The tile is therefore unreachable, not merely unstyled.                                                                                                                                                                                                                                                                                                                                                                                          |
| Connected rows carry no icon/brand field and _cannot_ get one                         | `packages/api-types/src/connectors.ts:121-136`; `services/backend/src/backend_app/connectors/routes.py:60-78`                                                                                                                                     | **DISPUTED.** `Connector.slug` exists (`connectors.ts:124`) and is emitted by `ConnectorResponseModel.slug` (`routes.py:67`). `AppIcon`'s `BRAND_GLYPHS` is keyed by lower-cased slug (`design-system/src/index.tsx:514-515`). A slug is all the tile needs — **no wire field, no migration, no `extra="forbid"` change**.                                                                                                                                                                                                                                                        |
| Catalog entries carry `icon_hint`                                                     | `services/backend/src/backend_app/connectors/routes.py:80-85, 220-226`; `packages/api-types/src/connectors.ts:204-208`; `services/backend/src/backend_app/connectors/catalog.yaml:27-59`                                                          | CONFIRMED — `icon_hint` is present on `ConnectorCatalogEntryModel` only, populated for 9 catalog slugs. Nothing consumes it: `grep icon_hint packages/chat-surface apps/` → 0 hits.                                                                                                                                                                                                                                                                                                                                                                                               |
| Desktop has **no connect flow**                                                       | `apps/desktop/renderer/destinationBinders.tsx:445-491`                                                                                                                                                                                            | **PARTLY DISPUTED.** `ConnectModal` is never mounted (CONFIRMED — the desktop binder passes only `items/filter/onFilterChange/onConnect/onOpenCatalogEntry/onOpenApprovalSettings/onRetry`, and `onConnect={() => setFilter("available")}` at `:485`). But desktop _does_ own a working OAuth broker: `CONNECTOR_CHANNELS.connect` (`apps/desktop/main/connectors/channels.ts:17`) → `apps/desktop/main/ipc/handlers.ts:406` → `apps/desktop/main/connectors/oauth-coordinator.ts:201,239`. What is missing is the **dialog and the custom-MCP path**, not the transport.         |
| Backend desktop OAuth routes are ready                                                | `services/backend/src/backend_app/connectors/desktop_routes.py:169,194,231`; `services/backend-facade/src/backend_facade/connector_routes.py:52-54,183`                                                                                           | CONFIRMED — `/v1/connectors/desktop/catalog`, `/v1/connectors/{slug}/desktop/start-oauth`, `/v1/connectors/desktop/oauth-callback` all exist and are proxied by the facade.                                                                                                                                                                                                                                                                                                                                                                                                       |
| Desktop cannot open an external URL from the renderer                                 | `apps/desktop/renderer/onboarding/firstRunConnectorsPort.ts:17-21`                                                                                                                                                                                | CONFIRMED, verbatim: "main denies `window.open` and exposes no generic `openExternal` channel". Any shared connect flow must therefore treat "open the authorization URL" as an injected capability.                                                                                                                                                                                                                                                                                                                                                                              |
| A port for exactly this data already exists, with both host factories                 | `packages/chat-surface/src/onboarding/ports/FirstRunConnectorsPort.ts` (exported `src/index.ts:1572,1576`); `apps/desktop/renderer/onboarding/firstRunConnectorsPort.ts:39-90`; `apps/frontend/src/features/connectors/composerConnectorsPort.ts` | CONFIRMED. `listServers / listCatalog / installFromCatalog / addCustomServer / beginAuth` over `/v1/mcp/*`. Desktop binds it from a `Transport`, web from `identity`.                                                                                                                                                                                                                                                                                                                                                                                                             |
| Layout family divergence: grid vs row list                                            | `ConnectorsDestination.tsx:322,357`; `packages/chat-surface/src/shell/CardGrid.tsx:28-38`                                                                                                                                                         | CONFIRMED. `CardGrid` = `display:grid; repeat(auto-fill, minmax(260px,1fr)); gap:12`, borderless. Report `report-default.md` HIGH rows: `default.rowlist backgroundColor rgb(17,17,20) → transparent`, `borderColor --line → --tx`, MEDIUM `display flex → grid`, `flexDirection column → row`.                                                                                                                                                                                                                                                                                   |
| **The row-list vocabulary already exists in chat-surface and Tools never adopted it** | `packages/chat-surface/src/destinations/_shared/{RowList,Row,SectionHeader,PageLead}.tsx`; `_shared/index.ts:1-8`                                                                                                                                 | CONFIRMED and decisive. `RowList.tsx:28-43` is literally `.rowlist` (1px `--color-border`, `--radius-md`, `--color-surface`, hairline between rows); `Row.tsx:55-120` is `.lrow`; `SectionHeader.tsx:36-45` is `.sect-h` (mono, `--font-size-2xs`, `.12em`, uppercase, `--color-text-subtle`) with a built-in `count` and right-aligned `action` slot; `PageLead.tsx:21-27` is `.pg-lead`. `_shared/index.ts:2-3` says these exist "so Activity / Chats / Projects can't drift" — **Tools is not in that list**, and `ActivityDestination.tsx:51,460` already consumes `RowList`. |
| `Row`'s sub-line uses the body face                                                   | `_shared/Row.tsx:106-112`                                                                                                                                                                                                                         | CONFIRMED — `subStyle.fontSize: var(--font-size-2xs)`, no `fontFamily`, so it inherits body. Correct for Activity/Chats (which override to `var(--body)` in the mock) but **wrong for connectors**, whose `.lrow__sub` keeps the mono face (`copilot.css:1643-1648`). Report HIGH: `default.row.sub fontFamily mono → sans`, `fontSize 11px → 13.6px`.                                                                                                                                                                                                                            |
| Page title + duplicated filter tabs are live-only                                     | `ConnectorsDestination.tsx:180-196`; `apps/frontend/src/features/connectors/ConnectorsRoute.tsx:553-560, 583-601`                                                                                                                                 | CONFIRMED. `PageHeader` renders an `<h1>` at `--font-size-2xl` (`shell/PageHeader.tsx:49-56, :106-108`). `FilterTabs` is mounted at `ConnectorsDestination.tsx:190` **and again** inside the web aside via `ConnectorsPanel` (`ConnectorsPanel.tsx:53-59`) — two tablists for one state.                                                                                                                                                                                                                                                                                          |
| The CTA is hand-rolled and draws an accent-on-accent border                           | `packages/chat-surface/src/shell/PageHeader.tsx:70-81`                                                                                                                                                                                            | CONFIRMED verbatim: `border: "1px solid var(--color-accent)"` over `backgroundColor: "var(--color-accent)"`, `height: 32`, `padding: "0 14px"`, `fontSize: var(--font-size-sm)`. Report HIGH: `borderColor transparent → rgb(95,178,236)`, `fontSize 11.5px → 13.6px`.                                                                                                                                                                                                                                                                                                            |
| …and the correct recipe already exists                                                | `packages/design-system/src/styles.css:409-428, 443-449, 462-465`                                                                                                                                                                                 | CONFIRMED. `.ui-button` base sets `border: 1px solid transparent`; `.ui-button--sm` = `--radius-sm`, `--font-size-2xs` (11.2px), `--font-weight-medium`, `min-height: 1.5rem`, `padding: .25rem .55rem`. Design `.cbtn--sm` = `padding: 4px 9px; font-size: 11.5px` (`copilot.css:567-570`) with `.cbtn--pri { border-color: transparent }` (`:491-496`). Near-exact match.                                                                                                                                                                                                       |
| Selected segment carries an accent ring the design does not have                      | `packages/chat-surface/src/destinations/connectors/AccessModeSegment.tsx:158-176`                                                                                                                                                                 | CONFIRMED. `boxShadow: selected ? "0 0 0 1px var(--color-accent, #d97757)" : "none"` (`:172`), plus `fontWeight: selected ? 600 : 500` (`:166`). Design (`copilot.css:716-733`): weight is a constant `500`; `[data-on="true"]` changes **only** `background: var(--panel3)` and `color: var(--tx)`.                                                                                                                                                                                                                                                                              |
| Segment surfaces are inverted relative to the design                                  | `AccessModeSegment.tsx:148-156, 169-171`                                                                                                                                                                                                          | CONFIRMED. Group `background: var(--color-bg)` (#09090b) and selected `var(--color-bg-elevated)` (#0d0d10) — both **darker** than the surface they sit on. Design: group `--panel` #111114, selected `--panel3` #1d1d23 — both **lighter**. Report HIGH ×3.                                                                                                                                                                                                                                                                                                                       |
| Status pill is live-only chrome                                                       | `ConnectorCard.tsx:136`; `copilot-app.jsx:133`                                                                                                                                                                                                    | CONFIRMED. Design row = `<span className="lrow__name">{c.name}</span>` and nothing else; the 8px `.lrow__name` gap is never populated for connectors. Live renders a `CONNECTED` `StatusPill` on every row of a list whose membership already means connected.                                                                                                                                                                                                                                                                                                                    |
| Custom-MCP row gets a distinct dashed **and** pinned treatment                        | `copilot.css:510-526` vs `copilot.css:2350-2364`; `copilot-flows.jsx:477-494`                                                                                                                                                                     | **HALF DISPUTED.** `.mrow--dash { border-style: dashed; background: transparent }` and most of `.mrow--pin` are declared at `:510-526` but `.mrow` is declared **later** at `:2350` with equal specificity and re-declares `border`, `border-radius`, `padding`, `background`, `margin-bottom`. So dashed/borderless/full-bleed-padding all lose. What survives and therefore **is** the spec: `position: sticky; bottom: -15px; margin-top: 10px; margin-inline: -15px; width: calc(100% + 30px)`. The escape hatch is **pinned, not dashed**.                                   |
| Live custom row is an ordinary catalog row with a generic glyph                       | `packages/chat-surface/src/destinations/connectors/ConnectModal.tsx:347-370`                                                                                                                                                                      | CONFIRMED — same `pickRowStyle` as every catalog entry, no sticky. Catalog rows also render a literal `◆` for **every** entry (`:397-399`), and the custom row a `＋` (`:356-358`).                                                                                                                                                                                                                                                                                                                                                                                               |
| Modal identity-tile chrome exists but is fed a text glyph                             | `packages/chat-surface/src/settings/Modal.tsx:161-171`; `ConnectModal.tsx:307`                                                                                                                                                                    | CONFIRMED. `logoStyle` = 30×30, `--radius-md`, `--color-surface-muted` — correct chrome. `ConnectModal` passes `logo={<span aria-hidden="true">◆</span>}`. Design `.modal__logo` (`copilot.css:2260-2271`) = 34×34, radius 8, `--panel3` / `--tx2`.                                                                                                                                                                                                                                                                                                                               |
| Scrim depends on a token that does not exist yet                                      | `packages/chat-surface/src/settings/Modal.tsx:141-145`                                                                                                                                                                                            | CONFIRMED verbatim: "The design system has no scrim token yet" → `var(--color-scrim, rgb(0 0 0 / 0.54))`. Design `.scrim` (`copilot.css:2223-2232`) = `rgba(4,4,6,.66)` + `backdrop-filter: blur(2px)`. PRD-01 §D adds `--color-scrim` / `--blur-scrim` (`PRD-01-design-tokens.md:247-253`).                                                                                                                                                                                                                                                                                      |
| `access_mode` is not on the connector wire model                                      | `services/backend/src/backend_app/connectors/routes.py:60-78`                                                                                                                                                                                     | CONFIRMED — `ConnectorResponseModel` has no `access_mode` field and is `extra="forbid"`, while `api-types` `Connector.access_mode?` exists (`connectors.ts:136`) and the destination reads it (`ConnectorsDestination.tsx:338`). This is PRD-06's scope, not this PRD's.                                                                                                                                                                                                                                                                                                          |
| Design row anatomy                                                                    | `copilot.css:1574-1616`; `copilot-app.jsx:126-152`                                                                                                                                                                                                | CONFIRMED — `.rowlist` flex column / `--panel` / 1px `--line` / `--r` 8px / `overflow hidden`; `.lrow` `padding 11px 14px; gap 12px; align-items center; border-bottom 1px --line`; `.lrow__logo` `30×30; border-radius 7px; background var(--panel3) !important; color var(--tx2) !important`. The mock's inline `style={{background: c.color}}` (`copilot-app.jsx:129`) is **neutralised by the `!important` pair** — the computed tile is neutral.                                                                                                                             |

## Design intent

Literal values from `tools/design-parity/design-kit/app-v3/`. Token equivalences are exact:
`--panel #111114` = `--color-surface`, `--panel3 #1d1d23` = `--color-surface-elevated`,
`--ink2 #0d0d10` = `--color-bg-elevated`, `--line` = `--color-border`, `--line2` =
`--color-border-strong`, `--tx #ececf1` = `--color-text`, `--tx2 #d4d4db` =
`--color-text-strong`, `--mut #98989f` = `--color-text-muted`, `--mut2 #64646d` =
`--color-text-subtle` (`design-system/src/styles.css:168-203`).

**Page opening** — one lead paragraph, no page title (`copilot-app.jsx:99-113`):

> "The apps the agent can read from and act through — a destination, not a settings tab.
> Per-tool access lives here; the agent's approval _policy_ lives in
> [Settings → Model & behavior]."

`.pg-lead { font-size: 12px; color: var(--mut); margin: -2px 0 18px; max-width: 72ch; line-height: 1.6 }`
(`copilot.css:1556-1562`). The inline link is a plain `a { color: var(--accent); text-decoration: none }`
(`copilot.css:127-130`) — **no underline**.

**Section header** — the only header on the page (`copilot-app.jsx:114-125`):

```jsx
<div style={{ display: "flex", alignItems: "center", marginBottom: 14 }}>
  <div className="sect-h" style={{ margin: 0 }}>
    Connected · {connectors.length}
  </div>
  <button className="cbtn cbtn--pri cbtn--sm" style={{ marginLeft: "auto" }}>
    <Icon.plus /> Connect a tool
  </button>
</div>
```

`.sect-h { font-family: var(--mono); font-size: 9.5px; letter-spacing: .12em; text-transform: uppercase; color: var(--mut2) }`
(`copilot.css:1563-1570`). CTA: `.cbtn` `gap 6px; border 1px solid var(--line2)` +
`.cbtn--pri { background: var(--accent); color: var(--accent-ink); border-color: transparent; font-weight: 600 }`

- `.cbtn--sm { padding: 4px 9px; font-size: 11.5px }` → computed **122×23**.

**Row list** (`copilot.css:1574-1616`, markup `copilot-app.jsx:126-152`):

```css
.rowlist {
  display: flex;
  flex-direction: column;
  border: 1px solid var(--line);
  border-radius: var(--r) /*8px*/;
  overflow: hidden;
  background: var(--panel);
}
.lrow {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 11px 14px;
  border-bottom: 1px solid var(--line);
  background: transparent;
}
.lrow:last-child {
  border-bottom: 0;
}
.lrow__logo {
  width: 30px;
  height: 30px;
  border-radius: 7px;
  display: grid;
  place-items: center;
  font-weight: 600;
  font-size: 12px;
  flex: none;
  background: var(--panel3) !important;
  color: var(--tx2) !important;
}
.lrow__name {
  font-size: 12.5px;
  font-weight: 500;
  color: var(--tx);
  display: flex;
  gap: 8px;
}
.lrow__sub {
  font-size: 11px;
  color: var(--mut2);
  margin-top: 1px;
  font-family: var(--mono);
}
.lrow__act {
  flex: none;
  display: flex;
  align-items: center;
  gap: 9px;
}
```

Computed: list **912×369**, row **910×61.25**. Row cursor is `default`
(`copilot-app.jsx:128`) — connector rows are **not navigable**.

**Segmented control** (`copilot.css:708-733`), computed **192×31**, selected **87×25**:

```css
.seg {
  display: inline-flex;
  gap: 2px;
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 7px;
  padding: 2px;
}
.seg button {
  font-size: 12px;
  font-weight: 500;
  color: var(--mut);
  background: transparent;
  border: 0;
  border-radius: 5px;
  padding: 5px 12px;
}
.seg button[data-on="true"] {
  background: var(--panel3);
  color: var(--tx);
}
```

Selection changes **background and colour only**. No ring, no weight change.

**Connect modal** (`copilot-flows.jsx:404-495`): header tile `.modal__logo` 34×34 radius 8,
neutral (`copilot.css:2260-2271`); subtitle copy is the **trust model**, not a task —
"the agent acts through your accounts" (`copilot-flows.jsx:455`). Catalog rows `.mrow`
`padding 10px; border 1px solid var(--line2); border-radius 8px; margin-bottom 7px; background var(--ink2); gap 10px`
with a 28×28 radius-7 neutral `.mrow__logo` (`copilot.css:2350-2392`), computed **468×58**.
The escape hatch is `<button className="mrow mrow--dash mrow--pin">` with a mono `{ }` glyph
and copy "Custom MCP server / paste a JSON config — stdio or remote"
(`copilot-flows.jsx:477-494`); after the cascade it computes as an ordinary `.mrow` that is
`position: sticky; bottom: -15px; margin: 10px -15px 7px; width: calc(100% + 30px)`.

**Scrim** `.scrim { background: rgba(4,4,6,.66); backdrop-filter: blur(2px); padding: 22px }`
(`copilot.css:2223-2232`).

## Architectural decision

Seven decisions. Six of them **consume things that already exist**; one deletes a feature.

### D1 — Migrate Tools onto the `destinations/_shared` list vocabulary. Delete `ConnectorCard`.

`RowList` / `Row` / `SectionHeader` / `PageLead` are already the `.rowlist` / `.lrow` /
`.sect-h` / `.pg-lead` spec, already token-driven, already substrate-clean, already
consumed by Activity. Tools was simply skipped. Using them **is** the fix.

`ConnectorCard.tsx` is deleted, not restyled: its entire contract (fat card chrome,
`minHeight: 132`, status pill, "Agent access" label, footer last-sync row, card-level
`onClick`) is the wrong shape. `CardGrid` stays in the codebase (Library/Home use it) but
is unmounted from this surface.

Two additive changes to `_shared/Row` — both are real cross-surface spec differences, not
Tools-specific escapes:

- `subFont?: "body" | "mono"` (default `"body"`). Connectors' `.lrow__sub` keeps the mono
  face; Activity/Chats override to body in the mock. Encoding it as a prop keeps the
  divergence in the primitive where the next surface can see it.
- `iconSize?: 28 | 30` (default `28`) — `.lrow__ic` is 28×28, `.lrow__logo` is 30×30.

The `.lrow__act` trailing cell is the existing `meta` slot (flex:none, right-aligned);
the `AccessModeSegment` goes there, with `stopPropagation` retained.

_Rejected:_ re-skinning `ConnectorCard` in place (leaves two row vocabularies, guarantees
the next surface drifts again); adding `variant="row"` to `ConnectorCard` (a flag on the
wrong abstraction — the standing constraint says delete it instead).

### D2 — Delete `FilterTabs` from Tools. The catalog lives only in the modal.

The design has no tabs. It has one mono eyebrow, `Connected · N`, and a CTA that opens the
connect dialog whose first step **is** the catalog. Live has: a tab strip, rendered twice
on web, whose `available` tab duplicates the modal's catalog and whose `custom` tab is a
permanent `EmptyState` (`ConnectorsDestination.tsx:271-278`).

So: remove `filter` / `onFilterChange` / `counts` / `onOpenCatalogEntry` from
`ConnectorsDestinationProps`, delete the `available` and `custom` render branches, delete
`FilterTabs` from `ConnectorsPanel`, and delete the 240px aside from `ConnectorsRoute`
(its only remaining content is one Webhooks link). Webhooks moves into `SectionHeader`'s
existing `action` slot as a `.ui-button--sm .ui-button--ghost`, beside the primary CTA.

This is the seam fix for the double-render: nothing is de-duplicated, the second renderer
stops existing. It also repairs desktop's dead CTA for free — with no tab to switch to,
`onConnect` can only mean "open the modal" on both hosts.

_Rejected:_ hoisting the tabs into a shared component both mounts read (keeps a control
the design does not have); keeping the aside for Webhooks (a 240px chrome column for one
link, and the design's connectors surface is single-column).

### D3 — Identity tile = `AppIcon`, keyed on `connector.slug`, neutral tone, rendered by default.

No new wire field, no migration, no api-types change. `Connector.slug` is already on the
wire (`routes.py:67`) and `BRAND_GLYPHS` is slug-keyed. This explicitly overrules the
brief's "connected rows carry no icon/brand field and cannot".

Three changes:

1. **design-system** gains `.ui-app-icon--tile` (`width/height: 1.875rem` = 30px,
   `border-radius: var(--radius-md)`, `font-size: var(--font-size-xs)`) and `AppIcon`
   gains `size?: "sm" | "lg" | "tile"`, plus `tone?: "brand" | "neutral"` (default
   `"brand"`; `"neutral"` suppresses the inline `brand.bg/fg` so the tile renders
   `--color-surface-elevated` / `--color-text-strong`).
2. **Kill the copy**: `apps/frontend/src/features/connectors/mcp/mcp-wizard.css:362-366`
   is deleted and `McpOverlay`'s call site passes `size="tile"`.
3. **The tile stops depending on a host binding.** `ConnectorsDestination` renders
   `<AppIcon name={c.slug} size="tile" tone="neutral" />` when `renderIcon` is undefined.
   `renderIcon` survives as an _override_ (a host with `logo_url` may want it), but the
   default is no longer "nothing".

Tone is **neutral** because the spec's `!important` pair (`copilot.css:1613-1614`)
neutralises the mock's inline brand colour, and because a brand-saturated 30px tile is the
loudest object on an otherwise hairline page. `tone="brand"` remains available and is a
one-word reversal if design disagrees.

`icon_hint` stays catalog-only. It is a _hint for slugs the user has not installed_, which
is precisely the modal's catalog step; adding it to `ConnectorResponseModel` would mean a
wire change, a facade passthrough and an `extra="forbid"` edit to duplicate data the slug
already carries.

_Rejected:_ a chat-surface-local tile component (a parallel primitive to one that exists);
adding `icon_hint` to the connector wire (a migration to restate the slug).

### D4 — Desktop mounts the **same** `ConnectModal`; only "open the authorization URL" is host-specific.

`ConnectModal` is already fully host-driven and substrate-clean: it owns the
catalog→oauth→permission phase machine and takes `onSelectEntry`, `pending`, `error`,
`onConnect(slug, permission)`, `onAddCustomServer` (`ConnectModal.tsx:100-209`). Nothing
in it is web-specific. What _is_ web-specific lives in `ConnectorsRoute.tsx:283-420`:
~140 lines of orchestration (SSE completion tracking, `window.open`, custom-server
create → `startMcpAuth`). Copying that into `destinationBinders.tsx` is exactly the
bandaid the standing constraint forbids.

**The seam:** lift that orchestration into
`packages/chat-surface/src/destinations/connectors/useConnectFlow.ts`, parameterised by

- the **existing** `FirstRunConnectorsPort` (`listCatalog / addCustomServer / beginAuth`)
  — both hosts already ship a factory (`apps/frontend/.../composerConnectorsPort.ts`,
  `apps/desktop/renderer/onboarding/firstRunConnectorsPort.ts`); and
- one new injected capability on the hook's options:
  `authorize(request: { url?: string; slug?: ConnectorSlug }): Promise<void>`.
  Web implements it as `window.open(url, "_blank", "noopener,noreferrer")`; desktop as
  `bridge.ipc.invoke(CONNECTOR_CHANNELS.connect, { slug })` for catalog picks and rejects
  URL-only requests it cannot open. This is required, not cosmetic: the desktop renderer
  is denied `window.open` (`firstRunConnectorsPort.ts:17-21`) and chat-surface bans bare
  `window` by eslint.
- completion signalling stays a callback the host drives (`markConnected(slug)`), so the
  web SSE stream and the desktop IPC resolution both feed one state machine.

Then the desktop binder mounts `<ConnectModal>` with `onConnect={() => setConnectOpen(true)}`,
and desktop gains the custom-MCP add it has never had — over the same
`addCustomServer` port method it already implements. This retires the "desktop custom-MCP
add" follow-up recorded in the frontend-parity-v3 notes.

_Rejected:_ a desktop-only connect dialog (two dialogs, guaranteed drift); moving the web
route onto `Transport` so both hosts share one data layer (correct long-term, but it is a
web-transport refactor with its own blast radius — out of scope here, and the port
abstraction makes it a later swap rather than a rewrite); exposing a generic
`openExternal` IPC channel on desktop (widens the renderer's capability surface for a
convenience).

### D5 — Selection is neutral. Geometry follows the token ladder, and the 1px delta is declared.

In `AccessModeSegment`: delete `boxShadow` (`:172`), pin `fontWeight` to
`var(--font-weight-medium)` for both states (`:166`), retint group background
`--color-bg` → `--color-surface` (`:155`) and selected `--color-bg-elevated` →
`--color-surface-elevated` (`:169`).

Radius stays `--radius-md` (8px) outer / `--radius-sm` (6px) inner against the design's
7/5. PRD-01 §B explicitly refuses to add rungs to the ladder, the outer−inner relationship
(2px, equal to the group's padding) is preserved, and one control does not justify two new
tokens. This is recorded as an `expectDivergence` in `surfaces/tools/anchors.json` with
that reasoning so the harness reports it as INFO rather than re-raising it forever.

_Rejected:_ `--radius-xs: 5px` + a 7px rung (token proliferation for a 1px delta, against
PRD-01's stated ladder policy); inline `borderRadius: 7` (a magic number, exactly what the
UI-kit consolidation program removed).

### D6 — The CTA uses the existing `.ui-button` recipe.

`SectionHeader`'s `action` slot receives
`<Button size="sm" variant="primary">Connect a tool</Button>`. `.ui-button`'s base
`border: 1px solid transparent` is precisely the design's `.cbtn--pri { border-color: transparent }`,
so the accent-on-accent border disappears as a consequence of using the recipe rather than
as a patch. `PageHeader` is not mounted on this surface at all, so its hand-rolled
`primaryButtonStyle` is untouched — repairing it for other surfaces is PRD-03/PRD-09
territory, not a second fix here.

### D7 — The custom-MCP escape hatch is **pinned**, not dashed.

Per the cascade proof in Evidence, `.mrow--dash` is dead CSS in the spec. Implement what
the spec computes and what is functionally meaningful: the row sticks to the bottom of the
scrolling catalog (`position: sticky; bottom: -15px; margin-inline: -15px; width: calc(100% + 30px)`,
i.e. full-bleed against `Modal` `bodyStyle`'s 15px padding at `settings/Modal.tsx:190-192`),
with the design's mono `{ }` glyph and its copy. Do **not** add a dashed border. Catalog
rows and the escape hatch both get a real `AppIcon` (`size="sm"`-tier 28px tile,
`tone="neutral"`) in place of the literal `◆` / `＋`, and the modal header logo takes
`<AppIcon name={selected.slug} size="tile" tone="neutral" />` in place of `◆`.

### No backend contract, no migration

This PRD adds no route, no column, no index, no api-types field, and no authorization
rule. Everything it needs is already on the wire (`slug`) or is another PRD's contract
(`access_mode` → PRD-06). Any implementation that reaches for a migration has taken a
wrong turn.

## Scope

**`packages/design-system`**

| File                                   | Reason                                                                                        |
| -------------------------------------- | --------------------------------------------------------------------------------------------- |
| `src/index.tsx` (`AppIcon`, ~:492-581) | Add `size: "tile"` and `tone: "brand" \| "neutral"`.                                          |
| `src/styles.css` (near `:854-897`)     | Add `.ui-app-icon--tile` (30px, `--radius-md`, `--font-size-xs`) and `.ui-app-icon--neutral`. |

**`packages/chat-surface`**

| File                                                         | Reason                                                                                                                                                                                                                                                                                                    |
| ------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `src/destinations/_shared/Row.tsx`                           | Add `subFont` and `iconSize` props.                                                                                                                                                                                                                                                                       |
| `src/destinations/_shared/Row.test.tsx`                      | Cover both new props.                                                                                                                                                                                                                                                                                     |
| `src/destinations/connectors/ConnectorsDestination.tsx`      | Rewrite the body onto `PageLead` + `SectionHeader` + `RowList` + `Row`; drop `PageHeader`/`FilterTabs`/`CardGrid`/`CatalogCard`; drop `filter`/`counts`/`onOpenCatalogEntry`; render the default `AppIcon` tile; restore the design's lead copy; move the status chip to error/expired/disconnected only. |
| `src/destinations/connectors/ConnectorCard.tsx`              | **Delete.**                                                                                                                                                                                                                                                                                               |
| `src/destinations/connectors/ConnectorCard.test.tsx`         | **Delete.**                                                                                                                                                                                                                                                                                               |
| `src/destinations/connectors/AccessModeSegment.tsx`          | Remove ring + weight flip; retint group/selected surfaces.                                                                                                                                                                                                                                                |
| `src/destinations/connectors/AccessModeSegment.test.tsx`     | Assert neutral selection.                                                                                                                                                                                                                                                                                 |
| `src/destinations/connectors/ConnectorsPanel.tsx`            | Remove `FilterTabs`, `filter`, `counts`; webhooks-only (or delete if the route stops mounting it).                                                                                                                                                                                                        |
| `src/destinations/connectors/ConnectModal.tsx`               | `AppIcon` for header + catalog + custom rows; pinned custom row; design subtitle copy ("the agent acts through your accounts"); `.mrow`-parity row metrics (`--color-border-strong`, `--color-bg-elevated`, gap 10).                                                                                      |
| `src/destinations/connectors/useConnectFlow.ts`              | **New.** Host-neutral connect orchestration over `FirstRunConnectorsPort` + `authorize`.                                                                                                                                                                                                                  |
| `src/destinations/connectors/useConnectFlow.test.ts`         | **New.** Phase machine, error, custom-add, `authorize` dispatch.                                                                                                                                                                                                                                          |
| `src/destinations/connectors/ConnectorsDestination.test.tsx` | Rewrite for the row list; add the no-`renderIcon` tile regression guard.                                                                                                                                                                                                                                  |
| `src/destinations/connectors/ConnectModal.test.tsx`          | Update for tiles + pinned row.                                                                                                                                                                                                                                                                            |
| `src/settings/Modal.tsx`                                     | Scrim → `var(--color-scrim)` / `var(--blur-scrim)` (PRD-01); logo tile 30→34px.                                                                                                                                                                                                                           |
| `src/index.ts`                                               | Export `useConnectFlow`; drop the `ConnectorCard` export.                                                                                                                                                                                                                                                 |

**`apps/frontend`**

| File                                                         | Reason                                                                                                                                                         |
| ------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `src/features/connectors/ConnectorsRoute.tsx`                | Delete the aside + `filter` state; move connect orchestration to `useConnectFlow` with `authorize = window.open`; keep the SSE stream feeding `markConnected`. |
| `src/features/connectors/__tests__/ConnectorsRoute.test.tsx` | Update for the removed tabs/aside; keep the connect + custom-add assertions.                                                                                   |
| `src/features/connectors/mcp/mcp-wizard.css`                 | Delete `.mcp-card__icon.ui-app-icon` (`:362-366`).                                                                                                             |
| `src/features/connectors/mcp/McpOverlay.tsx`                 | Pass `size="tile"` at `:385, :679`.                                                                                                                            |

**`apps/desktop`**

| File                                   | Reason                                                                                                                               |
| -------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------ |
| `renderer/destinationBinders.tsx`      | Mount `ConnectModal`; `onConnect` opens it; wire `useConnectFlow` with `authorize` = `CONNECTOR_CHANNELS.connect`; drop `setFilter`. |
| `renderer/destinationBinders.test.tsx` | Assert the modal opens from the CTA and that the custom-server path reaches the port.                                                |

**`tools/design-parity`**

| File                             | Reason                                                                                                                                                                             |
| -------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `surfaces/tools/anchors.json`    | Repoint live selectors at the row-list DOM; `default.section.head` and `default.row.logo` must resolve; add `expectDivergence` for the 8/6-vs-7/5 radius and the modal `StepDots`. |
| `lib/render-live-tools.test.tsx` | Re-render both states against the new component tree.                                                                                                                              |

## Non-goals

- **`access_mode` on the wire.** PRD-06 owns `ConnectorResponseModel.access_mode`, the
  PATCH route and its authorization rule. This PRD renders whatever PRD-06 delivers and
  keeps the existing `?? "off"` least-privilege default.
- **`renderIcon` / `onSetAccessMode` / `ConnectModal` prop plumbing on the hosts.** PRD-03
  owns those bindings; PRD-11 changes what the shell does when they are _absent_.
- **`--color-scrim` / `--blur-scrim` / `--font-size-sm` retune.** PRD-01 owns the tokens;
  this PRD only stops the local fallback in `settings/Modal.tsx`.
- **`PageHeader`'s hand-rolled primary button.** Tools stops mounting `PageHeader`; other
  surfaces still use it. Fixing it there belongs to whichever PRD keeps it.
- **`CardGrid`.** It remains correct for Library/Home. Only the Tools mount goes.
- **Connector detail view.** `ConnectorDetailView.tsx`, `ScopeReviewTab`, `ReadAuditTab`,
  `ConsumersTab` and the webhooks subtree are untouched; only the entry point moves.
  Note that the design's rows are `cursor: default` and non-navigable, so the row title
  (not the row) carries the detail affordance.
- **A generic desktop `openExternal` IPC channel.** `authorize` routes catalog picks
  through the existing slug-scoped broker; widening the renderer's capability surface is
  not on the table.
- **Migrating other `.ui-app-icon` overrides** (e.g. `apps/frontend/src/styles.css:1225-1239`
  `.aui-user-card__avatar`) — that is an accent user chip, a different role.

## Risks & rollback

| Risk                                                                         | Guard                                                                                                                                                                                                                        | Recovery                                                                                                              |
| ---------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------- |
| Removing `filter`/`counts` breaks both hosts at compile time.                | Intentional — `npm run typecheck --workspace @0x-copilot/frontend` and `--workspace @0x-copilot/desktop` fail loudly rather than silently rendering a stale tab.                                                             | Type errors enumerate every call site.                                                                                |
| Deleting the "Available" tab removes the only non-modal path to the catalog. | `ConnectorsRoute.test.tsx:419-517` (connect flow) and the new desktop binder test both assert the modal path reaches the catalog.                                                                                            | If the modal path regresses, users cannot install anything — this is the highest-value test in the set, run it first. |
| `useConnectFlow` extraction changes web behaviour that currently passes.     | `apps/frontend/src/features/connectors/__tests__/ConnectorsRoute.test.tsx` (connect flow at `:419`, custom-server add at `:518`) must pass **unmodified in assertion content**; only mount/selectors may change.             | Revert the hook file and re-inline; the modal contract is unchanged either way.                                       |
| Deleting `ConnectorCard` orphans another consumer.                           | `grep -rn "ConnectorCard" apps/ packages/ --include='*.tsx' --include='*.ts'` must return only `apps/frontend/src/features/connectors/ConnectorCard.tsx` (a **different**, legacy web-local component) after the change.     | Both files exist today; do not delete the `apps/frontend` one.                                                        |
| `AppIcon` gaining props regresses ~15 existing call sites.                   | Both new props are optional with today's behaviour as the default; `npm run test --workspace @0x-copilot/design-system` plus the citation tests (`SourceFavicon.test.tsx`, `MessageSourcesStrip`) cover the untouched paths. | Revert `styles.css` + `index.tsx` independently of the surface work.                                                  |
| Neutral tiles read as "less branded" and design objects.                     | Recorded as a decision with the `!important` proof.                                                                                                                                                                          | `tone="brand"` on one call site.                                                                                      |
| Rollback of the whole PRD.                                                   | Every change is additive-or-deletive within four packages and touches no schema.                                                                                                                                             | `git revert` the PRD's commits; PRD-01/03/06 remain independently valid.                                              |

## Definition of Done

1. `grep -rn "CardGrid\|PageHeader\|FilterTabs" packages/chat-surface/src/destinations/connectors/` returns **0 matches**.
2. `packages/chat-surface/src/destinations/connectors/ConnectorCard.tsx` and `ConnectorCard.test.tsx` no longer exist, and `grep -rn "destinations/connectors/ConnectorCard" packages apps --include='*.ts' --include='*.tsx' | grep -v node_modules` returns 0 matches.
3. **Regression guard for the missing tile:** `ConnectorsDestination.test.tsx` contains a test that renders `<ConnectorsDestination items={…one connected connector with slug "gmail"…} />` **without** a `renderIcon` prop and asserts `container.querySelector('.ui-app-icon--tile')` is non-null. (This is the exact defect: `renderIcon` was bound by neither host, so no tile ever rendered.)
4. **Design value pinned numerically:** `packages/design-system/src/styles.css` declares `.ui-app-icon--tile { height: 1.875rem; width: 1.875rem; border-radius: var(--radius-md); }` — 1.875rem × 16 = **30px**, matching `copilot.css:1605-1607` `.lrow__logo { width: 30px; height: 30px }`. Verify: `grep -A4 '\.ui-app-icon--tile' packages/design-system/src/styles.css`.
5. **Second design value pinned:** `AccessModeSegment.tsx`'s group background resolves to `var(--color-surface)` (= `#111114` = `--panel`, `copilot.css:10`) and the selected item's to `var(--color-surface-elevated)` (= `#1d1d23` = `--panel3`, `copilot.css:12`). Verify: `grep -n "color-surface\|color-bg" packages/chat-surface/src/destinations/connectors/AccessModeSegment.tsx` shows no `--color-bg` reference.
6. `grep -n "boxShadow" packages/chat-surface/src/destinations/connectors/AccessModeSegment.tsx` returns **0 matches**, and `AccessModeSegment.test.tsx` asserts that the selected and unselected options report the same `fontWeight` from `getComputedStyle`.
7. `cd packages/chat-surface && npm test -- src/destinations/connectors src/destinations/_shared` passes, including the new `useConnectFlow.test.ts`.
8. `apps/desktop/renderer/destinationBinders.test.tsx` asserts (a) clicking the Tools destination's "Connect a tool" CTA renders `[data-testid="settings-modal"]` with the title "Connect a tool", and (b) submitting the custom-server form calls the injected port's `addCustomServer`. Run: `npm test --workspace @0x-copilot/desktop -- destinationBinders`.
9. `apps/frontend/src/features/connectors/__tests__/ConnectorsRoute.test.tsx` still contains its connect-flow (`:419`) and custom-server-add (`:518`) suites with unchanged assertion content, and `npm test --workspace @0x-copilot/frontend -- ConnectorsRoute` passes.
10. `grep -rn "mcp-card__icon.ui-app-icon" apps/frontend/src` returns 0 matches and `McpOverlay.tsx` passes `size="tile"`.
11. `npm run typecheck --workspace @0x-copilot/frontend && npm run typecheck --workspace @0x-copilot/desktop && npm run typecheck --workspace @0x-copilot/chat-surface && npm run typecheck --workspace @0x-copilot/design-system` all pass.
12. `npm run lint --workspace @0x-copilot/chat-surface` passes — proving `useConnectFlow` contains no bare `window` / `fetch` / `localStorage` (the substrate ban).
13. **Parity re-run** (procedure: `tools/design-parity/SKILL.md` steps 2-4; live fixtures via `node_modules/.bin/vitest run --config tools/design-parity/vitest.config.mjs`, then `node lib/compare.mjs surfaces/tools/out/design-default.json surfaces/tools/out/live-default.json --anchors surfaces/tools/anchors.json --out surfaces/tools/out/report-default.md --state default`): the regenerated `surfaces/tools/out/report-default.md` shows **0 HIGH rows** for the groups `Section header`, `List`, `Row` and `Permission control` — down from 15 HIGH total today.
14. In that regenerated report, `default.section.head` and `default.row.logo` no longer appear as `missing-in-live`, and `live.filter.tabs`, `live.status.pill` and `live.connectors.panel` no longer appear as `extra-in-live`.
15. `surfaces/tools/anchors.json` carries an `expectDivergence` string on the segment radius anchors and on `live.connect.stepdots`, each stating its reason (token-ladder quantization; multi-step OAuth needs a progress affordance the instant mock does not).
16. The regenerated `report-connect.md` shows 0 HIGH rows for the `Catalog` group — i.e. `connect.catalog.row.logo` renders a real per-slug tile, not `◆`.
17. `grep -rn "color-scrim, " packages/chat-surface/src/settings/Modal.tsx` returns 0 matches (the local fallback is gone once PRD-01's token lands).

## Dependencies

**Must land first**

- **PRD-01 (design tokens)** — `--color-scrim` / `--blur-scrim` for the modal scrim (DoD 17), and the `--font-size-sm` → 13px retune that most of the ≤2px type deltas on this surface ride on.
- **PRD-06 (access-mode backend)** — `ConnectorResponseModel.access_mode` on the wire plus the PATCH route. Without it the segment renders a permanent `off` for every connector and D5's visual work is unverifiable end-to-end.
- **PRD-03 (host bindings)** — `onSetAccessMode` / `ConnectModal` prop plumbing. PRD-11 changes what the shell does when bindings are absent; PRD-03 supplies them when present. If PRD-03 has not landed, ship D1/D3/D5/D6/D7 (presentation) and hold D4 (the desktop modal mount) until it does.

**Soft ordering**

- **PRD-09 (chats) / the projects and activity PRDs** also consume `_shared/Row`. PRD-11 **owns** the `subFont` and `iconSize` additions to `Row`; siblings consume them and must not re-add equivalents.

**This unblocks**

- Retiring the legacy web-local `apps/frontend/src/features/connectors/{ConnectorCard,ConnectorRow}.tsx` pair, whose only remaining justification was that the shared destination could not render a tile.
- The "desktop custom-MCP add" follow-up carried since frontend-parity-v3 — closed by D4.
- Any future surface that needs a squared identity tile, via `.ui-app-icon--tile` instead of a third app-local CSS override.
