# PRD-03 — Revision review: post-hoc diff and revert

**Status:** specified
**Closes:** live Bug 2 (c) — "I didn't see the inline diff, or edit/accept/reject"
**Decision:** gate at the boundary, version in the middle

## Implementer brief

When the model revises an artifact, show the r(n-1)→r(n) diff with **keep** or
**revert**. Do **not** add a pre-commit approval gate on artifact writes. The
primitives already exist; this PRD is mostly wiring plus one new durable fact.

## The decision and why

The user expected stage → diff → approve, as connector writes get. The PRD
deliberately does not build that. Reasoning, recorded so it can be revisited:

**Where irreversibility lives.** The effect stager exists because sending an email
gets exactly one shot — the only control point is _before_. Artifacts are
immutable revisions with full history; revert is appending a copy of the prior
revision. The artifact repository is already a transaction log. A pre-commit gate
on an append-only versioned store solves a problem the store already solved. The
irreversible step is not the revision — it is content _leaving_ the system (send,
workspace write, connector commit), and those are already gated effects.

**Scale.** Confirmed by the product owner: working with ~15 apps in one
run/session is expected. Pre-commit gating every artifact write makes that session
unusable and trains reflex-approval, which erodes the gate on acts that genuinely
cannot be undone.

**Reversibility of the decision.** post-hoc → pre-commit is additive. pre-commit →
post-hoc requires removing a gate and migrating staged rows. Post-hoc is the
choice that stays cheap to revisit.

**The auto-send finding.** The product owner confirmed an auto-send/auto-execute
mode is planned, switchable per-tool or per-chat. That initially reads as the
condition that would favour the stager, but it is not: in auto mode the user has
_deliberately disabled_ gating, so an artifact gate would be bypassed too; in
staged mode egress is already gated. What it actually proves is stronger and is
why D2 below exists — **if gates are switchable, gates cannot be the only place
safety lives.** Versioning survives the mode toggle; gates do not, by definition.

**The one-approval-path principle.** `generative-surfaces-v2-1/README.md:20`
forbids a second approval path. Revert is version control, not approval, so the
gate stays singular and lives at egress. This is the honest tension in the
decision and is recorded as such.

**What would flip this:** an auto-consume path where a bad revision is silently
egressed _without the user having opted out of gating_. No such path exists today.

## Interfaces consumed

Already built, all of it:

- `compareArtifactText` + `ArtifactSurface.compareToCurrent` (`ArtifactSurface.tsx:148`)
- `restore(targetRevision)` (`ArtifactSurface.tsx:211`) — the revert primitive
- `DiffText` + word-level diff from PRD-06
- PRD-01 causal lane; PRD-02 `artifact.revise`

## Design

### D1. Diff surfaces automatically on a model revision

When a revision arrives whose author is `MODEL`/`SUBAGENT` and which is **newer
than the revision currently on screen**, the surface opens the comparison inline
rather than silently swapping content. The comparison base is **the revision that
was on screen** — normally the landed revision's parent, but not always (below).
Two actions:

- **Keep** — dismiss; the new revision stays current.
- **Revert** — `restore(baseRevision)`, which appends a new revision equal to the
  revision that was being read. History is never rewritten.

Any newer agent revision qualifies, not only the direct child. A turn that writes
r2 and r3 moves the tab r1→r3, so `parent_revision` (2) is not the revision on
screen (1); scoping the rule to the direct child let exactly that case swap
content silently, which is the defect this feature exists to prevent. The rule is
therefore stated on distance-independent terms: newer than what the reader had.

Three guards bound it, and none of them moved:

- **Reader navigation is not an arrival.** Selecting a revision from history
  raises nothing — the reader asked for it. First paint is likewise not an
  arrival.
- **The landed revision must be the head.** The comparison is always
  base→head, so a landed revision a newer head has already superseded has no
  honest reading and raises nothing.
- **A user-authored revision does not raise the diff** — the user just made it.
  That includes the revert appended by **Revert**.

### D2. Execution mode is a recorded fact

No `execution_mode` / `approval_mode` concept exists in the service today
(verified: no matches). This PRD introduces the **recording seam**, not the mode:

- every artifact-domain operation records the effective mode it ran under
  (`staged` today, `auto` once that mode ships) as a durable, auditable field;
- the value is server-derived, never client-supplied;
- the audit record answers "was this gated?" after the fact.

