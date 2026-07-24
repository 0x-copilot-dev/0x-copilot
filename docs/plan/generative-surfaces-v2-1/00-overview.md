# Generative Surfaces v2.1 — Universal Artifacts & Effects

**Product and architecture overview · v0.1 · 2026-07-24 · status: for review**

This package extends the shipped
[Generative Surfaces v2](../generative-surfaces-v2/01-problem-and-requirements.md)
design. It does not replace the core v2 principles: declarative views rendered by fixed
components, a ledger as workflow truth, rev-pinned approval, honest fallback, and
usage attribution all remain. It corrects one under-scoped boundary in v2:
**artifact creation, effect classification, and presentation were wired too closely to
MCP tool results.**

Companion documents:

- [01-sdr.md](01-sdr.md) — logical architecture, contracts, boundaries, sequences,
  persistence, security, and migration.
- [02-prds.md](02-prds.md) — implementation order and one-PR-per-PRD index.
- [`prds/`](prds/) — implementation-ready PRDs with tests and definitions of done.

---

## 1. Executive summary

An agent can produce useful work in several ways:

1. answer in prose;
2. show an incidental code block or table in chat;
3. author a durable code, document, CSV, or generic file artifact;
4. read from a tool or local workspace;
5. propose a mutation to SaaS, the local filesystem, a browser session, or another
   target;
6. execute pure computation or create an isolated sandbox patch.

Those are not the same thing. In particular:

- a tool call does not imply that a surface should exist;
- a surface does not require an external tool call;
- model-authored content is allowed to become an artifact even though the model never
  authors renderer code;
- creating an app-owned artifact is not the same risk as mutating a user's external
  system or host filesystem;
- MCP is one execution transport, not the safety perimeter;
- a local filesystem mutation must receive the same exact-review, policy, audit, and
  recovery guarantees as a connector mutation.

The v2.1 architecture introduces three transport-neutral domain seams:

1. **Artifact Service** — stores durable content and revisions independently of how the
   content was produced or where it may later be saved.
2. **Operation Gateway** — normalizes model output, built-in tools, MCP, workspace,
   sandbox, and browser work into typed operations; classifies effects; and chooses
   chat/activity/artifact/stage disposition without conflating those choices.
3. **Effect Coordinator** — stages every external mutation, pins an artifact revision or
   exact argument manifest, obtains the effective policy decision, and commits through a
   target-specific executor.

For local files, the agent no longer writes through to the host after a generic
interrupt. It writes to a **versioned workspace overlay**. Reads see the overlay first,
so the agent can create, inspect, and revise `report.csv` naturally while the real file
remains untouched. Approval commits the exact reviewed overlay revision through the
Electron-main capability broker using path grants, preconditions, preimages, atomic
mutation, idempotency, and crash reconciliation.

No new deployable service is required.

---

## 2. The problem being solved

The shipped v2 implementation is strongest when a SaaS tool returns a record or when a
known workflow explicitly invokes the staged-write engine. It has four gaps:

### 2.1 Model-only work has no first-class artifact path

If the model answers with code or authored content but invokes no external tool, the
content remains chat text. The Studio canvas has no stable artifact identity, revisions,
editable representation, download target, or later save operation.

This is not a renderer-safety requirement. The model may author **content** while fixed
code/doc/table/file renderers own **presentation**.

### 2.2 Tool execution and presentation are coupled

`CallMcpTool` currently owns the read-ledger and surface-envelope seam. This leads to the
wrong implication in both directions:

- some tool calls produce audit/activity only and should not occupy the canvas;
- some non-tool work should create a surface.

Presentation must be an explicit disposition of a result or artifact, not a side effect
of crossing the MCP middleware boundary.

### 2.3 Non-MCP effects have a different safety path

The desktop already has a hardened capability broker and `/workspace/` backend. Writable
workspace calls currently pause through Deep Agents filesystem permissions, snapshot a
preimage, and then write through. That is safer than unrestricted filesystem access, but
it does not provide the v2 surface promise:

