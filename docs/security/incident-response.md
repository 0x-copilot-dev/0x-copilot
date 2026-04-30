# Incident Response Plan

## Severity

- Critical: active compromise, exposed production secrets, auth bypass, tenant
  data exposure, destructive data-loss path.
- High: exploitable privilege escalation, durable audit loss, unapproved
  connector access, CI supply-chain bypass.
- Medium: defense-in-depth gaps, missing evidence, limited data exposure.

## Process

1. Triage the report, preserve logs, and assign an incident owner.
2. Contain the issue by rotating affected secrets, disabling vulnerable routes,
   or blocking affected integrations.
3. Eradicate root cause with reviewed code/config changes and regression tests.
4. Recover service and verify audit, deletion, and tenant isolation behavior.
5. Produce a post-incident record with impact, timeline, controls changed, and
   customer/regulator notification decisions.

## Evidence

Incident records should reference runtime audit logs, CI run IDs, SBOM versions,
secret rotation evidence, affected tenants, and follow-up control mappings.
