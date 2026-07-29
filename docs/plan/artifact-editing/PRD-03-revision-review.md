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

When a revision arrives whose author is `MODEL`/`SUBAGENT` and whose
`parent_revision` is the revision currently on screen, the surface opens the
r(n-1)→r(n) comparison inline rather than silently swapping content. Two actions:

- **Keep** — dismiss; the new revision stays current.
- **Revert** — `restore(parent_revision)`, which appends a new revision equal to
  the parent. History is never rewritten.

A user-authored revision does not raise the diff — the user just made it.

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

## Implementation plan

1. `ArtifactSurface.tsx` — detect model-authored revision arrival; open comparison
   against `parent_revision`; render keep/revert actions.
2. Reuse `compareToCurrent` + `restore`; no new transport calls.
3. Backend: add the mode field to the artifact operation audit record; derive
   server-side; assert `staged` in tests.
4. Surface the diff in the dataset renderer path too, so a CSV revise shows changed
   cells rather than only a text diff, if the existing dataset model supports it
   without new machinery; otherwise fall back to the text diff and record the gap.

## Test plan

- Model revision arrives → diff shown against `parent_revision`, content not
  silently swapped.
- **Keep** → new revision remains current, no extra revision appended.
- **Revert** → a further revision is appended equal to the parent; history intact
  (r1, r2, r3 all retrievable).
- User-authored revision → no diff prompt.
- Oversized revision → existing `too_large` path, unchanged.
- Audit: every artifact operation carries a server-derived mode; `staged` today.

## Definition of done

- [ ] A model revision presents a diff with keep/revert rather than a silent swap.
- [ ] Revert appends rather than rewrites; all revisions remain retrievable.
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