- the complete proposed bytes are not a durable editable artifact before the host
  effect;
- a sequence of agent edits is not one reviewable overlay with read-your-writes;
- approval is not necessarily pinned to the same artifact/revision contract used for
  SaaS writes;
- commit/recovery semantics are split between the generic interrupt path and the staged
  write engine.

### 2.4 The empty-canvas state is semantically ambiguous

A chat-only run can correctly produce no artifact, but Studio currently renders the same
“Nothing open yet” state while a run is assembling, after a chat-only answer, and when a
surface failed to materialize. Those are different states. A zero-write receipt can also
be technically correct while being a poor default canvas selection.

---

## 3. Binding architectural principles

These principles are requirements, not implementation suggestions.

### P1 — Tool execution, artifacts, effects, and presentation are orthogonal

The runtime decides four independent facts:

1. **Execution:** was any capability invoked?
2. **Artifact:** was durable user-relevant content created or revised?
3. **Effect:** was an external or host mutation proposed/applied?
4. **Presentation:** should the result appear in chat, activity, a surface, or more than
   one of those?

No one answer determines another.

### P2 — The model authors content, never renderer code

The model may produce source code, Markdown, CSV bytes, JSON, or another artifact body.
It may declare metadata from a closed schema: title, media type, language, and requested
disposition. It may not emit React, HTML, CSS, executable UI code, arbitrary component
names, or unvalidated renderer configuration.

Fixed first-party renderers and the existing constrained `SurfaceSpec` path own UI.

### P3 — Durable artifact intent is explicit

The system does not infer “artifact” merely because a final answer contains a fenced code
block. Artifact publication is explicit through a provider-neutral `ArtifactIntent`
contract. Producers may include:

- a model content part;
- the built-in `publish_artifact` adapter for tool-capable models;
- an existing draft write;
- a tool/result adapter;
- a user action such as **Open as artifact**.

All producers call the same Artifact Service. Plain-text-only models degrade honestly to
chat; they never rely on a fragile markdown sentinel parser.

### P4 — App-owned artifact creation is reversible, not an external write

Appending an immutable revision to the product's artifact store does not mutate SaaS or
the host filesystem and does not require write approval. Saving, sending, applying,
uploading, deleting, moving, or otherwise affecting an external target does.

Artifact retention/deletion remains governed by product data policy and tenant
authorization.

### P5 — Every external mutation stages before execution

Regardless of producer or transport, an external mutation becomes a stage containing:

- exact target and operation;
- exact artifact revision or canonical argument manifest;
- content/argument digest and size;
- preconditions captured from the target;
- risk class and effective policy;
- authorship and provenance.

Even `allow_always` creates the stage and approval-equivalent policy decision before it
queues a commit. This preserves one ledger and one WYSIWYG contract.

### P6 — Executors are adapters, not policy owners

MCP, workspace, browser, sandbox-patch, and future executors implement a narrow
prepare/apply/reconcile protocol. They do not decide whether an action is allowed, which
revision is approved, or how it renders.

### P7 — Host authority remains in Electron main

The AI service never gains Python/Node host-path access. Local workspace operations use
opaque grants and virtual paths through the authenticated Electron-main broker. A model,
renderer, transcript, or approval cannot create or broaden a grant.

### P8 — Workspace writes use a staged overlay

Agent `write_file`, `edit_file`, mkdir, move, and delete operations modify an app-owned
overlay manifest, not the host root. Reads merge overlay state over the brokered base
snapshot. Only the commit worker may ask Electron main to mutate host state.

### P9 — “What you approved” includes bytes, target, and operation

Approval pins:

- stage and revision;
- content or canonical arguments;
- source and destination virtual paths;
- operation kind;
- expected target hash/absence;
- batch manifest, when applicable;
- policy and grant snapshot.

Changing any of them invalidates the decision.

### P10 — Replay and recovery are product behavior

