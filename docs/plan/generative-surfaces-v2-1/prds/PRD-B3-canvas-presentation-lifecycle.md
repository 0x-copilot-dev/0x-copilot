# PRD-B3 — Canvas presentation and lifecycle 🎨

**Goal.** Make Studio and Focus honest across chat-only answers, model artifacts, tool
activity, reads with/without a surface, gates, stages, and terminal receipts. Surface
creation becomes a selective presentation decision rather than an automatic consequence
of a tool returning an object.

## Implementer brief

Read:

1. `../00-overview.md` §§2.2, 2.4, 5–7.E.
2. `../01-sdr.md` §§7.3, 12, 13 S1–S3.
3. `PRD-A3-operation-gateway.md`.
4. `PRD-B1-agent-authored-artifacts.md`.
5. `PRD-B2-artifact-renderers-editors.md`.
6. `packages/chat-surface/src/thread-canvas/ThreadCanvas.tsx`.
7. `packages/chat-surface/src/thread-canvas/TcSurfaceMount.tsx`.
8. `packages/chat-surface/src/destinations/run/RunDestination.tsx`.
9. `packages/chat-surface/src/destinations/run/projectReceipt.ts`.
10. `packages/chat-surface/src/thread-canvas/ledgerProjection.ts`.

The screenshots motivating this PR show the defect: a chat-only answer can leave a
blank Studio canvas during the run and later replace it with a read-only receipt. Fix
the lifecycle, not only the empty-state copy.

## Context

Surfaces are durable views over subjects; they are not proof that work happened. Some
runs should never create one. The UI must distinguish:

- waiting to know whether a surface will exist;
- chat-only completion;
- a surface that is still loading;
- a parked gate/staged effect;
- a failed surface with raw fallback;
- a terminal receipt that is useful but should not steal focus.

## Interfaces consumed

- A3 predicted/canonical `artifact.presentation_decided` and operation events.
- B1 artifact events.
- B2 artifact subject renderers.
- Existing surface/view/stage/gate/receipt events and client projectors.
- Existing Studio/Focus shared `RunDestination`.

## Interfaces exposed

`PresentationPolicy` server-side and a matching pure client projection:

```text
PresentationDecision:
  decision: canvas | chat_card | activity_only | none
  subject: SurfaceSubject?
  renderer_hint?
  basis
  priority
```

`CanvasLifecycleState`:

```text
assembling | presenting | chat_only | parked | failed | complete_empty
```

`CanvasProjection`:

```text
tabs, active_tab, lifecycle, pending_subjects, terminal_receipt,
activity_summary, failure
```

## Design

### D1. Server presentation policy

Policy inputs:

- explicit artifact presentation preference;
- operation descriptor/result kind;
- artifact kind/size/renderer availability;
- stage/gate state;
- user mode/preferences;
- current run surface state.

Policy table:

| Input                                              | Default                                                                                         |
| -------------------------------------------------- | ----------------------------------------------------------------------------------------------- |
| explicit artifact `canvas`                         | canvas if renderer supported, else chat card/raw                                                |
| explicit artifact `none`                           | none                                                                                            |
| durable code/document/dataset artifact with `auto` | canvas                                                                                          |
| generic file artifact                              | canvas metadata or chat card by size/type                                                       |
| record/table read selected as revisitable          | canvas                                                                                          |
| transient status/scalar read                       | activity only                                                                                   |
| external effect stage                              | canvas stage surface                                                                            |
| auth/grant gate                                    | parked gate card; canvas only if stage subject already exists                                   |
| receipt                                            | rail/summary; canvas only when user opens or no better subject and receipt has meaningful facts |
| chat-only answer                                   | none                                                                                            |

The policy never treats “mapping output” as sufficient evidence for a canvas.

### D2. Explicit assembling state

At run start in Studio:

- show activity/progress and “Preparing this run…” only while a presentation decision
  is genuinely unresolved;
- do not say “Nothing open yet” as if it were an error;
- do not fabricate a skeleton tab without a subject.

Transition:

- first canvas decision → `presenting`;
- gate/stage waiting → `parked` as appropriate;
- run completes with no canvas subject → `chat_only`;
- run completes with no narrative or subject due to failure → `failed`;
- explicitly empty cancelled run → `complete_empty`.

The state is a projection of events, not an independent mutable flag.

### D3. Chat-only completion

For a normal answer:

- chat remains primary;
- Studio canvas shows a compact, intentional “Answered in chat” state;
- no tab is created;
- no receipt auto-opens;
- metrics may show zero reads/writes without occupying the canvas;
- user can still open run receipt from rail/menu.

Exact copy should be design-reviewed and test-pinned. It must not imply missing work.

### D4. Existing surface continuity

If a follow-up turn produces no new surface:

- keep the last relevant open surface visible;
- show new activity in chat/rail;
- do not clear or replace the canvas with a receipt;
- if the user manually closed all tabs, respect that state.

