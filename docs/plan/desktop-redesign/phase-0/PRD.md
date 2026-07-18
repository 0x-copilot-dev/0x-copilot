# Phase 0 — Foundation & de-risk · Implementation PRD

**Branch:** `feat/desktop-redesign` · **Worktree:** `/Users/parthpahwa/Documents/work/enterprise-search-redesign`
**Plan:** [`PLAN.md`](../PLAN.md) §8 "Phase 0 — Foundation & de-risk" (0A–0E) · §4 token updates · §7 consolidation map
**Design source of truth:** [`design-reference/DESIGN-SPEC.md`](../design-reference/DESIGN-SPEC.md) §0 (tokens & dims), §9 (decisions overlay)
**Template:** [`_TEMPLATE.md`](../_TEMPLATE.md) — all 12 sections, in order.

---

## 1. Context & problem

Phase 0 is the foundation the rest of the redesign stands on: it makes the desktop shell **actually styled**, folds the v2 "quiet" token _values_ into the single design-system source of truth, kills the last hardcoded decorative colors, adds a deployment-profile signal to the front-end substrate, and prepares `chat-surface` to receive the production interaction layer in Phase 1.

Today the desktop renderer is **unstyled**: `apps/desktop/esbuild.config.mjs` declares loaders only for `.tsx`/`.ts` (no `.css`, no `.woff2`), `apps/desktop/renderer/bootstrap.tsx` never imports `@0x-copilot/design-system/styles.css`, and `apps/desktop/renderer/index.html` links no stylesheet (it only sets an inline `background:#101113` on `<body>` and a stale `<title>Atlas</title>`). The web app wires the same stylesheet correctly (`apps/frontend/src/app/App.tsx:5` and `apps/frontend/src/walletEntry.tsx:15` both `import "@0x-copilot/design-system/styles.css"` under Vite), so this is a desktop-bundler gap, not a design-system gap.

Separately, the design-system tokens are close to but not the v2 "quiet" values (`packages/design-system/src/styles.css` uses `--color-bg:#0f0f10` where DESIGN-SPEC §0 specifies `--ink #09090b`; the light theme is a warm cream `#f5f2ec`; the `@font-face` block still ships Space Grotesk + Instrument Sans brand faces where §0 drops them for a system stack). The `[data-accent]`/`ACCENT_SCHEMES` set in `packages/design-system/src/index.tsx` currently has **nine** slugs — `sky, atlas-orange, gold, amber, red, lime, teal, blue, violet` (verified: `sky #5fb2ec` is already the default and `violet #a78bd6` already exists) — where the spec keeps only `sky/jade/ember/violet`, so the work is prune six + add the two new semantic-named slugs (`jade`, `ember`), not a from-scratch rewrite. A hardcoded lime `#c2ff5a` (plus a family of ad-hoc greys like `#181a1c`/`#2a2d31`) is baked into `packages/surface-renderers/src` and `packages/chat-surface/src/thread-canvas` — verified 14 literal hits across exactly the files named in §6 — violating the single-accent discipline of DESIGN-SPEC §0/§9.7. Finally, the front-end has **no** deployment-profile signal (the Python services do — `packages/service-contracts/src/copilot_service_contracts/deployment_profile.py`, which defines `PROFILE_SINGLE_USER_DESKTOP = "single_user_desktop"` alongside `saas_multi_tenant` / `single_tenant_managed` / `single_tenant_self_hosted`, and the facade `/v1/health`), and `chat-surface` has module homes for `messages/composer/citations` but **not** for `subagents/approvals/workspace`, which Phase 1 needs.

This phase builds on nothing upstream (it is the DAG root) and unblocks Phase 1 (consolidation needs the module homes + shim pattern from 0E), Phase 2 (shell restyle needs the v2 tokens from 0B and the styled renderer from 0A), and Phase 5 (Settings gating needs the DeploymentProfile port from 0D).

---

## 2. Goals / Non-goals

### Goals

- **G1 (0A):** Desktop renderer loads `@0x-copilot/design-system/styles.css` + the JetBrains Mono `@font-face` so the shell renders in the design language instead of unstyled HTML. esbuild bundles CSS + vendored font, `index.html` links the emitted stylesheet, no CSP/`file://` regressions.
- **G2 (0B):** Fold the v2 "quiet" token **values** (§4 / DESIGN-SPEC §0) into `packages/design-system/src/styles.css` — system font stacks, near-black neutrals, hairline borders, single sky accent, radii/density, reduce-motion — **without changing token structure or the public `@0x-copilot/design-system` API**, so it stays the single source of truth.
- **G3 (0C):** Reconcile every hardcoded `#c2ff5a` (and the co-located ad-hoc greys) in `surface-renderers` + `thread-canvas` to design-system CSS variables; neutralize decorative per-connector / per-lane color to `--color-surface-muted`/`--color-text`.
- **G4 (0D):** Add a `DeploymentProfile` port + React context to `chat-surface` (values `single_user_desktop | team`), host-supplied (desktop defaults `single_user_desktop`; web derives `team`), so Phase 2/5 can gate Workspace/Members/Billing.
- **G5 (0E):** Create the three missing `chat-surface` module homes (`subagents/`, `approvals/`, `workspace/`), establish the re-export **shim pattern** that Phase 1 uses to keep `apps/frontend` green, and tighten the ESLint boundary guard.
- **G6 (cross):** The web app (`apps/frontend`) stays **behaviorally identical** — token _value_ changes are visual-only and covered by existing snapshot/DOM tests; no public export of `@0x-copilot/design-system` or `@0x-copilot/chat-surface` is renamed or removed.

### Non-goals (explicitly deferred)

- **Moving any component** out of `apps/frontend` into `chat-surface` — that is Phase 1 (1A–1F). Phase 0 only creates empty module homes + the shim convention.
- **Restyling `ChatShell`/`AppRail`/`Topbar`** to 48px/46px v2 dims — Phase 2 (2A).
- **Profile-gating the destination set** (6-destination IA) or gating Settings sections — Phase 2 (2B) / Phase 5 (5E). Phase 0 only exposes the signal.
- **Mounting `ThreadCanvas` as Run**, timeline scrub, approvals routing — Phase 3.
- **Removing `DesktopPlaceholder`** from the mount — Phase 2E / 6C.
- **Any backend/Python change** to deployment-profile loaders — those already exist; 0D is a front-end-only consumer.
- Local-models / BYOK / Settings surfaces — Phase 5.

---

## 3. User stories

Roles: **Solo user** (primary, `single_user_desktop`), **Team admin** (only where profile-gated), **Developer/maintainer** (DX/architecture).

### US-0.1 — Styled desktop shell (0A)

_As a Solo user, I want the desktop app to render in the 0xCopilot design language, so that it looks like a product and not unstyled HTML._

- **Given** a freshly built desktop bundle, **When** the renderer mounts, **Then** `document.styleSheets` includes the design-system stylesheet and computed `body` `font-family` resolves to `var(--font-sans)`, `background` to `var(--color-bg)`.
- **Given** the app is offline / packaged (`file://`), **When** the renderer loads, **Then** CSS + font load from the local bundle with **no** network request and no CSP `connect-src` violation in the console.
- **Edge (font swap):** **Given** JetBrains Mono has not painted yet, **When** mono text renders, **Then** it falls back to `ui-monospace/SFMono-Regular` (per `--font-mono`) with no layout tofu.
- **Error:** **Given** the emitted CSS file is missing from `out/renderer/`, **When** the build runs, **Then** the build **fails loudly** (asset-copy step is part of the build graph), not silently ships an unstyled app.

