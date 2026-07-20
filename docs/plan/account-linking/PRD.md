# PRD — Account linking & merge

Status: **Approved for build** · Owner: identity · Last updated: 2026-07-19

Decisions locked by product:

- **D-02 — Full data merge.** When linking an identity that already belongs to another account, the two accounts are **merged** (product data consolidated), not kept separate.
- **D-01 — Non-empty conflicts route through the full-merge job.** There is no "reject on conflict" path and no "empty-only" restriction; any conflict runs the same merge.

---

## 1. Problem statement

The system has **no first-class account**. The unit of identity is `(org_id, user_id)`, and every self-signup identity provisions its **own personal org** (`provision_personal_org` creates org + user + membership + admin role in one transaction). Consequences:

- A single human who signs in with **Google** and with a **wallet** becomes **two separate accounts** with two separate sets of conversations, runs, memory, skills, keys, and audit history.
- There is **no write path** that attaches a second identity to an existing user, and **no merge** anywhere. `_link_or_provision` (SIWE) and `_link_or_provision_user` (OIDC/SAML) only _find-or-create by the incoming identity_ — they never bind to the already-authenticated caller.
- PR #102 made the identity surfaces honest but modelled identity as **email XOR wallet** — a single anchor per account, which this feature supersedes.

**What users need:**

1. A wallet account can **link Google** — which is also how they **add an email** (no SMTP; "Continue with Google" brings a verified address). The `<address>@wallet.invalid` placeholder is upgraded to the real Google email.
2. A Google (or dev/local) account can **link a wallet**.
3. When the identity being linked is **already another account**, the two accounts are **merged** into one (D-02), including all product data across backend and ai-backend (D-01: this is the only conflict path).
4. Users can **see and unlink** their linked identities.

**Why it's hard (the honest scope):** because personal-org == user, "merge two accounts" == **consolidate two orgs** — re-keying `(org_id, user_id)` across ~40 backend tables **and** all ai-backend runtime tables, across a hard service boundary, past encryption-AAD, per-org RLS, per-org audit hash-chains, PK/unique collisions, live sessions, and in-flight runs.

## 2. Goals / Non-goals

**Goals**

- Authenticated, proof-gated linking of wallet and Google identities to the current account.
- Placeholder-email → real-verified-email upgrade on Google link.
- List + unlink linked identities (with a last-method-standing guard).
- Full, correct, tenant-isolated **account merge** when a linked identity is already an account.
- Audit + reversibility (support-grade) for every link/unlink/merge.

**Non-goals (v1)**

- Email/password or magic-link _sign-in_ as the "add email" path — superseded by Google link (D per product).
- SAML link/merge — mirror OIDC in a later pass (design parity noted, not built).
- Merging **shared/team** orgs (more than one member) — v1 covers personal orgs only; a merge that would touch a multi-member org is refused with a clear error.
- User-facing "undo merge" — merge is product-data-irreversible; reversal is a support operation from the soft-disabled stub + audit trail.

## 3. Definitions

