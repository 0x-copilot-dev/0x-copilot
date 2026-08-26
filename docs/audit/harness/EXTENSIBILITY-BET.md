# Recommendation: a user-authorable extension surface for 0xCopilot

**Verdict: make a smaller bet than the audit implies. Ship document authoring, not a plugin SDK. Stage 0+1 (~2 weeks) is worth doing regardless of what you decide about the rest.**

---

## 1. The bet, in one paragraph

We would commit to **extensions being typed declarative documents, authored through the facade, snapshotted at run start, and compiled into graph-construction kwargs the rented loop already accepts** — never to third-party code running inside the agent process. Three document kinds (Skills, Rules, Roles), one editor destination in `packages/chat-surface`, one compiler, no loader, no sandbox, no SDK, no registry. `hooks/registry.py:2-8` — "Nothing here loads code from disk, from a config document, or from a package name" — stays literally true, permanently, and the PR #632 seam stays a first-party observability instrument rather than becoming a public API. What we give up by not doing this: the skills library stays at 3 because there is no way to author a fourth on the shipping product; the largest measured workload in the product (~495 filesystem calls across five tools) stays unshapeable by the user, because tool-use policy is 12 cells keyed by tool name with no path awareness; two finished HTTP contracts (`PUT /v1/agent/subagents/{name}`, `system_prompt_override`) stay dark; and the honest answer to "can I customize this?" stays "file an issue." What we give up **by** doing it: a plugin ecosystem, a third-party developer story, and any surface that requires user code — permanently, under this design.

---

## 2. What already exists — the honest inventory

**Root cause C, as written, is materially false.** "Every extension point is a Python edit + redeploy" is true of three surfaces and false of six. The audit's dimension row — _"no hook seam of any kind in OURS"_ — is now the least true statement about this repo.

| Surface                                                                             | Authorable without Python?                                                           | Restart-free?                               | Reachable on desktop?                                                                                                                                               |
| ----------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------ | ------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| MCP servers + whole-config editor                                                   | **yes** (12 facade routes)                                                           | **yes**                                     | **yes** — `destinationBinders.tsx:440,594,599`                                                                                                                      |
| Tool-use policy (3 axes × 4 modes)                                                  | **yes** — `me_routes.py:243-308`                                                     | **yes** (snapshot at run start)             | **yes** — `SettingsMount.tsx:763-820`                                                                                                                               |
| Skills                                                                              | **yes** — facade CRUD `app.py:1333-1418` → backend `app.py:1704-1800`                | **yes** — per-run fetch `virtual.py:99-121` | **no UI** — `destinationBinders.tsx:777-780`                                                                                                                        |
| Declared subagents                                                                  | **yes** — `PUT /v1/agent/subagents/{name}`, `subagent_definition_routes.py:113-148`  | **yes** (deps rebuilt per run)              | **no UI anywhere**; contract published at `api-types/src/subagentDefinitions.ts:6-8`                                                                                |
| `system_prompt_override`                                                            | **yes** — typed, 8 KB-capped, audited, facade-proxied, `api-types/src/index.ts:1257` | n/a                                         | **zero consumers** — verified: 6 hits in `services/ai-backend/src/`, all self-referential, the only non-schema one a comment at `workspace_defaults_service.py:288` |
| backend `/v1/tools` (CRUD, ACL, audit, versioning, `kind='code'`, `pending_review`) | **yes**                                                                              | —                                           | **runtime never calls `POST /internal/v1/tools/by_ids`** (`tools/routes.py:605`); executor is a 501 `code_sandbox_not_yet_wired`                                    |
| Hand-dropped `$STORE/skills/<name>/SKILL.md`                                        | **yes** (text editor)                                                                | **yes**                                     | undocumented; **silently shadows a shipped skill** at precedence 1 vs 0 — `dependencies.py:444-462`                                                                 |
| Hook seam                                                                           | **no — Python only, by explicit design**                                             | no (registry written at handler `__init__`) | n/a                                                                                                                                                                 |
| Prompts, builtin tools, hyperparameters, renderers                                  | **no**                                                                               | no                                          | —                                                                                                                                                                   |

