# CI/CD platform resilience and break-glass

## Scope

This runbook covers **loss or compromise of the CI/CD control plane** (GitHub Actions unavailable, runner fleet unhealthy, registry outage). It does **not** replace application HA runbooks.

## Normal operation

- Pull requests run path-filtered workflows under [.github/workflows](../../.github/workflows).
- `main` pushes matching paths run `release-images.yml`, producing immutable digest references and `deployment-manifest.json`.

## Break-glass: ship without Actions

1. **Identify last known-good images** from `deployment-manifest` artifacts on a recent green `release-images` run, or from GHCR tags (`sha-<gitsha>` style tags from metadata-action).
2. **Promote by digest**, not `:latest`, in your orchestrator (Kubernetes, ECS, etc.).
3. **Record** approver identity and digest in your change ticket; after recovery, reconcile manifests so audit trails stay consistent.

## Runner compromise

1. Rotate **GitHub organization secrets** and **environment secrets** that the runner could have observed.
2. Revoke **GHCR tokens** if exfiltration is suspected; invalidate OIDC sessions per cloud vendor guidance.
3. Re-image self-hosted runners from a known-good AMI/golden image.

## GitHub outage

- Merge and deploy processes pause; application runtime may still be healthy if clusters already run pinned digests.
- Use break-glass promotion only with executive approval and documented risk acceptance.

## Targets

Document your organization’s **RPO/RTO** for CI separately from product SLA—GitHub publishes its own availability commitments.
