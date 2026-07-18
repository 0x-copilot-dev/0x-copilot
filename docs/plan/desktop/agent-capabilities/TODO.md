# Desktop Agent Capabilities — TODO roadmap

Status: **living checklist** · Owner: Desktop + Agent Runtime · Last updated: 2026-07-18 (post AC6–AC10 wave; see [10-ac10-hardening-rollout.md](10-ac10-hardening-rollout.md) for the release-gate evidence bundle)

This is the priority-ordered execution roadmap for the desktop agent-capabilities track. It is the operational companion to the architecture PRDs ([README.md](README.md), [00-overview.md](00-overview.md)); the PRDs own **why** and **contracts**, this file owns **order, definition of done, and current status**.

Status values: **done** (merged on `feat/desktop-redesign`, code + tests present), **in-progress** (partially merged; named remainder open), **planned** (not started).

Built-in-first applies throughout (overview §4): use the DeepAgents/LangChain built-in as the engine — Monty `InterpreterPort`, `SandboxBackendProtocol`, the Playwright toolkit / browser-MCP behind `langchain-mcp-adapters`, `HumanInTheLoopMiddleware`, and LangGraph savers — and add only the thin approval/budget/event/persistence enforcement layer. Precedents already merged: the `AsyncSqliteSaver` checkpointer, the DeepAgents-native `interrupt_on` approval, and `CompositeBackend` routes.

## P0 — Finish the loop (real desktop chat over the existing store)

Goal: the desktop shell drives a real run end-to-end on the in-process worker, no placeholder.

| #   | Item                                                                     | Definition of done                                                                                                        | Status |
| --- | ------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------- | ------ |
| 1   | Mount the profile-gated 6-destination shell; remove `DesktopPlaceholder` | Desktop renders the real Chats/Thread destination via the desktop controller, not a placeholder.                          | done   |
| 2   | Send / stream / cancel on the facade run + SSE path                      | A desktop chat opens a run, streams events, and cancels over the existing facade run+SSE path on the in-process worker.   | done   |
| 3   | Inline + in-chat approvals (on-surface `ApprovalCard`)                   | An `ask`/`require` tool approval interrupts and resumes on the desktop path, reusing the native `interrupt_on` interrupt. | done   |
| 4   | Run empty / idle / multi-run lifecycle states                            | The run canvas reflects idle, streaming, multi-run, and terminal (incl. `timed_out`) states.                              | done   |

## P1 — Trustworthy local store (LIGHT file-native session store)

Goal: desktop history and runtime state are recoverable from canonical, append-only files; SQLite stays disposable.

| #   | Item                                                                | Definition of done                                                                                                                                                                    | Status |
| --- | ------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------ |
| 5   | LIGHT file-native session store adapter                             | Append-only `events.jsonl` + `objects/sha256` + disposable SQLite index; deleting the index and rebuilding by scanning JSONL restores all durable query state.                        | done   |
| 6   | Flag-gated file-native store selection                              | `RUNTIME_STORE_BACKEND=file` valid only under `single_user_desktop` + feature flag (opt-in); the postgres/web path is byte-for-byte unchanged.                                        | done   |
| 7   | `SqliteSaver` checkpointer + AC4 offload + `CompositeBackend` reads | Large tool results offload to `objects/sha256`; graph/approval checkpoints survive worker restart via `AsyncSqliteSaver`; composite reads resolve typed refs (incl. approval resume). | done   |
| 8   | Crash-safety: torn-tail-ignore + fail-closed on interior corruption | An unacknowledged trailing line is ignored on load; interior (non-final) corruption makes the conversation read-only for explicit repair rather than silent truncation.               | done   |

## P1.5 — Capabilities AC6–AC9 (built-in-first)

Goal: scoped host files, bounded code, isolated full execution, browser control, and desktop OAuth — each an epic of multiple ordered sub-PRs.

