# Generative Surfaces v2.1 — End-to-End Handoff

**Snapshot:** 2026-07-27

**Baseline:** `origin/main` at `b47e4ee9`

**Purpose:** Continue delivery without rediscovering architectural decisions, completed corrective work, merge order, or remaining release evidence.

## 1. Product boundary

Generative Surfaces is a Studio-mode interaction system. The model may request
tools and declarative surface shapes, but it must never obtain arbitrary UI-code
generation privileges or a direct path to an external or local side effect.

The system has four distinct paths:

1. **Plain chat:** model response only; no tool result needs a canvas.
2. **Read surface:** a tool result becomes a provenance-bearing, declarative
   table, record, raw view, or receipt on the canvas.
3. **Staged mutation:** a requested change becomes an immutable, revisioned
   proposal. It may be rendered and edited, but may not execute until the exact
   revision is approved.
4. **Desktop filesystem mutation:** the same staged-effect protocol reaches the
   Electron main-process authority through an explicit permit. Model/runtime
   code does not obtain filesystem authority.

The `WorkLedger` is the replayable source for surface and approval state. It
does not replace durable artifact bytes, effect claims, or main-process
filesystem authority; it records the relationship between them.

## 2. Architecture that must remain true

### 2.1 Surface and effect separation

- Surface construction is declarative and renderer-owned. The model supplies
  data, intent, and a constrained shape request—not TSX/JS.
- A view can be generic, shaped, raw, or user-pinned. Regeneration consumes the
  stored payload; it must not repeat connector traffic.
- A write is a stage/revision/decision/effect lifecycle, never a side effect
  hidden inside rendering or a tool adapter.

### 2.2 Capability direction

```text
model-visible tool
  -> neutral operation request/result
  -> staging gateway
  -> immutable artifact + durable stage + ledger
  -> exact-revision approval
  -> durable effect command
  -> worker effect coordinator
  -> connector or Electron-main authority
```

No earlier layer may retain a raw connector, outbox, overlay store, Electron
IPC sender, or filesystem-capable object that can bypass the next layer. Python
objects alone are not a security boundary when model-reachable code can use
reflection; sensitive authority requires a real process/RPC boundary or an
unforgeable host-owned capability.

### 2.3 Fail-closed lifecycle

For every effect, stage persistence, visible overlay projection, approval
eligibility, and outbox dispatch must describe the same immutable proposal.
If any binding or compensating action cannot be durably recorded, the effect
must become execution-denied. It is insufficient to return an error while a
previously held stage remains approvable.

### 2.4 Filesystem-first desktop rule

Desktop/local-file work uses the Electron main-process broker as the authority.
Use local fixtures for CSV, Markdown, documents, code, email/X/Discord
simulations, and their edit histories. Do not introduce a Postgres fallback for
the desktop authority path. PostgreSQL is a hosted/shared-runtime concern, not
the desktop filesystem owner.

## 3. Merged work

| Area                                                                                                     | Evidence                  | Status                              |
| -------------------------------------------------------------------------------------------------------- | ------------------------- | ----------------------------------- |
| Ledger contracts, surface store, provenance, views, classifier/gates, staged writes, receipts, approvals | Historical A1–E2 delivery | Merged; final release audit remains |
| Immutable draft ownership                                                                                | PR #356                   | Merged                              |
| Filesystem-first desktop defaults                                                                        | PR #370                   | Merged                              |
| G0 plain-chat supervised journey harness                                                                 | PR #376                   | Merged                              |
| G1 Markdown lifecycle harness                                                                            | PR #382 / `d383c44c`      | Merged                              |
| G2 CSV lifecycle harness                                                                                 | PR #383 / `42fe4ec4`      | Merged                              |
| E2 rollout admission                                                                                     | PR #380 / `3fd70913`      | Merged                              |
| D3 hosted OpenAI container provider                                                                      | PR #381 / `63e6c10b`      | Merged but deliberately dark        |
| F012 workspace mutation boundary and durable projection binding                                          | PR #371 / `67ba983b`      | Merged                              |
| F012 projection-binding race hardening                                                                   | `459c238a`                | Merged                              |
| F013 neutral operation boundary                                                                          | PR #374                   | Merged                              |

### 3.1 E2 rollout admission

Admission gates both generic and staged effect lanes. A governed stage carries a
durable rollout capability mark, so a kill switch, cohort removal, or restart
after queueing cannot silently treat it as a legacy command. Connector discovery
is gated before MCP cards are exposed. The remaining evidence is a supervised
staging run with a test MCP server: prove card suppression and prove a queued,
approved command performs zero outbound call after cohort removal/kill/restart.

