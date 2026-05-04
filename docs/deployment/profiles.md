# Deployment Profiles

Every backend service (`backend-facade`, `backend`, `ai-backend`) reads
`ENTERPRISE_DEPLOYMENT_PROFILE` at process startup to decide which safety
defaults to apply. Same container image; behavior is gated by config.

The profile is logged at startup (`deployment_profile=… toggles_hash=…`) and
exposed via `GET /v1/health` on each service.

## Profiles

| Profile                     | What it means                                        |
| --------------------------- | ---------------------------------------------------- |
| `saas_multi_tenant`         | Default. Many orgs in one DB, our infra.             |
| `single_tenant_managed`     | One org in one DB, our infra (e.g. dedicated VPC).   |
| `single_tenant_self_hosted` | One org in one DB, customer infra (Helm or Compose). |

## Toggle matrix

| Toggle                           | saas_multi_tenant | single_tenant_managed | single_tenant_self_hosted | dev (no profile) |
| -------------------------------- | ----------------- | --------------------- | ------------------------- | ---------------- |
| `dev_auth_bypass_allowed`        | false             | false                 | false                     | **true**         |
| `require_kms_token_vault`        | true              | true                  | true                      | false            |
| `require_field_level_encryption` | false             | true                  | true                      | false            |
| `siem_export_required`           | true              | true                  | true                      | false            |
| `enforce_rls`                    | true              | true                  | true                      | false            |
| `allow_self_signup`              | true              | false                 | false                     | true             |
| `allow_vendor_telemetry`         | true              | false                 | false                     | true             |
| `allow_embedded_provider_keys`   | true              | true                  | **false**                 | true             |
| `default_retention_days`         | 365               | 365                   | 365                       | 365              |

`dev (no profile)` only applies when both `ENTERPRISE_DEPLOYMENT_PROFILE` is
unset _and_ the service-specific environment variable
(`FACADE_ENVIRONMENT` / `BACKEND_ENVIRONMENT` / `RUNTIME_ENVIRONMENT`) equals
`development`.

## Required vs optional

| Service-specific env                                     | Behavior when `ENTERPRISE_DEPLOYMENT_PROFILE` is unset                     |
| -------------------------------------------------------- | -------------------------------------------------------------------------- |
| `*_ENVIRONMENT=development`                              | Defaults to `saas_multi_tenant` with **dev relaxations** (bypass allowed). |
| `*_ENVIRONMENT=staging` (or any non-prod, non-dev value) | Defaults to `saas_multi_tenant` with **production-style** lockdown.        |
| `*_ENVIRONMENT=production`                               | **Process refuses to start.** Operator must set the profile explicitly.    |

## Conflicts that fail closed at startup

The loader rejects these combinations and exits with code `78` (sysexits.h
`EX_CONFIG`):

- `ENTERPRISE_DEPLOYMENT_PROFILE` set to a value not in the allowed list.
- `ENTERPRISE_DEPLOYMENT_PROFILE=single_tenant_*` AND `DEV_AUTH_BYPASS=true` —
  the bypass cannot be silently honored under a regulated profile even if
  someone leaks `DEV_AUTH_BYPASS=true` from a dev shell.

## Per-service env vars

Each service uses its own legacy `*_ENVIRONMENT` env var so we don't break
existing deploy YAML in this PR. The `ENTERPRISE_DEPLOYMENT_PROFILE` env var
is shared across all three.

| Service        | Environment env var   |
| -------------- | --------------------- |
| backend-facade | `FACADE_ENVIRONMENT`  |
| backend        | `BACKEND_ENVIRONMENT` |
| ai-backend     | `RUNTIME_ENVIRONMENT` |

## Setting the profile in deploy artifacts

**Helm chart values:**

```yaml
env:
  ENTERPRISE_DEPLOYMENT_PROFILE: single_tenant_managed
  RUNTIME_ENVIRONMENT: production
  BACKEND_ENVIRONMENT: production
  FACADE_ENVIRONMENT: production
```

**Docker Compose:**

```yaml
services:
  backend-facade:
    environment:
      - ENTERPRISE_DEPLOYMENT_PROFILE=single_tenant_self_hosted
      - FACADE_ENVIRONMENT=production
```

## Bank / government deployment baseline

Use `single_tenant_managed` if you operate it; `single_tenant_self_hosted` if
the customer does. Either way, all three services get the same value.

## How later PRs consume toggles

This PR (C1) only resolves and exposes the toggles. Downstream PRs read them:

- C5 (RLS): `enforce_rls`
- C6 (KMS BYOK): `require_kms_token_vault`
- C7 (field encryption): `require_field_level_encryption`
- C9 (SIEM export): `siem_export_required`
- A4 (local password): `allow_self_signup`
- B3 (pricing): `allow_embedded_provider_keys`
- C8 (retention): `default_retention_days`

## Verifying

```sh
$ curl http://localhost:8200/v1/health
{
  "service": "backend-facade",
  "deployment_profile": "saas_multi_tenant",
  "feature_toggles_hash": "a1b2c3d4"
}
```

A drift in `feature_toggles_hash` between two service replicas with the same
profile signals a misconfigured rollout — alert on it.
