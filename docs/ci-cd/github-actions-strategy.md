# GitHub Actions Strategy

## CI/CD In Plain English

CI checks every pull request before merge. CD deploys approved code after it lands on `main`.

For this monorepo, CI/CD should be path-aware: a change to `services/ai-backend` should not force Mac or Windows builds unless shared contracts changed.

## Pull Request CI

Run on every pull request:

- Install dependencies for changed components.
- Lint changed components.
- Typecheck changed components.
- Run unit tests for changed components.
- Build changed apps/services.
- Validate Docker builds for changed backend services.

## Deployment CD

After merge to `main`:

- Build production Docker images for changed backend services.
- Push images to GitHub Container Registry.
- Deploy staging automatically.
- Deploy production only through GitHub Environments with manual approval.

## Docker Images

Each deployable has its own image published by `release-images.yml`:

- `ghcr.io/<org>/enterprise-search-backend`
- `ghcr.io/<org>/enterprise-search-backend-facade`
- `ghcr.io/<org>/enterprise-search-ai-backend`
- `ghcr.io/<org>/enterprise-search-frontend`

Dockerfiles should be reproducible, minimal, and scoped to their service. Do not bake secrets into images.

## Secrets

- Store secrets in GitHub Actions secrets or environment-level secrets.
- Never commit `.env` files with real credentials.
- CI should fail fast if required secrets are missing for deploy jobs.
- Pull request CI should not require production secrets.

## Desktop Builds

Desktop build pipelines should come later:

- Mac: macOS GitHub runners, or a dedicated Mac build service if signing/notarization requires it.
- Windows: Windows GitHub runner for native/Electron/Tauri packaging.

Do not block backend CI on desktop packaging unless the PR touches desktop app code or shared packages required by desktop apps.

## Workflow layout (implemented)

- `.github/workflows/ci-repo.yml` — repo-wide lint + secret scan.
- `.github/workflows/ci-backend.yml`, `ci-backend-facade.yml`, `ci-ai-backend.yml`, `ci-frontend.yml` — path-filtered component CI.
- `.github/workflows/release-images.yml` — GHCR publish on `main` (filtered paths).
- `.github/workflows/deploy-staging.yml`, `deploy-production.yml` — manual environment gates (wire orchestrator per tenant).

## Assurance specification

For the consolidated description of CI/CD controls (locking, SBOM, Trivy, attestations, manifests), see **[`ci-assurance-spec.md`](ci-assurance-spec.md)**.