### US-0.2 — v2 "quiet" visual language (0B)

_As a Solo user, I want near-black surfaces, hairline borders, system fonts, and a single sky accent, so that the app reads as calm and focused per the design spec._

- **Given** dark theme (default), **When** the shell renders, **Then** `--color-bg` computes to `#09090b` and `--color-border` to `rgba(255,255,255,.06)` (DESIGN-SPEC §0).
- **Given** `--font-sans` is consumed, **When** measured, **Then** it resolves to the system stack `-apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI", system-ui, sans-serif` (no Space Grotesk / Instrument Sans brand face requested).
- **Given** `[data-accent]` swatches, **When** the accent set is enumerated, **Then** the options are `sky/jade/ember/violet` with sky default `#5fb2ec`.
- **Empty/edge (light theme):** **Given** `data-theme="light"`, **When** rendered, **Then** neutrals use the spec light values (`--color-bg #f4f4f6`, lines `rgba(10,10,14,.07/.12/.22)`), not the old warm cream.
- **Reduce-motion:** **Given** `[data-reduce-motion="always"]`, **When** any animated element renders, **Then** its animation/transition duration is zeroed (existing rule preserved).

### US-0.3 — Single-accent discipline, no stray lime (0C)

_As a Solo user, I want no decorative lime-green or ad-hoc greys anywhere, so that color carries meaning (sky = accent, jade = live, ember = destructive) instead of noise._

- **Given** the Run thread-canvas subtree renders, **When** any element's color is inspected, **Then** no computed value equals `#c2ff5a` and every color resolves from a `var(--color-*)` token.
- **Given** a sheet diff with a changed row, **When** it renders, **Then** the "changed/selected" highlight uses `var(--color-accent)` (sky), not lime.
- **Given** a timeline lane / connector logo, **When** it renders, **Then** its color is neutralized to `--color-surface-muted`/`--color-text` (monochrome), reserving jade for live and ember for destructive.
- **Regression:** **Given** the web app renders the same components, **When** existing vitest DOM tests run, **Then** they still pass (assertions keyed on tokens/structure, not the literal `#c2ff5a`).

### US-0.4 — Design-system is the only token source (0B/0C, Developer)

_As a Developer, I want exactly one place that owns colors/type/spacing, so that theming and density stay coherent and future phases don't fork a second CSS system._

- **Given** a grep for `#c2ff5a` across `packages/` and `apps/`, **When** Phase 0 lands, **Then** zero source (non-test-fixture) matches remain.
- **Given** the design-system public API (`@0x-copilot/design-system` `index.tsx` exports, `./styles.css` export), **When** compared before/after, **Then** no export is removed or renamed (only token _values_ + `@font-face` change).

### US-0.5 — Profile-aware shell signal (0D)

_As a Solo user, I want the app to know it is running in solo desktop mode, so that (in later phases) team-only surfaces never appear._

