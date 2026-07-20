---
id: audit-index
kind: report
audit_date: 2026-07-20
---

# 0xCopilot Architecture Audit

A full-repository architecture audit of the 0xCopilot enterprise agent platform — every
deployable component and every major cross-cutting flow, mapped, adversarially verified, and
synthesized into a knowledge graph plus a ranked findings set.

**Generated:** 2026-07-20 · **Base:** worktree `worktree-arch-audit-v2` at HEAD.

**Start here:** [`00-overview.md`](00-overview.md) — the front door (executive summary, system
context, cluster map, request-path traces, cross-cutting concerns, and the Top 10 risks &
opportunities). This README is the index and methodology.

---

## Purpose

This audit exists to give a principal-engineer-level, evidence-linked answer to three questions:

1. **What is actually here?** — a complete, non-overlapping map of the codebase (18 clusters + 6
   flows), proven to cover 95.1% of tracked LOC with the remainder itemized ([coverage.md](coverage.md)).
2. **What does it actually do at runtime?** — six end-to-end flow traces that follow behavior across
   service boundaries, distinguishing what the code *claims* from what *executes in a shipped
   configuration*.
3. **What should change, and in what order?** — a de-duplicated, severity-ranked findings set with
   repo-relative evidence, separating confirmed behavioral risks from acknowledged low-severity
   items and deployment assumptions.

It is written for two audiences: engineers navigating or refactoring the system, and
compliance/architecture reviewers assessing it for regulated buyers. Findings follow the repo's own
compliance-review discipline — a control counts as implemented only when code, config, tests, and
docs all support it; deployment controls (TLS, KMS, SIEM, backup) are marked "not evidenced in
repo" unless deploy config is present.

---

## Methodology

A five-stage fleet audit:

1. **Fleet decomposition (18 clusters + 6 flows).** The repo was partitioned into 18 non-overlapping
   clusters by deployable boundary and cohesion, plus 6 end-to-end flows that deliberately cross
   clusters (auth, run+streaming, MCP, desktop boot, contracts, data). One auditor per cluster/flow
   produced a report (Purpose · node/edge inventory · Health Assessment · numbered findings with
   evidence paths) and a graph partial.