| #   | Item                                                               | Definition of done                                                                                                                                                                  | Status                                                                                                                                                                                                             |
| --- | ------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| 9   | AC5 scoped host filesystem (broker → read → write)                 | `/workspace/<grant_id>` read + write behind grant modes, per-run grant snapshot, and approval; brokered via authenticated loopback.                                                 | in-progress (slices 1/2/3a/3b + write-through host mutation behind `HumanInTheLoopMiddleware` approval + per-run snapshot merged; host delete/move + full mutation matrix remain)                                  |
| 10  | [AC6](06-ac6-monty-code-mode.md) Monty code mode                   | `run_code_mode` over the Monty `InterpreterPort`; every external call through one `PolicyToolInvoker`; ships pure-compute-only until the four-mode direct-path policy engine lands. | done (interpreter foundation + Option-B `HitlPolicyToolInvoker` merged, gated `RUNTIME_ENABLE_MONTY` off; factory toolset-threading + double-dispatch CAS deferred — AC10 §5)                                      |
| 11  | [AC7](07-ac7-remote-sandbox-execution.md) Remote sandbox execution | DeepAgents `SandboxBackendProtocol` (LangSmith) with snapshot-in / patch-out over AC4/AC5, deny-all egress, short-lived secret refs, durable reaper.                                | in-progress (execute-only `run_in_sandbox` Option-D merged, gated off; snapshot/egress/patch-apply/reaper deferred — AC10 §5)                                                                                      |
| 12  | [AC8](08-ac8-agentic-browser.md) Agentic browser                   | Supervised Playwright worker + local browser MCP via `langchain-mcp-adapters`, exact-origin policy, ephemeral-by-default profiles, per-action approval for side effects.            | in-progress (foundation + browser-MCP provider + action-RPC/downloads-to-staging/HITL side-effect gating merged, gated off; profiles/consent + worker↔main approval transport + `browser_upload` remain — AC10 §5) |
| 13  | [AC9](09-ac9-desktop-connectors.md) Desktop OAuth connectors       | Desktop-only loopback/deep-link OAuth callback variant reusing backend MCP/OAuth/`TokenVault`; shared `api-types` stays additive (web impact none).                                 | done (loopback + deep-link OAuth end-to-end; provider tokens confined to `TokenVault`, canary-proven; web `api-types` additive)                                                                                    |

## P2 — Hardening within delivered scope

Goal: make the shipped broker, path, retention, and audit surfaces adversarially sound before default-on.

| #   | Item                                                                              | Definition of done                                                                                                                                                                                             | Status                                                                                                                                                                                                |
| --- | --------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 14  | Broker trust hardening (no host-root leak, sensitive-path deny, per-run snapshot) | Broker never returns host absolute paths to the worker; sensitive-path policy denies secret files; grant snapshot is immutable per run.                                                                        | done (G1 host-root leak + G2 sensitive-path incl. nested-credential-dir + per-run snapshot merged; symlink/TOCTOU covered by the adversarial matrix; Windows parity is deployment evidence — AC10 §4) |
| 15  | Path-security suite (traversal, symlink/junction/ADS, TOCTOU) macOS + Windows     | The adversarial path corpus (traversal, absolute, Unicode, Windows devices/ADS, symlink/junction/reparse, mount swap, time-of-check/time-of-use) passes on both platforms.                                     | in-progress (macOS corpus — traversal/absolute/NUL/RTL/symlink/TOCTOU — merged; Windows devices/ADS/junction parity is deployment evidence — AC10 §4)                                                 |
| 16  | Retention + deletion cascade (right-sized for `single_user_desktop`)              | Deleting a conversation removes its session directory and decrements AC4 object reachability; deleting a workspace also revokes grants, stops browser contexts, and requests backend connector/token deletion. | in-progress (physical delete + reachability object-GC + quota/age-sweep merged; workspace-delete → grant-revoke/browser-stop cascade remains)                                                         |
| 17  | Local tamper-evident audit chain (`packages/audit-chain`)                         | Capability/audit records are hash-chained and disclosed honestly as tamper-evident, not tamper-proof or immutable.                                                                                             | done (hash-chained signed manifests for delete/export/host-write-through + independent `verify()`; disclosed tamper-evident, not immutable)                                                           |

## P3 — AC10 release gate (migration, repair, rollout/backout)

Goal: the evidence bundle that lets desktop capabilities go default-on.

| #   | Item                                                                           | Definition of done                                                                                                                                                         | Status                                                                                                                                                                                                               |
| --- | ------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 18  | Migration + repair tooling                                                     | Quiesced one-way postgres→file cutover with count/hash verification; JSONL repair reports the first bad offset; index rebuild proven equivalent.                           | done (port-based one-way migration w/ `--dry-run`+`--verify` count/content equality; JSONL diagnose/salvage; torn-index self-heal + rebuild equivalence)                                                             |
| 19  | [AC10](10-ac10-hardening-rollout.md) cross-platform + rollout/backout evidence | Packaged macOS arm64/x64 + Windows x64 smoke, adversarial, retention-expiry, process-leak, and data-preserving backout evidence complete; default-on only after it passes. | in-progress (product-control evidence complete + full regression green — [10-ac10-hardening-rollout.md](10-ac10-hardening-rollout.md); packaged cross-platform smoke + backout drill OUTSTANDING, blocks default-on) |

## Notes

- P0 and the P1 store were built in parallel (overview §23 Wave 1): AC3a real chat did not depend on the file store, and both are merged.
- AC3b (separate-process worker + cross-process recovery) is **deferred**, not on this list — the single in-process worker plus `AsyncSqliteSaver` already delivers the durable loop (README PR index; overview §7).
- Item numbers are stable handles; statuses move as PRs merge. When a status changes, update this file in the same PR that lands the change.
