# Desktop Agent Capabilities Roadmap

Status: **accepted architecture track; implementation PRDs planned** · Owner: Desktop + Agent Runtime · Last updated: 2026-07-18

This track extends the implemented Electron desktop with file-native agent sessions, scoped host capabilities, durable execution, browser automation, and connector reuse. Start with [00-overview.md](00-overview.md). The existing [Desktop App PRD](../PRD.md) remains the delivery history for the Electron product; this track is the accepted follow-on for agent capabilities.

## Scope

In scope:

- Desktop-only runtime selection and capability contracts.
- File-native conversations, events, subagent transcripts, checkpoints, and artifacts.
- A narrow Electron-main capability broker for user-granted host filesystem access.
- A bounded Monty code mode and a separate provider-backed remote execution path.
- A supervised browser worker and desktop-local MCP provider.
- Reuse of backend-owned MCP registration, OAuth state, token vault, policy, and audit.
- Migration, repair, retention, observability, security, and macOS/Windows hardening.

Out of scope:

- Changing web product behavior or moving web persistence off PostgreSQL.
- Giving the renderer Node.js, filesystem, browser-credential, or broker-token access.
- Running arbitrary shell commands in Electron main, the renderer, or the trusted AI worker.
- Replacing backend-owned connector registration, OAuth, token storage, or policy.
- Treating an embedded interpreter as a remote sandbox or full CPython environment.
- Creating a general desktop plugin or extension API.

## No-web-impact invariant

Every AC1–AC10 PR has **web impact: none**, and this is a constraint on the design, not a claim to be asserted for free:

