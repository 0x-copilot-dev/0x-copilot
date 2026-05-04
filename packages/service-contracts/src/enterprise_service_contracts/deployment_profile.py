"""Shared deployment-profile constants used by every backend service.

Each deployable service owns its own loader (so service boundaries stay hard),
but the env var name, profile values, and toggle keys are shared so a typo in
one service produces a mismatch we catch at lint time rather than at runtime.

Per the monorepo rule, this package is constants-only. No behavior here.
"""

ENV_DEPLOYMENT_PROFILE = "ENTERPRISE_DEPLOYMENT_PROFILE"

# Profile values --------------------------------------------------------------

PROFILE_SAAS_MULTI_TENANT = "saas_multi_tenant"
PROFILE_SINGLE_TENANT_MANAGED = "single_tenant_managed"
PROFILE_SINGLE_TENANT_SELF_HOSTED = "single_tenant_self_hosted"

ALLOWED_PROFILES = frozenset(
    {
        PROFILE_SAAS_MULTI_TENANT,
        PROFILE_SINGLE_TENANT_MANAGED,
        PROFILE_SINGLE_TENANT_SELF_HOSTED,
    }
)

SINGLE_TENANT_PROFILES = frozenset(
    {PROFILE_SINGLE_TENANT_MANAGED, PROFILE_SINGLE_TENANT_SELF_HOSTED}
)

# Toggle keys -----------------------------------------------------------------
# Stable string keys for the typed DeploymentFeatureToggles object that each
# service builds from its own loader. Keep these alphabetised so readers can
# scan them quickly.

TOGGLE_ALLOW_EMBEDDED_PROVIDER_KEYS = "allow_embedded_provider_keys"
TOGGLE_ALLOW_SELF_SIGNUP = "allow_self_signup"
TOGGLE_ALLOW_VENDOR_TELEMETRY = "allow_vendor_telemetry"
TOGGLE_DEFAULT_RETENTION_DAYS = "default_retention_days"
TOGGLE_DEV_AUTH_BYPASS_ALLOWED = "dev_auth_bypass_allowed"
TOGGLE_ENFORCE_RLS = "enforce_rls"
TOGGLE_REQUIRE_FIELD_LEVEL_ENCRYPTION = "require_field_level_encryption"
TOGGLE_REQUIRE_KMS_TOKEN_VAULT = "require_kms_token_vault"
TOGGLE_SIEM_EXPORT_REQUIRED = "siem_export_required"

ALL_TOGGLE_KEYS = frozenset(
    {
        TOGGLE_ALLOW_EMBEDDED_PROVIDER_KEYS,
        TOGGLE_ALLOW_SELF_SIGNUP,
        TOGGLE_ALLOW_VENDOR_TELEMETRY,
        TOGGLE_DEFAULT_RETENTION_DAYS,
        TOGGLE_DEV_AUTH_BYPASS_ALLOWED,
        TOGGLE_ENFORCE_RLS,
        TOGGLE_REQUIRE_FIELD_LEVEL_ENCRYPTION,
        TOGGLE_REQUIRE_KMS_TOKEN_VAULT,
        TOGGLE_SIEM_EXPORT_REQUIRED,
    }
)
