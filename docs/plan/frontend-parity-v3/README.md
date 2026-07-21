# Frontend Parity v3 — PRD suite

> **Implementation status → [STATUS.md](STATUS.md).** Shipped so far: PRD-A, B, C,
> D (✅) and PRD-E PR-E.1 (🟡). Remaining: E.2/E.3 (web settings convergence), F
> (provider keys), G (destinations), H (backend). Every landed commit is green
> and on the branch; the PRDs below are the spec for the rest.

**Goal.** Bring the shipping product into parity with the imported Claude Design
**0xCopilot App v3** (design project `73f810d9`, file `0xCopilot App v3.html`),
across the seven surfaces audited (left rail, settings nav, provider keys, ⌘K
palette, Activity, Projects, Conversation/Chats) — **UI fidelity _and_ backend
linkage** — converging web (`apps/frontend`) and desktop (`apps/desktop`) on the
single-source-of-truth interaction layer `@0x-copilot/chat-surface`.

**Method.** The audit found that the seven surface-level gaps are symptoms of a
smaller set of **root causes**. We fix root causes, not symptoms. Each root cause
is one PRD; the surfaces then fall into parity as consumers of the corrected
primitives.

## Token crosswalk (verified 1:1)

The design's `copilot.css` custom properties map exactly onto
`packages/design-system/src/styles.css` `--color-*`. So every "wrong colour" in
this suite is a component choosing the wrong token, referencing a **non-existent**
token, or hard-coding a literal — never a palette mismatch.

| design (`copilot.css`)                           | value                             | design-system (`styles.css`)                                           |
| ------------------------------------------------ | --------------------------------- | ---------------------------------------------------------------------- |
| `--ink`                                          | `#09090b`                         | `--color-bg`                                                           |
| `--ink2`                                         | `#0d0d10`                         | `--color-bg-elevated`                                                  |
| `--panel`                                        | `#111114`                         | `--color-surface`                                                      |
| `--panel2`                                       | `#16161a`                         | `--color-surface-muted`                                                |
| `--panel3`                                       | `#1d1d23`                         | **(no token — gap)**                                                   |
| `--line` / `--line2`                             | `.06` / `.10` white               | `--color-border` / `--color-border-strong`                             |
| `--line3`                                        | `.18` white                       | **(no token — gap)**                                                   |
| `--tx` / `--mut` / `--mut2`                      | `#ececf1` / `#98989f` / `#64646d` | `--color-text` / `--color-text-muted` / `--color-text-subtle`          |
| `--tx2`                                          | `#d4d4db`                         | **(no token — gap)**                                                   |
| `--accent`(sky) / `--accent-hi` / `--accent-ink` | `#5fb2ec` / `#8cc8f4` / `#08131d` | `--color-accent` / `--color-accent-strong` / `--color-accent-contrast` |
| `--jade` / `--ember` / `--amber`                 | `#57c785` / `#f0764f` / `#e8b45e` | `--color-success` / `--color-danger` / `--color-warning`               |

Three design tokens (`--panel3`, `--line3`, `--tx2`) have **no design-system
equivalent**. PRD-B decides whether to add them (`--color-surface-elevated`,
`--color-border-stronger`, `--color-text-strong`) or map to the nearest existing
token; several audited bugs come from components inventing an undefined token
(`--color-surface-elevated`) and silently falling back to a hard-coded grey.

## The eight work-streams