### 3.2 D3 hosted provider

The provider is a narrow OpenAI SDK transport. It requires a one-use capability
minted by the remote-execution service only after attestation and durable cleanup
reservation. Direct `create`/transport calls fail before SDK traffic; composition
must inject the SDK client (no ambient credential construction). It remains dark
until C1/A2/import prerequisites exist. Enabling it without those prerequisites
is not permitted.

### 3.3 G1/G2 journey rules

The journeys are real supervised-Electron acceptance harnesses, not mocks.
CI/release must fail unmet runtime or BYOK prerequisites; manual invocations may
report a structured skip. A valid run proves Electron-main production posture,
exact immutable artifact ID/reference/digest, exact approved bytes, and native
journal/receipt evidence.

## 4. Completed corrective designs

### 4.1 PR #371 — F012 workspace mutation boundary (MERGED)

PR #371 merged the durable projection-binding design specified in
`prds/PRD-371-workspace-projection-binding.md`. Workspace stages now require an
exact current-revision `effect.projection_bound` event before approval or
execution. Proposal material is persisted before staging, staging precedes
overlay projection, and the canonical append result removes the second
post-append read failure window.

The fold, approval service, and worker independently fail closed for missing,
stale, or forged bindings. Retrying a projection/binding failure reuses the same
operation and stage idempotently. Follow-up `459c238a` hardened the two remaining
races: approval now revalidates the binding inside the transaction that enqueues
the command, and cancellation now retries the canonical stage after a
projection failure.

Model-reachability canaries remain at the real typed tool and Monty JSON seams.
Actual host writes still cross the Electron main-process capability broker; no
new authority service or database migration was introduced.

### 4.2 PR #374 — F013 neutral operation boundary (MERGED)

Merged to `main` by PR #374. The repair retained neutral presentation ownership,
added gateway-owned post-approval authorization for the exact immutable
revision, and routes dispatch through the shared effect coordinator without
restoring presenter/projector/raw-connector authority to adapters.

## 5. Required process for every implementation PR

1. Implement in an isolated worktree and commit only scoped changes.
2. Run focused behavior tests, type checks, format/lint, then the affected full
   service suite. Test failure/cancellation/replay—not only happy paths.
3. Push a draft PR. Do not merge from a stale `main`.
4. Perform an independent architectural review when the change crosses an
   authority, approval, connector, filesystem, or process boundary. The review
   attacks bypasses, reflection/indirection, stale/replayed commands, restart,
   rollback, flag transitions, and tenant scope.
5. Rebase in that same worktree immediately before merge:

   ```bash
   git fetch origin +refs/heads/main:refs/remotes/origin/main
   git rebase origin/main
   git push --force-with-lease
   ```

6. Wait for CI produced by the rebased head, then mark ready and merge with the
   exact `headRefOid` supplied to the GitHub merge endpoint.
7. Fetch `origin/main` after each merge. Rebase subsequent branches again; do
   not reuse CI from an ancestor.

## 6. Remaining release evidence

| ID  | Gate                | Completion evidence                                                               |
| --- | ------------------- | --------------------------------------------------------------------------------- |
| R1  | Repair/merge F012   | **Done:** PR #371 plus race hardening `459c238a` merged on `main`                 |
| R2  | Repair/merge F013   | **Done:** PR #374 merged on `main`                                                |
| R3  | D3 C1/A2 handoff    | Snapshot export, durable deliverables, explicit patch import                      |
| R4  | Real G0/G1/G2 runs  | Supervised desktop, actual BYOK, main-process local-file write, exact receipts    |
| R5  | Remaining journeys  | Code, Markdown, CSV, docs, local fake email/X/Discord, multi-step revisions       |
| R6  | Design parity       | Computed-style audit using `tools/design-parity`, not screenshot comparison       |
| R7  | Regression          | Current-main full service/app regression after all merges                         |
| R8  | Final release smoke | Web plus supervised desktop smoke and requirement-by-requirement release decision |

## 7. Operational safety

- The shared checkout may be user-dirty. Never reset, clean, or commit it.
- Use a dedicated worktree for every repair and rebase it independently.
- Never echo or commit `.env` provider keys. Real-run tooling reads keys only
  inside the running application.
- Do not run broad Git garbage collection/prune to suppress worktree warnings.
- A green hermetic suite is necessary but does not substitute for supervised
  desktop, connector, or provider evidence where the requirement is real-world.
