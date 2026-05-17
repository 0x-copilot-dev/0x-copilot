# PRD: MFA API surface boundary

**Status:** Implemented (closed)
**Owner:** Frontend platform
**Related audit:** [05-dry-audit.md §2](../05-dry-audit.md)

## Background

The frontend has three modules that touch MFA. The audit flagged the
overlap between `mfaApi.ts` and `authApi.ts` because both used to
export `listMfaFactors`, `enrollTotp`, `confirmTotp`, etc., with
different payload shapes — a guarantee that a future caller would pick
the wrong one.

This PRD locks in the boundary that has since been adopted and removes
ambiguity for future contributors.

## The boundary

| Module                                                       | Endpoint prefix            | Scope                        | When to use                                                                                                                                                                |
| ------------------------------------------------------------ | -------------------------- | ---------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [`api/authApi.ts`](../../src/api/authApi.ts)                 | `/v1/auth/mfa/*`           | Pre-session login dance      | The login screen has detected MFA is required; it issues a challenge then verifies a code or recovery code. No bearer yet.                                                 |
| [`api/mfaApi.ts`](../../src/api/mfaApi.ts)                   | `/v1/me/mfa/*`             | Post-session factor CRUD     | The user is authenticated and is managing their factors in Settings → Profile → Sign-in & security (enroll TOTP, confirm code, register a WebAuthn key, disable a factor). |
| [`api/workspaceMfaApi.ts`](../../src/api/workspaceMfaApi.ts) | `/v1/workspace/mfa-policy` | Org-level enforcement policy | An admin is editing the workspace MFA policy (require-mfa toggle, grace-period, recovery-code policy).                                                                     |

## Invariants

1. **No factor CRUD lives in `authApi.ts`.** That surface is
   intentionally limited to: `issueMfaChallenge`, `verifyMfaChallenge`,
   `consumeRecoveryCode`. Anything that creates, lists, or deletes a
   factor belongs in `mfaApi.ts`.
2. **No policy CRUD lives in `mfaApi.ts`.** Per-user factor management
   only. Workspace policy is `workspaceMfaApi.ts`.
3. **Docstrings at the top of each module** cross-reference the other
   two so a new contributor lands in the right place even if they only
   read one file.

## How this PRD enforces itself

The cross-references are in the source. Reviewers should fail a PR
that:

- Adds a `list*Factor` / `enroll*` / `confirm*` / `disable*Factor`
  function to `authApi.ts`.
- Adds an `issue*Challenge` / `verify*Challenge` / `consume*Recovery`
  function to `mfaApi.ts`.
- Adds a `getPolicy` / `updatePolicy` function to either of the user-
  scoped modules.

## Out of scope

- Migrating the raw `fetch + assertOk` pattern in `mfaApi.ts` and
  `workspaceMfaApi.ts` to `getAppTransport().request()`. See
  [05-dry-audit.md §10](../05-dry-audit.md) and the upcoming transport
  migration PRD.
- Unifying error shape across the three surfaces (each already returns
  the FastAPI `{detail}` envelope, and `errorMessage` normalises them
  for the UI).