**The seam itself.** All 7 `HookPhase` members have live production dispatch sites (`runtime_tool_control.py:700/:737/:422-:459`, `runtime_gate.py:150`, `run.py:676/:1065`), 34 tests pass, and the plumbing is finished. What is "one" is the number of **consumers** — and even that is two registrations sharing one name (`tool_observability.py:343-352`), whose output drains to `logging.getLogger(__name__)` and nothing else (`run.py:1193-1207`). **Any proposal budgeting to "build the seam" is double-paying.**

**Three seam facts that shape the design, all verified:**

1. **The seam is not outermost, despite its own docstring.** `hooks/contracts.py:17-20` claims `tool.execute.before` is "the OUTERMOST wrap_tool_call wrapper." deepagents composes six of its own middleware outside ours (`graph.py:366-377`). The safety conclusion survives for a different reason (`HostPathToolMiddleware` and `HostFilesystemFloor` are both inside), but the stated reason is wrong and the defending test cites stale line numbers.
2. **Hooks fire strictly after human consent.** `HumanInTheLoopMiddleware` implements `after_model` (resolves in the model node); hooks fire inside `wrap_tool_call` (tool node, `runtime_tool_control.py:571`). So a hook's `REWRITE_ARGUMENTS` can execute against a consent card the user granted for a _different_ path — the shape screen and the floor both pass. The seam's contract addresses the floor and not the consent.
3. **The approval-resume path binds no hook session.** `bind_for_run` appears only at `run.py:668,674`; `RuntimeApprovalHandler` (`approval.py:163`) drives the same graph and binds nothing, so `HookDispatch.enabled` is False. **The one flow with a human in it is the one flow no hook can see.**

**The mechanism nobody costed.** The rule engine a user-authored path policy needs is **already in production**. `factory.py:2290-2295` builds `InterruptOnConfig(allowed_decisions=…, when=_bulk_when_outside_granted_ground(...))` and merges it at the single composition point `factory.py:552`. The predicate (`factory.py:2299-2335`, read in full) takes `request.tool_call["args"]`, normalizes through deepagents' own `validate_path`, and tests against a `HostBulkReadScope` built from a **run-start snapshot**. Meanwhile `tool_use_enforcement.py:207` builds `interrupt_on[tool_name] = {…}` — a plain dict, no predicate. **Path-aware, argument-aware, run-start-snapshotted narrowing is shipped machinery; only the source of the rules is system-derived instead of user-authored.**

---

## 3. The recommendation

**One design: extensions are documents. Our Python is the only interpreter. Enforcement compiles into graph-construction kwargs, not into hooks.**

Three document kinds — **Skills** (procedural knowledge), **Rules** (narrowing), **Roles** (declared subagents) — plus one model-callable tool that may only write _drafts_. Authored and stored in `backend` (PDP), read once at run start, enforced in-process (PEP), rendered by one destination in `packages/chat-surface` that both hosts bind.

### Where the judges disagreed, and how I resolved it

**(a) Should `system_prompt_override` be the flagship first increment?** The maintenance judge says its forever cost is near-negative: no new published contract, retires an orphaned one, adds a divergence detector. The compliance and closes-root-cause-C judges say it is the thinnest possible extension (one global prose blob — not named, scoped, composable, or shareable) and that its store is workspace-configuration CRUD living inside `ai-backend`, the documented "do not grow this service" category. **Both are right, so it is demoted, not dropped.** It ships in Stage 0 as a two-day chore because retiring a fully-built write-only dead end is cheap and correct — but it cannot carry the program's argument, because a settings textarea does not unlock authoring.

**(b) Where does a path rule get enforced?** The `risk` proposal sites PathRule matching "in `capabilities/` middleware," citing `ToolUsePolicySnapshot.from_response`. **That is wrong for the `ask` half**, and it is the single most valuable correction in the judging: `capabilities/` middleware fires in the tool node, after consent has already resolved in `after_model`. As specified, the `block` lane builds and the `ask` lane does not — and `ask` is the verb users actually want. **I take `risk`'s artifact (an effect enum of `ask|block` with deliberately no `allow`, so widening is unrepresentable at the type level) and `user`'s mechanism (compile into `interrupt_on` at `factory.py:552`).** This is the graft the whole exercise produced.

