# PR 18 — A9: Frontend Login Page, Auth Context, MFA Prompts, Session Routing

**Spec ID:** A9 | **Track:** Identity & Access | **Wave:** 4 (Auth Completion) | **Estimated effort:** L
**Depends on:** A2, A3, A4, A5, A6 (the endpoints this UI consumes)
**Required for:** end-to-end usable auth in product

---

## 1. Functional Specification

### 1.1 Goal

Today the frontend assumes identity is already loaded — it calls `GET /v1/session` and shows "Loading session…" forever if it returns 401. We need a real login page, MFA prompt page, session-list/management UI, and an auth context that handles 401 by routing to login.

### 1.2 User-visible behavior

- **Anonymous user:** visits any route → redirected to `/login`.
- **Login screen:** shows IdP picker (Google / SSO buttons / "Sign in with email" form) based on the org's enabled providers. Bank deploys hide signup + reset links.
- **MFA prompt:** after password (or local auth flow), prompted for TOTP code or WebAuthn ceremony.
- **Account settings → Sessions:** list of active sessions with revoke + "log out other devices" buttons.
- **Logout button** in profile menu.

### 1.3 Out of scope

- Admin UX for IdP/SCIM provisioning (separate later PR).
- Localization beyond English.
- Native apps (`apps/mac`, `apps/windows`).

---

## 2. Technical Specification

### 2.1 Architecture

