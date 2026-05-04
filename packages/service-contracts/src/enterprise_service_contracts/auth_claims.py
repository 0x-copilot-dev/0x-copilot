"""Shared bearer-token claim names.

Both the facade (mints + verifies) and the backend (mints + verifies on
internal session APIs) must agree on the claim shape. Per the monorepo
hard-boundary rule, behavior is not shared — only the strings.
"""

# Existing claims (already on every issued token)
CLAIM_ORG_ID = "org_id"
CLAIM_USER_ID = "user_id"
CLAIM_ROLES = "roles"
CLAIM_PERMISSION_SCOPES = "permission_scopes"
CLAIM_CONNECTOR_SCOPES = "connector_scopes"

# New A2 claims
CLAIM_SID = "sid"
CLAIM_EXPIRES_AT = "exp"  # seconds since epoch (RFC 7519 style)


# Env var that flips facade behavior from "trust HMAC alone" (back-compat) to
# "HMAC + per-request backend touch + sid required" (the eventual production
# state once login flows have shipped). Default during this PR is unset/false
# so existing externally-minted tokens continue to work.
ENV_REQUIRE_SESSION_BINDING = "REQUIRE_SESSION_BINDING"
