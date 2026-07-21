# ADR 0001 — Separate principal (human) from tenant (workspace)

Status: PROPOSED · 2026-07-21 · Owner: identity

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

## Migration sketch (staged, reversible)

1. `principals` table + `identities.principal_id` (backfill: one principal
   per existing user; identities re-point).
2. Sign-in ramps resolve via principal; "sign-in becomes link" when a live
   session exists (kills new fractures immediately — shippable before the
   rest).
3. Workspace picker reads memberships via principal.
4. Legacy fractured accounts: offer the existing merge engine.

## Alternatives considered

- Keep per-identity accounts + merge forever: rejected — every merge is a
  privileged cross-tenant data migration; the steady state should not
  require one.
- Global-unique email as the join key: rejected — wallets have no email;
  email is deliberately not globally unique here.

## Prerequisites

The live merge gate (`make test-merge-live`) stays green throughout; the
data-lifecycle registry generalizes to the backfill verification.