Artifacts, overlays, stages, decisions, commits, and surfaces rebuild after API, worker,
desktop, or app restart. A timeout or crash never causes an unreviewed resend. Ambiguous
effects surface as `outcome_unknown` and require inspection.

### P11 — One safety seam is enforced structurally

Every side-effecting capability must register through the Operation Gateway and execute
through the Effect Coordinator. A repository architecture test rejects direct
side-effect clients in model-call paths. Exceptions are limited to explicitly enumerated
product-internal stores and user-gesture lanes.

---

## 4. Domain vocabulary

| Term                  | Definition                                                                                                                                                       |
| --------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Narrative**         | Assistant text intended to be read in chat. It may contain markdown or incidental code and need not be durable as an artifact.                                   |
| **Operation**         | One requested or executed unit of work from the model, a user, a built-in capability, MCP, workspace, sandbox, browser, or system job.                           |
| **Result**            | The output of an operation. A result may be transient, cited, offloaded, or promoted into an artifact.                                                           |
| **Artifact**          | Durable, revisioned, user-relevant content owned by the product: code, document, dataset/CSV, or generic file.                                                   |
| **Artifact revision** | Immutable content bytes plus digest, size, media metadata, author, parent revision, and provenance.                                                              |
| **Surface**           | A named UI projection of a subject (artifact, result, stage, gate, or receipt). A surface is not the content store.                                              |
| **Effect**            | A proposed or applied mutation outside the app-owned artifact repository: SaaS write, host-file mutation, browser submit/upload, sandbox patch application, etc. |
| **Stage**             | The immutable review unit for an effect, with revisions/decisions and target preconditions.                                                                      |
| **Executor**          | Trusted adapter that prepares, applies, and reconciles an approved stage against one target class.                                                               |
| **Workspace overlay** | Per-run/conversation staged filesystem state layered over a brokered host snapshot, providing read-your-writes without host mutation.                            |
| **Capability grant**  | Electron-main authority for a user-selected folder and mode. It is not an approval and cannot be inferred from a path.                                           |
| **Disposition**       | Explicit result handling: `chat_only`, `activity_only`, `artifact`, `stage`, or a validated combination.                                                         |

---

## 5. Required behavior matrix

The following matrix is binding. “Tool” means an external or built-in execution
capability; internal serialization of an `ArtifactIntent` is not exposed as an external
tool action.

