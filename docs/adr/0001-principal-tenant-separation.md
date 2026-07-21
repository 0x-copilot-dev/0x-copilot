# ADR 0001 — Separate principal (human) from tenant (workspace)

Status: ACCEPTED · 2026-07-21 · Owner: identity
Execution: staged expand → migrate → contract. **Stage 1 (Expand) delivered.**

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

**Stage 2 — MIGRATE (next).**

- Auth-identity tables (`wallet_identities`, `oidc_identities`, …) gain
  `principal_id`, backfilled from their user; writers set it.
- Sign-in resolution reads identity → principal → the principal's personal
  `(org, user)`. **"Sign-in becomes link":** when an authenticated session
  already exists, a new-identity sign-in attaches to that principal instead
  of provisioning — killing NEW fractures at the source.
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