2. **Coverage proof.** Every one of the 3,205 tracked source files (excluding the audit's own output)
   was classified to exactly one cluster by longest-prefix match, with an explicit test-attribution
   rule. Result: 95.1% of LOC claimed with zero overlap and no dead claims; the 4.9% orphan remainder
   (service chassis, migrations, per-service docs) and tracked junk are itemized. This is the proof
   that the cluster map has no blind spots — see [coverage.md](coverage.md). The one real source gap
   it surfaced (the unmodularized `backend_app` chassis) was given its own cluster, *backend-core*.
3. **Per-cluster adversarial verification.** Each cluster/flow report was re-checked by an independent
   verify pass that attempted to refute each finding against the code — confirming, downgrading, or
   dropping it. The 24 verdict records live in [`_verify/`](_verify/). Findings carry the resulting
   state (confirmed / accepted / plausible) and, where multiple auditors independently found the same
   issue, an auditor count.
4. **Knowledge-graph synthesis.** The 24 graph partials were merged into one graph (367 nodes, 681
   edges, 0 dropped, 0 endpoint remaps) with every edge endpoint resolved to an existing node — both
   human-readable ([graph/knowledge-graph.md](graph/knowledge-graph.md)) and machine-readable
   ([graph/knowledge-graph.json](graph/knowledge-graph.json)).
5. **Findings synthesis.** Cluster-local findings were de-duplicated and merged across clusters into
   seven cross-cutting reports by finding *type* (risks, boundary violations, SSOT violations,
   duplication, dead code, refactor/simplify, replace-with-libraries), each ordered by blast radius.
   Cross-references between them are explicit (e.g. a dead-code item that hides a latent bug links the
   risk). The [Top 10](00-overview.md#top-10-risks--opportunities) is drawn from these.

**How to read a finding.** Each has an id (`RISK-*`, `BND-*`, `SSOT-*`, `DUP-*`, `DEAD-*`, `REF-*`,
`LIB-*`), a severity/confidence pair, a verification state, the owning cluster(s), evidence paths
with line numbers, a remediation, and (for structural items) a payoff. *Confirmed* = load-bearing;
*accepted* = low-severity, acknowledged-as-written; *plausible* = re-verify at current HEAD first.

---

## Contents — annotated listing

Everything below is under `docs/audit/`.

### Top-level

- [`00-overview.md`](00-overview.md) — **the front door.** Executive summary, system context (users /
  external systems / deploy targets), the cluster map (mermaid + role/LOC/health table), four
  request-path traces, the four cross-cutting concerns, the Top 10 risks & opportunities, and this
  "how to use" guide.
- [`README.md`](README.md) — this index: purpose, methodology, and the annotated listing.
- [`coverage.md`](coverage.md) — the coverage proof. Method, per-cluster file/LOC table, the full
  orphan breakdown (308 files / 36,690 LOC grouped by cause), and the tracked-junk report. Read this
  to trust that the map covers the repo.

### `clusters/` — 18 per-cluster reports

Each: frontmatter (id · kind · audit_date) · Purpose · a node inventory and inbound/outbound edge
tables · a Health Assessment (strengths / weaknesses / risks) · numbered findings with evidence.

| # | File | Cluster | One-line health |
|---|---|---|---|
| 01 | [01-ai-runtime-execution.md](clusters/01-ai-runtime-execution.md) | ai-runtime-execution | Well-engineered domain core; medium risk from dead machinery + prompt-vs-enforcement drift. |
| 02 | [02-ai-runtime-capabilities.md](clusters/02-ai-runtime-capabilities.md) | ai-runtime-capabilities | Fail-closed and careful; ~16% production-dead + a domain→API boundary violation. |
| 03 | [03-ai-runtime-persistence.md](clusters/03-ai-runtime-persistence.md) | ai-runtime-persistence | Strong contracts but drifting; god port, Postgres gaps, file/in-memory duplication. |
| 04 | [04-ai-runtime-api.md](clusters/04-ai-runtime-api.md) | ai-runtime-api | Healthy core, strong invariants; soft RBAC/readiness + process-local inbox bus. |
| 05 | [05-ai-runtime-worker.md](clusters/05-ai-runtime-worker.md) | ai-runtime-worker | Strong hot path; ~3.2k LOC dormant jobs, handler drift, serial dispatch. |
| 06 | [06-backend-identity.md](clusters/06-backend-identity.md) | backend-identity | Most disciplined code in the repo; debt is wiring + cross-copy contracts. |
| 07 | [07-backend-product.md](clusters/07-backend-product.md) | backend-product | High discipline; every destination store in-memory + several stub features. |
| 08 | [08-backend-platform.md](clusters/08-backend-platform.md) | backend-platform | Vault/chain healthy; audit egress facade-only; create_app overgrown. |
| 09 | [09-backend-facade.md](clusters/09-backend-facade.md) | backend-facade | Good trust boundary; accretion + oldest routes on weakest auth. |
| 10 | [10-frontend-web.md](clusters/10-frontend-web.md) | frontend-web | Disciplined host; ~17.8k LOC dead destinations + dual Settings. |
| 11 | [11-chat-surface-core.md](clusters/11-chat-surface-core.md) | chat-surface-core | Deliberately architected SSOT; runs in no CI; cockpit has forked. |
| 12 | [12-chat-surface-destinations.md](clusters/12-chat-surface-destinations.md) | chat-surface-destinations | Live sixth staff-level; ~68% unmounted-but-exported. |
| 13 | [13-desktop-app.md](clusters/13-desktop-app.md) | desktop-app | One of the healthiest; security-serious but over-provisioned. |
| 14 | [14-desktop-distribution.md](clusters/14-desktop-distribution.md) | desktop-distribution | Well-crafted glue; SSOT seams + Electron major skew. |
| 15 | [15-shared-packages.md](clusters/15-shared-packages.md) | shared-packages | Disciplined; central risk is the api-types hand-mirror. |
| 16 | [16-build-deploy.md](clusters/16-build-deploy.md) | build-deploy | Mature CI/CD; safety-critical parts least exercised. |
| 17 | [17-docs-corpus.md](clusters/17-docs-corpus.md) | docs-corpus | Living contracts strong; prior-art stratum has landmines. |
| 18 | [18-backend-core.md](clusters/18-backend-core.md) | backend-core | FUNCTIONAL BUT AT RISK — the unmodularized backend chassis. |

### `flows/` — 6 end-to-end flow reports

Each: overview (entry/exit points) · a numbered cross-cluster trace · a sequence diagram · the
contracts involved · failure modes as-implemented · findings.

- [flows/auth-identity.md](flows/auth-identity.md) — `flow-auth`: six sign-in ramps → one HMAC bearer
  → facade verification → trusted service headers; BYOK provider keys; desktop posture.
- [flows/run-lifecycle-streaming.md](flows/run-lifecycle-streaming.md) — `flow-run-streaming`: goal →
  queued run → worker claim → LangGraph execution → sequence-numbered events → SSE → client
  projection; approvals and cancel.
- [flows/mcp-connectors.md](flows/mcp-connectors.md) — `flow-mcp`: MCP server registration → OAuth +
  vault → internal cards → tool exposure → JSON-RPC `tools/call` via the backend proxy.
- [flows/desktop-boot.md](flows/desktop-boot.md) — `flow-desktop-boot`: `copilot` CLI → runtime
  staging → Electron supervisor (Postgres + 3 services) → health gate → transport/auth wiring →
  shell mount.
- [flows/contracts-and-types.md](flows/contracts-and-types.md) — `flow-contracts`: Pydantic → facade
  → api-types mirror → chat-transport → chat-surface → hosts (the SSOT audit).
- [flows/data-persistence-retention.md](flows/data-persistence-retention.md) — `flow-data`: the
  nine-store inventory, retention sweeps, user deletion, legal holds, and audit export to SIEM.

### `findings/` — 7 cross-cutting synthesis reports

De-duplicated and merged across all clusters/flows, each ordered by blast radius.

- [findings/risks.md](findings/risks.md) — behavioral defects: access-control gaps, non-durable
  compliance controls, UI-first wiring, docs that misdescribe reality (`RISK-*`).
- [findings/boundary-violations.md](findings/boundary-violations.md) — layering / dependency-direction
  breaks (`BND-*`).
- [findings/ssot-violations.md](findings/ssot-violations.md) — one fact maintained in many places;
  silent drift vectors (`SSOT-*`).
- [findings/duplication.md](findings/duplication.md) — copy-pasted logic to DRY out (`DUP-*`).
- [findings/dead-code.md](findings/dead-code.md) — shipped-but-unreachable surface, ~95k LOC total
  (`DEAD-*`).
- [findings/refactor-simplify.md](findings/refactor-simplify.md) — god-modules, overlapping machinery,
  split-brain interfaces (`REF-*`).
- [findings/replace-with-libraries.md](findings/replace-with-libraries.md) — bespoke reimplementations
  of solved problems (`LIB-*`).

*(There is no `findings/README.md`; the Top 10 synthesis lives in
[`00-overview.md`](00-overview.md#top-10-risks--opportunities).)*

### `graph/` — the knowledge graph

- [graph/knowledge-graph.md](graph/knowledge-graph.md) — human-readable: stats (367 nodes / 681
  edges), the cluster-level mermaid, and per-cluster node/edge tables with external systems.
- [graph/knowledge-graph.json](graph/knowledge-graph.json) — **machine-readable, for programmatic KG
  use.** Nodes (`id` · `kind` · `path` · `summary`) and edges (`from` · `to` · `kind` · `label`).
  Query it for impact analysis, "what imports/calls X", dependency-direction checks, or to feed an
  agent that needs the architecture graph. Every edge endpoint resolves to a node id.
- [graph/partials/](graph/partials/) — the 24 per-cluster/flow fragments the merged graph was built
  from (provenance).

### `_verify/` and `_meta/` — provenance

- [`_verify/`](_verify/) — 24 adversarial-verification records (one per cluster/flow) capturing
  which findings were confirmed / downgraded / dropped during the verify pass.
- [`_meta/findings-raw.json`](_meta/) — the raw pre-synthesis finding set behind the seven reports.

---

## At a glance

- **Scope:** 18 clusters + 6 flows · ~3,200 tracked source files · ~745k LOC · **95.1% LOC coverage**,
  proven, with the remainder itemized.
- **Graph:** 367 nodes · 681 edges · 33 external systems.
- **Headline verdict:** engines strong, wiring and contracts are the debt — a build-ahead phase with
  ~95k LOC of production-dead code, volatile product persistence, compliance controls wired in no
  deployment, an untested SSOT layer, and a hand-mirrored contract surface. Fixes are mechanical and
  well-scoped; see the [Top 10](00-overview.md#top-10-risks--opportunities).
