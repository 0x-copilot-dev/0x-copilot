# Security Policy

## Supported Scope

Security reports for this repository should cover the product facade, backend,
AI runtime, frontend, shared API types, Dockerfiles, CI, and deployment docs.

## Reporting

Report suspected vulnerabilities to the security owner listed in `CODEOWNERS`.
Do not open public issues for secrets, authentication bypasses, tenant isolation
failures, prompt-injection paths, or data deletion failures.

## Required Evidence

Security fixes must include code/config evidence, tests when practical, and
documentation updates for any changed customer-facing control. Regulated-buyer
claims should map to `docs/security/control-mapping.md`.

## Secret Handling

Do not commit `.env` files or provider keys. Production deployments must source
`ENTERPRISE_AUTH_SECRET`, `ENTERPRISE_SERVICE_TOKEN`, and
`MCP_TOKEN_VAULT_SECRET` from managed secret storage. Local examples belong in
`.env.example` files only.
