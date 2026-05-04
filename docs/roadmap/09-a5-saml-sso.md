# PR 09 — A5: SAML 2.0 SSO

**Spec ID:** A5 | **Track:** Identity & Access | **Wave:** 3 (Parallel) | **Estimated effort:** L
**Depends on:** A1, A2
**Required for:** A6 (MFA can layer on top), A8, A9
**Parallel with:** A3 (OIDC), A4 (local password), A7 (SCIM)

---

## 1. Functional Specification

### 1.1 Goal

Add SAML 2.0 SSO. The dominant enterprise IdP path for banks and government orgs (Okta, ADFS, Ping, Azure AD via SAML, Shibboleth). SP-initiated SSO by default; IdP-initiated allowed when explicitly enabled per-provider.

### 1.2 User-visible behavior

- **End user:** clicks "Sign in with SSO" → IdP login screen → returns logged in.
- **Org admin:** uploads IdP metadata (XML) or fills `idp_entity_id` + `idp_sso_url` + `idp_x509_cert` per-org via internal admin CLI.
- **IdP admin:** consumes our SP metadata at `GET /v1/auth/saml/{provider_id}/metadata`.

### 1.3 Out of scope

- Frontend UI (A9).
- Single Logout (SLO) protocol — defer (rarely required; revocation via A2 is enough).
- ECP (Enhanced Client/Proxy) — defer.
- Encrypted assertions in the _first_ PR (the column for `sp_decryption_key_ref` is added; verifier wired in a follow-up PR if a customer needs it).

---

## 2. Technical Specification

### 2.1 Architecture

- Use `python3-saml` (OneLogin) — most battle-tested SAML library.
- Provider config schema lives in `auth_providers.config` (JSONB): `idp_entity_id`, `idp_sso_url`, `idp_x509_cert` (PEM), `sp_entity_id`, `sp_acs_url`, `attribute_map`, `allow_idp_initiated`, optional `sp_signing_key_ref`/`sp_decryption_key_ref` (vault refs).
- Replay guard: `assertion_id UNIQUE` in `saml_authentications`.
- Attribute mapping: configurable mapping from SAML attribute names (e.g. `http://schemas.xmlsoap.org/claims/EmailAddress`) to local fields (`email`, `display_name`, `groups`).
- JIT provisioning: same gate as OIDC (`auto_provision_user`).

### 2.2 Schema changes

Migration `services/backend/migrations/0008_saml.sql`:

```sql
CREATE TABLE saml_authentications (
    auth_id        TEXT PRIMARY KEY,
    org_id         TEXT NOT NULL,
    provider_id    TEXT NOT NULL REFERENCES auth_providers(provider_id),
    request_id     TEXT,                              -- SP-initiated only
    assertion_id   TEXT NOT NULL,                     -- replay guard
    relay_state    TEXT,
    status         TEXT NOT NULL CHECK (status IN ('pending','consumed','rejected')),
    requested_at   TIMESTAMPTZ NOT NULL,
    expires_at     TIMESTAMPTZ NOT NULL,
    consumed_at    TIMESTAMPTZ,
    ip             TEXT,
    user_agent     TEXT
);
CREATE UNIQUE INDEX idx_saml_assertion_replay ON saml_authentications (assertion_id);
CREATE INDEX idx_saml_request ON saml_authentications (request_id) WHERE request_id IS NOT NULL;
CREATE INDEX idx_saml_pending ON saml_authentications (expires_at) WHERE status = 'pending';

CREATE TABLE saml_identities (
    identity_id      TEXT PRIMARY KEY,
    org_id           TEXT NOT NULL,
    user_id          TEXT NOT NULL REFERENCES users(user_id),
    provider_id      TEXT NOT NULL REFERENCES auth_providers(provider_id),
    name_id          TEXT NOT NULL,
    name_id_format   TEXT NOT NULL,
    linked_at        TIMESTAMPTZ NOT NULL,
    unlinked_at      TIMESTAMPTZ
);
CREATE UNIQUE INDEX idx_saml_identity_nameid
    ON saml_identities (provider_id, name_id) WHERE unlinked_at IS NULL;
CREATE INDEX idx_saml_identity_user
    ON saml_identities (user_id) WHERE unlinked_at IS NULL;
```

### 2.3 Endpoints

**Facade public:**