| User intent / runtime event                                 | Capability execution                         | Artifact                                 | Surface                  | External effect           | Required behavior                                                                                                                      |
| ----------------------------------------------------------- | -------------------------------------------- | ---------------------------------------- | ------------------------ | ------------------------- | -------------------------------------------------------------------------------------------------------------------------------------- |
| “What is 2 + 2?”                                            | Model only                                   | No                                       | No                       | No                        | Answer in chat. Studio shows an explicit completed-no-artifact state, not a broken empty pane.                                         |
| “Show me a Python example that parses JSON.”                | Model only                                   | Usually no                               | No                       | No                        | Render a normal chat code block unless the user/model explicitly chooses artifact disposition.                                         |
| “Create an editable Python script that parses these files.” | Model + artifact publisher                   | Yes, `code`                              | Yes                      | No                        | Publish an app-owned code artifact, open a fixed code surface, retain concise narration in chat. No approval merely to create it.      |
| “Create a CSV of these rows.”                               | Model or pure compute + artifact publisher   | Yes, `dataset` (`text/csv`)              | Yes                      | No                        | Preserve exact CSV bytes, show bounded table preview + raw/download, allow revision editing within limits.                             |
| “Save that CSV to my project as `exports/q3.csv`.”          | Workspace stage path                         | Existing artifact reused                 | Staged file surface      | Yes                       | Create/attach a workspace target, overlay the exact artifact revision, show create/replace diff, and apply only after policy decision. |
| Same request, no folder grant                               | Workspace gate                               | Artifact may still exist                 | Artifact + gate          | Not yet                   | Keep authored artifact; park only the save branch; ask the user to attach/authorize a folder in the native user lane.                  |
| Read a local file for context                               | Workspace read                               | No by default                            | No by default            | No                        | Read through grant/broker, record operation/source/citation. Do not create a tab solely because a read happened.                       |
| “Open `report.csv` so I can inspect/edit it.”               | Workspace read + artifact import             | Yes                                      | Yes                      | No until save             | Import a revision referencing/copying the exact bytes, show CSV surface, preserve source hash for later conflict detection.            |
| MCP read used only to answer a question                     | MCP read                                     | Optional                                 | No by default            | No                        | Activity + provenance + citation; result can remain off-canvas.                                                                        |
| MCP read returns a record the user asked to inspect         | MCP read + artifact/result presentation      | Optional result artifact                 | Yes                      | No                        | Surface the record/table. Tool execution and surface creation remain separate ledger facts.                                            |
| MCP write call                                              | No connector dispatch during proposal        | Optional artifact or exact args manifest | Staged preview           | Yes                       | Classify before dispatch, stage exact args/content, return “staged” to the agent, commit later through MCP executor.                   |
| Unknown MCP operation                                       | No dispatch until classified/policy resolved | Generic args artifact as needed          | Honest generic/raw stage | Potentially               | Default write/held. Never invoke it merely to discover whether it was safe.                                                            |
| Pure code-mode calculation                                  | Built-in sandboxed compute                   | No unless published                      | No unless published      | No                        | Return result to chat; artifact publisher may persist a substantial output. Usage is metered.                                          |
| Remote sandbox modifies snapshot                            | Sandbox execution                            | Patch artifact                           | Patch surface            | Host effect only on apply | Sandbox cannot mutate live host root. Review patch artifact, then stage through workspace executor.                                    |
| Browser download                                            | Browser capability                           | File artifact                            | File surface/card        | No host write yet         | Store in app artifact repository/quarantine. Saving to host is a separate staged workspace effect.                                     |
| Browser form submit/upload                                  | Browser capability                           | Request/preview artifact                 | Staged action surface    | Yes                       | Stage exact origin, method/action, fields, upload refs, and page precondition; commit via browser executor.                            |
| Delete or move a host file                                  | Workspace overlay                            | Tombstone/move manifest                  | Destructive stage        | Yes, destructive          | Always explicit per-call approval; `allow_always` cannot weaken the destructive risk floor.                                            |
| Tool call returns no useful display data                    | Any                                          | No                                       | No                       | Depends                   | Ledger/activity still records the operation. Never fabricate a surface.                                                                |
| Renderer does not support an artifact                       | Any                                          | Yes                                      | Raw/file fallback        | No additional effect      | Preserve all bytes; show safe metadata/raw/download. “Nothing is hidden.”                                                              |

---

## 6. Product behavior by mode

### 6.1 Studio

Studio remains canvas-centered, but the canvas has an explicit lifecycle:

1. **assembling** — run accepted/working and no artifact exists yet;
2. **artifact available** — first surface opens; later surfaces become tabs;
3. **completed, chat only** — the run answered in chat and created no artifact;
4. **failed before artifact** — safe failure and retry context;
5. **parked** — capability/auth/grant gate with unaffected artifacts preserved.

Starting a new run must not make the UI look broken. The center pane shows current run
progress and may offer a collapsed “Previous run artifacts” affordance, but it must not
misrepresent a prior-run surface as belonging to the new run.

A receipt is still emitted for audit. It is not automatically selected when it contains
no reads, artifacts, stages, or decisions. Zero-write copy is conditional; the UI does
not display “Every write was decided…” when no write existed.

### 6.2 Focus

Focus remains chat-centered and does not mount generative surfaces. Artifact events
render as fixed rich cards containing:

- title, kind, revision, size, and status;
- **Open in Studio**;
- safe download/copy where allowed;
- pending-decision state and a route to the approval surface.

No full editor or generated layout appears inside Focus chat.

### 6.3 Cross-run continuity

