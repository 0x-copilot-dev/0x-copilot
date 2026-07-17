# Deployment Profiles

How the facade selects safety defaults based on the deployment environment.

See also:

- [architecture/00-system-map.md](../architecture/00-system-map.md) — module map

Source: `backend_facade/deployment_profile.py`

---

## What it does

At process startup, `DeploymentProfileLoader.load()` reads `ENTERPRISE_DEPLOYMENT_PROFILE`
and resolves a frozen `DeploymentProfile` with a set of `DeploymentFeatureToggles`.
The same profile module exists in all three Python services (backend, ai-backend,
backend-facade) — they are not shared to preserve service isolation; only the profile
name constants come from `copilot_service_contracts`.

The profile is stored on `app.state.deployment` and is read-only for the lifetime
of the process.

---

## Profiles

| Profile                   | `ENTERPRISE_DEPLOYMENT_PROFILE` value | Intended for              |
| ------------------------- | ------------------------------------- | ------------------------- |
| SaaS multi-tenant         | `saas_multi_tenant`                   | Hosted product            |
| Single-tenant managed     | `single_tenant_managed`               | Managed private deploy    |
| Single-tenant self-hosted | `single_tenant_self_hosted`           | Customer-operated on-prem |

An unset profile in `production` → process exits with code 78 (configuration error).
An unset profile in `development` → dev defaults applied (relaxed, auth bypass allowed).
An unset profile in any other environment (staging, CI) → `saas_multi_tenant` defaults.

---

## Feature toggles

| Toggle                           | `saas_multi_tenant` | `single_tenant_managed` | `single_tenant_self_hosted` | dev default |
| -------------------------------- | ------------------- | ----------------------- | --------------------------- | ----------- |
| `allow_embedded_provider_keys`   | `true`              | `true`                  | `false`                     | `true`      |
| `allow_self_signup`              | `true`              | `false`                 | `false`                     | `true`      |
| `allow_vendor_telemetry`         | `true`              | `false`                 | `false`                     | `true`      |
| `default_retention_days`         | `365`               | `365`                   | `365`                       | `365`       |
| `dev_auth_bypass_allowed`        | `false`             | `false`                 | `false`                     | `true`      |
| `enforce_rls`                    | `true`              | `true`                  | `true`                      | `false`     |
| `require_field_level_encryption` | `false`             | `true`                  | `true`                      | `false`     |
| `require_kms_token_vault`        | `true`              | `true`                  | `true`                      | `false`     |
| `siem_export_required`           | `true`              | `true`                  | `true`                      | `false`     |

---

## Consistency enforcement

`DeploymentProfileLoader._enforce_consistency()` rejects env combinations that
contradict the profile. Currently enforced:

- `DEV_AUTH_BYPASS=true` + a profile where `dev_auth_bypass_allowed=false` → exits with
  code 78. Prevents accidentally enabling dev auth in a hardened deploy.

---

## Boot behaviour

```python
# app.py startup
profile = resolve_or_exit()      # exits if invalid
log_profile(profile)             # logs: deployment_profile=saas_multi_tenant toggles_hash=<8-char-sha256>
app.state.deployment = profile
```

The `toggles_hash` is a stable 8-char SHA-256 of the serialised toggles — useful for
correlating ops dashboard alerts with a specific configuration state.

---

## Extension points

To add a new toggle:

1. Add the field to `DeploymentFeatureToggles` in all three services.
2. Set values in `_DEFAULTS_BY_PROFILE` and `_DEV_DEFAULT` for all profiles.
3. Add a consistency check in `_enforce_consistency()` if the new toggle has constraints.
4. Update this doc and the env-vars reference.
