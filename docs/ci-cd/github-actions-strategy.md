# GitHub Actions strategy

## Intent

Path-filtered CI per deployable, plus a manual-dispatch release path for the two
things this repo actually ships: the **`@0x-copilot/cli` npm package** and the
**desktop app**. A change to `services/ai-backend` should retest only
`services/ai-backend` and any package it imports (`packages/service-contracts`,
`packages/api-types`) — not `services/backend` or the frontend.

> **Scope note.** This repo does **not** publish container images or run
> per-tenant deploys. There is no `deploy.yml`, `release-images.yml`, GHCR
> publish, or `tenant-<id>-<env>` Environment. Earlier revisions of this file
> listed those as "implemented"; they were never built. Distribution is
> CLI-first — see [branching-and-release.md](branching-and-release.md).

## Workflows that exist

**Unconditional required checks** — no `paths:` filter, deliberately:

- [ci-repo.yml](../../.github/workflows/ci-repo.yml) — repo-wide lint + secret scan.
- [ci-gates.yml](../../.github/workflows/ci-gates.yml) — the repo's architectural gates.

A required status check must be unconditional. GitHub reports a required check
that never starts as _pending_, not skipped, so adding `paths:` to a required
workflow wedges every PR whose diff misses those paths. Do not add one.

**Path-filtered component CI:**

- [ci-ai-backend.yml](../../.github/workflows/ci-ai-backend.yml),
  [ci-backend.yml](../../.github/workflows/ci-backend.yml),
  [ci-backend-facade.yml](../../.github/workflows/ci-backend-facade.yml)
- [ci-frontend.yml](../../.github/workflows/ci-frontend.yml),
  [ci-desktop.yml](../../.github/workflows/ci-desktop.yml),
  [ci-cli.yml](../../.github/workflows/ci-cli.yml),
  [ci-host-typecheck.yml](../../.github/workflows/ci-host-typecheck.yml)

**Conformance and performance gates:**

- [ci-e2-final-conformance.yml](../../.github/workflows/ci-e2-final-conformance.yml)
- [ci-e2-performance.yml](../../.github/workflows/ci-e2-performance.yml)
- [ci-merge-live-gate.yml](../../.github/workflows/ci-merge-live-gate.yml)

**Release and promotion** (manual dispatch, dry-run by default):

- [promote-to-main.yml](../../.github/workflows/promote-to-main.yml) — `dev` → `main`, **fast-forward**, so `main` ends byte-identical to `dev`.
- [release-cli.yml](../../.github/workflows/release-cli.yml) — npm publish. Owns the version bump and `CHANGELOG.md`; never hand-edit either.
- [release-desktop.yml](../../.github/workflows/release-desktop.yml) — desktop app release.
- [deploy-website.yml](../../.github/workflows/deploy-website.yml) — `apps/website` to GitHub Pages. The only "deploy" in the repo.

**Drills and administration:**

- [desktop-supervised-boot-drill.yml](../../.github/workflows/desktop-supervised-boot-drill.yml)
- [file-store-backup-drill.yml](../../.github/workflows/file-store-backup-drill.yml)
- [apply-branch-protection.yml](../../.github/workflows/apply-branch-protection.yml)

A dormant workflow rots. Path-filtered drills that rarely trigger have been
found broken the first time they ran in months — one had never installed
`pytest`, another booted without a required secret. If you touch a drill,
confirm it actually ran and passed.

## Secrets

- Pull-request CI does not require production secrets or live third-party services.
- Never interpolate `${{ }}` inside an embedded script. A `type: boolean` input
  substitutes as lowercase `true` (a `NameError` in Python), and any value
  pasted into program text is a script-injection vector. Pass values through
  `env:` and read `os.environ`.
- `administration` is not a valid `permissions:` key — it makes the whole
  workflow unparseable (HTTP 422 on dispatch). Repository administration cannot
  be granted to `GITHUB_TOKEN`; ruleset writes need a fine-grained PAT.

## Required-check contexts

Contexts are **bare job names** (`lint-and-secrets`), with the matrix suffix
where applicable (`cli (ubuntu-latest)`). The `workflow / job` form the PR UI
displays matches nothing. Verify with:

```bash
gh api repos/OWNER/REPO/commits/SHA/check-runs --jq '.check_runs[].name'
```

## Related

- [branching-and-release.md](branching-and-release.md) — the branch model and release mechanics.
- Root [CLAUDE.md](../../CLAUDE.md) — the CI rules that have already cost a day each, with the tests that guard them in `tools/test_apply_branch_protection.py`.
