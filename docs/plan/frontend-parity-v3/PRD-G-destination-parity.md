# PRD-G — Destination surface parity (Activity / Projects / Chats)

**Status:** Draft · **Surface:** Activity, Projects, Chats (web + desktop) ·
**Package:** `@0x-copilot/chat-surface` · **Blocked by:** A, B · **Feeds from:** H
(Chats projection)

## 1. Context & problem

The three list destinations share the same v3 anatomy — a `.pg-lead` intro,
mono `.sect-h` section headers, and one bordered `.rowlist` card per group whose
rows (`.lrow`) carry a **leading icon**, a name + status chip, a body-font
sub-line, and a mono time. All three diverge from it in the same ways, plus
surface-specific issues.

- **Activity** (`destinations/activity/ActivityDestination.tsx`): rows have **no
  leading icon**; each row is its own bordered chip on `--color-bg-elevated`
  instead of one `.rowlist` card per day; chip schema inverted (done→grey,
  stopped→red — fixed by PRD-B `statusTone`); meta uses mono not body font; adds a
  22px `PageHeader` the design omits; `.act-day` 12.5px vs 10px. Structurally
  correct otherwise (tab-less, day-grouped — matches design).
- **Projects** (`destinations/projects/*`): the **web list is a bare `<ul>`
  scaffold**, not the `.grid3` card grid (`ProjectsRoute.tsx:826-946`); detail is
  an **8-tab `TabsBar`** with no design counterpart (design uses `.sect-h`
  sections); list has a `FilterTabs` bar the design lacks; icon tile is an emoji on
  `hsl()` (design: project-colour + first letter); status pill hard-codes colours
  (PRD-B).
- **Chats** (`destinations/chats/ChatsArchive.tsx`): correct 3-bucket grouping but
  rows have **no leading icon** (design: live→brand `Mark` in success, else chats
  icon); missing `.pg-lead`; a 22px `PageHeader` + oversized top-right "New chat"
  instead of a small primary on the Pinned header; sub-line drops the inline mono
  model; done→grey chip (PRD-B); "Archived" vs "Archived · history".

## 2. Goals / Non-goals

**Goals**

- G1 — A shared row primitive (`.lrow` equivalent) + `.rowlist` container + a
  `.sect-h` section header + a `.pg-lead` lead, used by all three destinations, so
  row anatomy is defined once.
- G2 — Each destination adopts the design layout: leading icons, one card per
  group, chip via PRD-B `statusTone`, body-font meta, mono time, correct section
  headers.
- G3 — Projects: render the `.grid3` card grid on the web list; replace the detail
  `TabsBar` with `.sect-h` sections (team tabs profile-gated, not deleted); drop
  the list `FilterTabs`; icon tile = project-colour + first letter.
- G4 — Resolve the `PageHeader` question (README decision 1): drop the page title,
  keep a `.pg-lead`.

**Non-goals**

- NG1 — Chats pinned/preview/model **data** (unpopulated metadata) — that's PRD-H;
  this PRD renders them when present and lays out the row for them.
- NG2 — Projects backend `/stream` + durable store — PRD-H.
- NG3 — Deleting the team/ACL project model — it's profile-gated behind `team`.

## 3. User stories

| ID     | As a…     | I want…                                                      | so that…                                          |
| ------ | --------- | ------------------------------------------------------------ | ------------------------------------------------- |
| US-G.1 | Solo user | list rows with a leading icon + status chip in one card      | the lists read like the design, not stacked chips |
| US-G.2 | Solo user | Projects as a card grid, opening to sectioned detail         | I browse projects visually, not as a text list    |
| US-G.3 | Solo user | a completed run shown "done" in green with a clock/live icon | status + liveness are legible at a glance         |
| US-G.4 | Developer | one row/rowlist/section primitive                            | the three destinations can't drift row anatomy    |

**Acceptance (US-G.2):** _Given_ the Projects destination on web, _when_ it loads,
_then_ it shows a `.grid3` of project cards (colour tile + first letter + name +
"N chats · M files"); _when_ I open one, the detail shows "Chats · N" and
"Files · M" `.sect-h` sections over `.rowlist`s, with no tab bar (solo profile).

## 4. Functional requirements

- **FR-G.1** — Add shared primitives in `chat-surface/src/shell/` (or
  `destinations/_shared/`): `RowList` (bordered/rounded `--color-surface` card with
  internal hairlines), `Row` (`.lrow`: leading `slot`/`Icon`, main title+sub,
  right meta), `SectionHeader` (`.sect-h` mono uppercase), `PageLead` (`.pg-lead`
  12px muted). All token-driven; consume PRD-A `Icon` + PRD-B `statusTone`.
- **FR-G.2 (Activity)** — Rows use `Row` with a leading icon (`clock`, or live →
  brand `Mark` size 18 in `--color-success`); one `RowList` per day; meta body
  font; `.act-day` 10px + trailing hairline; drop `PageHeader`, add `PageLead`;
  chip via `statusTone` (dot live-only).
- **FR-G.3 (Chats)** — Rows use `Row` with a leading icon (live → brand `Mark`
  success, else `chats`); sub-line = `preview · <mono>model</mono>`; move "New
  chat" to the Pinned `SectionHeader` as a small primary button with a `plus` icon;
  add `PageLead`; drop `PageHeader` title; "Archived · history"; chip via
  `statusTone`. (Pinned/preview/model may be empty until PRD-H; the row renders
  gracefully.)
