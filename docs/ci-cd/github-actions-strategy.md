# GitHub Actions strategy

## Intent

Path-filtered CI per deployable + a reproducible release pipeline that publishes a single signed artifact set per commit, promoted to many tenants via [deploy.yml](../../.github/workflows/deploy.yml).

A change to `services/ai-backend` should retest only `services/ai-backend` and any package it imports (`packages/service-contracts`, `packages/api-types`) — not `services/backend` or the frontend.

## Workflows (implemented)

- [.github/workflows/ci-repo.yml](../../.github/workflows/ci-repo.yml) — repo-wide lint, secret scan, tenant-registry lint.
- [.github/workflows/ci-backend.yml](../../.github/workflows/ci-backend.yml), [ci-backend-facade.yml](../../.github/workflows/ci-backend-facade.yml), [ci-ai-backend.yml](../../.github/workflows/ci-ai-backend.yml), [ci-frontend.yml](../../.github/workflows/ci-frontend.yml) — path-filtered component CI.
- [.github/workflows/release-images.yml](../../.github/workflows/release-images.yml) — GHCR publish on `main`, with cosign signing and provenance attestation.
- [.github/workflows/deploy.yml](../../.github/workflows/deploy.yml) — parameterized per-tenant deploy.
- [.github/workflows/deploy-staging.yml](../../.github/workflows/deploy-staging.yml), [deploy-production.yml](../../.github/workflows/deploy-production.yml) — thin wrappers around `deploy.yml`.

## Image registry

Each deployable has its own image:

- `ghcr.io/<owner>/0x-copilot-backend`
- `ghcr.io/<owner>/0x-copilot-backend-facade`
- `ghcr.io/<owner>/0x-copilot-ai-backend`
- `ghcr.io/<owner>/0x-copilot-frontend`

(The lowercase repository owner is computed at build time; matches `release-images.yml`.)

## Secrets

- Pull-request CI does not require production secrets.
- Image publish secrets (`GITHUB_TOKEN` with `packages: write`) are scoped to the `build-and-push` job in `release-images.yml`.
- Deploy workflows use **OIDC federation** (no long-lived credentials) into the tenant cloud account. See [runner-trust.md](runner-trust.md) and [multi-tenant-deploy.md](multi-tenant-deploy.md).
- Per-tenant secrets live in the GitHub Environment `tenant-<id>-<env>`. Adding a tenant requires creating those Environments and configuring the cloud IdP trust binding.

## Authoritative spec

All control details (gating vs. evidence-only, attestation, cosign, SBOM, Trivy gating, lock-drift, known gaps) live in [ci-assurance-spec.md](ci-assurance-spec.md). This document only states intent; the spec is the source of truth.
