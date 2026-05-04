# Roadmap: Database, Identity, Token Usage, and Deployment Hardening

This folder contains the engineer-shareable specifications for a 30-PR roadmap that hardens the product for bank/government and SaaS deployments. Files are numbered in **recommended merge order**.

## How to use this folder

- Start with [00-overview.md](00-overview.md) for the big picture, architecture decisions, and dependency graph.
- Each numbered file `NN-<spec-id>-<topic>.md` is a self-contained PR brief: **Functional Spec + Technical Spec + Requirements & Acceptance Criteria + Critical Files**.
- Engineers should pick up one file at a time and write a more detailed implementation spec MD inside the relevant `services/<svc>/docs/specs/` directory before writing code (per the spec-first convention in [services/ai-backend/CLAUDE.md](../../services/ai-backend/CLAUDE.md)).
- Each PR file lists `Depends on` and `Required for` to make ordering explicit.

## Track summary

- **Track A — Identity & Access** (10 PRs): user/org schema, sessions, OIDC, local password, SAML, MFA, SCIM, lockout, frontend login, RBAC.
- **Track B — Token Usage, Metering, Budgets** (8 PRs): per-run usage, per-call usage, pricing, rollups, /context, /usage, budgets, per-tool budget.
- **Track C — Deployment Models & DB Hardening** (12 PRs): deployment profiles, migration tooling, atomicity fixes, pool tuning, RLS, KMS BYOK, field encryption, retention sweeper, SIEM export, read replicas, statement observability, backup/restore.

## Merge order (waves)

| Wave                   | PRs                                                                                                                                                                                                                                                                                                          | Purpose                                                                   |
| ---------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------- |
| 0 — Foundation         | [01-C1](01-c1-deployment-profiles.md), [02-C2](02-c2-migration-tooling.md)                                                                                                                                                                                                                                   | Deployment profile + migration tooling. **Must land first.**              |
| 1 — Atomicity          | [03-C3](03-c3-atomicity-fixes.md), [04-C4](04-c4-pool-tuning.md)                                                                                                                                                                                                                                             | Fix DB anti-patterns; tune pools.                                         |
| 2 — Auth Foundation    | [05-A1](05-a1-user-org-schema.md), [06-A2](06-a2-sessions.md)                                                                                                                                                                                                                                                | User/org schema; server-issued sessions.                                  |
| 3 — Parallel           | [07-A3](07-a3-oidc-sso.md), [08-A4](08-a4-local-password.md), [09-A5](09-a5-saml-sso.md), [10-A7](10-a7-scim.md), [11-B1](11-b1-runtime-run-usage.md), [12-B2](12-b2-per-step-usage.md), [13-B3](13-b3-pricing-and-cost.md), [14-B4](14-b4-aggregation-endpoints.md), [15-C5](15-c5-rls-tenant-isolation.md) | Auth IdPs in parallel; usage backend; RLS defense-in-depth.               |
| 4 — Auth Completion    | [16-A6](16-a6-mfa.md), [17-A8](17-a8-lockout.md), [18-A9](18-a9-frontend-login.md)                                                                                                                                                                                                                           | MFA, lockout, frontend login UX.                                          |
| 5 — Usage UX + Budgets | [19-B5](19-b5-context-command.md), [20-B6](20-b6-usage-command.md), [21-B7](21-b7-budgets.md), [22-B8](22-b8-tool-budget.md)                                                                                                                                                                                 | /context, /usage, budgets, per-tool budget.                               |
| 6 — Security Hardening | [23-C6](23-c6-byok-kms.md), [24-C7](24-c7-field-encryption.md)                                                                                                                                                                                                                                               | KMS BYOK; field-level encryption.                                         |
| 7 — Operations         | [25-C8](25-c8-retention-sweeper.md), [26-C9](26-c9-siem-export.md), [27-C10](27-c10-read-replica.md), [28-C11](28-c11-statement-observability.md)                                                                                                                                                            | Retention sweeper, SIEM export, replica routing, statement observability. |
| 8 — RBAC + Restore     | [29-A10](29-a10-rbac-enforcement.md), [30-C12](30-c12-backup-restore.md)                                                                                                                                                                                                                                     | Default-deny RBAC at every route; tested restore drill.                   |

## Required by deploy profile

- **Any production deploy:** A1, A2, B1–B4, C1–C5.
- **Bank/gov deploy adds:** A5/A7/A6, C6/C7, C8 (retention), C9 (SIEM), C12 (restore).
- **Operational maturity adds:** A8, A9, A10, B5–B8, C10, C11.

## File index (alphabetical by spec ID)

