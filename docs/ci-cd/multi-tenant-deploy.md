# Multi-tenant deployment

## Scope

This document defines how a single signed image set published by [release-images.yml](../../.github/workflows/release-images.yml) is promoted into multiple tenant environments by [deploy.yml](../../.github/workflows/deploy.yml). It covers the tenant registry, OIDC trust convention, deploy contract, audit event shape, staged-rollout policy, and onboarding/offboarding runbooks.

Runtime tenant isolation (org-scoping in the request path, persistence boundaries) is described in [docs/architecture/multi-tenant-deployment.md](../architecture/multi-tenant-deployment.md). This document is the deployment-side complement.

## Model

- One image build per release SHA. The same `ghcr.io/<owner>/<service>@<digest>` is deployed into every tenant's cluster — no per-tenant rebuilds.
- Per-tenant credentials. Each deploy job federates via OIDC into the tenant's cloud account using a tenant-scoped subject; no long-lived secrets in GitHub.
- Per-tenant orchestrator. Deploy logic is thin. `deploy.yml` resolves the orchestrator (Argo / Helm / Flux) from `deploy/tenants.yml`, then invokes it with the digest from `deployment-manifest.json`.
- Tenant registry in git. Adding a tenant is a reviewable PR to [deploy/tenants.yml](../../deploy/tenants.yml). The deploy workflow does not require YAML edits per tenant.

## Tenant registry

Schema: [deploy/tenants.schema.json](../../deploy/tenants.schema.json). Validator: [deploy/scripts/validate_tenants.py](../../deploy/scripts/validate_tenants.py), enforced by the `tenants-lint` job in [ci-repo.yml](../../.github/workflows/ci-repo.yml).

Required fields per tenant:

| Field                              | Purpose                                                                                                                                |
| ---------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------- |
| `id`                               | URL-safe identifier. Drives every other reference.                                                                                     |
| `display_name`                     | Human label for approvals and audit.                                                                                                   |
| `tier`                             | `canary` \| `early` \| `general`. Sets rollout cohort (see Staged rollout).                                                            |
| `regions`                          | Cloud regions hosting the tenant. Drives data-residency checks.                                                                        |
| `environments`                     | Subset of `[staging, production]`.                                                                                                     |
| `gh_environment_prefix`            | Always `tenant-<id>`. The validator enforces this. Combined with environment to form `tenant-<id>-staging` / `tenant-<id>-production`. |
| `oidc.audience`                    | Audience claim the tenant cloud IdP expects.                                                                                           |
| `oidc.expected_subject_pattern`    | Documented subject pattern; cloud IdP enforces it.                                                                                     |
| `orchestrator.type`                | `argo` \| `helm` \| `flux`.                                                                                                            |
| `orchestrator.endpoint_secret_ref` | Name of the GitHub Environment secret holding the orchestrator endpoint URL.                                                           |

Optional `data_residency.allowed_image_registries` lets a tenant restrict images to a specific GHCR mirror (for sovereign-cloud tenants).

## OIDC trust convention

For every tenant environment, the cloud IdP must trust GitHub's OIDC issuer (`https://token.actions.githubusercontent.com`) with a subject restricted to:

```
repo:<owner>/<repo>:environment:tenant-<tenant-id>-<env>
```

Examples:

```
repo:enterprise-search/enterprise-search:environment:tenant-acme-corp-staging
repo:enterprise-search/enterprise-search:environment:tenant-acme-corp-production
```

This means:

- A workflow run that does not run in the `tenant-acme-corp-production` GitHub Environment cannot mint tokens for the production tenant cloud account.
- Compromise of one tenant's cloud trust does not extend to other tenants.

Trust policies live in the tenant's cloud IdP (AWS IAM / GCP Workload Identity / Azure Federated Credentials). Do not commit real account IDs or role ARNs to this repo. The deploy workflow reads the role ARN at runtime from the per-environment secret `TENANT_<ID>_DEPLOY_ROLE_ARN`.

## Deploy workflow contract

Workflow file: [.github/workflows/deploy.yml](../../.github/workflows/deploy.yml).

Inputs:

| Input          | Required | Notes                                                                                        |
| -------------- | -------- | -------------------------------------------------------------------------------------------- |
| `tenant_id`    | yes      | Must exist in `deploy/tenants.yml`.                                                          |
| `environment`  | yes      | `staging` \| `production`. Must be in the tenant's `environments`.                           |
| `release_sha`  | no       | Defaults to latest green `main`.                                                             |
| `force_deploy` | no       | Default `false`. Required `true` to bypass staged-rollout policy; demands a second approver. |

Steps the workflow performs (in order):