**(c) The ranking split.** Two judges rank `user` first; the maintenance judge ranks it second and is right about why — a new document kind costs four places forever, and its compiler binds to `InterruptOnConfig` and deepagents `permissions`, the fastest-moving part of a loop we rent. I price that rather than dismiss it: **the kind count is capped at three**, every kind carries a `schema_version` from day one, and Stage 2 is explicitly gated on Stage 1 being _used_.

**(d) One thing all three judges converged on independently:** a mechanical declared-vs-consumed conformance gate. Three of three, from three different lenses, is the strongest signal in the exercise. It ships in Stage 0.

### Stage 0 — Debt and detectors (~1 week)

**Ships:** (i) bind a hook/observation session on the approval-resume path (`approval.py:163`), with a test that drives a real interrupt and asserts a post-approval tool call is observed; (ii) refuse `REWRITE_ARGUMENTS` on any tool consent-gated this turn (closes the approved-path-vs-executed-path divergence); (iii) fix the false "OUTERMOST" claim at `hooks/contracts.py:17-20` and the stale line refs in its test; (iv) `COPILOT_EXTENSIONS_OFF=1`, one env var that makes the runtime ignore every user-authored document at once; (v) the **conformance gate** — CI fails if any field of any published extension contract has zero reference sites in the consuming service; (vi) wire `system_prompt_override` as a `PROMPT_ASSEMBLE` consumer, append-only, capped, delimited, attributed.

**Unlocks on its own:** three latent defects closed, a kill switch support will need on day one of Stage 1, and a detector that would have caught all four known instances of this failure mode (ours ×2, OpenCode's dead `permission.ask`, Hermes' unread `provides_hooks`).

**Costs:** ~1 week. The only forever cost is the gate itself, and it gets cheaper as the repo grows because it converts coherence from human review attention into CI.

### Stage 1 — The Skill editor: the review surface (~1 week) — **do this regardless**

**Ships:** the `SkillEditor` in `packages/chat-surface`, the desktop route that binds `onEditSkill`/`onNewSkill`, a real Pydantic frontmatter contract replacing `validate_markdown`, collision refusal against system skills, `provenance: user|agent`, `schema_version`.

**Unlocks on its own:** the extension artifact with the fully proven data path finally has a front door; the audit's "authoring path is what caps it at 3" stops being true; and the undocumented precedence-1 shadowing hole (`dependencies.py:444-462`) is closed as a side effect. It also establishes the _place to look at a document_ before anything can write one — the ordering that would have prevented both Hermes CVEs.

**Costs:** ~1 week. No new document kind, no runtime change, no new attachment point. Forever: one editor, one validator, one contract.

### Stage 2 — The Rules document (~2–3 weeks) — **gated on Stage 1 usage**

**Ships:** a narrowing-only document stored in `backend` as policy data, added to the existing tool-use policy record (which already drops unknown values forward-additively). Entries are `{match: {axis, path_glob}, effect: ask|block}`. Fetched once at run start alongside `ToolUsePolicySnapshot.from_response`. Compiled in-process into (a) deepagents `permissions` rules for `block` and (b) `InterruptOnConfig.when` closures for `ask`, merged at `factory.py:552` next to the existing bulk-read merge. **The matcher delegates to `HostFilesystemFloor`'s** — a second matcher on a security control is a control that silently fails open, and this repo has already shipped exactly that bug ($COPILOT_HOME dotted segments). Denials surface as a **typed run event**, never a log line.

**Unlocks on its own:** "ask before writing anywhere under `~/work/prod`", "never touch `**/node_modules`" — the only proposal item justified by measured usage rather than inferred demand.

**Costs:** ~2–3 weeks; the compiler is the only genuinely new code and has a working template ~40 lines from where it lands. Forever: one document kind × four places, a narrowing property test per rule kind, and a "which documents were in effect for this run" surface in the run cockpit.

### Stage 3 — Roles UI + the `fs_permissions` clamp (~1–2 weeks) — **gated on an open question**

**Ships:** the editor for the already-live subagent contract, and a clamp so `fs_permissions` **intersects** the parent's granted roots instead of replacing them (`factory.py:2919-2932`). Blocked on settling whether deepagents merges or prefers child permissions (§8).

### Stage 4 — `propose_extension`, draft-only (~1–2 weeks)

**Ships:** a model-callable tool that writes a **draft** of any kind and never a live document; human approval promotes. Plus conditional skill visibility (`requires_tools` / `fallback_for_tools` filtering the per-run card index), whose frontmatter fields are **reserved in Stage 1's contract** so this needs no schema break.

