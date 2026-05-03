# Runner trust, OIDC, and egress (reference)

This repository’s workflows default to **GitHub-hosted** `ubuntu-latest` runners. For government or regulated tenants you typically need one or more of:

1. **Self-hosted runners** inside your VPC or sovereign cloud, with hardened OS images and outbound **egress allowlists** (your firewall/proxy policy—not expressible purely in YAML).
2. **OIDC federation** (`permissions: id-token: write`) so deploy jobs mint **short-lived** cloud credentials instead of storing long-lived `AWS_SECRET_ACCESS_KEY`-style secrets in GitHub. Pair `deploy-staging.yml` / `deploy-production.yml` with your cloud provider’s “configure AWS credentials” action or equivalent.
3. **Least-privilege `GITHUB_TOKEN`**: keep `contents: read` on jobs that do not mutate the repo; scope `packages: write` only on image publish jobs (`release-images.yml`).

Release and deploy workflows reserve `id-token: write` for future OIDC wiring. Document IAM trust subjects (allowed `sub` claims) in your cloud IdP—do not commit real account IDs or role ARNs here.

## Related workflows

- [.github/workflows/release-images.yml](../../.github/workflows/release-images.yml)
- [.github/workflows/deploy-staging.yml](../../.github/workflows/deploy-staging.yml)
- [.github/workflows/deploy-production.yml](../../.github/workflows/deploy-production.yml)
