# PRD-03 — ArchetypeRenderer pack (Wave 1)

**Goal:** the tier-1.5 workhorse: one generic, hand-built, pure-render `SaaSRendererAdapter` per archetype scheme, driven by `{spec, data, diff}` from the surface payload. After this PR, any envelope PRD-02 emits renders richly on desktop (which already registers `registerAll()`).

**Depends on:** PRD-01. **Scope:** `packages/surface-renderers` (+ re-exported types from `@0x-copilot/api-types` only).
**Split-friendly:** if dispatched to multiple agents, split per archetype; `_shared/` + `record` first.

## Scope — files

| File                                 | Change                                                                                                                                                                                                                                                                     |
| ------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `src/_shared/path.ts`                | NEW — `resolvePath(data, "a.b.0.c"): unknown` (dots + numeric indices only, ~40 LOC, total — returns undefined on any miss) + `formatValue(v, format?)` (text/number/currency/datetime/badge/user; locale-safe, `tabular-nums` numbers)                                    |
| `src/_shared/specTypes.ts`           | NEW — re-export `SurfaceSpec`/`SurfaceEnvelope`/`SurfaceArchetype` from `@0x-copilot/api-types`; narrow helpers `specFromState(state)`, `dataFromState(state)` with defensive unknown-handling                                                                             |
| `src/archetypes/RecordRenderer.tsx`  | NEW — scheme `record`. renderCurrent: title/subtitle header + label/value field grid (reuse the visual grammar of `OpportunityRenderer`); renderDiff: per-field before→after rows (reuse `OpportunityFieldRow` patterns — struck-through old, accent new, provenance pill) |
| `src/archetypes/TableRenderer.tsx`   | NEW — scheme `table`. Columns from spec, rows from `items_path`; ≥50-col windowing via the existing `sheet/_columns.ts` helper (import it); renderDiff: per-row change highlighting compatible with `SheetDiff` visual language                                            |
| `src/archetypes/MessageRenderer.tsx` | NEW — scheme `message`. Composer-card layout (to/subject/body from spec paths); renderDiff: pending body block (reuse the `EmailRenderer` PENDING treatment; PRD-06 upgrades it to word diff)                                                                              |
| `src/archetypes/DocRenderer.tsx`     | NEW — scheme `doc`. Title + sections list (heading/body); renderDiff: changed-section highlight                                                                                                                                                                            |
| `src/archetypes/BoardRenderer.tsx`   | NEW — scheme `board`. Lanes via `group_by_path`, cards via `items_path` + title/fields; renderDiff: moved/changed card badges                                                                                                                                              |
| `src/archetypes/index.ts`            | NEW — `registerArchetypeAdapters()` registering the 5 above (metadata: `origin: "first-party"`, `schemaVersion: 1`, `matches: uri.startsWith("<scheme>://")`)                                                                                                              |
| `src/index.ts`                       | EXTEND — `registerAll()` additionally calls `registerArchetypeAdapters()`; export the new adapters + types                                                                                                                                                                 |

Archetypes `event | timeline | dashboard | file | form` are **out of this PRD** (follow-up PRDs may add them); unknown archetypes fall to tier-3 by design.

## Behavior (normative)

- **Spec optional:** `renderCurrent(state)` with `state.spec` undefined renders a minimal header + "Preparing view…" hint and the same data via a compact generic field list — never blank, never throw. (The projector may deliver the spec later; the adapter just re-renders.)
- **Defensive by contract:** state is `unknown` at the boundary; every access goes through `resolvePath`/guards. A malformed spec/data must render the fallback body, not throw (TcSurfaceMount's boundary is the last resort, not the plan).
- **All payload data renders as React text nodes** — no `dangerouslySetInnerHTML`, no URL usage except `link.url_path` values rendered as plain text href AFTER `http(s)://` prefix validation (else render as text).
- **Budget:** each renderCurrent must stay well inside the 100 ms budget at 500 rows / 60 fields (cap rendering: >200 rows shows "showing 200 of N").
- Styling: `_shared/palette.ts` tokens + design-system vars only, matching existing tier-1 renderers. D28 lint (`eslint.config.js` in this package) must pass — no window/fetch/transport.

## Acceptance criteria

1. Vitest per archetype: golden render (React test renderer / existing harness) for the PRD-01 fixtures (`linear_get_issue` → RecordRenderer; `github_list_issues` → TableRenderer; `gmail_message` → MessageRenderer).
2. Spec-less render test: data-only state renders the fallback body (assert no throw + hint text present).
3. Hostile-input tests: circular-safe? (data arrives JSON-parsed, so no cycles — but test deep nesting 20 levels, 10k-char strings truncated at display, `url_path` = `javascript:alert(1)` renders as text not href).
4. Diff tests: RecordRenderer renderDiff shows old struck-through / new highlighted for a 3-field change fixture.
5. Package eslint + typecheck green; `registerAll()` idempotence test (double-call replaces, not duplicates — registry same-version replace semantics).

## Non-goals / guardrails

- No changes to `chat-surface` (registry/mount stay untouched), no host apps, no new npm deps.
- Do not modify the four existing tier-1 adapters beyond importing shared helpers FROM them (never the reverse — keep them stable).
- No interactivity/inputs inside adapters (host owns controls — D28). Edit surfaces are PRD-09.