**Why it matters:** Hermes has 182 SKILL.md packages and we have 3, and the difference is not an editor — `skill_manage` is a model-callable tool in its core toolset, so the library grows during normal work. This is that mechanism with the write authority removed.

---

## 4. The security model

**The trust boundary is the `ai-backend` process, and it is not moving.** On the shipping desktop `RUNTIME_START_IN_PROCESS_WORKER='true'` (`service-env.ts:546`) — there is not even a process boundary between the HTTP API, the graph, and any hypothetical plugin. Inside that process: BYOK provider keys in plaintext (`provider_kwargs.py:218-220`) and deployment keys placed in `os.environ` (`settings.py:608-609`); `ENTERPRISE_SERVICE_TOKEN`, which with attacker-chosen org/user headers addresses the entirety of backend's `/internal/v1/*` (`proxy_plane.py:104-120`); the per-boot desktop broker bearer, the key to every granted host root (`broker.ts:40-45`); open network by design; and plain `open()`, which bypasses **both** the deepagents permission rules and `HostFilesystemFloor`, because both are composed around the tool path and not around the process.

**Therefore nothing a user or the model authors ever becomes code in that process.** There is no sandbox in this design because there is nothing to sandbox. `pydantic-monty` stays pinned and fail-closed; `/v1/tools`'s `code` kind stays a 501; the stdio MCP transport stays refused (`tool_source.py:401-408`) even though backend already persists an arbitrary `command`/`args`/`env`/`cwd` validated only against NUL/CR/LF.

**Five invariants, enforced by types and tests, not by review convention:**

1. **Non-widening by type.** Every effect enum omits every widening verb. Rules are `ask|block` with no `allow` — a widening rule is unrepresentable, not merely disallowed. Skill `allowed_tools` keeps its union-to-narrow semantics (`tool_gate.py:11-34`). Filesystem rules **intersect** granted roots, never union. _If a proposed document kind cannot be made non-widening by type, it does not ship._
2. **Escalation is the only additive verb.** A rule may raise a call from silent to ASK; it can never lower ASK to silent. This is Hermes' `approve`-as-escalation with the untrusted party removed.
3. **Enforcement runs before consent, not after.** Compiling into `interrupt_on` means the predicate is evaluated in `after_model`, so the human sees the real, final arguments. A document structurally cannot desynchronize what was approved from what executes — which a hook can.
4. **The agent proposes; a human promotes.** `provenance` records which, so a future curator prunes only what the agent wrote.
5. **No repo-scoped tier, ever.** No `.copilot/rules` read from cwd. "The user cloned a hostile repo" is a live threat model for a coding agent and is exactly what produced GHSA-5qr3-c538-wm9j.

**What this does not defend against — write it down and ship it with the feature.** A user who reads and approves a hostile skill. A model that writes a benign-looking skill after reading a hostile web page — and `web_search` at 324 calls is the most-used tool in the product, so this is the live channel. Approval fatigue. The controls are draft-only writes, provenance, untrusted-attribution delimiters at load, visibility filtering keeping unloaded skills out of context, and a visible diff. **They are mitigations, not a boundary.** Hermes' SECURITY.md §2.5 is the artifact worth copying, and that candour — not a sandbox — is what makes a full-authority surface defensible on a single-user machine.

**The gate for reopening the code question** (so nobody re-litigates it informally): a committed trust-model doc _before_ any loader; credentials out of the process first (BYOK and service token behind broker calls — until then, sandboxing a plugin is theatre, the escape target is three feet away); a per-call capability gate the extension cannot address around; an enable-allowlist in the _same commit_ as any loader, default-deny, fail-closed on non-TTY; no project-scoped tier; and the conformance gate already in place.

---

## 5. What this rules out, and what we maintain forever

**Ruled out:** any plugin loader (npm, pip, disk scan, dynamic import) — `hooks/registry.py:1-8` is never amended. A second hook consumer beyond first-party observability. All sandboxing work. User-authored model-callable tools — new tools stay MCP-only, which means backend's fully-built `/v1/tools` destination stays deliberately dark. Finishing the stdio MCP transport. Any client-side/UI extension tier in `chat-surface` or `surface-renderers`. A marketplace and any third-party developer story. And the entire transform/redaction class — "strip SSNs from every tool result," custom telemetry sinks, org-specific post-processing — which stays a Python edit by us. **For that class, root cause C stands unresolved and this proposal does not pretend otherwise.**