Surfaces remain per-run. Artifacts may be conversation-scoped and revised in a later run.
A later run creates its own surface pointing at the artifact/revision; receipts preserve
which run authored, edited, targeted, and applied each revision.

---

## 7. Functional requirements

### A. Output disposition

- **UA-FR-A1 — Chat-only is first-class.** A run may complete successfully with no
  artifact or surface.
- **UA-FR-A2 — Explicit artifact publication.** Model, tool, user, and system producers
  publish through one validated `ArtifactIntent`.
- **UA-FR-A3 — No code-fence inference.** Fenced code alone never silently creates a
  durable artifact.
- **UA-FR-A4 — User promotion.** A user can promote eligible chat content/tool output to
  an artifact without re-running the model/tool.
- **UA-FR-A5 — Selective tool presentation.** Tool calls can be activity-only; surface
  creation is independently declared or deterministically selected.
- **UA-FR-A6 — Honest degradation.** Invalid artifact intent falls back to chat/result
  activity with a safe diagnostic; the response is not lost.

### B. Artifact lifecycle

- **UA-FR-B1 — Stable identity and immutable revisions.** Every artifact has a stable id;
  revisions are immutable, ordered, content-addressed, and provenance-bearing.
- **UA-FR-B2 — Supported launch kinds.** `code`, `document`, `dataset`, and `file`.
  Kinds are content semantics, not arbitrary renderer names.
- **UA-FR-B3 — Exact bytes.** Storage, download, stage, and commit preserve the exact
  approved revision bytes. Views may parse/format but never rewrite silently.
- **UA-FR-B4 — Edit with optimistic concurrency.** User and agent edits append revisions
  against `expected_rev`; stale edits return a conflict rather than overwriting.
- **UA-FR-B5 — Authorship.** Agent/user/system authorship and diff spans are durable.
- **UA-FR-B6 — Reuse and targeting.** One artifact revision can be downloaded, copied,
  attached to a target, or used as the proposal for multiple separately approved effects.
- **UA-FR-B7 — Large/binary behavior.** Editable and preview limits are explicit;
  over-limit/binary artifacts remain downloadable and reviewable via metadata/raw
  fallback.
- **UA-FR-B8 — Retention/deletion.** Artifacts inherit conversation/workspace retention;
  blob garbage collection is reference-safe; product deletion never deletes a user's
  committed host file.

### C. Universal operations and effects

- **UA-FR-C1 — Normalized operation contract.** Every producer declares origin,
  capability, operation, target class, potential effects, and result disposition.
- **UA-FR-C2 — Layered classification.** Server-owned catalog → trusted built-in
  descriptor → untrusted protocol hints → default write/held. Model declarations never
  grant a lower risk class.
- **UA-FR-C3 — Universal policy.** Read/write/destructive policy applies across MCP,
  workspace, browser, sandbox patch, and future executors.
- **UA-FR-C4 — Exact stage.** All external writes stage exact content/arguments and
  preconditions before execution.
- **UA-FR-C5 — Executor registry.** Only server-registered executors can apply effects.
  Unknown executor/operation combinations fail closed.
- **UA-FR-C6 — Generic commit ordering.** approval gate → precondition re-check →
  idempotency claim → execute → reconcile/complete → ledger.
- **UA-FR-C7 — Read/tool independence.** Executed reads are ledgered even when no surface
  is created; surfaces may exist without an executed read.
- **UA-FR-C8 — Architecture enforcement.** CI detects model-facing direct write clients
  or executor calls outside the Effect Coordinator.

### D. Local workspace

- **UA-FR-D1 — Native grants only.** Host access requires a user-selected Electron-main
  grant. No model-supplied absolute path creates authority.
- **UA-FR-D2 — Virtual paths only.** Model, API, events, artifacts, and UI use mount-safe
  virtual paths; physical paths and native identities never leave Electron main.
- **UA-FR-D3 — Overlay mutations.** Agent writes/edit/mkdir/delete/move update a durable
  overlay and stage, never live host bytes.
