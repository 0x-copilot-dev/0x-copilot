# CI/CD assurance specification

This document is the source of truth for CI/CD controls in this repository. Each control is classified as one of:

- **Gating** — failure of the control fails the pipeline.
- **Evidence-only** — the control produces a verifiable artifact but does not block the build.
- **Known gap** — the control is intended but not yet implemented; tracked here for transparency.

## Workflows

| Workflow                                                                                                                                 | Purpose                                                                                                                                                  | Evidence produced                                                                                                              |
| ---------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------ |
| [ci-repo.yml](../../.github/workflows/ci-repo.yml)                                                                                       | Pre-commit + Gitleaks on every PR/push to `main`; tenant-registry lint.                                                                                  | gitleaks output, pre-commit run log, tenants-lint result                                                                       |
| [ci-backend.yml](../../.github/workflows/ci-backend.yml)                                                                                 | Backend pytest, `pip-audit`, hash-locked `requirements.txt`, lock-drift check.                                                                           | `sbom-backend` artifact (CycloneDX)                                                                                            |
| [ci-backend-facade.yml](../../.github/workflows/ci-backend-facade.yml)                                                                   | Facade pytest, `pip-audit`, hash-locked `requirements.txt`, lock-drift check.                                                                            | `sbom-backend-facade` artifact                                                                                                 |
| [ci-ai-backend.yml](../../.github/workflows/ci-ai-backend.yml)                                                                           | AI backend pytest, `pip-audit`, `pip check`. **Hash-lock not yet adopted** (see Known gaps).                                                             | `sbom-ai-backend` artifact                                                                                                     |
| [ci-frontend.yml](../../.github/workflows/ci-frontend.yml)                                                                               | npm ci, typecheck (api-types + design-system + frontend), build, npm audit.                                                                              | `sbom-frontend-npm` artifact                                                                                                   |
| [release-images.yml](../../.github/workflows/release-images.yml)                                                                         | Build/push four images to GHCR (digest-pinned), build provenance attestation, cosign keyless signature, Trivy CRITICAL gate, Trivy HIGH triage SARIF.    | `release-image-*.image-ref.txt` per component, `deployment-manifest` artifact (v2 schema with cosign + SBOM refs), Trivy SARIF |
| [deploy.yml](../../.github/workflows/deploy.yml)                                                                                         | Parameterized per-tenant promotion: cosign verify, attestation verify, staged-rollout enforcement, OIDC federation, orchestrator invocation, audit POST. | `deployment-record-*` artifact per deploy                                                                                      |
| [deploy-staging.yml](../../.github/workflows/deploy-staging.yml), [deploy-production.yml](../../.github/workflows/deploy-production.yml) | Thin wrappers around `deploy.yml` for the two environments.                                                                                              | (delegated)                                                                                                                    |

## Controls

### Gating (failure blocks the build / promotion)

| Control                                                                                            | Where                                                          | Note                                                                                      |
| -------------------------------------------------------------------------------------------------- | -------------------------------------------------------------- | ----------------------------------------------------------------------------------------- |
| Pre-commit hooks (ruff, ruff-format, prettier)                                                     | `ci-repo.yml`                                                  |                                                                                           |
| Gitleaks secret scan                                                                               | `ci-repo.yml`                                                  |                                                                                           |
| Tenant-registry validation (`deploy/tenants.yml` against schema, no duplicates, prefix invariants) | `ci-repo.yml::tenants-lint`                                    | Validator: [deploy/scripts/validate_tenants.py](../../deploy/scripts/validate_tenants.py) |
| pytest per service                                                                                 | `ci-backend.yml`, `ci-backend-facade.yml`, `ci-ai-backend.yml` |                                                                                           |
| pip-audit per service                                                                              | all backend workflows                                          |                                                                                           |
| Hash-locked installs (`pip install --require-hashes`)                                              | `ci-backend.yml`, `ci-backend-facade.yml`                      | ai-backend pending, see Known gaps                                                        |
| Lock-drift check (`pip-compile` then `git diff --exit-code`)                                       | `ci-backend.yml`, `ci-backend-facade.yml`                      |                                                                                           |
| npm ci + typecheck + build                                                                         | `ci-frontend.yml`                                              |                                                                                           |
| Trivy CRITICAL findings                                                                            | `release-images.yml`                                           | `severity: CRITICAL`, `exit-code: 1`, `ignore-unfixed: true`                              |
| Build provenance attestation (`actions/attest-build-provenance`)                                   | `release-images.yml`                                           | `continue-on-error` removed; failure blocks the release                                   |
| Cosign keyless signing                                                                             | `release-images.yml`                                           | `cosign sign --yes` against the digest                                                    |
| Cosign verify (issuer + subject regex)                                                             | `deploy.yml`                                                   | All four images verified before federation                                                |
| GitHub attestation verify (`gh attestation verify`)                                                | `deploy.yml`                                                   | Asserts repo owner                                                                        |
| Tenant existence + environment match                                                               | `deploy.yml`                                                   | From `deploy/tenants.yml`                                                                 |
| Staged-rollout policy (canary → early → general)                                                   | `deploy.yml` (production only)                                 | `--force` available with second-approver requirement                                      |
| OIDC federation (no long-lived credentials)                                                        | `deploy.yml`                                                   | Default = `aws-actions/configure-aws-credentials`; documented swap-in for GCP/Azure       |
| Deploy audit event posted to backend `/internal/v1/audit/deploy`                                   | `deploy.yml`                                                   | Service-token-authenticated; tenant_id must equal verified org_id                         |