| PRD                                          | Root cause it fixes                                                                                | Fixes symptoms in                                                                            |
| -------------------------------------------- | -------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------- |
| **A — Shared icon system**                   | No canonical icon set; every surface hand-draws or omits icons                                     | Rail icons, settings-nav icons (absent), palette glyphs, destination-row icons               |
| **B — Tokens & status-tone**                 | Wrong/undefined tokens; inverted status→tone; heavy button/badge scale; stale terracotta fallbacks | Rail colours, palette selected-row, chip schema (Activity/Chats), Projects pill, all buttons |
| **C — App rail parity**                      | Rail uses its own glyphs + wrong tokens; no badge/identity                                         | Left rail (area 1)                                                                           |
| **D — Command palette**                      | No static command-launcher layer; row/structure divergence                                         | ⌘K (area 4)                                                                                  |
| **E — Settings convergence + nav icons**     | Web renders a legacy settings screen; nav never feeds `renderNavIcon`                              | Settings nav (area 2), web/desktop split                                                     |
| **F — Provider keys convergence + fidelity** | Web renders legacy provider-keys; button/CTA drift; `validate` unused                              | Provider keys (area 3)                                                                       |
| **G — Destination surface parity**           | Rows lack icons; per-row chips vs one card; added `PageHeader`; missing `.sect-h`                  | Activity (5), Projects (6), Chats (7)                                                        |
| **H — Backend data-plane gaps**              | Dead `/projects/stream`; unpopulated chat metadata; missing badge/identity/validate data           | Projects live-update, Chats pinned/preview/model, rail badge, provider validate              |

## Dependency DAG

```
A (icons) ─┬─► C (rail)
           ├─► D (palette)
           ├─► E (settings) ─► F (provider keys)
           └─► G (destinations)
B (tokens)─┴─► C, D, F, G

H (backend) runs in parallel; its outputs feed C (badge), F (validate), G/Chats (projection).
```

Sequencing: **A, B first** (foundations, touch shared primitives — do NOT
parallelise across worktrees, they edit the same files). Then **C, D, G** (pure
chat-surface consumers) and **E** can proceed in parallel. **F** follows **E**
(it rides the web mount E establishes). **H** is a backend track that can start
immediately and lands its consumers into C/F/G as they're ready.

## Global conventions

- **IDs.** User stories `US-<PRD>.<n>`; functional reqs `FR-<PRD>.<n>`;
  non-functional reqs `NFR-<PRD>.<n>`; PRs `PR-<PRD>.<n>`. e.g. `FR-A.3`, `PR-C.2`.
- **SSOT discipline.** Every concept has exactly one canonical owner. The rule of
  this suite: if a thing is shared by web + desktop, it lives in `chat-surface`
  and both hosts bind it through ports/props. No `apps/* → apps/*` imports;
  `chat-surface` stays browser-primitive-free (ports only). See
  `packages/chat-surface/CLAUDE.md`.
- **PR size.** Each PR independently mergeable, reviewable, ≤ ~800 LOC, leaves the
  tree green (`npm run typecheck` + `vitest` for TS; `pytest` in the owning
  service `.venv` for Python).
- **Parity source.** The design spec is the imported v3 files; the extracted
  reference lives in the audit scratchpad `DESIGN-REFERENCE.md` and the per-area
  `FINDINGS-*.md`. Exact tokens/dimensions are quoted in each PRD's UI/UX
  checklist.

## Cross-cutting decisions (called out, not silently made)

1. **`PageHeader` on destinations.** The v3 destinations open directly with a
   small `.pg-lead` intro — the rail already labels the screen — whereas the
   current Activity/Chats/Projects each render a 22px `PageHeader` title.
   **Decision (PRD-G):** drop the page title, keep a one-line lead. Recorded as a
   deliberate divergence-removal, not a regression.
2. **Button weight 650.** `design-system/styles.css` documents `.ui-button`
   `font-weight: 650` as an _intentional brand "press-me" vocabulary_. The v3
   design uses 500/600 and a denser `--sm`. **Decision (PRD-B):** align `--sm`
   geometry to the v3 dense scale and reduce CTA weight toward the design; because
   this touches the global button vocabulary, PRD-B carries the explicit rationale
   and a scoped fallback (a `--dense` row-button modifier) if the team wants to
   keep 650 for hero CTAs.
3. **⌘K search vs launcher.** The current palette is a full backend search index
   (richer than the v3 static 13-command list). **Decision (PRD-D):** keep the
   live index and add the static command layer _on top_ as the empty-query default
   and always-present `command` hits — complementary, not either/or.
4. **Projects team-model.** The current Projects surface implements a team/ACL
   model (members/todos/inbox/library/routines tabs) with no v3 counterpart.
   **Decision (PRD-G):** the solo v3 parity target is tab-less (`.sect-h`
   sections); the team tabs are profile-gated (`team`) rather than deleted, so the
   team model survives behind the deployment profile.