- `GET /v1/auth/saml/{provider_id}/start?relay_state=` → returns `{auth_url}` or 302 to IdP.
- `POST /v1/auth/saml/{provider_id}/acs` (AssertionConsumerService) — form-encoded `SAMLResponse=<base64>`. Validates, mints session.
- `GET /v1/auth/saml/{provider_id}/metadata` → SP metadata XML (Content-Type: application/xml).

**Backend internal:**

- `POST /internal/v1/auth/saml/{provider_id}/authorize` → AuthnRequest body, `auth_id`, `expires_at`.
- `POST /internal/v1/auth/saml/consume` body `{provider_id, saml_response, relay_state}` → session.

### 2.4 Code changes

**New:**

- `services/backend/src/backend_app/identity/saml.py` — wraps `python3-saml`'s `OneLogin_Saml2_Auth`; handles validation, attribute extraction, role mapping, JIT provisioning.

**Modify:**

- [services/backend/src/backend_app/app.py](../../services/backend/src/backend_app/app.py)
- `services/backend-facade/src/backend_facade/auth_routes.py`
- `services/backend/requirements.txt` — add `python3-saml`. (Note: requires `xmlsec` system library; document in `docs/deployment/dependencies.md`.)

### 2.5 Trust model & failure semantics

- Validate signature against `auth_providers.config.idp_x509_cert`.
- Validate `NotBefore` / `NotOnOrAfter` with 60s leeway.
- Validate `AudienceRestriction` matches `sp_entity_id`.
- For SP-initiated, validate `InResponseTo` matches a pending `request_id`.
- `assertion_id` must be unique → reject replays.
- Encrypted assertions: if `sp_decryption_key_ref` present, decrypt; else 400 if assertion is encrypted.
- Unknown `name_id` + `auto_provision_user=false` → 401, audit row.

### 2.6 Tenant isolation

- IdP cert is per-`auth_providers` row (per-org).
- Assertion signed by org_a's IdP cert cannot authenticate into org_b: ACS endpoint takes `provider_id`, lookup is `(provider_id, name_id)` not `(name_id)`.

### 2.7 Observability

- Audit: `saml.authorize_started`, `saml.acs_succeeded`, `saml.acs_failed{reason}`, `saml.user_provisioned`, `saml.role_synced`.
- Login attempts: every ACS, success or fail, writes a `login_attempts` row.
- Metrics: `saml_login_total{provider_id_hash, outcome}`.

---

## 3. Requirements & Acceptance Criteria

### 3.1 Functional acceptance criteria

- [ ] SP metadata at `GET /v1/auth/saml/{id}/metadata` validates per SAML 2.0 schema.
- [ ] SP-initiated round trip against fake IdP fixture mints session.
- [ ] Replay (same `assertion_id` twice) rejected with 400 + audit.
- [ ] Bad signature rejected.
- [ ] `NotOnOrAfter` in past rejected.
- [ ] Wrong `AudienceRestriction` rejected.
- [ ] IdP-initiated with `allow_idp_initiated=false` rejected with 400.

### 3.2 Test plan

**Unit:**

- Each validation rule as a separate test (signature, time bounds, audience, replay).
- Attribute map applies correctly.
- Role map syncs role assignments inside one transaction with session create.

**Integration:**

- Round-trip against in-process fake IdP fixture (signed assertion).
- JIT provisioning creates `users` + `saml_identities` rows.
- Cross-tenant: assertion signed by org_a IdP cert sent to org_b's ACS endpoint → rejected.

### 3.3 Compliance evidence produced

- Replay protection (`assertion_id UNIQUE`).
- Signed-assertion validation.
- Encrypted-assertion column ready for high-sensitivity deployments.
- Audit of every SSO event.

### 3.4 Rollout plan

- IdP added per-org via admin CLI; SP metadata URL given to customer's IdP admin.
- Off by default; enabled per org.

### 3.5 Backout plan

Set `auth_providers.enabled=false`.

### 3.6 Definition of done

- [ ] Migration 0008 applied.
- [ ] `python3-saml` + system `xmlsec` documented in `docs/deployment/dependencies.md`.
- [ ] Round-trip tested against fake IdP.
- [ ] All validation rules covered by unit tests.

---

## 4. Critical files

- New: `services/backend/migrations/0008_saml.sql` (+ rollback)
- New: `services/backend/src/backend_app/identity/saml.py`
- Modify: [services/backend/src/backend_app/app.py](../../services/backend/src/backend_app/app.py)
- Modify: `services/backend-facade/src/backend_facade/auth_routes.py`
- Modify: `services/backend/requirements.txt`
- New/modify: `docs/deployment/dependencies.md` — list `xmlsec1` system package.