### Evidence-only (produces an artifact, does not block)

| Control                                                                                                  | Where                | Note                                                                       |
| -------------------------------------------------------------------------------------------------------- | -------------------- | -------------------------------------------------------------------------- |
| CycloneDX SBOM per service                                                                               | all CI workflows     | Uploaded as `sbom-*` artifacts; referenced by `deployment-manifest.json`   |
| Trivy HIGH findings                                                                                      | `release-images.yml` | Surfaced in SARIF for triage; does not block                               |
| Trivy SARIF upload to GitHub Security                                                                    | `release-images.yml` | `continue-on-error: true`; tolerates orgs without GitHub Advanced Security |
| Deployment manifest (v2: image digests, cosign metadata, attestation metadata, SBOM artifact references) | `release-images.yml` | Consumed by `deploy.yml`                                                   |
| Per-deploy enriched manifest (release SHA, tenant, approver, outcome)                                    | `deploy.yml`         | Uploaded as `deployment-record-*`                                          |

### Known gaps (intended but not yet implemented in this repo)

| Gap                                                                             | Why it matters                                                                                                                                                                                                                     | Owner               |
| ------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------- |
| `services/ai-backend` hash-locked requirements                                  | `--require-hashes` cannot be enabled until ai-backend adopts `requirements.in` + `pip-compile --generate-hashes`. Today the install is pinned but not hash-verified.                                                               | ai-runtime owners   |
| Postgres-backed deploy audit store                                              | `InMemoryDeployAuditStore` is sufficient for tests but not durable across backend restarts. The Postgres adapter must be added before the deploy-audit control can be claimed compliant.                                           | backend owners      |
| Audit query API for staged-rollout check                                        | `deploy.yml` derives prior deploys from prior `deploy.yml` workflow runs. A read-side `/internal/v1/audit/deploys?release_sha=…` endpoint would close the loop with the audit record, removing dependence on workflow-run lookups. | backend owners      |
| SIEM export of GitHub Actions logs                                              | Required for regulated buyers. Implementation lives outside the repo (cloud SIEM connector).                                                                                                                                       | platform / security |
| Legal hold on Actions logs                                                      | Same as SIEM export — outside-repo implementation; document in deploy runbook when wired.                                                                                                                                          | platform / security |
| Regional DR for GitHub                                                          | GitHub publishes its own SLAs; cross-region runner / registry mirror is deployment infra.                                                                                                                                          | platform / security |
| Cosign verify policy enforcement at runtime (e.g. Kyverno/admission controller) | `deploy.yml` verifies before promotion, but in-cluster admission policy would catch sideloaded workloads.                                                                                                                          | platform / security |

## Python locking model

- `services/backend`, `services/backend-facade`: edit `requirements.in`, run `pip-compile --generate-hashes --resolver=backtracking requirements.in -o requirements.txt`, commit both. CI fails if `requirements.txt` drifts (`git diff --exit-code` after `pip-compile`).
- `services/ai-backend`: today a single fully pinned `requirements.txt` (no hashes). CI runs `pip check` after install. **Tracked as a known gap** above.

Dockerfiles for `backend` and `backend-facade` use `pip install -r requirements.txt --require-hashes`.

## Frontend image correctness

`apps/frontend/Dockerfile` copies `packages/design-system` so `npm ci` matches monorepo workspace layout.

## Contract smoke test

[services/backend-facade/tests/test_public_route_contract.py](../../services/backend-facade/tests/test_public_route_contract.py) asserts core `/v1/*` paths exist in OpenAPI (no network).

## Multi-tenant deployment

See [multi-tenant-deploy.md](multi-tenant-deploy.md) for the full per-tenant promotion model: tenant registry schema, OIDC trust convention, staged-rollout policy, audit shape, onboarding/offboarding runbooks.

## Operational docs

- [runner-trust.md](runner-trust.md) — action pinning, OIDC, self-hosted runners, egress allowlist.
- [runbooks/ci-platform-resilience.md](runbooks/ci-platform-resilience.md) — partial-degradation matrix, break-glass, runner compromise.

## Branch protection

Required checks for `main` (enforced by [deploy/branch-protection.json](../../deploy/branch-protection.json) applied via [.github/workflows/apply-branch-protection.yml](../../.github/workflows/apply-branch-protection.yml)):

- `ci-repo / lint-and-secrets`
- `ci-repo / tenants-lint`
- `ci-backend / test-and-audit`
- `ci-backend-facade / test-and-audit`
- `ci-ai-backend / test-and-audit`
- `ci-frontend / build-and-audit`

Plus: required code-owner review, dismiss stale reviews on push, no force pushes, linear history, signed commits.