- **UA-FR-D4 — Read-your-writes.** Workspace reads resolve overlay entries/tombstones
  before broker base state.
- **UA-FR-D5 — Coalesced revisions.** Repeated writes to the same target update one
  artifact/stage revision chain rather than creating approval spam.
- **UA-FR-D6 — Two-phase host commit.** Prepare, preimage, content upload, atomic commit,
  postcondition verification, and journaled reconciliation occur in Electron main.
- **UA-FR-D7 — Conflict behavior.** Target drift, grant revoke/downgrade, path identity
  change, or stale approval prevents mutation and leaves the overlay intact for review.
- **UA-FR-D8 — Destructive floor.** Delete, move, restore, and batch patch always require
  explicit approval regardless of permissive general write posture.
- **UA-FR-D9 — Platform honesty.** Web/server runs cannot interpret local paths as server
  paths. They offer app artifact/download flows; only the desktop grant lane offers host
  mutation.
- **UA-FR-D10 — Recovery.** Restart can prove committed, safe-to-retry, conflict, or
  unknown; it never blindly repeats a host mutation.

### E. Presentation

- **UA-FR-E1 — Artifact surfaces.** Code, document, dataset/CSV, and generic file
  surfaces use fixed first-party components.
- **UA-FR-E2 — Code safety.** Code is syntax-highlighted/editable text only; viewing never
  executes it.
- **UA-FR-E3 — Dataset fidelity.** CSV table preview is bounded and virtualized; raw bytes
  and download remain available; formula-like cells are visibly warned, not silently
  rewritten.
- **UA-FR-E4 — Stage-aware views.** A surface shows base, overlay/proposed, exact revision,
  target, preconditions, policy, and decision status.
- **UA-FR-E5 — Processing continuity.** Studio distinguishes assembling, chat-only,
  parked, failed, and artifact-ready states.
- **UA-FR-E6 — Conditional receipt.** Receipt rows/counts cover artifacts and all
  executors; empty/inapplicable claims are omitted.
- **UA-FR-E7 — Focus cards.** Focus uses rich cards and routes to Studio; no full
  generative surface is mounted in chat.

### F. Accountability, usage, and security

- **UA-FR-F1 — One ledger.** Operations, artifact revisions, surface projections, stages,
  decisions, executor attempts, recovery, and receipts share stable ledger ids.
- **UA-FR-F2 — Complete provenance.** Every artifact revision names its producing
  model-call/tool/operation/user action and parent revision.
- **UA-FR-F3 — Usage attribution.** All model calls remain metered; artifact publication
  that invokes no additional model adds no synthetic usage. Future artifact-specific
  model work receives its own purpose.
- **UA-FR-F4 — Tenant isolation.** Artifact metadata/content and stage APIs derive
  org/user/conversation/run scope from verified identity, never request payload alone.
- **UA-FR-F5 — Content confidentiality.** Events/logs carry refs, hashes, sizes, and safe
  labels—not artifact bodies, physical paths, broker secrets, or raw target credentials.
- **UA-FR-F6 — Audit export.** Receipt export includes artifact/effect/recovery events and
  remains tamper-evident.

---

## 8. Non-functional requirements

- **UA-NFR-1 — Fail closed for effects, fail soft for presentation.** A failed view never
  blocks a read or loses content; an uncertain permission/precondition never executes a
  write.
- **UA-NFR-2 — Deterministic replay.** Given ledger events and referenced blobs, server
  and client projectors rebuild byte-equivalent artifact, surface, overlay, stage, and
  receipt state.
- **UA-NFR-3 — No large bodies on SSE.** Ledger/event payloads contain metadata and
  opaque refs only. Default maximum projected event size is 64 KiB.
- **UA-NFR-4 — Streaming storage.** Blob ports support bounded streaming/range reads.
  The initial maximum artifact is configurable and defaults to 512 MiB; no base64 JSON
  path is used for large files.
