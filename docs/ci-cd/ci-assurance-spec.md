# CI/CD assurance specification (implemented)

This document replaces the exploratory plans under `docs/plans/ci-cd-high-assurance/`. It describes **what is implemented in this repository** versus what remains **deployment/organization** responsibility.

## Goals

- **Path-filtered CI** so unrelated deployables do not share one runner’s pip/npm graph.
- **Reproducible Python installs** for `backend` and `backend-facade` via `pip-tools` hashed locks.
- **Immutable release artifacts** (container images) published to GHCR with digests recorded for audit.
- **Supply-chain signals**: build attestations (best-effort), Trivy SARIF upload (best-effort), CycloneDX SBOM fragments in CI.
- **Gated promotion placeholders** using GitHub Environments for staging/production.
- **Evidence artifact**: `deployment-manifest.json` linking git SHA and image digests.

## Workflows (authoritative list)

| Workflow                                                                                   | Purpose                                                                                                               |
| ------------------------------------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------- |
| [`.github/workflows/ci-repo.yml`](../../.github/workflows/ci-repo.yml)                     | Pre-commit + Gitleaks on every PR/`main` push.                                                                        |
| [`.github/workflows/ci-backend.yml`](../../.github/workflows/ci-backend.yml)               | Backend pytest, `pip-audit`, SBOM; isolated deps + lock verification.                                                 |
| [`.github/workflows/ci-backend-facade.yml`](../../.github/workflows/ci-backend-facade.yml) | Facade pytest, `pip-audit`, SBOM; isolated deps + lock verification.                                                  |
| [`.github/workflows/ci-ai-backend.yml`](../../.github/workflows/ci-ai-backend.yml)         | AI backend pytest, `pip-audit`, `pip check`, SBOM.                                                                    |
| [`.github/workflows/ci-frontend.yml`](../../.github/workflows/ci-frontend.yml)             | npm ci, api-types + design-system + frontend typecheck/build, npm audit, npm SBOM.                                    |
| [`.github/workflows/release-images.yml`](../../.github/workflows/release-images.yml)       | Build/push four images to `ghcr.io/<owner>/…`, Trivy SARIF, attestations (best-effort), deployment manifest artifact. |
| [`.github/workflows/deploy-staging.yml`](../../.github/workflows/deploy-staging.yml)       | Manual staging gate (`environment: staging`).                                                                         |
| [`.github/workflows/deploy-production.yml`](../../.github/workflows/deploy-production.yml) | Manual production gate (`environment: production`).                                                                   |

## Python locking model

- **`services/backend`** and **`services/backend-facade`**: edit `requirements.in`, run `pip-compile --generate-hashes --resolver=backtracking requirements.in -o requirements.txt`, commit both. CI fails if `requirements.txt` drifts.
- **`services/ai-backend`**: remains a single fully pinned `requirements.txt`; CI runs `pip check` after install.

Dockerfiles for backend and facade use `pip install -r requirements.txt --require-hashes`.

## Frontend image correctness

`apps/frontend/Dockerfile` copies `packages/design-system` so `npm ci` matches monorepo workspace layout.

## Security scanning posture

- **Dependency audits** remain in component CI (`pip-audit`, `npm audit`).
- **Trivy** scans images after push on `main` (CRITICAL/HIGH SARIF; upload may be a no-op without Advanced Security—step is `continue-on-error`).
- **Build attestations** use `actions/attest-build-provenance@v2` with `continue-on-error` for compatibility.

## Contract smoke test

`services/backend-facade/tests/test_public_route_contract.py` asserts core `/v1/*` paths exist in OpenAPI (no network).

## Operational docs

- [`runner-trust.md`](runner-trust.md) — OIDC, self-hosted runners, egress expectations.
- [`runbooks/ci-platform-resilience.md`](runbooks/ci-platform-resilience.md) — break-glass and CI outage narrative.

## Out of scope / customer-owned

- Actual Kubernetes/Helm deploy commands inside staging/production workflows.
- SIEM export of logs, legal hold on Actions logs, regional DR for GitHub—document processes externally.
- Five-nines **product** HA (runtime topology, multi-region database)—not CI.

## Branch protection reminder

Replace any legacy required check pointing at deleted `security-ci` with the new workflow/job names (`ci-repo`, `ci-backend`, etc.).