- **FR-G.4 (Projects list)** — Replace the web `<ul>` scaffold with a `.grid3` of
  project cards (`proj-ic` = project colour bg + `name[0]`, 32×32; name
  `--font-size-md` display; desc; "N chats · M files"); grid `repeat(3,1fr)`
  collapsing to 1 col < 900px; drop the list `FilterTabs` for the solo profile.
- **FR-G.5 (Projects detail)** — Replace `TabsBar` with `.sect-h` sections
  ("Chats · N" → `RowList`; "Files · M" → `RowList`) for the solo profile; keep the
  team tabs only under `profile === "team"` (gated, not deleted); backlink "← All
  projects"; icon tile project-colour + first letter; status via `statusTone`
  (PRD-B).
- **FR-G.6** — Requires the `ItemLink kind="project"` resolver to surface real
  project names (currently returns the literal "Project"), so card/detail can link
  by name (blocks the scaffold removal).

## 5. Architecture & system design

- **SSOT.** One `Row`/`RowList`/`SectionHeader`/`PageLead` family owns list-surface
  anatomy; Activity/Chats/Projects compose it with destination-specific data. Chip
  colour = `statusTone` (B); icons = `Icon` (A). No destination hard-codes row
  chrome.
- **Boundaries.** Presentational; data via existing host binders
  (`ActivityRoute`/`ChatsArchiveRoute`/`ProjectsRoute`). `chat-surface` stays
  port-clean.
- **Reuse vs new.** New: `Row`/`RowList`/`SectionHeader`/`PageLead` (some may
  generalise existing `DocList`/`ActivityList`). Modify the three destination
  components + `ProjectsRoute` scaffold + `ItemLink` project resolver. Prefer
  generalising `DocList` over a parallel primitive.

## 6. Affected files

- **Create:** `chat-surface/src/destinations/_shared/{Row,RowList,SectionHeader,
PageLead}.tsx` (+ tests) — or generalise `shell/DocList.tsx`.
- **Modify:** `destinations/activity/ActivityDestination.tsx`;
  `destinations/chats/ChatsArchive.tsx`;
  `destinations/projects/ProjectsDestination.tsx`, `ProjectDetailView.tsx`;
  `apps/frontend/src/features/projects/ProjectsRoute.tsx` (scaffold → grid);
  `refs/index.ts` (`ItemLink` project name resolver).

## 7. PR / commit breakdown

- **PR-G.1** — Shared `Row`/`RowList`/`SectionHeader`/`PageLead` primitives + tests. M.
- **PR-G.2** — Activity adopts primitives (icons, one card/day, meta font,
  act-day, drop PageHeader). Depends A, B, PR-G.1. M.
- **PR-G.3** — Chats adopts primitives (row icon, sub-line, Pinned-header CTA,
  lead, archived label). Depends A, B, PR-G.1 (+ H for data, soft). M.
- **PR-G.4** — Projects list card grid (+ `ItemLink` resolver). Depends PR-G.1. M.
- **PR-G.5** — Projects detail `.sect-h` sections; team tabs profile-gated. M.

## 8. Testing plan

- **Unit** (primitives): `Row` renders leading icon slot + title + sub + right
  meta; `RowList` one bordered card with hairline separators; `SectionHeader` mono
  uppercase; `PageLead` 12px muted.
- **Unit** (Activity): live row → brand `Mark` (success), else `clock`; one
  `RowList` per day; chip via `statusTone` (done→success, stopped→muted); meta body
  font; no `PageHeader`.
- **Unit** (Chats): live → `Mark` success else `chats`; sub-line has mono model
  when present; "New chat" in Pinned header; "Archived · history".
- **Unit** (Projects): list renders `.grid3` cards (colour tile + first letter +
  "N chats · M files"); detail renders `.sect-h` sections, no `TabsBar` for solo;
  team profile still shows tabs.
- **Integration:** `ItemLink kind="project"` resolves a real name.
- **Regression:** existing destination tests updated; empty/loading/error states
  render (empty section shows, per design, rather than vanish).

## 9. UI/UX acceptance checklist

- [ ] All three: `.pg-lead` present; no 22px page title; one `RowList` card per
      group; rows have leading icons; chips via `statusTone`; mono time; body-font
      meta.
- [ ] Activity: `.act-day` 10px + trailing hairline; live row brand mark in
      success; dot live-only.
- [ ] Chats: 3 sections (Pinned/Recent/Archived · history); Pinned-header primary
      "＋ New chat"; sub-line `preview · model`.
- [ ] Projects: `.grid3` cards (colour tile + first letter); detail `.sect-h`
      sections, tab-less for solo, tabs gated for team; backlink "← All projects".
- [ ] States default/hover/active/empty; light + dark; reduced-motion.

## 10. Dependencies & sequencing

Upstream A, B. Soft dep on H (Chats data, Projects `/stream`). Land PR-G.1 first;
then G.2/G.3/G.4/G.5 in parallel.

## 11. Risks & mitigations

| Risk                                       | Mitigation                                                                 |
| ------------------------------------------ | -------------------------------------------------------------------------- |
| Removing Projects tabs hides team features | Profile-gate (team) not delete; solo gets `.sect-h`, team keeps tabs       |
| Chats rows look bare until H lands         | Row degrades gracefully (no model/preview → title+chip+time); H fills them |
| New primitive duplicates `DocList`         | Generalise `DocList` instead of a parallel component                       |

## 12. Definition of done

- [ ] Shared row primitives shipped + consumed by all three destinations.
- [ ] Activity/Chats/Projects match the design layout; `PageHeader` decision
      applied; Projects list is a card grid + sectioned detail.
- [ ] Chip colours via `statusTone`; icons via `Icon`; unit + integration green;
      web + desktop verified; typecheck + vitest green.