| Term         | Meaning                                                                                                                     |
| ------------ | --------------------------------------------------------------------------------------------------------------------------- |
| **Account**  | `(org_id, user_id)`. For self-signup, the org is 1:1 with the user.                                                         |
| **Identity** | An external credential edge: a `wallet_identities` row (address→user) or `oidc_identities` row (provider+subject→user).     |
| **Survivor** | The account that persists after a merge (the caller's current account).                                                     |
| **Absorbed** | The account whose identity is being linked and whose data + identities are moved to the survivor, then soft-disabled.       |
| **Re-key**   | Rewriting the tenancy columns (`org_id`, and owner `user_id`) of a row from the absorbed account to the survivor.           |
| **Proof**    | A fresh SIWE signature or Google id_token demonstrating control of the identity being linked. Serves as the consent record. |

## 4. Functional requirements

### Linking

- **FR-L1 — Link wallet.** An authenticated user can link a wallet via `POST /v1/me/identities/wallet`, reusing the full SIWE verify pipeline (nonce → origin/domain binding → time window → signature recovery → single-use nonce consume). On success the wallet is bound to the **current** `(org_id, user_id)`; no new session is minted.
- **FR-L2 — Link Google + email upgrade.** An authenticated user can start a Google link via `POST /v1/me/identities/google/link/start` (binds `link_user_id`/`link_org_id` onto the OAuth state row) and complete it on the existing public `/v1/auth/oidc/callback`. The callback **requires `email_verified=true`**, attaches the identity, and if the current email is the `@wallet.invalid` placeholder, **upgrades** `primary_email` + `email_verified_at` to the Google values (collision-guarded against `UNIQUE(org_id, lower(email))`).
- **FR-L3 — Proof of ownership.** Linking any identity requires cryptographic proof of control (SIWE signature / verified id_token). The request identity (survivor) is derived from the verified session, never from the request body.
- **FR-L4 — List identities.** `GET /v1/me/profile` (or a dedicated `/v1/me/identities`) returns the caller's linked identities: email/Google (with verified state) + each wallet (address + chain) + auth methods. Backed by new `list_identities_by_user` reads on the OIDC and wallet stores.
- **FR-L5 — Unlink.** `DELETE /v1/me/identities/{kind}/{id}` soft-unlinks an identity, **refused** when it is the account's last remaining sign-in method (lockout guard).
- **FR-L6 — Idempotency.** Linking an identity already bound to the **current** account is a 200 no-op. Linking one bound to a **different** account triggers merge (FR-M1).

### Merge

- **FR-M1 — Conflict trigger.** When a link proof resolves to an identity already owned by a _different_ account, the operation becomes a merge (D-01: always; no reject path).
- **FR-M2 — Survivor selection.** The **caller's current account is the survivor**; the identity's prior account is absorbed. (Rationale: the caller holds a live session and just proved control; the survivor keeps the caller's bearer valid via re-mint.)
- **FR-M3 — Backend re-key.** All `(org_id, user_id)`-scoped backend rows of the absorbed account are re-keyed to the survivor: memberships/roles, provider keys, skills, MCP servers/sessions/tokens, profile+preferences+avatars, todos, notifications, api-keys, privacy/tool-use/tenant settings, adapters, identity rows — with encrypted columns decrypt-old/re-encrypt-new, PK/unique collisions resolved, executed under a privileged (BYPASSRLS) context.
- **FR-M4 — ai-backend re-key.** Because the backend must not import ai-backend, the absorbed account's runtime data (conversations, messages, runs, events, memory, drafts, approvals, usage, checkpoints, file-native store) is re-keyed via a **new authenticated ai-backend endpoint** the backend calls over HTTP (`POST /internal/v1/admin/merge` or equivalent), with the same encryption-rewrap + collision rules; audit hash-chains are **not** rewritten (append a merge marker instead).
- **FR-M5 — Sessions.** The absorbed account's sessions are **revoked**; if the caller authenticated on an absorbed identity, a fresh **survivor bearer is minted first**, returned, then absorbed sessions revoked (no stranded in-flight request).
- **FR-M6 — In-flight quiesce.** Runs in `waiting_for_approval` and pending outbox rows for the absorbed account are drained/blocked before re-tenanting.
- **FR-M7 — Absorbed disposal + audit.** The absorbed user is **soft-disabled** (`deleted_at`/`status=disabled`, `absorbed_into_user_id` set), never hard-deleted; its identities are re-pointed to the survivor; immutable `account_merged` audit rows are written on both orgs' trails referencing the proof.
- **FR-M8 — Conflict resolution within merge.** Deterministic rules for collisions: duplicate provider keys (survivor wins, absorbed dropped w/ audit), same-day usage rollups (SUM/merge rows), memory-scope uniques (namespace re-key or merge), `workspace_defaults` (survivor row wins), duplicate email (survivor keeps its email; absorbed's placeholder is dropped).

### UI

- **FR-U1 — Linked accounts panel.** Settings shows a "Linked accounts" list (email/Google + wallets), each with copyable detail, a **Link** affordance per kind, and **Unlink** (gated per FR-L5). Replaces #102's XOR anchor on **both** desktop and web.
- **FR-U2 — Merge confirm.** When a link would trigger a merge, the user sees an explicit, honest confirmation ("This wallet/Google account already exists — linking it will move its data into this account and disable the other login") before the merge runs.
- **FR-U3 — Both hosts.** Desktop (`SettingsMount`/chat-surface) and web (`Profile.tsx`) both render the panel via their own binders.

## 5. Non-functional requirements

- **NFR-1 — Proof-gated, no impersonation.** Every link/merge requires fresh proof of the linked identity; the survivor is always the verified session's user, never a body-supplied id. Optional step-up MFA (`requires_recent_mfa`) on merge.
- **NFR-2 — Tenant isolation preserved.** A merge only ever moves data **between the two named accounts**; no third account's rows are read or written. Cross-org steps run under an explicit privileged context (BYPASSRLS / per-statement org GUC), never a broad table scan. Verified by an isolation test that seeds a third account and asserts it is untouched.
- **NFR-3 — Atomicity / consistency.** Each store's re-key is transactional. The cross-service merge is a **saga** with a recorded state machine (`account_merges` row: `pending → backend_done → runtime_done → sessions_revoked → completed`, with `failed`); on partial failure it is **resumable/idempotent**, never leaving data half-owned. No step is destructive before its target is confirmed written.
- **NFR-4 — Encryption correctness.** Every encrypted column bound to `org_id` in its AAD is decrypt-with-old-org / re-encrypt-with-new-org during re-key; a post-merge decrypt smoke on migrated encrypted rows is part of the test gate. No ciphertext is left bound to a stale org.
- **NFR-5 — Audit: immutable, complete, exportable.** `identity.linked`, `identity.unlinked`, and `account_merged` are appended to the append-only trail with actor, absorbed→survivor ids, proof reference, and the per-store row counts moved. Audit hash-chains are **appended to, never rewritten**. Closes the pre-existing gap: `identity_audit_events` gains a DB-level immutability guard.
- **NFR-6 — Reversibility (support-grade).** The soft-disabled absorbed stub + `absorbed_into_user_id` + the immutable trail are the reversal record. No user-facing undo; product data consolidation is irreversible by design.
- **NFR-7 — Performance.** Link is a synchronous sub-second op. Merge is an **async job** (it can move large data); the UI shows progress and the caller's new bearer is returned immediately after FR-M5. In-flight runs are quiesced, not killed.
- **NFR-8 — Retry-safe idempotency.** Re-invoking a merge for an already-merged/absorbed identity is a no-op that returns the survivor; a resumed saga skips completed steps by state.
- **NFR-9 — Substrate + boundary rules.** No `apps/*→apps/*` imports; chat-surface stays substrate-agnostic (ports only); backend never imports ai-backend (the re-key is HTTP-coordinated); both host binders updated together for any shared contract change.
- **NFR-10 — Observability.** The merge job emits structured progress + per-store counts; failures are actionable (which store, which step, which collision) — never a silent partial merge.

## 6. Architecture

### 6.1 Account model & data changes

- `oidc_authentications`: add nullable `link_user_id`, `link_org_id` (bind an OAuth flow to the current user at authenticated start). Migration `00NN_oidc_link.sql`.
- `users`: add `absorbed_into_user_id TEXT NULL`, `merged_at TIMESTAMPTZ NULL` (merge lineage).
- New `account_merges` table: `(merge_id, survivor_org, survivor_user, absorbed_org, absorbed_user, state, proof_ref, started_at, completed_at, error)` — the saga record + audit anchor.
- New reads: `OidcStore.list_identities_by_user(org_id,user_id)`, `SiweStore.list_wallets_by_user(org_id,user_id)` (generalizes the existing first-linked lookup).

### 6.2 Linking flows

- **Wallet link** reuses `SiweService.verify`'s validation prefix verbatim; the `if not existing` provision branch is replaced with `create_wallet_identity(current_org, current_user)`; the `existing` branch splits into no-op / merge-trigger. Its own audit action (not a login attempt — no lockout pollution).
- **Google link** reuses provider config + PKCE token exchange + id_token verify unchanged; only the state-row binding (authenticated start) and the callback fork (`link_user_id` set → `_link_to_user` + email upgrade) are new. Callback stays public; link intent is recovered server-side from the consumed state row (no bearer needed at callback).
- **Facade**: new authenticated routes under `/v1/me/identities/*` mirroring the `_forward_me` + `verify_with_touch(cache_bypass=True)` pattern; backend routes under `/internal/v1/me/identities/*` with `RequireScopes(RUNTIME_USE)` + `internal_scoped_identity`.

### 6.3 Merge protocol (the saga)

1. **Preconditions** — both are personal orgs (single active member); else refuse (non-goal). Record `account_merges` row `pending`.
2. **Quiesce** (FR-M6) — block new runs for the absorbed account; drain pending outbox + waiting-for-approval runs.
3. **Backend re-key** (FR-M3) — under BYPASSRLS: move identities → survivor; re-key every `(org_id,user_id)` table with encryption re-wrap + collision rules; state → `backend_done`.
4. **ai-backend re-key** (FR-M4) — backend calls the new ai-backend merge endpoint (service-token auth) with `{absorbed, survivor}`; ai-backend re-keys its tenant tables under BYPASSRLS with encryption re-wrap, appends a merge marker to its audit chain (never rewrites it), returns per-store counts; state → `runtime_done`.
5. **Sessions** (FR-M5) — mint survivor bearer (if caller was on absorbed) → return → `revoke_all_by_user(absorbed)`; state → `sessions_revoked`.
6. **Disposal + audit** (FR-M7) — soft-disable absorbed user, set lineage, write immutable `account_merged` rows both sides; state → `completed`.

- **Failure/resume**: each step is idempotent and keyed by `account_merges.state`; a resumed job skips completed steps. Nothing is soft-disabled or session-revoked until re-key is confirmed.

### 6.4 Cross-service contract (new)

`POST /internal/v1/admin/account-merge` on ai-backend (service-token + explicit `absorbed`/`survivor` in body; not tenant-scoped — runs privileged). Request/response typed in `packages/api-types` (or service-contracts). Documented in a service-boundary note (per repo rule for new cross-service surfaces).

## 7. Security analysis

| Vector                                          | Mitigation                                                                                                                                                                                                                    |
| ----------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Link-jacking (attach my wallet to your account) | Survivor is always the caller's verified session; linking requires proof of the _linked_ identity, and the linked identity binds to the _caller_, so an attacker can only ever link things they control to their own account. |
| CSRF on the Google link flow                    | `state` remains the unguessable single-use CSRF token; the link binding is server-side on the state row, never in the browser round-trip; `email_verified` required.                                                          |
| SIWE replay / cross-origin                      | Nonce single-use compare-and-set + origin/domain binding unchanged; link uses a distinct audit action so failed links can't lock out login.                                                                                   |
| Cross-tenant leakage during merge               | NFR-2: only the two named accounts are touched; privileged context is explicit and scoped; isolation test with a decoy third account.                                                                                         |
| Incomplete merge → orphaned/leaked data         | Saga state machine + idempotent resume (NFR-3/8); no destructive step before re-key confirmed; per-store counts reconciled.                                                                                                   |
| Audit tampering / broken chains                 | Append-only; hash-chains appended not rewritten (NFR-5); `identity_audit_events` immutability guard added.                                                                                                                    |
| Merge of a shared org                           | Refused in preconditions (non-goal) — never silently absorbs other members' data.                                                                                                                                             |

## 8. Test plan

- **Linking (unit+integration):** wallet link (mine=no-op / new / conflict→merge), Google link + email upgrade (verified required, collision-guarded), unlink last-method refusal, idempotent re-link.
- **Merge correctness:** every `(org_id,user_id)` store re-keyed (row-count reconciliation), encrypted-column decrypt smoke post-merge, PK/unique-collision cases (dup provider key, same-day usage, memory scope, workspace_defaults), audit markers appended both sides.
- **Tenant isolation:** decoy third account seeded → asserted untouched after a merge.
- **Sessions:** absorbed sessions revoked; survivor bearer valid; caller-on-absorbed re-mint ordering (no stranded request).
- **Saga:** injected failure at each step → resume is idempotent, no half-owned data, no double-disable.
- **Deletion cascade / retention:** absorbed soft-disable does not strand its re-pointed identities (guards the `siwe._link_or_provision` "deleted user" fatal path).
- **Live-stack gate:** because merge moves real data across tenants under RLS + encryption, a **live embedded-Postgres integration run** (desktop stack) is required before it is considered production-safe — unit tests alone are insufficient for the migration correctness (encryption AAD, RLS context).

## 9. Delivery sequence (PRs)

1. **Spec** — this PRD (+ service-boundary note for the new ai-backend merge surface).
2. **Contract + reads** — `list_identities_by_user` (OIDC) + `list_wallets_by_user` (SIWE); profile payload → identities list; api-types; `oidc_authentications` link-columns + `users` lineage + `account_merges` migrations.
3. **Link wallet** — authenticated SIWE link endpoint (no-conflict path) + tests.
4. **Link Google + email upgrade** — authenticated OAuth start + callback fork + upgrade + tests.
5. **Linked-accounts UI** — replace #102 XOR anchor (desktop + web) + unlink guard.
6. **Merge engine** — the saga: backend re-key + ai-backend merge endpoint + sessions + audit + conflict rules; wire the link endpoints' conflict branch to it; merge-confirm UI. Gated on the live-stack integration run (§8).

## 10. Open risks

- **Encryption re-wrap** is the highest-risk mechanic (AAD bound to org_id); needs the live decrypt-smoke gate.
- **RLS privileged context** — the merge write path must run as `enterprise_admin`/BYPASSRLS or per-statement GUC; get this wrong and the merge either can't see the absorbed rows or leaks.
- **Cross-service saga** partial-failure handling — the state machine must be genuinely idempotent; a live failure-injection test is required.
- **Pre-existing audit immutability gap** on `identity_audit_events` (no DB trigger) — closed as part of NFR-5.
- **Scope realism** — steps 2–5 are well-bounded and shippable; step 6 (merge) is a large, integration-heavy build and is the one that needs the live-stack gate before production.

## 11. Implementation deviations & deferrals (post-ship amendment)

Recorded after the adversarial FR/NFR verification of PR #125 + the
hardening PR, so the paper trail matches what shipped:

- **Google merge never executes from the public callback (SECURITY).** The
  callback completer is unauthenticated, so an attacker-initiated link URL
  must not let a victim's Google sign-in absorb the victim's account
  (confused deputy). `link_confirm_merge` is still recorded on the state row
  for a future AUTHENTICATED completion endpoint; until then Google
  conflicts return `merge_required` and the merge runs through the
  authenticated wallet-link path (`confirm_merge` on
  `POST /v1/me/identities/wallet`), which requires the caller's own bearer.
  The plain (non-merge) Google link from the callback carries a residual
  attacker-initiated-URL risk on SHARED deployments — accepted for the
  local-first desktop (each user runs their own backend, foreign state
  tokens don't resolve) and tracked for the authenticated-complete redesign.
- **FR-M5 re-mint clause**: every shipped entry point makes the CALLER the
  survivor (FR-M2), so the "caller on an absorbed identity" re-mint branch
  is unreachable and unimplemented. Any future admin-initiated merge path
  must implement it first.
- **FR-M6 quiesce is deferred**: the saga does not yet block new runs or
  drain waiting-for-approval/outbox work; the ai-backend re-keyers re-key
  pending rows and surface a `queue_not_drained` warning instead.
- **FR-L5 "soft-unlink"**: OIDC identities soft-unlink (`unlinked_at`);
  wallet unlink is a HARD delete by design — `wallet_identities.address` is
  deployment-wide UNIQUE with no unlink column, so a soft row would block
  the wallet from ever re-linking. Both unlinks append immutable
  `identity.*_unlinked` audit rows. The guard→delete sequence is not
  transactional (TOCTOU) — accepted for the single-user desktop.
- **NFR-7 async job**: the merge runs synchronously per-request (runtime leg
  ≤60s HTTP budget), resumable at checkpoints; the async-job + progress UI
  shape is deferred with the client work below.
- **Client surfaces pending**: the merge-confirm dialog, unlink buttons,
  wallet-link CTA, and a proper browser landing page for the Google
  callback are not yet wired in the apps; the API contracts (409
  `merge_required`, `confirm_merge`, `DELETE /v1/me/identities/*`) are live
  and tested.
- **NFR-4 decrypt smoke** runs with the live embedded-Postgres gate (§8),
  which remains the production sign-off for both Postgres re-key executors.