- New `AuthContext` provider replaces inline identity state at [apps/frontend/src/app/App.tsx:113-146](../../apps/frontend/src/app/App.tsx#L113-L146).
- HTTP client (`http.ts`) intercepts 401 → emits an "auth lost" event → `AuthContext` navigates to `/login`.
- Login state machine: `anonymous → password_pending → mfa_pending → authenticated`.
- IdP picker fetches `GET /v1/auth/providers?org_slug=` (resolved either from URL subdomain in SaaS or from `window.location` in single-tenant) and renders enabled providers.
- All API calls continue to attach the bearer token; the bearer is now stored in memory + (configurably) in `localStorage` for persistence across page refreshes.

### 2.2 Schema changes

None.

### 2.3 Endpoints used (all from earlier PRs)

- `GET /v1/auth/providers?org_slug=` (A3)
- `POST /v1/auth/login` (A4)
- `GET /v1/auth/oidc/{id}/start` (A3)
- `GET /v1/auth/saml/{id}/start` (A5)
- `POST /v1/auth/mfa/challenge`, `POST /v1/auth/mfa/verify` (A6)
- `GET /v1/auth/sessions`, `DELETE /v1/auth/sessions/{id}` (A2)
- `POST /v1/auth/logout` (A2)

### 2.4 Code changes

**New files:**

- `apps/frontend/src/api/authApi.ts` — typed API client for the above endpoints.
- `apps/frontend/src/features/auth/AuthContext.tsx` — provider; exposes `useAuth()` with `{identity, status, login, logout, refresh}`.
- `apps/frontend/src/features/auth/LoginScreen.tsx` — IdP picker + email/password form.
- `apps/frontend/src/features/auth/MfaPrompt.tsx` — TOTP input + WebAuthn ceremony.
- `apps/frontend/src/features/settings/AccountSessionsPanel.tsx` — list active sessions, revoke each.
- `apps/frontend/src/app/routes.tsx` (if not present, refactor App's inline routing) — adds `/login`, `/mfa`.

**Modify:**

- [apps/frontend/src/app/App.tsx:113-146](../../apps/frontend/src/app/App.tsx#L113-L146) — remove inline identity load; wrap in `<AuthProvider>`.
- [apps/frontend/src/api/http.ts](../../apps/frontend/src/api/http.ts) — 401 interceptor.
- [apps/frontend/src/api/sessionApi.ts](../../apps/frontend/src/api/sessionApi.ts) — wires through AuthContext rather than direct fetch.
- [packages/api-types/src/index.ts](../../packages/api-types/src/index.ts) — export `AuthProvider`, `LoginRequest`, `LoginResponse`, `MfaChallenge`, `MfaVerifyRequest`, `Session`, `AccountSessionsResponse`.

**Per-org branding hook** (small): `AuthProvider.config.branding.{logo_url, accent_color, login_message}` honored on login screen. Bank deploys ship a per-org branding bundle.

**Vite proxy:** [apps/frontend/vite.config.ts](../../apps/frontend/vite.config.ts) is fine as-is (already proxies `/v1/*`).

### 2.5 Trust model & failure semantics

- Bearer stored in-memory; optional `localStorage` for refresh persistence (operator config; default off in bank profile).
- 401 → AuthContext clears identity, navigates to `/login`, preserves intended return URL via `?return_to=`.
- MFA verify failure → form error, no login proceeds.
- Network failure during MFA → user can retry; challenge_id remains valid until `expires_at`.

### 2.6 Tenant isolation

N/A directly. The frontend doesn't make tenant decisions — the backend does. The org_slug in the URL is a hint to the providers endpoint.

### 2.7 Observability

- Console / Sentry breadcrumbs on auth state transitions.
- No PII logged on the client side.

---

## 3. Requirements & Acceptance Criteria

### 3.1 Functional acceptance criteria

- [ ] Visiting `/` with no token → redirected to `/login`.
- [ ] Login screen renders the providers returned by the API.
- [ ] Email+password login → success → returns to original route.
- [ ] OIDC button → external IdP → callback returns user to app.
- [ ] When MFA is required, app routes to `/mfa` with the challenge.
- [ ] After verify, returns to original route.
- [ ] Settings → Sessions lists active sessions; revoke works.
- [ ] Logout button revokes current session and routes to `/login`.
- [ ] Bank deploy variant: signup/reset links hidden when those features are off.

### 3.2 Test plan (vitest + Playwright for e2e)

**Unit (vitest + RTL):**

- AuthContext state transitions.
- 401 interceptor triggers redirect.
- Login form validation.
- MFA prompt renders both TOTP and WebAuthn paths.
- Session list revoke calls the right endpoint.

**Component:**

- LoginScreen with various provider configs (Google only, OIDC + email, SSO only).
- Bank-mode renders without signup/reset.

**E2E (Playwright):**

- Full login → conversation → logout flow.
- MFA flow with virtual TOTP.
- Revoke other-device session — verify other "device" loses access on next request.

**Build:**

- `npm run typecheck --workspace @0x-copilot/frontend` clean.
- `npm run build` clean.

### 3.3 Compliance evidence produced

- Explicit logout + per-session revocation UX (compliance: "session revocation").
- MFA prompt UX with phishing-resistant option (FIDO2 button).
- Per-org branding hook documents tenant-aware presentation.

### 3.4 Rollout plan

- Behind `frontend.auth.enabled` feature flag (build-time env).
- When off, the existing "expects identity to be there" behavior is preserved.
- Flipped on per-environment.

### 3.5 Backout plan

Set `frontend.auth.enabled=false`. App falls back to legacy session-load.

### 3.6 Definition of done

- [ ] All new components ship with vitest tests.
- [ ] At least one Playwright e2e covers login + MFA + logout.
- [ ] api-types fully typed and exported.
- [ ] No regression on existing chat/settings flows.
- [ ] Bank-mode variant tested.

---

## 4. Critical files

- Modify: [apps/frontend/src/app/App.tsx:38-288](../../apps/frontend/src/app/App.tsx#L38-L288) — refactor.
- Modify: [apps/frontend/src/api/sessionApi.ts](../../apps/frontend/src/api/sessionApi.ts)
- Modify: [apps/frontend/src/api/http.ts](../../apps/frontend/src/api/http.ts) — 401 interceptor.
- New: `apps/frontend/src/api/authApi.ts`
- New: `apps/frontend/src/features/auth/{AuthContext,LoginScreen,MfaPrompt}.tsx`
- New: `apps/frontend/src/features/settings/AccountSessionsPanel.tsx`
- Modify: [packages/api-types/src/index.ts](../../packages/api-types/src/index.ts)
- New: `apps/frontend/docs/specs/auth/A9-login-ux.md` (this spec lives in `docs/roadmap/`; copy referenced from there)