- **UA-NFR-5 — Bounded rendering.** Defaults: editable UTF-8 code/doc ≤ 5 MiB; dataset
  preview ≤ 100,000 cells and ≤ 10,000 rows; larger content remains read-only/raw and
  downloadable. Limits are centralized and testable.
- **UA-NFR-6 — Responsive feedback.** Run acceptance immediately produces an assembling
  state; artifact metadata/skeleton renders before content hydration; purpose shaping
  never blocks generic/fixed renderers.
- **UA-NFR-7 — Idempotency.** Every artifact publish, revision append, stage, decision,
  and commit command has a caller-stable idempotency key and conflict semantics.
- **UA-NFR-8 — Horizontal worker safety.** Claims and revision increments are durable
  atomic operations; no correctness depends on in-process locks or one worker.
- **UA-NFR-9 — Cross-platform boundaries.** Shared domain contracts contain no Electron,
  browser, MCP, filesystem, or provider implementation types.
- **UA-NFR-10 — Renderer containment.** Markdown/HTML is sanitized; code is never
  evaluated; CSV cells render as text; downloads use safe MIME/content-disposition and
  never auto-open.
- **UA-NFR-11 — Accessibility and kit fidelity.** Keyboard editing, focus management,
  screen-reader status, diff semantics, and design-token parity are required in both
  hosts.
- **UA-NFR-12 — Observable recovery.** Metrics distinguish staged, queued, committed,
  conflicted, failed, indeterminate, and reconciled attempts without logging content.
- **UA-NFR-13 — Backward-readable.** Existing v2 ledger payloads and run receipts remain
  replayable after cutover through versioned projectors.
- **UA-NFR-14 — No permanent dual truth.** Compatibility adapters have a removal PR and
  metric; the final architecture has one artifact model and one effect execution path.

---

## 9. Scope and non-goals

### In scope

- model-authored code/document/CSV/generic file artifacts;
- explicit promotion of existing chat/tool output;
- universal operation/effect contracts and enforcement;
- transport-neutral staging and executor registry;
- desktop workspace overlay, exact review, and brokered commit;
- MCP read/write convergence;
- hooks/adapters for pure compute, sandbox patches, browser downloads/submits, and
  subagents;
- Studio/Focus lifecycle behavior;
- audit, usage, retention, migration, and conformance tests.

### Non-goals for this package

- arbitrary model-authored UI or renderer code;
- arbitrary host shell/process/package execution (sandbox capability owns execution);
- recursive host delete, chmod/chown/ACL, links, device files, mount operations, or
  arbitrary file descriptors;
- live bidirectional host mounts into a remote sandbox;
- collaborative multi-user editing/CRDTs (optimistic revision conflicts are required);
- automatic background file sync;
- timeline UI;
- a new billing UI;
- interpreting a web deployment's server filesystem as the user's “local” filesystem;
- silently deciding that every code block/table is a durable artifact.

---

## 10. Current implementation: keep, generalize, replace

### Keep and reuse

| Existing capability                                                        | Why it survives                                                            |
| -------------------------------------------------------------------------- | -------------------------------------------------------------------------- |
| Work Ledger, SSE/replay, ledger id, receipt/audit chain                    | Correct event-sourced accountability foundation.                           |
| `WriteStager` revisions/decisions and `CommitEngine` ordering              | Correct WYSIWYG and claim-before-effect safety properties.                 |
| UsageMeter and purpose attribution                                         | Already independent of presentation.                                       |
| Surface spec generator, registry, lint, redaction, honest fallback         | Still the constrained view-authoring path for tool-shaped data.            |
| Fixed archetype renderer registry and chat-surface kit                     | Correct UI safety/design-system boundary.                                  |
| Desktop capability broker, grants, path validation, run context, preimages | Correct owner for physical host authority.                                 |
| File content-addressed object store                                        | Suitable initial blob primitive; generalized behind an artifact blob port. |
| Tool-use policy (`read/write/destructive × auto/ask/require/block`)        | Becomes universal rather than MCP/workspace-special-cased.                 |