1. Resolve the tenant from `deploy/tenants.yml`. Fail if unknown.
2. Select the GitHub Environment `tenant-<id>-<env>` (provides approvers, wait timers, environment-scoped secrets).
3. Download the `deployment-manifest.json` artifact for `release_sha`. Fail if missing or any image lacks attestation + cosign signature metadata.
4. Run `cosign verify` against each image digest, asserting OIDC issuer and `expected_subject_regex` from the manifest.
5. Run `gh attestation verify` to assert build provenance against the repo owner.
6. Apply staged-rollout policy (Production only — see below).
7. Federate via OIDC to the tenant's cloud account using its tenant-scoped subject.
8. Invoke the orchestrator with the digest. Argo: HTTP API call; Helm: `helm upgrade --install --set image.digest=<digest>`; Flux: image automation policy bump.
9. Poll rollout status until `Ready` or timeout.
10. POST a structured deploy event to backend's `/internal/v1/audit/deploy` (see Audit event shape).
11. Re-upload the manifest enriched with deployment metadata as a per-deploy artifact.

## Audit event shape

```
POST /internal/v1/audit/deploy
Headers:
  authorization: Bearer <ENTERPRISE_SERVICE_TOKEN>
  x-enterprise-org-id: <tenant-id>
  x-enterprise-user-id: ci:<approver-github-handle>
Body:
{
  "tenant_id": "acme-corp",
  "environment": "production",
  "release_sha": "abc123…",
  "image_digests": [
    {"component": "enterprise-search-backend", "digest": "sha256:…"},
    {"component": "enterprise-search-backend-facade", "digest": "sha256:…"},
    {"component": "enterprise-search-ai-backend", "digest": "sha256:…"},
    {"component": "enterprise-search-frontend", "digest": "sha256:…"}
  ],
  "approver": "alice",
  "workflow_run_url": "https://github.com/<owner>/<repo>/actions/runs/<id>",
  "started_at": "2026-05-03T18:00:00Z",
  "completed_at": "2026-05-03T18:04:12Z",
  "outcome": "success",
  "force_deploy": false
}
```

Backend persists the event into the existing audit-events store (see [services/backend/src/backend_app/store.py](../../services/backend/src/backend_app/store.py)) with `actor_kind="ci"` and tenant scoping. Reuse the same adapter — there is no parallel audit path.

## Staged-rollout policy

Production deploys follow tier order:

1. `canary` tenants first.
2. `early` tenants only after every `canary` tenant for the same `release_sha` has a `success` audit event at least `STAGE_GAP_HOURS` (default 4) old.
3. `general` tenants only after every `early` tenant for the same `release_sha` has a `success` audit event at least `STAGE_GAP_HOURS` old.

`force_deploy: true` bypasses the gap check but requires a second approver via the GitHub Environment's reviewer policy. The audit event records `force_deploy=true` for every bypass.

Staging deploys are unrestricted across tiers.

## Onboarding runbook (new tenant)

1. PR to add the tenant entry to `deploy/tenants.yml`. CI runs `tenants-lint`.
2. Repo admin creates the GitHub Environments `tenant-<id>-staging` and `tenant-<id>-production`. Configure required reviewers (≥ 2 for production), wait timer, and the secrets `TENANT_<ID>_DEPLOY_ROLE_ARN` and the `endpoint_secret_ref` value.
3. Tenant cloud IdP admin adds an OIDC trust binding for the subjects above and the tenant's deploy role.
4. Run `gh workflow run deploy.yml -f tenant_id=<id> -f environment=staging -f release_sha=<sha>` against a fixture (e.g. kind cluster) and confirm the audit event lands.
5. Promote first real release per Staged rollout.

## Offboarding runbook (tenant departure)

1. Pause: open PR removing the tenant from `deploy/tenants.yml`. Mark the entry deprecated for one release cycle (do not delete on day 0; deletion is a final step).
2. Coordinate with the tenant on data export and deletion windows per their contract.
3. Final cutoff: after data deletion is confirmed and any legal-hold periods elapse, delete the tenant entry, the GitHub Environments, and the cloud IdP trust binding.
4. Record the offboarding in the audit log via a manual `POST /internal/v1/audit/deploy` with `outcome="offboarded"` so the trail is searchable.

## Out of scope

- Building the orchestrator itself (Argo CD, Flux, or Helm runner installation).
- Per-tenant runtime configuration files (helm values, Argo Application manifests). These live in the tenant's deployment repo.
- Multi-region image replication. The single GHCR registry is authoritative; per-tenant pull-through caches are deployment infra.
- Cross-tenant rate limiting, fairness, or shared-cluster controls.

## Related

- [ci-assurance-spec.md](ci-assurance-spec.md) — overall CI/CD control posture.
- [runner-trust.md](runner-trust.md) — OIDC, action pinning, runner egress.
- [runbooks/ci-platform-resilience.md](runbooks/ci-platform-resilience.md) — what to do when CI is degraded.
- [docs/architecture/multi-tenant-deployment.md](../architecture/multi-tenant-deployment.md) — runtime tenant isolation model.
