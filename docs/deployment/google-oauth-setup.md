# Google OAuth setup ("Continue with Google")

0xCopilot ships a **deployment-global** Google sign-in provider. When
`GOOGLE_OAUTH_CLIENT_ID` is set on the `backend` process, the login screen shows
**Continue with Google** and any user (or the pre-workspace login screen, where
no org is known yet) can sign in with their Google account.

This guide covers creating the Google Cloud OAuth clients and wiring the env
vars for both deployment shapes:

- **Self-host (web):** a **Web application** OAuth client with an HTTPS redirect
  back to your host.
- **Packaged desktop app:** a **Desktop app** OAuth client that uses a loopback
  (`127.0.0.1`) redirect.

Under the hood the provider is env-configured in
[`services/backend/src/backend_app/identity/google.py`](../../services/backend/src/backend_app/identity/google.py)
and served through the facade routes in
[`services/backend-facade/src/backend_facade/auth_routes.py`](../../services/backend-facade/src/backend_facade/auth_routes.py).
Google's OIDC endpoints and the requested scopes (`openid`, `email`, `profile`)
are pinned constants — no boot-time discovery fetch.

---

## 1. Create a Google Cloud project

1. Open the [Google Cloud Console](https://console.cloud.google.com/).
2. Create a new project (or select an existing one) from the project picker.

## 2. Configure the OAuth consent screen

1. Go to **APIs & Services → OAuth consent screen**.
2. Choose a **User Type**:
   - **Internal** — only users in your Google Workspace org can sign in.
   - **External** — any Google account; while the app is in "Testing" you must
     list test users, or publish the app to allow anyone.
3. Fill in the app name, user support email, and developer contact.
4. Add the scopes `openid`, `email`, and `profile` (these are the only scopes
   0xCopilot requests).
5. Save.

## 3. Create the OAuth client(s)

Go to **APIs & Services → Credentials → Create credentials → OAuth client ID**.

### 3a. Web application client (self-host)

1. **Application type:** _Web application_.
2. **Authorized redirect URIs:** add your host's callback URL —

   ```
   https://<your-host>/v1/auth/oidc/callback
   ```

   The path is fixed (`/v1/auth/oidc/callback`, served by the facade). Add one
   entry per origin you serve from. For a local self-host test on the default
   gateway port, also add `http://localhost:8090/v1/auth/oidc/callback`.

3. Create, then copy the **Client ID** and **Client secret**. Google issues a
   secret for Web application clients, so a self-host web deployment sets both —
   the backend then uses `client_secret_post` for the token exchange.

### 3b. Desktop app client (packaged app)

1. **Application type:** _Desktop app_.
2. Google automatically allows loopback redirects (`http://127.0.0.1:<port>` and
   `http://localhost`) for this client type — the packaged app binds an
   **ephemeral loopback port** per sign-in, so there is no exact URI to
   pre-register.
3. Create, then copy the **Client ID**. The desktop flow is **PKCE-only**: leave
   `GOOGLE_OAUTH_CLIENT_SECRET` unset for the packaged app (the backend then uses
   `token_endpoint_auth_method: none`).

> The desktop main process opens the loopback flow described in
> [`apps/desktop/README.md`](../../apps/desktop/README.md) ("Continue with
> Google"): it calls `GET {facade}/v1/auth/oidc/google/start?redirect_uri=<loopback>&format=json`,
> opens the returned `auth_url` in the system browser, and exchanges the
> `state`+`code` at `GET {facade}/v1/auth/oidc/callback`.

## 4. Where the env vars go

| Deployment       | Variable                     | Value                             |
| ---------------- | ---------------------------- | --------------------------------- |
| Self-host (web)  | `GOOGLE_OAUTH_CLIENT_ID`     | Web application client ID         |
| Self-host (web)  | `GOOGLE_OAUTH_CLIENT_SECRET` | Web application client secret     |
| Packaged desktop | `GOOGLE_OAUTH_CLIENT_ID`     | Desktop app client ID (no secret) |

**Self-host:** put both in `deploy/self-host/.env` so the `backend` container
picks them up:

```dotenv
GOOGLE_OAUTH_CLIENT_ID=1234567890-abc.apps.googleusercontent.com
GOOGLE_OAUTH_CLIENT_SECRET=GOCSPX-xxxxxxxxxxxxxxxxxxxx
```

Restart the stack so the backend re-reads the environment.

**Desktop:** the desktop process forwards `GOOGLE_OAUTH_CLIENT_ID` to the
embedded backend via the curated passthrough allowlist in
[`apps/desktop/main/services/service-env.ts`](../../apps/desktop/main/services/service-env.ts)
(it is the one named OAuth passthrough — the child services otherwise get a
stripped environment). Provide it to the desktop build/launch environment; no
client secret is passed through.

Notes:

- `GOOGLE_OAUTH_CLIENT_ID` **empty or unset ⇒ the provider is simply absent** —
  not listed, not resolvable, and the button does not render.
- The secret, when set, is encrypted with the process `TokenVault` and never
  logged or persisted in plaintext.
- The provider id `google` is reserved; a per-org SSO provider may not claim it.

## 5. Verification checklist

- [ ] `GOOGLE_OAUTH_CLIENT_ID` is set on the `backend` process (for self-host,
      confirm the container environment; for desktop, confirm the passthrough).
- [ ] `GET /v1/auth/providers` (unscoped — `org_id=-` is allowed) lists an entry
      with `provider_id: "google"` and `enabled: true`. The frontend probes this
      exact call once on mount to decide whether to render the button.

  ```bash
  curl -sS "https://<your-host>/v1/auth/providers?org_id=-" | jq
  ```

- [ ] The login screen shows **Continue with Google**.
- [ ] Clicking it opens Google, and after consent you land back authenticated —
      the redirect URI Google shows must exactly match one you registered
      (`https://<your-host>/v1/auth/oidc/callback` for web).

### Troubleshooting

- **`redirect_uri_mismatch`** — the callback URL the app sent isn't in the
  client's Authorized redirect URIs. Add the exact
  `https://<your-host>/v1/auth/oidc/callback` (scheme, host, and path must match).
- **Button doesn't appear** — `GOOGLE_OAUTH_CLIENT_ID` isn't reaching the
  backend, or `/v1/auth/providers` doesn't list `google`. Re-check step 4 and
  restart.
- **Access blocked / app not verified** — your consent screen is in "Testing"
  and the account isn't a listed test user (External type). Add the user or
  publish the app.