### Generalize

| Current shape                                   | Generalized shape                                                           |
| ----------------------------------------------- | --------------------------------------------------------------------------- |
| connector-only `LedgerOpRef`                    | producer/capability/operation/target descriptors                            |
| draft-centric single-artifact staging           | artifact-revision or canonical-arguments staging                            |
| MCP-specific `WorkLedgerEmitter.on_tool_result` | producer-neutral Operation Gateway                                          |
| `StageCommitConnector`                          | typed executor registry (`mcp`, `workspace`, `browser`, `sandbox_patch`, …) |
| surface payload as tool-result projection       | surface subject reference to artifact/result/stage/gate/receipt             |
| `/drafts/` as authored-content island           | canonical Artifact Service with a compatibility adapter                     |

### Replace and retire

- direct host mutation from `BrokeredWorkspaceBackend.awrite/aedit`;
- blanket `/workspace/**` interrupt as the final approval mechanism;
- automatic “MCP result crossed middleware, therefore try to make a surface” coupling;
- permanent draft rows as a second artifact source of truth;
- connector-only commit request bodies that cannot carry generic exact arguments/bytes;
- unconditional empty receipt selection/copy.

---

## 11. Launch acceptance scenarios

The launch is not complete until these scenarios pass on the real desktop stack and in
hermetic conformance suites:

1. A math/chat-only run completes with no surface and a clear chat-only canvas state.
2. A small code example remains in chat.
3. “Create an editable script” creates one code artifact/surface with no external write.
4. The user edits the code surface; replay restores the exact revision and authorship.
5. “Create a CSV” produces exact downloadable bytes and a bounded table preview.
6. “Save it as `exports/q3.csv`” writes only after exact-revision approval.
7. The agent writes, reads, and edits the same staged workspace file multiple times; host
   bytes do not change until commit and only one coalesced stage waits.
8. Existing-file edit shows a correct base→overlay diff; external drift causes conflict
   and zero overwrite.
9. Grant revoke between approval and commit causes zero host mutation.
10. Worker/Desktop crash at every prepare/commit boundary reconciles without duplicate
    effect.
11. An MCP read used only for an answer creates activity/citation but no surface.
12. An MCP record explicitly opened creates a surface.
13. An MCP write stages exact canonical args and dispatches them only after approval.
14. Unknown tool defaults held and never executes merely for view discovery.
15. A sandbox patch becomes a patch artifact and cannot reach the host outside the
    workspace executor.
16. A browser download becomes an app artifact; save-to-host is a second staged effect.
17. Focus shows artifact/pending cards and routes to Studio without mounting editors.
18. Receipt export attributes artifacts, policy decisions, executor attempts, conflicts,
    and recovery with valid audit-chain verification.
19. Cross-tenant artifact ids, content refs, stage ids, and download URLs are rejected
    without existence disclosure.
20. Architecture gates prove no model-facing direct effect path remains after cutover.

---

## 12. Decisions taken in this design

These are explicit architecture decisions so implementers do not reopen them inside
individual PRs:

1. No new deployable service.
2. Artifact metadata/workflow lives in `ai-backend`; content is behind an
   `ArtifactBlobStorePort`.
3. Electron main remains the sole physical host-filesystem authority.
4. Workspace writes use an overlay and the staged Effect Coordinator, not write-through
   after interrupt.
5. Product artifact creation is auto-allowed; external writes follow policy; destructive
   actions always require an explicit decision.
6. Artifact intent is explicit; fenced-code heuristics are not canonical.
7. Existing v2 event names remain readable. New payload versions/events are additive;
   projectors support both during migration.
8. Surfaces remain per-run; artifacts may outlive a run within authorized conversation
   scope.
9. No model-generated renderer code. Code artifacts are inert text until the user
   separately invokes a sandbox/run capability.
10. Web supports artifacts/download; desktop grants are required for autonomous host-file
    effects.
