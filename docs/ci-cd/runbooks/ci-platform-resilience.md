# CI/CD platform resilience and break-glass

## Scope

This runbook covers degradation or compromise of the CI/CD control plane: GitHub Actions, runner fleet, GHCR, the OIDC issuer, or the cosign transparency log. It does not replace application HA runbooks or tenant-side cluster runbooks.

## Normal operation

- Pull requests run path-filtered workflows under [.github/workflows/](../../../.github/workflows/).
- `main` pushes matching paths run [release-images.yml](../../../.github/workflows/release-images.yml), producing digest-pinned, cosign-signed images and a `deployment-manifest.json` artifact.
- Tenant promotions run [deploy.yml](../../../.github/workflows/deploy.yml) (or its staging/production wrappers), gated by `tenant-<id>-<env>` GitHub Environments.

## Partial-degradation matrix

| Failure mode                                                 | What still works                                                                                                                      | What is blocked                                                              | Action                                                                                        |
| ------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------- |
| **Actions API down** (workflow runs cannot start)            | Existing in-flight runs continue; runtime clusters keep serving with previously deployed digests; engineers can read code and review. | New PR CI, new releases, new deploys.                                        | Wait for status.github.com clear; do not branch-bypass merges.                                |
| **Runner fleet unhealthy** (jobs queue but never pick up)    | Already-running jobs complete; runtime unaffected.                                                                                    | New jobs of any kind.                                                        | Self-hosted: rotate to a healthy runner pool. GitHub-hosted: wait for status.github.com.      |
| **GHCR unreachable** (push or pull fails)                    | Existing deployments with cached images keep running; PR CI not requiring GHCR still passes.                                          | `release-images.yml` (push); `deploy.yml` (pull).                            | Wait. Do not switch registries reactively — that breaks audit trails and cosign verification. |
| **OIDC issuer down** (`token.actions.githubusercontent.com`) | Workflows without `id-token: write` continue.                                                                                         | All deploys (cannot federate); cosign signing (no Fulcio cert); attestation. | Wait. Do not fall back to long-lived credentials.                                             |
| **Sigstore (Fulcio/Rekor) outage**                           | Existing signed images deploy fine (cosign verify uses cached transparency log entries with `--rekor-url` if mirrored).               | New `cosign sign` calls in `release-images.yml`.                             | Pause new releases; do not skip cosign signing as a workaround.                               |
| **Single component CI workflow flaky**                       | Other components release as normal (path filtering).                                                                                  | The flaky component's PRs and releases.                                      | Investigate the workflow; do not disable required-check enforcement to merge.                 |

## Break-glass: ship a known-good image without Actions

Use this only with documented risk acceptance and a second approver (recorded in the change ticket).

1. **Identify the last known-good release.** Read the most recent successful `release-images.yml` run on `main`; record the `head_sha`, the `deployment-manifest.json` artifact contents, and the per-image digests.
2. **Verify out-of-band.** From a workstation with cosign installed, run `cosign verify --certificate-oidc-issuer https://token.actions.githubusercontent.com --certificate-identity-regexp <regex from manifest> ghcr.io/<owner>/<image>@<digest>` for every image. Do not skip this — Actions outage does not relax the signing control.
3. **Promote by digest, not tag.** In your orchestrator (Argo/Helm/Flux), set `image.digest: sha256:…` directly. Never use `:latest`.
4. **Record the deployment.** Open a change ticket (Jira / ServiceNow / your equivalent) noting: tenant id, environment, release SHA, every image digest, both approvers, time of action.
5. **Reconcile after recovery.** When Actions is back, run `deploy.yml` against the same release SHA so the audit endpoint receives the deploy event and the `deployment-record-*` artifact exists. Cross-link the ticket.

## Runner compromise

1. **Rotate** every GitHub organization secret and every environment-scoped secret the runner could have read. Use `gh secret list --org <org>` and `gh secret list --env <env> --repo <repo>` to enumerate.
2. **Revoke** GHCR tokens and personal access tokens that the runner used. Invalidate active OIDC sessions per cloud-vendor guidance (AWS: revoke session via STS; GCP: revoke service-account keys; Azure: revoke session via tenant admin).
3. **Re-issue** any cosign signatures created during the compromise window if the OIDC identity is suspect (re-sign from a clean run).
4. **Re-image** self-hosted runners from a known-good golden AMI / golden image; rotate runner registration tokens.
5. **Audit** every successful deploy event during the compromise window via the backend audit endpoint; cross-check against intended releases.

## Game-day cadence

Run a tabletop or live-fire rehearsal of the break-glass procedure at least **quarterly**. Track outcomes (time-to-promote, missed steps, missing tooling) in the change-management system. Regulated buyers ask for evidence of these rehearsals.

## Targets (defaults; override per organization)

| Metric                         | Target                                                  |
| ------------------------------ | ------------------------------------------------------- |
| RPO (CI/CD)                    | ≤ commit lag (no CI state to lose; commits are durable) |
| RTO (release pipeline)         | ≤ 4 hours via break-glass digest promotion              |
| RTO (full Actions restoration) | depends on GitHub status; out of repo control           |
| Game-day cadence               | quarterly minimum                                       |

## Related

- [ci-assurance-spec.md](../ci-assurance-spec.md) — control inventory and known gaps.
- [multi-tenant-deploy.md](../multi-tenant-deploy.md) — per-tenant promotion model.
- [runner-trust.md](../runner-trust.md) — supply-chain controls.