**Maintained forever:** each document kind costs four places (a contract in `api-types`, a validator in `backend`, a run-start snapshot + compiler in the runtime, a renderer in `chat-surface`) — three kinds is the ceiling, and a fourth must argue for itself. Published contracts users have authored files against, **on a single-user desktop with no server-side backfill** — which is why `schema_version` plus upgrade-on-read is a Stage 1 requirement and not a later refinement. A narrowing property test per rule kind. The conformance gate. A "why did my agent refuse / what was in effect" surface. And a prune/curator story somewhere around 30–50 skills.

---

## 6. The first increment — precise enough to start Monday

**Stage 0 (three small parallel PRs) + Stage 1 (two PRs). ~2 weeks, one engineer.**

_Stage 0, PR-A — approval-resume + rewrite refusal._ Bind `RuntimeHookContext.bind_for_run()` / `ToolCallObservationContext.bind_for_run()` in `services/ai-backend/src/runtime_worker/handlers/approval.py:163` (the pattern is `handlers/run.py:668,674` — currently the only binding sites in the service). Add a test that drives a real interrupt/resume and asserts a post-approval tool call is observed. Separately, refuse `ToolExecuteBeforeAction.REWRITE_ARGUMENTS` for any tool consent-gated this turn (`runtime_tool_control.py:700-721`). Fix `hooks/contracts.py:17-20` and the stale `factory.py:537/:546` refs in `tests/unit/agent_runtime/hooks/test_runtime_hook_seam.py:259` (real lines are 585/594).

_Stage 0, PR-B — detectors._ `COPILOT_EXTENSIONS_OFF=1` short-circuiting every document read. The conformance gate: for each published extension contract, every field must have ≥1 reference site in the consuming service. Start with the grep-class floor (fields → references), not an AST field-map — the strict version can itself rot.

_Stage 0, PR-C — retire the dead contract._ A `PROMPT_ASSEMBLE` consumer for `system_prompt_override` (`runtime_api/schemas/workspace_defaults.py:232`), reading a run-start snapshot from the record already loaded on **both** graph-driving paths. Reconcile the two ceilings (8 KB field vs `MAX_APPENDED_CONTEXT_CHARS = 8_000`) by refusing at the API with a typed error, not truncating in the runtime. Desktop: a textarea bound to the object `SettingsMount.tsx:409,436` already holds.

_Stage 1, PR-D — the editor._ `packages/chat-surface/src/destinations/skills/SkillEditor.tsx`, wired through the props `SkillsDestination.tsx:78,80` already declares — note `SkillsDestination.test.tsx:94-118` **already tests those callbacks**, so the interface is specified and covered; only the editor is missing.

_Stage 1, PR-E — the contract and the binder._ Delete the omission at `apps/desktop/renderer/destinationBinders.tsx:777-780` ("The skill editor route isn't built on desktop yet, so Edit / New are omitted rather than faked") and bind the route. Replace `services/backend/src/backend_app/contracts.py:237-242` — verified to be literally _is a string, is not blank_ — with a Pydantic frontmatter contract: `name` (≤64, refused on collision with a shipped system skill), `description` (≤1024), `allowed_tools`, `provenance: user|agent` (only `user` writable now), `schema_version`, plus **reserved-but-unimplemented** `requires_tools` / `fallback_for_tools`. Body size cap. **Test the write path against live Postgres, not the in-memory store** — the skills store splats `SELECT *` on read, which is the documented shape of a green suite over a broken production path.

Ship PR-D/E as a real journey assertion on the packaged app, not a jsdom assertion: this repo has been burned specifically by injected-dependency tests staying green over a feature gated off at its seam.

---

## 7. What would make this the wrong bet