If a follow-up updates the same artifact/record, update the existing tab by stable
subject identity. Do not create duplicate tabs for revisions.

### D5. Receipt behavior

Receipt is a ledger projection, not a universal terminal canvas.

Auto-create/open rules:

- meaningful effects, gates, artifacts, or provenance may create a receipt subject;
- zero-operation chat-only run keeps receipt available in rail but not as a canvas tab;
- receipt never becomes active over an artifact/stage the user is reviewing;
- terminal event cannot clear the active tab.

Update the receipt fold so artifacts and generalized effects are counted. Copy must not
say “Every write…” for a run with no writes unless displayed as an applicable invariant
inside a real receipt.

### D6. Focus mode

Focus contains:

- narrative chat;
- compact artifact card with Open/Download/Save actions;
- compact staged-effect/gate cards;
- no full tabbed generative canvas.

Opening an artifact can switch to Studio or open the host-approved detail affordance.
Both modes consume the same events/projectors.

### D7. Activity-only operations

For a read/result with no surface:

- activity line/card identifies capability, operation, status, and safe summary;
- source/provenance appears in Sources;
- no tab;
- raw result is not dumped into chat unless the model uses it in its answer or the user
  explicitly opens raw details.

For a selected record/table surface:

- presentation event identifies subject and renderer;
- hydration resolves through canonical payload/artifact ref;
- loading and raw fallback are explicit.

### D8. Hydration contract repair

The current `SurfaceContentProjection` expects legacy `surface` fields in
`tool_result`, while the post-cutover event carries `output`. Replace this implicit join
with a declared resolver:

```text
surface/presentation event -> subject/ref -> authorized payload/artifact resolver
```

Tests must use actual production event shapes. Do not keep endpoint tests green by
manufacturing retired envelopes.

### D9. Tabs and selection

- tab identity is `subject_type + subject_id`, not title;
- revisions update tab content, not identity;
- deterministic ordering: user-pinned, active stage/gate, recently updated artifacts,
  records, receipt;
- titles are untrusted text;
- tab close/pin is local presentation preference, not ledger truth;
- deep links use opaque subject ids and run scope.

### D10. Failure behavior

- presentation policy error: activity continues; honest raw/no-surface fallback;
- hydration failure: tab remains with retry/raw metadata;
- renderer crash: error boundary isolates tab;
- operation failure before subject: canvas transitions to failed/chat-only based on
  final response;
- reconnect/replay reconstructs identical lifecycle and active-subject recommendation.

## Implementation plan

1. Add pure server PresentationPolicy with exhaustive table tests.
2. Emit `artifact.presentation_decided` from B1 flows and canonical decisions from
   gateway adapters.
3. Extend Python/TypeScript projectors with `CanvasLifecycleState`.
4. Repair content hydration around subject refs.
5. Refactor `ThreadCanvas` empty/loading/failure states.
6. Update `RunDestination` so terminal receipt cannot steal active selection.
7. Add Focus artifact/stage/gate cards.
8. Update Sources/receipt projections for artifacts/effects.
9. Add replay parity fixtures for all lifecycle states.
10. Add design-parity and live host smoke.

## Test plan

### Required journeys

1. bat-and-ball answer: no tab during/after; explicit chat-only state.
2. “write a Python function”: chat only unless explicitly published.
3. “create a Python file”: artifact tab appears before terminal receipt.
4. MCP scalar read: activity only.
5. MCP list selected for table: one table tab.
6. existing artifact revised: same tab identity.
7. workspace stage: stage tab remains active through run completion.
8. auth/grant park: gate card and recoverable parked state.
9. hydration error: raw/retry, no blank canvas.
10. reconnect at every event prefix: identical lifecycle.

### Selection regressions

- receipt does not replace active artifact/stage;
- chat-only follow-up does not clear previous surface;
- user-closed tabs stay closed locally;
- duplicate presentation events do not duplicate tabs.

### Host/accessibility

- Focus has cards but no canvas;
- web/desktop share behavior;
- keyboard tab management and announcements;
- design parity has 0 HIGH drift.

## Definition of done

- [ ] Chat-only runs have an intentional non-surface lifecycle.
- [ ] Tool execution no longer implies a canvas surface.
- [ ] Receipt is conditional and never steals active work.
- [ ] Production-shaped events hydrate surfaces correctly.
- [ ] Focus and Studio consume one projector with mode-specific presentation.
- [ ] Replay reconstructs every lifecycle state.
- [ ] UI and standard DoD pass.

## Out of scope

- New artifact content renderers beyond B2.
- Workspace broker implementation.
- Full MCP gateway cutover.
- Timeline redesign.

## Guardrails

- Do not fix this with a timeout that guesses “no surface.”
- Do not use terminal receipt as the default empty canvas.
- Do not derive tab identity from title.
- Do not create a surface merely because an output is an object.
- Do not maintain separate lifecycle truth in React component state.
