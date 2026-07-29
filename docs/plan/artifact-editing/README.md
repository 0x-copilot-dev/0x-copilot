# Artifact editing — correcting the Studio edit path

Four PRDs correcting defects found by driving the packaged desktop app on
2026-07-29. Every claim below is backed by the live logs, the live file-native
store, or code at `main@4bb1ed34`.

- [PRD-01 — user-edit causal lane](PRD-01-user-edit-causal-lane.md)
- [PRD-02 — `artifact.revise` + tab identity](PRD-02-artifact-revise-and-tab-identity.md)
- [PRD-03 — revision review (diff + revert)](PRD-03-revision-review.md)
- [PRD-04 — truthful publication reporting](PRD-04-truthful-publication.md)
- [STATUS](STATUS.md) — progress and Definition-of-Done checkoff

## The two reported defects

**Bug 1 — "Save patched revision" does nothing.** Editing cells and saving returns
409; the UI says "A newer revision exists" when no second revision exists.

**Bug 2 — "add a row" opens a second tab.** The model mints a new artifact instead
of revising, and no diff or accept/reject is offered.

A third defect was found while diagnosing: the model claimed the CSV was saved to
the user's documents folder, which was structurally impossible.

## Root causes, by category

The four hypotheses considered were: missing service boundary, missing service,
missing SSOT, or simply unimplemented. The honest answer differs per defect, and
**none of them is "missing service"** — `ArtifactService` exists and is correct.

| Defect                   | Category                                        | Why                                                                                                                                                 |
| ------------------------ | ----------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------- |
| Bug 1 (save refused)     | **Missing internal boundary**                   | Authorship and causality are independent axes; the code collapses them. `ArtifactAuthor` models `USER`, but `ArtifactScope` admits only a run lane. |
| Bug 2a (no revise verb)  | **Unimplemented — asymmetric surface exposure** | One correct service, two consumers (human HTTP, model tools); only the human side got the full verb set, and nothing asserted parity.               |
| Bug 2b (identical tabs)  | **SSOT break**                                  | Display identity derived twice; the tab synthesizes a label and discards the record's real `title`.                                                 |
| Bug 2c (no diff/approve) | **Never designed**                              | Artifacts are versioned-not-approved; only effects get stage→diff→approve.                                                                          |
| Publication claim        | **Missing grounding**                           | The tool result is silent on destination, so narration filled the gap.                                                                              |

## Decisions

**D-A. A user edit is caused by the conversation, not by a run.**
`RunTerminationCoordinator` is _"the seal authority … the only place that can
honestly promise 'everything this run caused is already in the ledger'"_
(`run_termination.py:106`). An edit made after a run ends was not caused by that
run. The terminal guard is therefore **correct**; claiming the run is the bug. The
fix strengthens the seal rather than relaxing it — see PRD-01 D2.

**D-B. Gate at the boundary, version in the middle.**
No pre-commit approval gate on artifact writes. Irreversibility lives at egress
(send / workspace write / connector commit), which is already gated. Artifacts are
immutable and revertible, so versioning is the right control at that layer. Full
reasoning, including the tension with the one-approval-path principle and the
condition that would reverse this, is recorded in PRD-03.

**D-C. If gates are switchable, gates cannot be the only place safety lives.**
An auto-send/auto-execute mode is planned, toggleable per-tool or per-chat. A gate
that a user can disable cannot carry the audit story alone, so execution mode
becomes a recorded, server-derived fact on every artifact operation — built now,
before the mode exists, so its arrival cannot silently lose the record.

**D-D. Grounding belongs in the result, not the prompt.**
The authoritative destination fact travels with the tool result, where narration is
formed — not only in a tool description the model read once.

## Order

PRD-01 → PRD-02 → PRD-03 → PRD-04. PRD-02's revise path depends on PRD-01's lane;
PRD-03 depends on PRD-02 producing model revisions to review. PRD-04 is
independent and lands last.