- **Given** the desktop host mounts the shell, **When** `useDeploymentProfile()` is read, **Then** it returns `single_user_desktop` (the desktop default).
- **Given** the web host mounts the shell (multi-tenant web build, no `single_user_desktop`), **When** the profile is read, **Then** `deploymentProfileFromContract(...)` collapses the backend profile to `team` (the web default) — or whatever solo/team value the host injects.
- **Team admin:** **Given** a `team` profile, **When** a consumer checks `isTeam(profile)`, **Then** it returns `true` (the gating primitive Phase 5 uses; no gating UI is added in Phase 0).
- **Error (missing provider):** **Given** a component calls `useDeploymentProfile()` with no `DeploymentProfileProvider` above it, **When** rendered, **Then** it throws a clear developer error (mirrors `useTheme`'s "must be used inside …" guard).

### US-0.6 — DeploymentProfile port contract (0D, Developer)

_As a Developer, I want the profile delivered through a typed port/context like the other substrate ports, so that `chat-surface` stays framework-agnostic and the host owns the value._

- **Given** `chat-surface` source, **When** linted, **Then** the profile module references **no** browser global and **no** app import (the profile is passed in as a prop, not read from `process.env`/`window`).
- **Given** the port type, **When** imported, **Then** `DeploymentProfile` is a **front-end binary** string-literal union `"single_user_desktop" | "team"` exported from the package barrel. `single_user_desktop` is byte-identical to the backend spelling `PROFILE_SINGLE_USER_DESKTOP` in `service-contracts`; `"team"` is a **front-end collapse label** — the backend has no `"team"` profile, so the four backend spellings map `single_user_desktop → "single_user_desktop"` and **every other** backend profile (`saas_multi_tenant`/`single_tenant_managed`/`single_tenant_self_hosted`) → `"team"`.
- **Given** a backend-supplied profile string, **When** the host maps it to the FE binary, **Then** the mapping is done by a single exported helper `deploymentProfileFromContract(raw: string): DeploymentProfile` (only `single_user_desktop` yields solo; unknown/other ⇒ `team`), so the collapse rule lives in one place, not re-implemented per host.

### US-0.7 — chat-surface module homes + shim pattern (0E, Developer)

_As a Developer, I want empty, barrel-exported homes for subagents/approvals/workspace plus a documented re-export shim, so that Phase 1 can move one component family per PR without churn or web breakage._

- **Given** `packages/chat-surface/src`, **When** Phase 0 lands, **Then** `subagents/`, `approvals/`, `workspace/` each exist with an `index.ts` barrel and a short README/placeholder documenting the shim pattern.
- **Given** the barrels, **When** `packages/chat-surface/src/index.ts` re-exports them, **Then** the package `typecheck` + `test` stay green (empty barrels export nothing runtime-breaking).
- **Given** the shim convention doc, **When** a Phase 1 author reads it, **Then** it specifies: move implementation into the module home → re-export from `packages/chat-surface/src/index.ts` → replace the `apps/frontend` file body with `export { X } from "@0x-copilot/chat-surface"`.

### US-0.8 — ESLint boundary guard (0E, Developer)

_As a Developer, I want lint to fail fast when someone reaches across the substrate/app boundary, so that the (a)-SSOT invariant is enforced mechanically, not by review vigilance._

- **Given** the existing `chat-surface` guard (bans `window/document/fetch/localStorage/…` and `@0x-copilot/frontend`/`apps/*` imports), **When** Phase 0 lands, **Then** the guard additionally covers the three new module homes (they are under `src/**` so are already in scope — verify, don't regress).
- **Given** a hypothetical `import { X } from "apps/frontend/..."` inside `chat-surface`, **When** linted, **Then** it errors with the substrate-boundary message.
- **Given** the desktop renderer, **When** it (later, Phase 1) imports moved components, **Then** it imports from the `@0x-copilot/chat-surface` barrel — the `apps/desktop/eslint.config.mjs` sibling-app ban still forbids `@0x-copilot/frontend`.

### US-0.9 — Density / reduce-motion / theme honored on desktop (cross, 0A+0B)

_As a Solo user who set reduce-motion or compact density, I want those honored in the desktop app, so that my accessibility choices carry across substrates._

- **Given** `documentElement.dataset.reduceMotion="always"`, **When** the desktop renderer runs with the wired stylesheet, **Then** animations are zeroed (the `:root[data-reduce-motion="always"]` rule is now present because styles.css is loaded).
- **Given** `[data-density="compact"]`, **When** measured, **Then** the density tokens drop per the compact block.
- **Given** `[data-theme="light"]`, **When** toggled, **Then** the desktop shell recolors (proves tokens, not hardcoded hex, drive the shell).

---

## 4. Functional requirements

Each FR maps to ≥1 story (§3) and ≥1 test (§8).

### Area A — Wire design-system into desktop (0A) → US-0.1, US-0.9

- **FR-0.1** The esbuild renderer build (`apps/desktop/esbuild.config.mjs`) MUST bundle CSS: a `.css` entry (or an `import "@0x-copilot/design-system/styles.css"` from `bootstrap.tsx`) is processed and emitted to `out/renderer/`.
- **FR-0.2** The renderer build MUST resolve the vendored `./fonts/*.woff2` referenced by `styles.css` via a `.woff2` loader (`file` or `dataurl`) so the font is available at runtime with no external fetch.
- **FR-0.3** `apps/desktop/renderer/index.html` MUST reference the emitted stylesheet (via `<link rel="stylesheet">` or via the `bootstrap.tsx` import) and MUST drop the stale inline `<body style="background:#101113">` in favor of the token-driven `--color-bg` (retain a minimal FOUC-guard bg equal to the token value is acceptable).
- **FR-0.4** After build, `out/renderer/` MUST contain the stylesheet + any emitted font asset; the asset-copy/build graph MUST fail the build if the stylesheet is absent (no silent unstyled ship).
- **FR-0.5** The renderer MUST NOT introduce any runtime `fetch`/XHR/network load (CSP `connect-src 'none'` in `apps/desktop/main/app-protocol.ts` stays satisfied; the `apps/desktop/eslint.config.mjs` renderer global-bans stay satisfied). The stylesheet + font load through the local `app:` scheme (or inlined `data:`), which the existing CSP already permits: `style-src 'self' app: 'unsafe-inline'` and `font-src 'self' app: data:` — note that a font `url()` is governed by `font-src` and the `<link>`/inline styles by `style-src`, **not** `connect-src`, so no CSP directive needs to change for either the `file` (`app:`) or `dataurl` (`data:`) loader strategy.
- **FR-0.6** `apps/desktop/renderer/index.html` `<title>` MUST read `0xCopilot` (retire the stale `Atlas`).

### Area B — v2 "quiet" token values (0B) → US-0.2, US-0.4, US-0.9

- **FR-0.7** `--font-display` and `--font-sans` MUST be the system stack `-apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI", system-ui, sans-serif`; `--font-mono` MUST remain `"JetBrains Mono", ui-monospace, SFMono-Regular, monospace`.
- **FR-0.8** The Space Grotesk + Instrument Sans `@font-face` blocks MUST be removed; only the two JetBrains Mono `@font-face` blocks (latin + latin-ext) remain; the corresponding unused `.woff2` files under `packages/design-system/src/fonts/` MUST be deleted.
- **FR-0.9** Dark neutrals MUST be updated to DESIGN-SPEC §0: `--color-bg #09090b`, `--color-bg-elevated #0d0d10`, surfaces `#111114 / #16161a / #1d1d23`, borders `rgba(255,255,255,.06 / .10 / .18)`, text `#ececf1 / #d4d4db`, muted `#98989f / #64646d`.
- **FR-0.10** Light-theme neutrals MUST be updated to §0 (`--color-bg #f4f4f6`, `--color-surface #ffffff`, `--color-text #141419`, lines `rgba(10,10,14,.07/.12/.22)`).
- **FR-0.11** The single accent MUST be sky `#5fb2ec` (already the default swatch). The `AccentScheme` union + `ACCENT_SCHEMES` array in `packages/design-system/src/index.tsx` MUST be pruned from the current nine (`sky, atlas-orange, gold, amber, red, lime, teal, blue, violet`) to exactly `sky/jade/ember/violet` — keep `sky` (`#5fb2ec`) and `violet` (relabel swatch to `#a98be0` per §0), drop `atlas-orange/gold/amber/red/lime/teal/blue`, and add `jade #57c785` + `ember #f0764f`. The `styles.css` `:root[data-accent="…"]` blocks MUST be updated to the same four ids. The type change is allowed here because it is the design system's own contract, but MUST stay internally consistent so the existing `isAccentScheme` guard (which tests membership in `ACCENT_SCHEMES`) resolves and a persisted retired slug degrades to the `DEFAULT_ACCENT = "sky"`.
- **FR-0.12** Semantic tokens MUST be: `--color-success` jade `#57c785` (live/success), `--color-danger`/`--color-ember` ember `#f0764f` (destructive), `--color-warning` amber `#e8b45e`.
- **FR-0.13** Radii MUST expose `8 / 12 / 6` px equivalents and base font 13px per §0 (map onto existing `--radius-*`; add `--r/--r-lg/--r-sm` aliases only if the prototype class names need them — otherwise keep the existing `--radius-*` names as the SSOT and document the mapping).
- **FR-0.14** `[data-density=compact|spacious]` and `[data-reduce-motion]` behavior MUST be retained (no regression to the existing blocks).
- **FR-0.15** No public export of `@0x-copilot/design-system` (`index.tsx`) or the `./styles.css` entry MAY be removed; only values, `@font-face`, and the accent enum change.

### Area C — Reconcile lime + ad-hoc hex to tokens (0C) → US-0.3, US-0.4

- **FR-0.16** Every literal `#c2ff5a` in `packages/surface-renderers/src` (`_shared/palette.ts`, `sheet/SheetRenderer.tsx`, `sheet/SheetDiff.tsx`) MUST be replaced by `var(--color-accent)` (or the semantic token the usage implies).
- **FR-0.17** Every literal `#c2ff5a` in `packages/chat-surface/src` (`thread-canvas/TcInlineDiff.tsx`, `thread-canvas/TcChat.tsx`, `thread-canvas/TcSurfaceMount.tsx`, `thread-canvas/TcSwimlanes.styles.ts`, `thread-canvas/TcTabs.tsx`, `surfaces/GenericStructuredDiff.tsx`, `thread-canvas/ThreadCanvas.tsx` fallback) MUST be replaced by `var(--color-accent)`.
- **FR-0.18** The co-located ad-hoc greys that define the local palettes (`cardBg #181a1c`, `cardBorder #2a2d31`, `textHi #f4f5f6`, `textLo #9aa0a6`, `pageBg #101113`, `surfaceMute #1f2226`, etc. in the same palette objects) MUST be mapped to the corresponding `--color-*` tokens (`--color-surface`, `--color-border`, `--color-text`, `--color-text-muted`, `--color-bg`, `--color-surface-muted`).
- **FR-0.19** Semantic non-accent colors in those files (`accepted #3ddc97`, `rejected #ef5a5a`, `pinned #f5c542`) MUST map to `--color-success`, `--color-danger`, `--color-warning` respectively — jade/ember/amber discipline.
- **FR-0.20** Decorative per-connector logo / per-lane colors MUST be neutralized to `--color-surface-muted`/`--color-text` (monochrome) per DESIGN-SPEC §0/§9.7.
- **FR-0.21** After 0C, `grep -rn "#c2ff5a"` over `packages/` and `apps/` (excluding test fixtures/snapshots) MUST return zero matches.

### Area D — DeploymentProfile port + context (0D) → US-0.5, US-0.6

- **FR-0.22** `chat-surface` MUST export a `DeploymentProfile` front-end binary string-literal union type `"single_user_desktop" | "team"`. The `single_user_desktop` member MUST be byte-identical to `PROFILE_SINGLE_USER_DESKTOP = "single_user_desktop"` in `service-contracts`; `"team"` is a front-end collapse label with no backend equivalent and MUST be documented as such in the module header.
- **FR-0.22b** `chat-surface` MUST export a pure mapper `deploymentProfileFromContract(raw: string): DeploymentProfile` implementing the collapse: `"single_user_desktop" → "single_user_desktop"`, and any other value (incl. `saas_multi_tenant`/`single_tenant_managed`/`single_tenant_self_hosted`/unknown) → `"team"`. Hosts that receive a backend profile string MUST route it through this mapper rather than hardcoding `"team"`.
- **FR-0.23** `chat-surface` MUST export a `DeploymentProfileProvider` React context provider taking a `profile` prop and a `useDeploymentProfile()` hook returning the current profile.
- **FR-0.24** `useDeploymentProfile()` MUST throw a clear developer error when no provider is above it, mirroring the shape of `useTheme` in `packages/design-system/src/index.tsx` (which throws `"useTheme must be used inside ThemeProvider"`); use the analogous `"useDeploymentProfile must be used inside DeploymentProfileProvider"`.
- **FR-0.25** `chat-surface` MUST export an `isTeam(profile)` (and/or `isSoloDesktop(profile)`) pure predicate for later gating.
- **FR-0.26** The desktop host (`apps/desktop/renderer/bootstrap.tsx`) MUST wrap the shell in `DeploymentProfileProvider` with a literal `profile="single_user_desktop"` (desktop is always solo — it does not read a backend profile). The web host (`apps/frontend/src/app/App.tsx`) MUST wrap the tree in `DeploymentProfileProvider` with a value derived via `deploymentProfileFromContract(...)` from whatever profile string the web deployment exposes (defaulting to `"team"` in the multi-tenant web build). No gating UI is added in Phase 0.
- **FR-0.27** The profile module MUST NOT read any browser global or `process.env` and MUST NOT import from any `apps/*` package (host supplies the value as a prop) — enforced by the existing `chat-surface` ESLint guard.

### Area E — Module homes + shim + guard (0E) → US-0.7, US-0.8

- **FR-0.28** `packages/chat-surface/src/subagents/`, `.../approvals/`, `.../workspace/` MUST each exist with an `index.ts` barrel (may re-export nothing yet) and a short `README.md`/header comment stating the module's Phase-1 target contents (per PLAN.md §7).
- **FR-0.29** `packages/chat-surface/src/index.ts` MUST re-export the three new barrels (guarded so empty barrels don't break `typecheck`/`test`).
- **FR-0.30** A shim-pattern note MUST be documented (in each module `README` and/or `docs/plan/desktop-redesign/phase-0/PRD.md` cross-ref) specifying the 3-step Phase-1 migration: implement in module home → export from package barrel → replace `apps/frontend` file body with a re-export.
- **FR-0.31** The `packages/chat-surface/eslint.config.js` boundary guard MUST remain in force over `src/**` (including the new homes): browser-global ban + `apps/*`/`@0x-copilot/frontend` import ban. Add a regression lint fixture/test asserting the guard covers the new paths.
- **FR-0.32** `apps/desktop/eslint.config.mjs` sibling-app ban MUST still forbid `@0x-copilot/frontend` imports from the renderer (verify unchanged).

---

## 5. Architecture & system design

### 5.1 Single source of truth

| Concept                                          | Canonical owner (after Phase 0)                                                                                                                                                                    | What is consolidated / removed                                                                                                                                                                                                                                                                                                                                                       |
| ------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Color / type / spacing / density / motion tokens | `packages/design-system/src/styles.css` (`:root` + `[data-theme]`/`[data-accent]`/`[data-density]`/`[data-reduce-motion]`)                                                                         | Hardcoded `#c2ff5a` and ad-hoc palette objects in `surface-renderers` + `thread-canvas` are deleted and replaced by `var(--color-*)`. No second CSS system.                                                                                                                                                                                                                          |
| Accent enum                                      | `ACCENT_SCHEMES` / `AccentScheme` in `packages/design-system/src/index.tsx`                                                                                                                        | Old Atlas swatch names (`atlas-orange/gold/red/teal/blue`) collapse to `sky/jade/ember/violet`.                                                                                                                                                                                                                                                                                      |
| Fonts                                            | `@font-face` in `styles.css` (JetBrains Mono only) + `--font-*` vars                                                                                                                               | Space Grotesk + Instrument Sans faces + `.woff2` assets deleted; system stack referenced by var.                                                                                                                                                                                                                                                                                     |
| Deployment-profile value spelling                | `packages/service-contracts/src/copilot_service_contracts/deployment_profile.py` (four profiles: `saas_multi_tenant`, `single_tenant_managed`, `single_tenant_self_hosted`, `single_user_desktop`) | Front-end `DeploymentProfile` is a **binary collapse** of the backend's four profiles: `single_user_desktop` passes through byte-identically; the other three ⇒ `"team"`. The front-end does **not** redefine profile semantics or add a backend `"team"` value — it is a display/gating signal only, and the collapse lives in one exported mapper `deploymentProfileFromContract`. |
| Interaction-layer component homes                | `packages/chat-surface/src/{messages,composer,citations,subagents,approvals,workspace}`                                                                                                            | Phase 0 creates the three missing homes; Phase 1 fills them and `apps/frontend` becomes a re-export shim.                                                                                                                                                                                                                                                                            |
| Desktop styling entry                            | `apps/desktop/esbuild.config.mjs` → emitted `out/renderer/*.css` linked by `index.html`                                                                                                            | The web already owns its wiring via Vite; desktop gains the equivalent. Same `styles.css` consumed by both.                                                                                                                                                                                                                                                                          |

### 5.2 Boundaries & ports (respect `CLAUDE.md`)

- **No deployable app imports another app's `src/`.** Enforced by `apps/desktop/eslint.config.mjs` (bans `@0x-copilot/frontend`) and `packages/chat-surface/eslint.config.js` (bans `apps/*`/`@0x-copilot/frontend`). Phase 0 does not weaken these; 0E re-affirms them over the new module homes.
- **`chat-surface` stays framework-agnostic** — the existing ports are `Transport`, `Router`, `KeyValueStore`, `PresenceSignal` (`packages/chat-surface/src/ports/index.ts`), plus `BadgePort`, `NotificationPort`, `FilePickerPort`, `ClipboardPort`, `PaletteSearchPort`, `SurfaceHost`. Phase 0 adds **`DeploymentProfile`** as a **host-supplied value delivered via React context** (not a runtime I/O port — it needs no adapter, only a prop), consistent with how the host owns the active destination (`bootstrap.tsx` maps rail clicks to local state, not the shell deriving it).
- The `DeploymentProfile` context is analogous to `ThemeProvider` in `packages/design-system/src/index.tsx`: host mounts a provider with an explicit value; components read a hook; missing-provider throws.

### 5.3 Data flow & key types

- **Styling (0A):** `bootstrap.tsx` (or a CSS entry) → esbuild CSS bundle → `out/renderer/bootstrap.css` (+ font asset) → `index.html` `<link>` → renderer paints with `:root` tokens. Font `url()` resolves relative to the emitted CSS.
- **Profile (0D):** desktop host passes the literal `"single_user_desktop"`; the web host maps its deployment profile string through `deploymentProfileFromContract(...)` (backend `single_user_desktop` ⇒ solo, everything else ⇒ `"team"`) → `<DeploymentProfileProvider profile=…>` → `useDeploymentProfile()` → (Phase 2/5) gating predicates `isTeam` / `isSoloDesktop`.
- **Named types/interfaces (new, all in `packages/chat-surface/src`):**
  - `DeploymentProfile` — `type DeploymentProfile = "single_user_desktop" | "team"` (new file `deployment/DeploymentProfile.ts`). `"team"` is a front-end collapse label, not a backend spelling.
  - `deploymentProfileFromContract(raw: string): DeploymentProfile` — pure mapper; only `"single_user_desktop"` yields solo, all other/unknown ⇒ `"team"`.
  - `DeploymentProfileProviderProps { profile: DeploymentProfile; children: ReactNode }`, `useDeploymentProfile(): DeploymentProfile`, `isTeam(p): boolean`, `isSoloDesktop(p): boolean`.

### 5.4 Reuse vs move vs new

| Item                                 | Disposition                                                      | Path                                                                                                                                                                  |
| ------------------------------------ | ---------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `styles.css` (tokens + `@font-face`) | **Reuse + modify values**                                        | `packages/design-system/src/styles.css`                                                                                                                               |
| `AccentScheme` / `ACCENT_SCHEMES`    | **Modify (enum values)**                                         | `packages/design-system/src/index.tsx`                                                                                                                                |
| Vendored brand faces                 | **Delete**                                                       | `packages/design-system/src/fonts/{space-grotesk-*,instrument-sans-*}.woff2`                                                                                          |
| JetBrains Mono faces                 | **Reuse (keep)**                                                 | `packages/design-system/src/fonts/jetbrains-mono-latin{,-ext}.woff2`                                                                                                  |
| esbuild renderer config              | **Modify** (add CSS + woff2 loaders, asset copy)                 | `apps/desktop/esbuild.config.mjs`                                                                                                                                     |
| desktop `index.html`                 | **Modify** (`<link>`, title, drop inline bg)                     | `apps/desktop/renderer/index.html`                                                                                                                                    |
| desktop `bootstrap.tsx`              | **Modify** (import styles.css; wrap `DeploymentProfileProvider`) | `apps/desktop/renderer/bootstrap.tsx`                                                                                                                                 |
| surface-renderers palette            | **Modify → tokens**                                              | `packages/surface-renderers/src/_shared/palette.ts`, `sheet/SheetRenderer.tsx`, `sheet/SheetDiff.tsx`                                                                 |
| thread-canvas palettes               | **Modify → tokens**                                              | `packages/chat-surface/src/thread-canvas/{TcInlineDiff,TcChat,TcSurfaceMount,TcTabs,ThreadCanvas}.tsx`, `TcSwimlanes.styles.ts`, `surfaces/GenericStructuredDiff.tsx` |
| DeploymentProfile context/port       | **New**                                                          | `packages/chat-surface/src/deployment/DeploymentProfile.ts` + provider                                                                                                |
| Module homes                         | **New (empty barrels + README)**                                 | `packages/chat-surface/src/{subagents,approvals,workspace}/index.ts` (+ `README.md`)                                                                                  |
| Package barrel                       | **Modify (re-export new modules + profile)**                     | `packages/chat-surface/src/index.ts`                                                                                                                                  |
| web host provider wrap               | **Modify**                                                       | `apps/frontend/src/app/App.tsx`                                                                                                                                       |
| ESLint boundary guard                | **Reuse + regression fixture**                                   | `packages/chat-surface/eslint.config.js`, `apps/desktop/eslint.config.mjs`                                                                                            |

---

## 6. Affected files / component inventory

### Create

- `packages/chat-surface/src/deployment/DeploymentProfile.ts` — `DeploymentProfile` type, `deploymentProfileFromContract` mapper, `isTeam`/`isSoloDesktop` predicates.
- `packages/chat-surface/src/deployment/DeploymentProfileProvider.tsx` — context provider + `useDeploymentProfile` hook.
- `packages/chat-surface/src/deployment/DeploymentProfile.test.ts` — predicate + hook guard tests.
- `packages/chat-surface/src/subagents/index.ts` (+ `README.md`) — empty barrel, Phase-1 target: `SubagentCard`, `SubagentFleetCard`, `FleetSubagentRow`.
- `packages/chat-surface/src/approvals/index.ts` (+ `README.md`) — empty barrel, Phase-1 target: `ApprovalCard`, `ApprovalReceipt`, approval routing.
- `packages/chat-surface/src/workspace/index.ts` (+ `README.md`) — empty barrel, Phase-1 target: `WorkspacePane` + tabs.
- `packages/chat-surface/src/deployment/__eslint-boundary.test.ts` _(or extend an existing guard test)_ — asserts new homes are covered by the substrate guard.

### Modify

- `apps/desktop/esbuild.config.mjs` — add `.css` + `.woff2` loaders / CSS entry; emit + copy CSS/font to `out/renderer/`.
- `apps/desktop/renderer/index.html` — `<link rel="stylesheet">`, `<title>0xCopilot</title>`, drop inline `background:#101113`.
- `apps/desktop/renderer/bootstrap.tsx` — `import "@0x-copilot/design-system/styles.css"` (if import-driven) + wrap shell in `DeploymentProfileProvider profile="single_user_desktop"`.
- `packages/design-system/src/styles.css` — remove Space Grotesk/Instrument Sans `@font-face`; update `--font-*`, dark/light neutrals, hairlines, accent swatches, semantic colors, radii mapping (per FR-0.7–0.14).
- `packages/design-system/src/index.tsx` — `AccentScheme` union + `ACCENT_SCHEMES` → `sky/jade/ember/violet`; `isAccentScheme` guard stays consistent.
- `packages/surface-renderers/src/_shared/palette.ts` — palette object → token vars.
- `packages/surface-renderers/src/sheet/SheetRenderer.tsx`, `packages/surface-renderers/src/sheet/SheetDiff.tsx` — lime + greys → tokens.
- `packages/chat-surface/src/thread-canvas/TcInlineDiff.tsx`, `TcChat.tsx`, `TcSurfaceMount.tsx`, `TcTabs.tsx`, `ThreadCanvas.tsx`, `TcSwimlanes.styles.ts` — lime + greys + semantic hex → tokens.
- `packages/chat-surface/src/surfaces/GenericStructuredDiff.tsx` — lime → token.
- `packages/chat-surface/src/index.ts` — export `deployment/*` + the three module barrels.
- `apps/frontend/src/app/App.tsx` — wrap in `DeploymentProfileProvider profile="team"`.

### Delete

- `packages/design-system/src/fonts/space-grotesk-latin.woff2`, `space-grotesk-latin-ext.woff2`, `instrument-sans-latin.woff2`, `instrument-sans-latin-ext.woff2`, `instrument-sans-latin-italic.woff2`, `instrument-sans-latin-ext-italic.woff2` — unused after the `@font-face` removal (keep only `jetbrains-mono-*`).

### Flagged / superseded

- `apps/desktop/renderer/DesktopPlaceholder.tsx` — **not** removed in Phase 0 (its removal is Phase 2E). Its inline `fontFamily` becomes moot once styles.css loads but is left as the "renderer mounted" signal.
- The old Atlas accent names disappearing (`atlas-orange/gold/red/teal/blue/lime`) is an intentional **design-system contract change**; any front-end code persisting one of those accent slugs in `localStorage` degrades to the default via the existing `isAccentScheme` guard — call out in the PR description.

---

## 7. PR / commit breakdown

Ordered, each independently mergeable, each leaves `main` + web green.

### PR-0.1 — Wire design-system styles + fonts into desktop renderer (0A)

- **Scope:** esbuild CSS/woff2 loaders + asset copy; `bootstrap.tsx` imports `styles.css`; `index.html` `<link>` + title + drop inline bg.
- **Files:** `apps/desktop/esbuild.config.mjs`, `apps/desktop/renderer/bootstrap.tsx`, `apps/desktop/renderer/index.html`.
- **Deps:** none (DAG root).
- **Acceptance:** `npm run build --workspace @0x-copilot/desktop` emits `out/renderer/*.css` (+ font asset); a renderer smoke test asserts the stylesheet is present and `body` computed `font-family`/`background` resolve to tokens; no `fetch`/CSP violation; ESLint renderer global-bans still pass.
- **Size:** S (~120 LOC config/markup).

### PR-0.2 — Fold v2 "quiet" token values into design-system (0B)

- **Scope:** system font stacks; remove brand `@font-face` + delete unused woff2; dark/light neutrals + hairlines; accent swatch set `sky/jade/ember/violet`; semantic jade/ember/amber; radii mapping; keep density/reduce-motion.
- **Files:** `packages/design-system/src/styles.css`, `packages/design-system/src/index.tsx`, deleted `fonts/{space-grotesk,instrument-sans}*.woff2`.
- **Deps:** none (parallel with 0.1); ordering-wise land after 0.1 so the desktop immediately shows the new values, but not a hard dep.
- **Acceptance:** design-system `typecheck` green; `apps/frontend` typecheck + vitest green (visual-only change); a token unit test asserts `getComputedStyle(:root)` `--color-bg #09090b` (dark) / `#f4f4f6` (light) and `ACCENT_SCHEMES` ids equal `[sky,jade,ember,violet]`; no removed `styles.css`/`index.tsx` export except the accent-enum values.
- **Size:** M (~400 LOC incl. font-face deletion).

### PR-0.3 — Reconcile surface-renderers palette to tokens (0C part 1)

- **Scope:** `#c2ff5a` + ad-hoc greys + semantic hex in `surface-renderers` → `var(--color-*)`; neutralize decorative color.
- **Files:** `packages/surface-renderers/src/_shared/palette.ts`, `sheet/SheetRenderer.tsx`, `sheet/SheetDiff.tsx`.
- **Deps:** PR-0.2 (tokens must exist first).
- **Acceptance:** `npm run test --workspace @0x-copilot/surface-renderers` green; `grep #c2ff5a packages/surface-renderers/src` = 0 (excl. fixtures); a DOM test asserts the changed-row highlight uses the accent token.
- **Size:** S–M (~250 LOC).

### PR-0.4 — Reconcile thread-canvas palettes to tokens (0C part 2)

- **Scope:** `#c2ff5a` + greys + semantic hex in `thread-canvas` + `surfaces/GenericStructuredDiff.tsx` → tokens; neutralize lane/connector color.
- **Files:** `packages/chat-surface/src/thread-canvas/{TcInlineDiff,TcChat,TcSurfaceMount,TcTabs,ThreadCanvas}.tsx`, `TcSwimlanes.styles.ts`, `surfaces/GenericStructuredDiff.tsx`.
- **Deps:** PR-0.2.
- **Acceptance:** `npm run test --workspace @0x-copilot/chat-surface` green; `grep #c2ff5a packages/chat-surface/src` = 0 (excl. fixtures); DOM tests for `TcTabs` selected underline + `TcSwimlanes` bead states assert token colors (jade for `.now`, accent for `.cur`).
- **Size:** M (~450 LOC across 7 files).

### PR-0.5 — DeploymentProfile port + context (0D)

- **Scope:** `DeploymentProfile` type + `deploymentProfileFromContract` mapper + predicates + provider + hook; export from barrel; wrap desktop (literal solo) + web (mapped) hosts.
- **Files:** `packages/chat-surface/src/deployment/{DeploymentProfile.ts,DeploymentProfileProvider.tsx,DeploymentProfile.test.ts}`, `packages/chat-surface/src/index.ts`, `apps/desktop/renderer/bootstrap.tsx`, `apps/frontend/src/app/App.tsx`.
- **Deps:** none hard (can land after 0.1). Independent of 0.2–0.4.
- **Acceptance:** chat-surface `typecheck` + `test` green; unit tests: `useDeploymentProfile` throws without provider, returns supplied value with it, `isTeam("team")===true`, `isSoloDesktop("single_user_desktop")===true`, mapper collapses all three non-desktop backend spellings + unknown → `"team"`; desktop wraps literal `single_user_desktop`, web wraps `deploymentProfileFromContract(...)`; both hosts typecheck; ESLint substrate guard passes (no browser global / app import in the new module).
- **Size:** S (~180 LOC).

### PR-0.6 — chat-surface module homes + shim pattern + guard fixture (0E)

- **Scope:** create `subagents/`, `approvals/`, `workspace/` barrels + READMEs documenting the 3-step shim; re-export from `index.ts`; add ESLint boundary regression fixture covering the new homes.
- **Files:** `packages/chat-surface/src/{subagents,approvals,workspace}/index.ts` + `README.md`, `packages/chat-surface/src/index.ts`, `packages/chat-surface/src/deployment/__eslint-boundary.test.ts` (or a shared guard test).
- **Deps:** none hard; sequence after 0.5 so `index.ts` is edited once alongside the profile export (avoids a merge on the barrel).
- **Acceptance:** chat-surface `typecheck` + `test` + `lint` green; the guard fixture proves an `apps/*` import inside a new home errors; README shim note present; web unaffected.
- **Size:** S (~120 LOC docs + barrels + fixture).

**Ordering:** 0.1 → 0.2 → (0.3 ∥ 0.4) → 0.5 → 0.6. 0.5 and 0.6 may swap; both edit `index.ts` so land them adjacently to minimize barrel merges.

---

## 8. Testing plan

Runners: TS via `npm run test --workspace <pkg>` (vitest); typecheck via `npm run typecheck --workspace <pkg>`. No Python changes in this phase (deployment-profile loaders already tested under `services/*/tests/test_deployment_profile.py` — untouched).

### Unit

- **FR-0.1/0.3/0.4 (0A):** `apps/desktop/renderer/bootstrap.test.tsx` (extend) — after mount, assert `document.querySelector('link[rel=stylesheet]')` (or `document.styleSheets.length > 0`) and `getComputedStyle(document.body).fontFamily` contains `-apple-system`; assert `document.title === "0xCopilot"`. Build-artifact assertion: a node test (or a `package.json` script check) that `out/renderer/` contains a `.css` after `npm run build`.
- **FR-0.7–0.12 (0B):** `packages/design-system/src/styles.test.ts` (new; jsdom) — inject `styles.css`, assert `getComputedStyle(document.documentElement).getPropertyValue('--color-bg').trim() === '#09090b'`; toggle `data-theme="light"` → `#f4f4f6`; assert `--font-sans` starts `-apple-system`; assert no `@font-face` family `"Space Grotesk"`/`"Instrument Sans"` present (scan the stylesheet text).
- **FR-0.11 (accent enum):** `packages/design-system/src/index.test.tsx` (new/extend) — `ACCENT_SCHEMES.map(s=>s.id)` deep-equals `["sky","jade","ember","violet"]`; `isAccentScheme("atlas-orange") === false`.
- **FR-0.16/0.21 (0C-1):** `packages/surface-renderers/src/sheet/SheetDiff.test.tsx` (extend) — changed-row style uses `var(--color-accent)`; a repo-level grep test (`packages/surface-renderers` `vitest` or a `scripts/` assertion) that no `#c2ff5a` remains in `src` (excl. `__fixtures__`).
- **FR-0.17/0.19/0.21 (0C-2):** `packages/chat-surface/src/thread-canvas/TcTabs.test.tsx`, `TcSwimlanes.test.ts(x)` — selected tab underline = `var(--color-accent)`; `.now` bead = `var(--color-success)`; `.cur` bead = `var(--color-accent)`; grep test for `#c2ff5a` = 0 in `chat-surface/src`.
- **FR-0.22–0.25 (0D):** `packages/chat-surface/src/deployment/DeploymentProfile.test.ts` — `isTeam`/`isSoloDesktop` truth table; **mapper** `deploymentProfileFromContract("single_user_desktop")==="single_user_desktop"`, `deploymentProfileFromContract("saas_multi_tenant")==="team"`, `deploymentProfileFromContract("single_tenant_managed")==="team"`, `deploymentProfileFromContract("single_tenant_self_hosted")==="team"`, `deploymentProfileFromContract("garbage")==="team"`; `useDeploymentProfile` throws `"useDeploymentProfile must be used inside DeploymentProfileProvider"` without a provider (render a bare consumer, expect throw) and returns the supplied value inside `<DeploymentProfileProvider profile="single_user_desktop">`.
- **FR-0.28–0.31 (0E):** `packages/chat-surface/src/deployment/__eslint-boundary.test.ts` — run ESLint programmatically over a fixture string `import x from "apps/frontend/foo"` placed logically under `src/**` and assert the `no-restricted-imports` boundary error fires; assert the three new `index.ts` barrels import without error (`import * as m from "../subagents"`).

### Integration

- **Shell mounts with tokens + profile (0A+0B+0D):** `apps/desktop/renderer/bootstrap.test.tsx` — render `<App/>` under jsdom with a stubbed `window.bridge`; assert the tree renders inside a `DeploymentProfileProvider` (a test consumer reading `useDeploymentProfile()` returns `single_user_desktop`) and that no unstyled fallback (`DesktopPlaceholder` inline font) overrides token font once styles are present.
- **Barrel integrity (0E+0D):** `packages/chat-surface/src` `typecheck` — `import { DeploymentProfileProvider, useDeploymentProfile, isTeam } from "@0x-copilot/chat-surface"` resolves; the three new module barrels are exported without type errors.

### E2E / live smoke (`apps/desktop/SMOKE.md`)

Run the packaged/dev renderer per `apps/desktop/SMOKE.md` Launch section (`npm run build` then `npm run dev` with `COPILOT_AUTH_MODE=dev-mint`, facade at `:8200`). Manual/observed checks:

1. **Boot shows styled shell:** the window paints near-black `#09090b` background, hairline borders, system font — **not** the old unstyled `#101113`/`system-ui` fallback.
2. **Fonts local-only:** DevTools Network shows the mono woff2 loaded from the bundle over the `app:` scheme (or inlined as `data:`), **zero** external font/CSS requests; Console shows no CSP violation for `style-src`/`font-src` (both already allow `'self' app: data:`) and `connect-src 'none'` is not tripped (verify with `fetch('https://example.com')` failing, per `app-protocol.ts`).
3. **Theme/accent/density live:** setting `documentElement.dataset.theme="light"` recolors the shell; `data-accent="jade"` shifts the accent; `data-density="compact"` tightens spacing; `data-reduce-motion="always"` stops the `ui-status-pill--running` pulse.
4. **No lime anywhere:** open any thread-canvas surface (once mounted in later phases; for Phase 0 verify via the surface-renderers Storybook/dev harness or `apps/frontend` if it renders these) — no lime highlight; changed rows are sky, live beads jade.
5. **Profile signal:** a temporary dev assertion (or React DevTools) confirms `useDeploymentProfile()` = `single_user_desktop` in desktop.
   > Rationale (per MEMORY / PLAN.md §10): unit fakes have hidden real-run breakage before — smoke the live boot, not just jsdom.

### Regression guard (web `apps/frontend` behaviorally identical)

- `npm run typecheck --workspace @0x-copilot/frontend` + `npm run build --workspace @0x-copilot/frontend` green after PR-0.2/0.5.
- `npm run test --workspace @0x-copilot/frontend` green — token _values_ changed but DOM structure/classNames unchanged; any existing snapshot that pinned a literal old hex (e.g. `#0f0f10`) is the only expected churn and MUST be re-baselined **in the same PR** with the diff called out.
- `apps/frontend` still imports `@0x-copilot/design-system/styles.css` unchanged; the accent-persistence path degrades gracefully for retired accent slugs (verify `isAccentScheme` guard).

### FR → test map

FR-0.1/0.3/0.4/0.6 → bootstrap.test.tsx + build assertion · FR-0.2/0.5 → smoke step 2 + eslint renderer bans · FR-0.7–0.10/0.13/0.14 → styles.test.ts + smoke 1/3 · FR-0.11 → index.test.tsx · FR-0.12 → styles.test.ts · FR-0.15 → frontend typecheck/test · FR-0.16/0.18/0.20/0.21 → SheetDiff.test + grep test · FR-0.17/0.19/0.21 → TcTabs/TcSwimlanes tests + grep test · FR-0.22/0.22b/0.23–0.27 → DeploymentProfile.test (incl. mapper truth table) + integration typecheck + eslint guard · FR-0.28–0.32 → \_\_eslint-boundary.test + barrel typecheck.

---

## 9. UI/UX acceptance checklist

Grounded in DESIGN-SPEC §0 exact tokens/dims. Phase 0 does **not** restyle the shell layout (that is Phase 2) — it makes the tokens **load** and **correct**. Checks below are the token/color/state surface Phase 0 owns.

**Tokens / values (dark, default)**

- [ ] `--color-bg` = `#09090b`; `--color-bg-elevated` = `#0d0d10`; surfaces `#111114 / #16161a / #1d1d23`.
- [ ] Hairline borders `rgba(255,255,255,.06 / .10 / .18)`.
- [ ] Text `#ececf1 / #d4d4db`; muted `#98989f / #64646d`.
- [ ] `--font-sans`/`--font-display` = system stack (`-apple-system, …`); `--font-mono` = `"JetBrains Mono", ui-monospace, SFMono-Regular, monospace`; base 13px / line-height 1.5.
- [ ] Radii map to `8 / 12 / 6` per §0.
- [ ] Focus ring `2px solid var(--color-accent)` offset 2 (existing focus token intact).

**Accent / semantic (single-accent discipline, §0/§9.7)**

- [ ] Sole accent = sky `#5fb2ec`; `[data-accent]` options `sky/jade/ember/violet` only.
- [ ] Jade `#57c785` = live/success; ember `#f0764f` = destructive; amber `#e8b45e` = warning.
- [ ] **Zero** `#c2ff5a` in shipped source; decorative connector/lane color neutralized to `--color-surface-muted`/`--color-text`.

**States** (as applicable to touched components — sheet diff rows, TcTabs, TcSwimlanes beads)

- [ ] default / hover / active / focus-visible resolve from tokens.
- [ ] loading: `ui-status-pill--running` pulse present (and stopped under reduce-motion).
- [ ] streaming: streaming cursor/`streaming · N%` chip color = accent (no lime).
- [ ] error/destructive tone = ember; success/live = jade.

**a11y**

- [ ] `prefers-reduced-motion` honored: `:root[data-reduce-motion="always"]` zeroes durations; OS `prefers-reduced-motion: reduce` honored for `data-reduce-motion="auto"`.
- [ ] Contrast: text on `--color-bg #09090b` and light `#f4f4f6` meets WCAG AA for body (`--color-text` vs bg); muted text used only for non-essential labels.
- [ ] Roles unchanged (Phase 0 does not alter DOM roles); focus-visible ring visible on the token accent in both themes.

**Theming / density**

- [ ] Light theme neutrals per §0 (`#f4f4f6` bg, `#ffffff` surface, `#141419` text, lines `rgba(10,10,14,.07/.12/.22)`).
- [ ] `[data-density=compact|spacious]` still adjusts spacing tokens; `[data-theme]`/`[data-accent]` still drive color.
- [ ] Desktop and web render identically for the same `data-*` attributes (same `styles.css`).

**Component reuse**

- [ ] No new visual components introduced; `SheetDiff`/`SheetRenderer`/`TcTabs`/`TcSwimlanes`/`TcInlineDiff`/`TcChat`/`GenericStructuredDiff` restyled **to tokens only** (structure unchanged).
- [ ] `DesktopPlaceholder` inline styling left as-is (removed Phase 2E) — flagged, not blocking.

---

## 10. Dependencies & sequencing

**Upstream (blocked by):** none — Phase 0 is the DAG root.

**Internal order:** PR-0.1 → PR-0.2 → (PR-0.3 ∥ PR-0.4) → PR-0.5 → PR-0.6. PR-0.3/0.4 both depend on PR-0.2 (tokens must exist). PR-0.5/0.6 both edit `packages/chat-surface/src/index.ts` — land adjacently.

**Downstream (blocks):**

- **Phase 1 (1A–1F consolidation)** — needs the module homes + shim pattern (PR-0.6) and token-clean thread-canvas (PR-0.4).
- **Phase 2 (2A shell restyle, 2B profile-gated destinations)** — needs the styled renderer (PR-0.1), v2 tokens (PR-0.2), and the `DeploymentProfile` signal (PR-0.5).
- **Phase 3 (Run cockpit)** — inherits token-clean `ThreadCanvas`/`TcSwimlanes`/surface-renderers.
- **Phase 5 (Settings gating, 5E)** — consumes `isTeam`/`useDeploymentProfile` (PR-0.5).

DAG (no cycles): `0.1→{0.2}→{0.3,0.4}` ; `0.1→0.5→0.6` ; Phase0 → {Phase1, Phase2} → Phase3 ; Phase0 → Phase5.

---

## 11. Risks & mitigations

| Risk                                                                                                                                            | Severity | Mitigation                                                                                                                                                                                                                                           | Rollback / flag                                                   |
| ----------------------------------------------------------------------------------------------------------------------------------------------- | -------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------- |
| esbuild CSS bundling emits a sibling `.css` the `index.html` doesn't reference → still unstyled                                                 | High     | FR-0.3/0.4: assert `out/renderer/*.css` exists + `<link>` present in a build test; smoke step 1                                                                                                                                                      | Revert PR-0.1 (isolated to 3 files); web unaffected               |
| Font `url()` in bundled CSS resolves to a wrong/relative path under the `app:` scheme (renderer is served via `app-protocol.ts`, not `file://`) | Med      | Use esbuild `.woff2: "dataurl"` (inline) to sidestep path resolution, or `file` + verified copy to `out/renderer/`; both are CSP-clean (`font-src 'self' app: data:` already permits `app:` and `data:`); smoke step 2 checks zero external requests | dataurl variant is a one-line loader swap                         |
| Token value change re-baselines web snapshot tests that pinned old hex                                                                          | Med      | §8 regression guard: re-baseline in the same PR, diff called out; prefer token-keyed assertions                                                                                                                                                      | Snapshots are per-PR; revert PR-0.2 restores old values           |
| Retiring Atlas accent slugs breaks a persisted `localStorage` accent                                                                            | Low      | `isAccentScheme` guard already degrades unknown → default `sky`; note in PR-0.2                                                                                                                                                                      | Additive: could keep old ids as aliases if a user report surfaces |
| Mechanical hex→token swap changes a semantic color by accident (e.g. lime that meant "success" vs "accent")                                     | Med      | Per-usage review in PR-0.3/0.4: map by _meaning_ (accent vs success/danger/warning), not blanket replace; DOM tests assert per-state token                                                                                                           | Small, reviewable diffs; revert the single file                   |
| `DeploymentProfileProvider` missing at a mount site → runtime throw in web/desktop                                                              | Low      | Wrap at both host roots (FR-0.26); integration test asserts provider presence; throw is a _developer_ error caught in CI, not shipped                                                                                                                | Provider wrap is 2 lines per host                                 |
| chat-surface `index.ts` barrel merge conflict between PR-0.5 and PR-0.6                                                                         | Low      | Land adjacently; single author sequences them                                                                                                                                                                                                        | Trivial rebase                                                    |

---

## 12. Definition of done

- [ ] **FRs:** FR-0.1 … FR-0.32 all met.
- [ ] **0A:** `npm run build --workspace @0x-copilot/desktop` emits + links `styles.css`; renderer paints in tokens; title `0xCopilot`; no external CSS/font fetch, no CSP error.
- [ ] **0B:** `styles.css` carries v2 "quiet" values (§0); brand `@font-face` + unused woff2 removed; accent set `sky/jade/ember/violet`; density/reduce-motion intact; no design-system export removed except accent-enum values.
- [ ] **0C:** `grep -rn "#c2ff5a" packages/ apps/` (excl. fixtures) = 0; touched files resolve color from `var(--color-*)`; semantic jade/ember/amber discipline honored.
- [ ] **0D:** `DeploymentProfile` type + `deploymentProfileFromContract` mapper + provider + hook + `isTeam`/`isSoloDesktop` exported from `@0x-copilot/chat-surface`; desktop wraps literal `single_user_desktop`, web wraps `deploymentProfileFromContract(...)` (defaults `team`); `"team"` documented as a front-end collapse label with no backend spelling; substrate-guard clean.
- [ ] **0E:** `subagents/`, `approvals/`, `workspace/` homes exist with barrels + shim README; re-exported from `index.ts`; ESLint boundary guard covers them (regression fixture green).
- [ ] **Tests green:** `npm run test`/`typecheck` for `@0x-copilot/design-system`, `@0x-copilot/surface-renderers`, `@0x-copilot/chat-surface`, `@0x-copilot/desktop`, `@0x-copilot/frontend`; live desktop smoke (§8) walked.
- [ ] **Web unregressed:** `apps/frontend` typecheck + build + tests green; behavior identical (visual token values only); re-baselined snapshots diffed in-PR.
- [ ] **UI/UX checklist (§9)** passed in light + dark, compact/comfortable/spacious, reduce-motion.
- [ ] **Docs:** module-home READMEs + shim note written; this PRD's reuse/move table reflects final paths; PLAN.md §8 Phase 0 boxes tickable.
- [ ] **No dead code introduced:** deleted brand woff2; `DesktopPlaceholder` intentionally retained (flagged for Phase 2E).

```

```