| Spec ID | File                                                                   | Title                                                      |
| ------- | ---------------------------------------------------------------------- | ---------------------------------------------------------- |
| A1      | [05-a1-user-org-schema.md](05-a1-user-org-schema.md)                   | User/Org/Role Schema Foundation                            |
| A2      | [06-a2-sessions.md](06-a2-sessions.md)                                 | Server-issued Sessions and Bearer-token Binding            |
| A3      | [07-a3-oidc-sso.md](07-a3-oidc-sso.md)                                 | OIDC SSO (Google + Generic)                                |
| A4      | [08-a4-local-password.md](08-a4-local-password.md)                     | Local Password Authentication and Bootstrap Admin          |
| A5      | [09-a5-saml-sso.md](09-a5-saml-sso.md)                                 | SAML 2.0 SSO                                               |
| A6      | [16-a6-mfa.md](16-a6-mfa.md)                                           | MFA (TOTP + WebAuthn)                                      |
| A7      | [10-a7-scim.md](10-a7-scim.md)                                         | SCIM 2.0 User/Group Provisioning                           |
| A8      | [17-a8-lockout.md](17-a8-lockout.md)                                   | Login Attempts Audit, Rate Limiting, Account Lockout       |
| A9      | [18-a9-frontend-login.md](18-a9-frontend-login.md)                     | Frontend Login Page, Auth Context, MFA Prompts             |
| A10     | [29-a10-rbac-enforcement.md](29-a10-rbac-enforcement.md)               | RBAC Enforcement at Every Route                            |
| B1      | [11-b1-runtime-run-usage.md](11-b1-runtime-run-usage.md)               | Denormalized Run Usage Table                               |
| B2      | [12-b2-per-step-usage.md](12-b2-per-step-usage.md)                     | Per-step Usage Events and Per-LLM-call Usage Table         |
| B3      | [13-b3-pricing-and-cost.md](13-b3-pricing-and-cost.md)                 | Pricing Catalog and Cost Calculation                       |
| B4      | [14-b4-aggregation-endpoints.md](14-b4-aggregation-endpoints.md)       | Daily Rollups and /v1/usage/\* Read Endpoints              |
| B5      | [19-b5-context-command.md](19-b5-context-command.md)                   | /context Slash Command                                     |
| B6      | [20-b6-usage-command.md](20-b6-usage-command.md)                       | /usage Slash Command and Panel                             |
| B7      | [21-b7-budgets.md](21-b7-budgets.md)                                   | Per-org and Per-user Budget Enforcement                    |
| B8      | [22-b8-tool-budget.md](22-b8-tool-budget.md)                           | Code-Enforced Per-tool Token Budget                        |
| C1      | [01-c1-deployment-profiles.md](01-c1-deployment-profiles.md)           | ENTERPRISE_DEPLOYMENT_PROFILE Config                       |
| C2      | [02-c2-migration-tooling.md](02-c2-migration-tooling.md)               | Adopt yoyo-migrations                                      |
| C3      | [03-c3-atomicity-fixes.md](03-c3-atomicity-fixes.md)                   | Atomic Upserts, Transaction Boundaries, Optimistic Locking |
| C4      | [04-c4-pool-tuning.md](04-c4-pool-tuning.md)                           | Connection Pool Tuning, Timeouts, Pool Metrics             |
| C5      | [15-c5-rls-tenant-isolation.md](15-c5-rls-tenant-isolation.md)         | Postgres Row-Level Security                                |
| C6      | [23-c6-byok-kms.md](23-c6-byok-kms.md)                                 | Managed Token Vault — KMS Adapter (AWS KMS)                |
| C7      | [24-c7-field-encryption.md](24-c7-field-encryption.md)                 | Field-level Encryption for PII                             |
| C8      | [25-c8-retention-sweeper.md](25-c8-retention-sweeper.md)               | Retention Sweeper + Checkpoint Pruning                     |
| C9      | [26-c9-siem-export.md](26-c9-siem-export.md)                           | SIEM Export Pump                                           |
| C10     | [27-c10-read-replica.md](27-c10-read-replica.md)                       | Read-replica Routing for Analytics                         |
| C11     | [28-c11-statement-observability.md](28-c11-statement-observability.md) | pg_stat_statements + Slow Query Metrics                    |
| C12     | [30-c12-backup-restore.md](30-c12-backup-restore.md)                   | Backup/Restore Documented and Tested                       |

## Spec template

Every PR file follows the same structure:

1. **Header:** Spec ID, Track, Wave, Estimated effort, Depends on, Required for.
2. **Functional Specification:** Goal, User-visible behavior, Out of scope.
3. **Technical Specification:** Architecture, Schema changes (DDL), Endpoints, Code changes (with file paths + line refs), Trust model & failure semantics, Tenant isolation, Observability.
4. **Requirements & Acceptance Criteria:** Functional acceptance criteria, Test plan (unit + integration + tenant-isolation + perf), Compliance evidence, Rollout plan, Backout plan, Definition of done.
5. **Critical files:** every file the PR touches, with absolute repo-relative paths.

## Engineering conventions

- **Spec-first:** before code, the assigned engineer should write a more detailed implementation spec MD under `services/<svc>/docs/specs/<topic>/` (referenced from the corresponding roadmap file).
- **One PR per file** unless the file explicitly says "combines small fixes" (only C3 does).
- **Migrations** land via yoyo (after C2 ships); each PR adds a numbered `.sql` + `.rollback.sql` + `MANIFEST.lock` entry.
- **Tenant scoping:** every new table has `org_id NOT NULL`; every compound index leads with `org_id`. C5 RLS is the defense-in-depth backstop.
- **Cost in micro-USD integers** everywhere (B3 onwards). No floats on the persistence path.
- **Hard service boundaries:** no Python imports across `services/*` or `apps/*`. Cross-component contracts via HTTP, [packages/api-types](../../packages/api-types), or constants-only [packages/service-contracts](../../packages/service-contracts).

## When in doubt

- See [00-overview.md § Architecture decisions](00-overview.md#architecture-decisions-apply-to-all-prs) for the cross-PR principles.
- See the [project CLAUDE.md](../../CLAUDE.md) for repo-wide engineering rules.
- See the per-track READMEs (one inside each `services/<svc>/docs/specs/`) once the assigned engineer has populated them.