- `apps/frontend` continues to use `backend-facade`; no desktop capability is exposed to it.
- The desktop renderer also continues to call `backend-facade` only.
- `RUNTIME_STORE_BACKEND=postgres`, non-desktop deployment profiles, public facade routes, and existing SSE semantics remain unchanged.
- New stores, tools, broker clients, browser providers, and interpreters must require both `ENTERPRISE_DEPLOYMENT_PROFILE=single_user_desktop` and their specific desktop feature gate.
- **Shared contracts stay backward-compatible by construction.** Any change to `packages/api-types` must be additive and must not break the web typecheck or the shipped web flows that consume it. Desktop-only transport needs (for example AC9's loopback/deep-link OAuth callback and `oauth_session_id`) are expressed as a **desktop-only variant/type**, never by mutating a field the web already consumes. "Web impact: none" is only earned once the shared-type diff is proven additive.
- A change that needs web behavior, a public API break, or a new non-desktop deployment dependency is a separate proposal and cannot be folded into this track.

## Documents

- [00-overview.md](00-overview.md) — accepted target architecture, prior art, security model, storage policy, dependency graph, and rollout.
- [Desktop App Architecture](../../../architecture/desktop-app.md) — implemented Electron baseline and registered forward target.
- [Service Boundaries](../../../architecture/service-boundaries.md) — ownership and the desktop-local AI-backend-to-Electron broker boundary.
- [Desktop App PRD](../PRD.md) — earlier Electron delivery plan; current code wins over stale phase labels.
- [AI Backend knowledge base](../../../../services/ai-backend/docs/README.md) and [Backend knowledge base](../../../../services/backend/docs/README.md) — current service behavior.

## Merge waves

Dependencies, not dates, define merge order. PRs in the same wave may proceed in parallel only after all dependencies are merged. Each AC below is an **epic**; the actual review/merge units are the sub-PRs in [PR decomposition](#pr-decomposition-each-ac-is-an-epic).

| Wave                                                              | PRs                                                                                                                                                                                          | Exit                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| ----------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 0 — Contracts                                                     | [AC1](01-ac1-desktop-capability-foundation.md)                                                                                                                                               | Desktop-only gates, wire contracts, storage layout, ownership, and versioning are frozen. AC1 is the **single normative source** (its §7.4/§8 define the LIGHT `FileSessionRecordV1` and flat `events.jsonl` layout); the overview is narrative and defers to it.                                                                                                                                                                                                                                                                                                |
| 1 — Product loop **and** LIGHT file store (parallel, independent) | **AC3a** (mount desktop chat on the **existing PostgreSQL store**), and separately the LIGHT file store [AC2](02-ac2-file-session-store.md) + offload wiring [AC4](04-ac4-artifact-store.md) | AC3a: real desktop chat (send/stream/cancel/approval) replaces `DesktopPlaceholder` on the run/SSE path that works today — **no dependency on AC2/AC4**. AC2/AC4: the [overview §25 storage decision is **closed** → file-native canonical, LIGHT single-writer variant](00-overview.md#25-alternatives-considered). AC2 ships append-only JSONL + `objects/sha256` + a disposable SQLite index (fewer PRs, no cross-process machinery); AC4 wires offload over AC2's object store. **AC2 is an independent bet, not a precondition for shipping desktop chat.** |
| 2 — Durable product wiring                                        | [AC5](05-ac5-filesystem-capability.md) (3 slices), [AC9](09-ac9-desktop-connectors.md); **AC3b deferred**                                                                                    | Scoped files (broker+grants → read ops → mutation) and backend-owned OAuth (via a desktop-only callback variant) work end to end. **AC3b (separate worker/cross-process recovery) is optional/deferred** — the single in-process worker on the LIGHT store already delivers the durable loop via AC3a.                                                                                                                                                                                                                                                           |
| 3 — Execution capabilities                                        | [AC6](06-ac6-monty-code-mode.md), [AC7](07-ac7-remote-sandbox-execution.md), [AC8](08-ac8-agentic-browser.md)                                                                                | Bounded interpreter, isolated remote execution, and policy-aware browser tools are independently usable.                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| 4 — Hardening                                                     | [AC10](10-ac10-hardening-rollout.md)                                                                                                                                                         | Migration, repair, retention, adversarial security, cross-platform, rollout, and backout evidence is complete.                                                                                                                                                                                                                                                                                                                                                                                                                                                   |

## PR index

The linked PRDs are the next documentation deliverables; until each file exists and is accepted, its status is **planned**, not implemented. "Depends on" is at epic granularity; sub-PR dependencies are in [PR decomposition](#pr-decomposition-each-ac-is-an-epic).

| ID   | PRD                                                                                     | Primary owners                              | Depends on                                                                                                                                                                                | Status                                                      | Web impact                                                              |
| ---- | --------------------------------------------------------------------------------------- | ------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------- | ----------------------------------------------------------------------- |
| AC1  | [Desktop capability profile and contracts](01-ac1-desktop-capability-foundation.md)     | Electron main, AI runtime                   | —                                                                                                                                                                                         | Planned                                                     | None                                                                    |
| AC2  | [File-native session store (LIGHT)](02-ac2-file-session-store.md)                       | AI runtime adapters                         | AC1; **overview §25 closed → file-native LIGHT**                                                                                                                                          | Planned (independent bet; ~4 PRs)                           | None                                                                    |
| AC3a | Mount desktop chat on the existing store (split from [AC3](03-ac3-runtime-recovery.md)) | Desktop shell, chat-surface                 | AC1; in-flight [phase-2](../phase-2/) components                                                                                                                                          | Planned (ship first)                                        | None                                                                    |
| AC3b | [Cross-process worker, checkpoint, and recovery](03-ac3-runtime-recovery.md)            | Runtime API/worker, desktop supervisor      | AC2, AC4 (only if activated)                                                                                                                                                              | **Optional / deferred** (single in-process worker suffices) | None                                                                    |
| AC4  | [Tool-result offload wiring over AC2's object store](04-ac4-artifact-store.md)          | AI runtime persistence/context              | AC1, **AC2 (object store)**                                                                                                                                                               | Planned (~2–3 PRs)                                          | None                                                                    |
| AC5  | [Scoped host filesystem access](05-ac5-filesystem-capability.md)                        | Electron capability broker, AI capabilities | AC1, AC4                                                                                                                                                                                  | Planned (3 ordered slices)                                  | None                                                                    |
| AC6  | [Monty interpreter/code mode](06-ac6-monty-code-mode.md)                                | AI execution/capabilities                   | AC2, AC4, and the AC3a in-process durable loop (Monty checkpoints reuse AC2-light + `SqliteSaver`; **AC3b not required**); **direct-path policy engine (`runtime_gate` wired) — see AC6** | Planned                                                     | None                                                                    |
| AC7  | [Remote sandbox execution](07-ac7-remote-sandbox-execution.md)                          | AI sandbox adapters                         | AC4, AC5                                                                                                                                                                                  | Planned                                                     | None                                                                    |
| AC8  | [Agentic browser runtime](08-ac8-agentic-browser.md)                                    | Desktop browser worker, AI MCP/capabilities | AC4, AC5, AC9                                                                                                                                                                             | Planned                                                     | None                                                                    |
| AC9  | [Desktop OAuth connectors](09-ac9-desktop-connectors.md)                                | Backend MCP/OAuth, facade, Electron main    | AC1                                                                                                                                                                                       | Planned                                                     | None (desktop-only callback variant; shared `api-types` stays additive) |
| AC10 | [Migration, repair, retention, and hardening](10-ac10-hardening-rollout.md)             | Desktop + AI runtime + backend              | AC2–AC9                                                                                                                                                                                   | Planned                                                     | None                                                                    |

## PR decomposition (each AC is an epic)

One index row is not one PR. A single-PR-per-AC framing with one wave-exit gate is not reviewable or mergeable; the enumerated rollout sub-steps in each PRD are the real PR boundaries. Sizing across the track is on the order of **~30–40 PRs / multiple engineer-months**, not ten PRs. The lead implementation spec for each epic (see [Implementation-spec handoff](#implementation-spec-handoff)) enumerates its sub-PRs; the baseline decomposition is:

- **AC1 — contracts (Wave 0):** (1) desktop deployment predicate + feature gates; (2) frozen wire/broker contract types (TS + Pydantic, conformance-tested); (3) storage-layout + versioning constants; (4) ownership/normativity doc. ~3–4 PRs.
- **AC2 — file-native session store, LIGHT (Wave 1, §25 closed):** (1) contracts + canonical encoder/hash goldens + `FileSessionRecordV1`; (2) `SessionJournal` (append-only JSONL, per-conversation `asyncio.Lock`, flush classes, load-with-torn-tail-ignore) + `ObjectStore` (`objects/sha256` put/get); (3) rebuildable SQLite + FTS5 + queue projection + port/satellite adapters + factory branch; (4) migration/export/backout + desktop supervision + runbooks. **No copy-on-write generations, cross-process locks, commit markers, tail-quarantine engine, or hash chains** — those are the deferred AC3b (separate-worker) machinery. ~4 PRs.
- **AC3a — mount desktop chat on the existing store (Wave 1, ship first):** (1) delete `DesktopPlaceholder`, mount `ChatsDestination`/`ThreadCanvas`/`TcChat`/`Composer` via the desktop controller; (2) wire send/stream/cancel/approval on the existing facade run+SSE path; (3) desktop attachment adapter (additive to phase-2 `2D`'s `onSend`). Coordinates with in-flight phase-2 `2A–2E`; **no AC2/AC4 dependency.** ~2–3 PRs.
- **AC3b — cross-process worker + recovery (OPTIONAL / DEFERRED):** separately supervised `runtime_worker`; `file_notify` cross-process wake; cross-process LangGraph saver hand-off; queue leases with stale-owner CAS; cross-process startup reconciliation; crash-safe cancellation. **Not on the shipping path** — the single in-process worker on the LIGHT store, plus LangGraph `SqliteSaver` and AC3a, already deliver the durable loop. Built only if the worker is later split into a separate process. ~8–9 PRs when/if activated.
- **AC4 — tool-result offload wiring (Wave 1):** reuse `ContextPayloadManager`/`OffloadWriter`/`ManagedContextPayload` to write large results into **AC2's** `objects/sha256` store as typed `ArtifactRefV1` + bounded preview; the `/large_tool_results/` `CompositeBackend` route; reachability/GC/retention policy. **The byte primitive is AC2-owned**; AC4 is wiring, not a second store. ~2–3 PRs.
- **AC5 — scoped filesystem, 3 slices (Wave 2):** **slice 1** Electron-main capability broker + native folder picker + grant model (`read_only`/`read_write_no_delete`/`read_write`, `safeStorage`-encrypted), NO filesystem ops; **slice 2** FS read ops (stat/list/read/glob/grep) + path validation (traversal/symlink/junction/ADS/TOCTOU); **slice 3** write/edit/mkdir + delete/move behind approval, per-run grant snapshot, Deep Agents `/workspace/` `CompositeBackend` route. Ordered dependency (2 needs 1, 3 needs 2). ~7–8 PRs across the three slices.
- **AC6 — Monty code mode (Wave 3):** **prerequisite PR to wire the direct-path policy engine (`runtime_gate`), which is currently unwired — see AC6**; Monty isolation spike; interpreter integration; `PolicyToolInvoker` + interrupt-from-tool-node; parity tests. ~4–5 PRs.
- **AC7 — remote sandbox (Wave 3):** provider adapter + egress policy; snapshot/artifact transfer; lifecycle/teardown. ~3–4 PRs.
- **AC8 — agentic browser (Wave 3):** supervised browser worker; origin/consent policy; action tools; SSRF/download hardening. ~4–5 PRs.
- **AC9 — desktop connectors (Wave 2):** desktop-only OAuth callback variant type (additive to shared `api-types`); loopback/deep-link handler in Electron main; facade wiring; reuse of backend MCP/OAuth/vault. ~3–4 PRs.
- **AC10 — hardening (Wave 4):** migration; repair; retention (right-sized per profile); adversarial/cross-platform; rollout/backout. ~4–6 PRs.

### Implementation status

Code is being built to these final decisions on the `feat/desktop-redesign` line, via isolated `feat/dr-*` branches:

- `feat/dr-ac3a` — AC3a chat wiring (mount desktop chat on the existing store).
- `feat/dr-filestore` — the LIGHT file-store foundation (AC2 append-only JSONL + `objects/sha256` + disposable SQLite index).
- `feat/dr-ac5` — AC5 slice 1 (Electron-main capability broker + native picker + grant model).
- `feat/dr-prds` — these PRDs (this doc set).

Queued next:

- **File-store PR2** — checkpointer → LangGraph `SqliteSaver`, AC4 offload wiring, and Deep Agents `CompositeBackend` `/large_tool_results/` routing over AC2's object store.
- **AC5 slices 2 and 3** — read ops + path security, then mutation + approval + `/workspace/` route.

AC3b (separate-process worker + cross-process recovery) is **not** queued — it is deferred unless the worker is later split into its own process.

## Status legend

- **Planned** — indexed here; PRD not yet accepted.
- **Draft** — PRD exists but has unresolved review findings.
- **Accepted** — requirements and decisions are frozen; implementation-spec handoff may begin.
- **In implementation** — accepted PRD and component-local implementation spec exist; code PR is open.
- **Implemented** — code, tests, operational docs, and acceptance evidence agree.
- **Blocked** — an explicit dependency or unresolved security/product decision prevents progress.
- **Superseded** — replaced by a linked decision; retained only for history.

Status is evidence-based. Code alone does not make a control implemented, and a roadmap statement does not describe current behavior.

## PRD template

Every AC PRD uses the following structure:

1. **Header** — spec ID, status, wave, estimated effort, dependencies, required-for, owners, and `Web impact: none`.
2. **Problem and why now** — current evidence paths and the user or operational failure.
3. **Goals and non-goals** — including user-visible and explicit failure behavior.
4. **Alternatives considered** — each rejection tied to security, operability, or ownership.
5. **Architecture and ownership** — SOLID/ports-and-adapters reasoning, process boundaries, data/control flow, and exact critical paths.
6. **Contracts** — Pydantic/TypeScript shapes, protocol and storage versions, compatibility rules, and migration behavior.
7. **Persistence and recovery** — canonical data, indexes/caches, atomicity, idempotency, crash repair, quotas, retention, deletion, and legal hold.
8. **Trust and permissions** — actors, grants, approvals, secret handling, threat cases, audit events, and residual risk.
9. **Observability** — logs, metrics, traces, event fields, redaction, and operator/user diagnostics.
10. **Tests** — unit, port conformance, integration, crash injection, adversarial security, macOS/Windows, and explicit web/Postgres regression coverage.
11. **Rollout and backout** — feature gates, migration stages, compatibility window, stop conditions, and data-preserving rollback.
12. **Acceptance criteria and definition of done** — testable requirements plus documentation and operational evidence.
13. **Critical files** — exact repo-relative paths; no speculative component imports.
14. **Unresolved risks** — accepted PRDs may retain risks, but no open implementation choice.

## Implementation-spec handoff

These roadmap PRDs are architecture and acceptance contracts, not implementation specs.

1. The AC PRD must be **Accepted** and all dependencies must be **Implemented**.
2. The implementer re-reads the applicable root and path-scoped rules, then verifies the PRD against current code.
3. Before code, write one detailed implementation spec under the primary owner's component-local spec tree, for example `services/ai-backend/docs/specs/desktop-agent-capabilities/` or `services/backend/docs/specs/desktop-agent-capabilities/`. If `apps/desktop` is primary and has no spec tree, the first such PR creates `apps/desktop/docs/specs/agent-capabilities/` and links it from `apps/desktop/README.md`.
4. A cross-component PR has one lead implementation spec. It links the accepted AC PRD and maps changes by owner; it does not duplicate the wire contract in multiple prose files.
5. The implementation spec pins dependency/API versions, resolves all file-level choices, names migrations and tests, and records any code-discovered deviation. A deviation that changes trust, storage, service ownership, public behavior, or the no-web-impact invariant returns to architecture review.
6. Code, tests, knowledge-base updates, migration/repair instructions, and acceptance evidence land together. Only then does this index move the PR to **Implemented**.

No implementation spec or code is created by this architecture-docs PR.
