# Security And Compliance Control Mapping

This mapping is implementation evidence for regulated readiness. A control is
not complete unless the linked code, config, tests, and docs remain accurate.

| Area                                   | Implemented Evidence                                                                                               | SOC 2        | ISO 27001      | NIST CSF           | OWASP ASVS |
| -------------------------------------- | ------------------------------------------------------------------------------------------------------------------ | ------------ | -------------- | ------------------ | ---------- |
| Authenticated identity propagation     | `services/backend-facade/src/backend_facade/auth.py`, trusted service headers, backend scoped identity enforcement | CC6.1, CC6.6 | A.5.16, A.5.18 | PR.AA-01, PR.AA-03 | V2, V4     |
| Runtime authorization context lockdown | `services/ai-backend/src/runtime_api/auth.py`, `runtime_api/http/routes.py`, `runtime_api/schemas/runs.py`         | CC6.3        | A.5.15, A.8.3  | PR.AA-05           | V4         |
| Service-to-service internal API auth   | `services/backend/src/backend_app/auth.py`, `services/backend/docs/specs/internal-api.md`                          | CC6.6        | A.8.20         | PR.AC-5            | V4, V14    |
| Secret and token handling              | `services/backend/src/backend_app/token_vault.py`, `.env.example` files, `SECURITY.md`                             | CC6.1, CC6.8 | A.5.10, A.8.24 | PR.DS-01           | V6         |
| Audit durability                       | `runtime_audit_log` schema, `PostgresRuntimeApiStore.write_audit_log`                                              | CC7.2, CC7.3 | A.8.15, A.8.16 | DE.CM-01           | V7         |
| Retention, deletion, legal hold        | `/v1/agent/history`, `runtime_legal_holds`, `runtime_deletion_evidence`, tombstoned messages                       | CC6.8, CC8.1 | A.5.33, A.8.10 | PR.DS-03           | V1, V7     |
| Supply-chain gates                     | `.github/workflows/security-ci.yml`, `CODEOWNERS`, Docker non-root users, SBOM artifacts                           | CC8.1        | A.8.8, A.8.9   | ID.RA-01, PR.PS-06 | V14        |

## Open Deployment Evidence

The repository still depends on deployment evidence for TLS termination, WAF,
private networking, backup/restore verification, SIEM export plumbing, branch
protection, and KMS-backed rotation runbooks. Do not present those controls as
implemented for customers until the deployment repository or runbooks are linked
here.
