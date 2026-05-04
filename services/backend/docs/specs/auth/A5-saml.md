# A5 — SAML 2.0 SSO (implementation contract)

Roadmap source: [docs/roadmap/09-a5-saml-sso.md](../../../../../docs/roadmap/09-a5-saml-sso.md).
This file records the _as-built_ implementation deltas — what we shipped and
why it differs from the roadmap text.

## Migration

- File: `services/backend/migrations/0014_saml.sql` (+ rollback).
- The roadmap says `0008` but that number was already taken by RLS. Numbering
  is monotonic; spec name does not gate the actual number.
- RLS policies on `saml_authentications` / `saml_identities` are added in a
  follow-up migration along with the rest of the post-RLS tables (matches the
  pattern used by `0011_mfa.sql` / `0012_account_lockouts.sql`).

## SAML library — pluggable verifier

`python3-saml` (OneLogin) is the production library, but its install requires
the `xmlsec1` system package and `lxml`. To keep the unit-test path independent
of a system dependency, we factor verification behind a small Protocol:

- `SamlVerifier` — `build_authn_request`, `build_metadata`, `parse_response`.
- `OneLoginSamlVerifier` — production wrapper. Imports `python3-saml` lazily.
- `FakeSamlVerifier` — in-memory test double. Returns a pre-configured
  `ParsedSamlAssertion` and can be told to raise `SamlSignatureError` /
  `SamlAssertionExpired` / `SamlAudienceMismatch` so the service-layer tests
  pin the trust-model contract without needing a real signed XML round-trip.

The integration test that exercises real signed assertions imports
`python3-saml` via `pytest.importorskip` so the suite stays green on a
host without `xmlsec1`.

## Provider config (auth_providers.config JSONB)

```
{
  "idp_entity_id":      str,
  "idp_sso_url":        str,
  "idp_x509_cert":      str (PEM, no headers OK),
  "sp_entity_id":       str,
  "sp_acs_url":         str,
  "attribute_map":      {"email": "...", "display_name": "...", "groups": "..."},
  "allow_idp_initiated": bool (default false),
  "auto_provision_user": bool (default false),
  "group_role_map":     {"<group_name>": "<role_name>"},
  "sp_signing_key_ref":     str | null  (vault ref, declared but not used yet)
  "sp_decryption_key_ref":  str | null  (vault ref, declared but not used yet)
}
```

`sp_signing_key_ref` / `sp_decryption_key_ref` columns/keys are declared but
not consumed by the `OneLoginSamlVerifier` yet — they exist so a follow-up
that wires assertion encryption (high-sensitivity deployments) doesn't have
to migrate the schema.

## Endpoints

**Backend internal** (require `SERVICE_TOKEN_HEADER`):

- `POST /internal/v1/auth/saml/{provider_id}/authorize` →
  `{auth_id, request_id, sso_url, request_xml, expires_at}`
- `POST /internal/v1/auth/saml/consume` body
  `{provider_id, saml_response, relay_state, ip, user_agent}` →
  `SamlConsumeResult` (mirrors `OidcCallbackResult`).
- `GET /internal/v1/auth/saml/{provider_id}/metadata` → SP metadata XML.

**Facade public** (anonymous):

- `GET /v1/auth/saml/{provider_id}/start?org_id=&relay_state=` → 302 to
  IdP SSO URL (or `format=json` for tests).
- `POST /v1/auth/saml/{provider_id}/acs` form-encoded `SAMLResponse=<base64>`
  → 200 `SamlConsumeResult` JSON, or 302 to `relay_state` when `format=redirect`.
- `GET /v1/auth/saml/{provider_id}/metadata` → XML, served by facade so the
  IdP admin only ever sees a public URL.

## Trust model

Validated _inside_ the verifier (production: by `python3-saml`):

- `Signature` — checked against `auth_providers.config.idp_x509_cert`.
- `NotBefore` / `NotOnOrAfter` — 60s clock skew.
- `AudienceRestriction` — must match `sp_entity_id`.
- `InResponseTo` — for SP-initiated, must match a pending `request_id`.

Validated by `SamlService` after parsing:

- `assertion_id` UNIQUE — replay defense (DB constraint, also surfaced as
  `SamlReplayDetected` for nicer logs).
- Org binding — assertion's resolved `(provider_id, name_id)` always looked up
  _together_, so an assertion signed by org_a's IdP cert that lands on org_b's
  ACS endpoint cannot link to an org_b user.

## Out of scope (per roadmap §1.3)

- Single Logout (SLO).
- ECP profile.
- IdP metadata XML auto-fetch (admin uploads manually for now).
- `auth_providers` admin CLI/UI to mint SAML providers (operator inserts the
  row via `services/backend/scripts/seed_saml_provider.py` — a follow-up PR).

## Tests

Unit (`services/backend/tests/identity/`):

- `test_saml_store.py` — in-memory store contracts (replay, partial unique).
- `test_saml_service.py` — authorize, consume happy path, replay rejection,
  signature failure (via FakeSamlVerifier), JIT provisioning, role sync,
  cross-tenant rejection.

Routes (`services/backend/tests/`):

- `test_saml_routes.py` — backend internal endpoints, error-code mapping.

Facade (`services/backend-facade/tests/`):

- `test_saml_facade.py` — start (redirect + json), ACS form parsing, metadata XML.