Building the seam now is the point: when auto-send lands, it cannot silently lose
the record, because the field already exists and the audit test already asserts it.
Today the recorded value is always `staged`, and a test pins that — an honest
constant, not a stub.

### D3. Revert is bounded

`restore` already enforces `revisionRestoreLimit` and returns `too_large` for
oversized revisions; that path is reused unchanged, including its existing UI.

### D4. A dataset revision diffs cells, not source text

A word-level diff of CSV source is close to unreadable, and the artifact that
produced the original report was a CSV, so this is the common path rather than an
edge case. A revised dataset therefore shows **which cells moved** — changed
cells, added rows, removed rows — each row labelled with its own line number.

- It reuses the dataset renderer's existing parse (`parseLosslessDelimited` →
  headers + rows, JSON object arrays included). No second CSV reader exists: the
  diff reads the same grid the table below it renders from.
- Rows align by trimming the shared leading and trailing rows — the same trim the
  text comparison applies to lines — and the remaining window pairs positionally.
- It **falls back to the existing word diff** when either side does not parse as a
  grid, either side was truncated by the preview budget, or the change moved no
  cell value (quoting, delimiters and whitespace live in the bytes, and only the
  text diff can show them). Over the per-kind byte bound nothing is compared at
  all, which is the pre-existing "cannot be shown as bounded UTF-8 text" path.

**Where it renders, and why it is not in the review panel.** The panel lives in
`chat-surface`; the parser lives in `surface-renderers`, and `surface-renderers →
chat-surface` is the only legal direction between the two. Parsing a grid in the
panel would either invert that dependency or fork the CSV reader. So the change
travels the other way instead: `ArtifactSurface` attaches a `datasetRevisionChange`
payload (base revision, base source, and the bounded text pair for the fallback) to
the render state — exactly how `datasetEditor` already reaches the same renderer —
and the dataset surface renders the cell table above its grid. The review panel
keeps the announcement and the two actions, and says where the change is shown
rather than putting a second, poorer reading of it on screen.

## Implementation plan

1. `ArtifactSurface.tsx` — detect model-authored revision arrival; open comparison
   against the revision that was on screen; render keep/revert actions.
2. Reuse `compareToCurrent` + `restore`; no new transport calls.
3. Backend: add the mode field to the artifact operation audit record; derive
   server-side; assert `staged` in tests.
4. Dataset path (D4): `ArtifactSurface` attaches the change to the dataset render
   state; `DatasetRevisionDiff` (in `surface-renderers`, next to the parser) diffs
   the two grids and renders changed cells above the table, falling back to the
   word diff for content that is not a grid on both sides. The review panel drops
   its text diff for that path via `changeShownInSurface`.

## Test plan

- Model revision arrives → diff shown against the revision that was on screen,
  content not silently swapped.
- Two revisions land in one turn (r1 on screen, head jumps to r3) → review raised
  with r1 as the base, `Revert to r1`.
- Reader navigates to a model revision themselves, or opens an artifact already
  sitting at one → no diff prompt.
- Landed revision already superseded by a newer head → no diff prompt.
- **Keep** → new revision remains current, no extra revision appended.
- **Revert** → a further revision is appended equal to the base; history intact
  (r1, r2, r3 all retrievable).
- User-authored revision → no diff prompt.
- Oversized revision → existing `too_large` path, unchanged.
- Dataset revision → a changed cell, an added row and a removed row each read as
  such at their own row number; not-a-grid and no-cell-value-moved fall back to
  the word diff; the review panel shows no second diff of its own.
- Audit: every artifact operation carries a server-derived mode; `staged` today.

## Definition of done

- [ ] A model revision presents a diff with keep/revert rather than a silent swap,
      including when the head skips past the revision on screen.
- [ ] Revert appends rather than rewrites; all revisions remain retrievable.
- [ ] A revised dataset presents changed cells; the word diff is the fallback.
- [ ] Execution mode is recorded and auditable on artifact operations.
- [ ] No pre-commit gate was added to the artifact write path.
- [ ] `chat-surface` / `surface-renderers` / `ai-backend` suites green.

## Out of scope

- Building auto-send/auto-execute mode itself.
- Per-hunk accept/reject on artifact content (PRD-09's overlay covers staged
  effects; artifacts are whole-revision).
- Routing artifact writes through the effect stager — explicitly rejected above.

## Guardrails

- Do **not** add a pre-commit approval gate to artifact writes.
- Do **not** implement revert by mutating or deleting a revision.
- Do **not** infer execution mode from client input.
