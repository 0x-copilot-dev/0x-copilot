# Runner trust, action pinning, OIDC, egress

This document covers the supply-chain controls that bind GitHub Actions to specific code, identities, and network destinations.

## 1. Action pinning (mandatory)

Every `uses:` line in this repo's workflows MUST be pinned to a 40-character commit SHA, with a comment recording the human-readable version:

```yaml
- uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683 # v4.2.2
```

**Why:** mutable tags (`@v4`, `@v6`) are reassignable by the upstream action owner. The `tj-actions/changed-files` compromise (March 2025) is the canonical example: the v45 tag was rewritten to point to a malicious commit, exfiltrating secrets from every workflow that pinned to a tag rather than a SHA.

**Enforcement:** verified by `grep -E "uses:.*@v[0-9]" .github/workflows/*.yml` returning zero hits in the pre-merge sweep, and by Dependabot opening update PRs (with new SHAs) so version updates land as reviewable diffs.

First-party `actions/*` and `github/*` actions are pinned to SHAs by the same rule. They are not exempt.

## 2. OIDC federation (no long-lived cloud credentials)

Release and deploy workflows declare `permissions: id-token: write` so each job can mint a short-lived GitHub OIDC token and exchange it for cloud credentials at the tenant boundary.

For the per-tenant deploy model, the trust subject convention is:

```
repo:<owner>/<repo>:environment:tenant-<tenant-id>-<env>
```

Configured in the tenant cloud IdP (AWS IAM trust policy / GCP Workload Identity Pool / Azure Federated Credentials). See [multi-tenant-deploy.md](multi-tenant-deploy.md) for the full convention and onboarding runbook.

Do not commit real account IDs, role ARNs, or principal identifiers to this repo. The deploy workflow reads these at runtime from per-environment secrets.

## 3. Least-privilege `GITHUB_TOKEN`

Default workflow permissions are read-only (`contents: read`). Each job widens scope only as needed:

- `release-images.yml::build-and-push` — `packages: write`, `id-token: write`, `attestations: write`, `security-events: write`.
- `release-images.yml::deployment-manifest` — `contents: read`, `actions: read`.
- `deploy.yml::promote` — `contents: read`, `id-token: write`, `actions: read`.

PR CI workflows keep `contents: read` only.

## 4. Self-hosted runners (when required)

This repo defaults to **GitHub-hosted** `ubuntu-latest` runners. For sovereign-cloud or government tenants you typically need self-hosted runners inside the tenant VPC.

Starter egress allowlist (the runner's outbound proxy/firewall must permit):

| Destination                                                       | Why                                    |
| ----------------------------------------------------------------- | -------------------------------------- |
| `github.com`, `api.github.com`, `*.actions.githubusercontent.com` | Workflow + artifact APIs               |
| `*.actions.githubusercontent.com`, `*.blob.core.windows.net`      | Actions cache CDN                      |
| `ghcr.io`, `*.ghcr.io`, `pkg-containers.githubusercontent.com`    | GHCR image push/pull                   |
| `pypi.org`, `files.pythonhosted.org`                              | Python packages                        |
| `registry.npmjs.org`                                              | Node packages                          |
| `token.actions.githubusercontent.com`                             | OIDC issuer JWKS                       |
| `fulcio.sigstore.dev`, `rekor.sigstore.dev`                       | Cosign keyless OIDC + transparency log |
| Tenant cloud STS endpoint (e.g. `sts.<region>.amazonaws.com`)     | OIDC-federated role assumption         |
| Tenant orchestrator endpoint                                      | Argo / Helm / Flux invocation          |

Runner image provenance for self-hosted fleets is the operator's responsibility. Document the AMI/golden-image source, the patch cadence, and the SBOM in your tenant-deployment runbook.

## 5. Network and registry residency

Per-tenant `data_residency.allowed_image_registries` in `deploy/tenants.yml` lets a tenant restrict which image registries are acceptable. [verify_release.py](../../deploy/scripts/verify_release.py) enforces the allowlist before federation and orchestrator invocation.

## Related workflows

- [.github/workflows/release-images.yml](../../.github/workflows/release-images.yml)
- [.github/workflows/deploy.yml](../../.github/workflows/deploy.yml)
- [.github/workflows/deploy-staging.yml](../../.github/workflows/deploy-staging.yml)
- [.github/workflows/deploy-production.yml](../../.github/workflows/deploy-production.yml)