**The strongest case against is that this document is an elaborate way of saying "do backlog item 9," and if that is true we should do backlog item 9 and skip the program.** I think that case is largely correct, and I am not going to hedge it: **finishing skill authoring beats a plugin surface outright.** A skill is the fullest thing a document can be — named, discoverable, composable, adds procedural behaviour, narrows tools by construction — its data path is already proven end to end and restart-free, it requires no trust decision, and the audit's own judgement is that the missing authoring path is what caps the library at 3. A plugin surface costs a permanent trust decision, a sandbox we cannot build, a published contract we can never break, and — per OpenCode's own v2 migration step 7, _"Remove Returned Hooks"_ — copies a contract its author has already scheduled for deletion. **If you read only one section, do Stage 0 + Stage 1 and stop.**

**The evidence for Stages 2–4 is weaker than it reads.** 321 runs on one machine — the decision-maker's own — not re-derived from the ledger, and it is unknown whether the counts include subagent tool calls or only supervisor ones. That histogram measures how _we_ use the product. Worse, `stage_rowset_write` at 0 is exactly as consistent with "unwanted" as with "unreachable" — **and that is the same ambiguity that decides whether skills-at-3 is an authoring problem or a demand problem.** If it is a demand problem, Stage 1 gets used by nobody, and Stages 2 and 4 buy nothing. That is why Stage 2 is gated on measured Stage 1 usage rather than scheduled.

**Other honest failure modes.** If the strategic bet is that a plugin ecosystem is a moat, this design argues against the moat and should be rejected on that basis, not on the threat model — the threat model is not in serious dispute, only its price. The comparison scorecard never improves: after all five stages we still have zero lines of user-authored code and no SDK, and my defence (OpenCode's own `permission.ask` is dead in its published contract; Hermes' manifest declares a key its parser never reads; hook _count_ is a poor proxy) is an argument, not a win, and will need making repeatedly. Documents are a ceiling as well as a floor — this design does not grow into user code; that would be a second trust decision requiring the sandbox work I am declining. And Stage 4 is the weakest joint by design: a skill written after reading a web page is a durable instruction to the model's future self, and human approval is defeated by approval fatigue.

**Cheapest way to be wrong less expensively:** instrument skill loads and rule hits before Stage 2, and get a distribution that is not one person's.

---

## 8. Open questions the ground-truth phase could not settle

1. **Does deepagents merge or replace a subagent's attached `permissions`?** ~30 minutes reading the vendored middleware. Decides whether the `fs_permissions` finding (`factory.py:2919-2932` translates user input into a grant with no clamp; `host_floor.py:224-230` short-circuits `permits_read` True for non-dotted paths) is a real read-widening vector or a paper one. **Gates Stage 3.**
2. **Is the approval-resume hook gap observable?** Confirmed by reading, not by running. ~30 minutes to drive a run through an interrupt and watch the ledger stay empty. Converts CONFIRMED-by-reading into CONFIRMED-by-running before PR-A.
3. **Where does `/v1/skills` actually land on the packaged single-user desktop** — embedded Postgres or file-native? The route exists; the storage backing on the shipping product is untraced, and it determines whether PR-E's live-Postgres test is the right test.
4. **Do the tools users would rule on name their subject in a stable argument?** Verified for the filesystem cluster. `publish_artifact` (142 calls) and `draft_reply` (30) have no path argument — rules over them degrade silently to tool-name-only, i.e. no better than today's 12 cells. The design is strongest exactly where usage is heaviest and weakest on the artifact and connector clusters.
5. **Merge semantics for user-level vs workspace-level Rules.** Undesigned. "Most restrictive wins" is the obvious answer and is not obviously what users expect — and it cannot be changed after the first document is authored.
6. **Do the usage counts include subagent tool calls?** Changes what the delegation surface (Stage 3) is worth.
7. **Why was `POST /internal/v1/tools/by_ids` never consumed** — descoped, blocked on the 501 executor, or never scheduled? A decision record would change the cost of ever revisiting it.
8. **Does the per-run skill card index have a clean attachment point for a visibility filter?** deepagents' `SkillsMiddleware` composes outside ours (`graph.py:366-375`), so the filter may have to apply at fetch time (`virtual.py`) rather than index time. Affects Stage 4 sizing.
9. **The enabled hook path is unbenchmarked.** Irrelevant to this design (nothing lands on `tool.execute.before`), but it becomes load-bearing the moment anyone puts an interpreter there.
10. **Windows/Linux confinement posture.** Only `macos-workspace-confinement.ts` exists, it is darwin-only, and it wraps the whole service rather than isolating anything from anything.
