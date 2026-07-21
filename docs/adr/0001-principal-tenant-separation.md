# ADR 0001 — Separate principal (human) from tenant (workspace)

Status: ACCEPTED · 2026-07-21 · Owner: identity
Execution: staged expand → migrate → contract. **Stages 1 + 2a (Expand) delivered.**

## Context

Today `account == (org_id, user_id)` and every self-signup identity
provisions its own personal org (`provision_personal_org`). Identity and
tenancy are the same row. Consequences we have paid for already:

- A human who signs in with Google and with a wallet becomes TWO accounts.
- The account-merge engine (PRD account-linking §6, PRs #125/#127) exists to
  repair exactly this — a saga, a cross-service HTTP re-key, encryption-AAD
  re-wrap, a schema-parsing registry guard. Excellent machinery; structural
  band-aid.
- Every future auth method multiplies the fracture surface.

## Decision (proposed)

Introduce a **principal**: one row per human. Auth identities
(wallet/OIDC/SAML/local) attach to the principal, N:1. Workspace access
becomes membership edges (principal ↔ org), M:N-capable. Sign-in resolves
identity → principal → workspace picker; a NEW identity for a signed-in
principal is a LINK, never a provision.

## Consequences

- Linking becomes an insert. Merge degrades to the legacy-repair tool for
  pre-migration fractures (keep the engine; stop needing it).
- The single-user desktop keeps its one-principal/one-org shape untouched.
- Product data stays keyed (org_id, user_id) — no mass re-key; user_id
  becomes a per-workspace projection of the principal.

## Execution — expand / migrate / contract (staged, reversible)

Parallel-change so every stage ships independently and no stage breaks a
read path until its writer has been dual-writing long enough to be trusted.

**Stage 1 — EXPAND (DELIVERED, migration 0039).**

- `principals` table (one row per human; no `org_id` — a principal is above
  tenancy, so it is outside RLS and the account-merge registry).
- `users.principal_id` (nullable), backfilled 1:1 as `prn_<user_id>`;
  absorbed users' principals carry `absorbed_into_principal_id` → the
  survivor's principal, mirroring the user-level merge lineage.
- The store DUAL-WRITES: `create_user` auto-mints `prn_<user_id>` when a
  caller supplies none, so from here on no new user lands without a
  principal. Deterministic id ⇒ app writes and the backfill agree.
- NO read path changed. Bearer/session/facade/ai-backend still carry only
  `(org_id, user_id)`. Fully contained in `services/backend` identity.
- Covered by the live-Postgres gate (backfill SQL + Postgres auto-mint).

**Stage 2a — EXPAND the edges (DELIVERED, migration 0040).**

- The three durable auth-identity EDGES — `wallet_identities`,
  `oidc_identities`, `saml_identities` — gain a nullable `principal_id`,
  backfilled 1:1 from the owning user, plus the index Stage 2b will resolve
  on. The stores DUAL-WRITE it (`with_default_principal`): a new edge fills
  `principal_id` from its user unless a caller supplies one, so nothing lands
  NULL going forward. Still no read path — inert like Stage 1.
- Deliberately NOT touched: `oidc_authentications` / `saml_authentications`
  are transient flow-state rows with no stable user binding for a plain
  sign-in (only the link flow sets `link_user_id`); their principal binding
  is a Stage 2b concern.
- Covered by the live-Postgres gate (edge INSERT round-trip + 0040 backfill).

**Stage 2b — CONTRACT: resolve via principal + "sign-in becomes link" (next, BEHAVIOR CHANGE — held for review).**

- Sign-in resolution reads identity → principal → the principal's personal
  `(org, user)`.
- **"Sign-in becomes link":** the public sign-in ramps (SIWE verify / OIDC
  callback) must NOT read the caller's session — doing so is the #127
  confused-deputy hole. So a signed-in user re-authenticating a new method is
  routed by the CLIENT through the authenticated `/me/identities/*` link path
  (which binds to the caller and is merge-aware), reusing the link machinery
  shipped in #143. Add the regression test proving a public ramp cannot
  absorb a caller's account.
- Merge, when it runs, reconciles principals (`absorbed_into_principal_id`).

**Stage 3 — CONTRACT (later, only if multi-workspace is pursued).**

- `users` becomes the `(principal × org)` membership projection; the direct
  identity→user coupling is dropped. Not required for the single-user
  desktop, which stays one-principal / one-org / one-user throughout.
- Legacy pre-migration fractures: offer the existing merge engine.

## Alternatives considered

- Keep per-identity accounts + merge forever: rejected — every merge is a
  privileged cross-tenant data migration; the steady state should not
  require one.
- Global-unique email as the join key: rejected — wallets have no email;
  email is deliberately not globally unique here.

## Prerequisites

The live merge gate (`make test-merge-live`) stays green throughout; the
data-lifecycle registry generalizes to the backfill verification.
