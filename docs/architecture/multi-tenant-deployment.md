# Multi-tenant deployment (architecture and status)

This document supersedes the exploratory checklist under `docs/plans/multi-tenant-deployment/` (removed after consolidation). It links **specifications**, **implemented controls**, and **planned work** for regulated multi-tenant deployments.

## Identity and trust chain

- Browser → **backend-facade** (HMAC bearer) → injects service headers → **ai-backend** / **backend**.
- Treat caller-supplied org/user in bodies/queries as untrusted unless overwritten by the facade or validated via service token + headers.

## Implemented artifacts

| Topic                                                             | Location                                                                                                                                                         |
| ----------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Tenant isolation tests (runtime API, MCP/skills, facade identity) | Tests under `services/*/tests/test_tenant_isolation*.py`                                                                                                         |
| Persistence org-scoping audit                                     | [services/ai-backend/docs/specs/11-persistence-org-scoping-audit.md](../../services/ai-backend/docs/specs/11-persistence-org-scoping-audit.md)                   |
| Worker queue command validation                                   | [services/ai-backend/docs/specs/13-runtime-worker-queue-tenant-validation.md](../../services/ai-backend/docs/specs/13-runtime-worker-queue-tenant-validation.md) |

## Planned / design-only tracks

These remain **deployment or future PR** work unless explicitly marked done above.

1. **Postgres Row-Level Security** — Shared-database SaaS: policies keyed on session `SET LOCAL` org claim; see plan rationale in historical tickets; not enabled in application DDL today.
2. **Tenant registry (`services/backend`)** — First-class org rows (active/suspended), onboarding hooks; owned by backend per service boundaries.
3. **Outbox atomicity** — Single-transaction run creation + enqueue where Postgres allows; reconciliation worker otherwise.
4. **Per-org rate limits** — Prefer choke point at facade + configurable quotas for expensive routes.
5. **Managed MCP token vault** — KMS-backed `ManagedSecretTokenVault` implementation for production.
6. **Audit / SIEM export** — Structured export path beyond append-only DB tables; ops-forwarded.
7. **HA / DR** — Runbooks, readiness probes, Postgres HA, stated RPO/RTO per tier.

## Related workspace rules

- [service-boundaries.md](service-boundaries.md)
- Compliance audit expectations in `.cursor/rules/compliance-audit-agent.mdc` when reviewing regulated buyers.
