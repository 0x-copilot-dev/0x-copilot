# Dev API Testing — curl, Postman, and the dev IdP

This is the single source of truth for hitting the local stack outside the
browser. The browser auto-mints a bearer via the W0.1 dev IdP; curl and
Postman need to do the same thing manually.

## How dev auth works

```
browser / curl  ──▶  Vite :5173  ──▶  facade :8200  ──▶  backend :8100
                       proxy            verify             verify same
                                       bearer              bearer
                                                  ──▶  ai-backend :8000
```

The chain is:

1. `BACKEND_ENVIRONMENT=development` makes `services/backend` register two
   extra routes:
   - `GET  /v1/dev/personas` — list available fixtures.
   - `POST /v1/dev/identity/mint` — exchange a persona slug for a signed
     bearer.
     The facade re-exposes both routes verbatim so apps still talk only to
     `:8200`.
2. The bearer is **signed with `ENTERPRISE_AUTH_SECRET`** — the same secret
   production uses to verify. The minting source is the only dev-specific
   bit; the verification path is identical to production.
3. Service-to-service calls (facade ↔ backend, ai-backend ↔ backend) use
   `ENTERPRISE_SERVICE_TOKEN` plus `x-enterprise-org-id` /
   `x-enterprise-user-id` headers derived from the verified user bearer.
   The backend treats those headers as untrusted unless the service token
   is valid.
4. **Production fails closed** without `ENTERPRISE_AUTH_SECRET` and
   `ENTERPRISE_SERVICE_TOKEN`. The `/v1/dev/*` routes are unregistered
   when `BACKEND_ENVIRONMENT != development`.

There is no `DEV_AUTH_BYPASS` shortcut. Every request, dev or prod, carries
a real bearer.

## Mint a bearer

Easiest path is the Make target:

```bash
export TOKEN=$(make dev-bearer)                       # default: sarah_acme
export TOKEN=$(make dev-bearer PERSONA=marcus_admin)  # admin variant
```

Equivalent direct call against the facade:

```bash
TOKEN=$(curl -sS -X POST http://127.0.0.1:8200/v1/dev/identity/mint \
  -H 'content-type: application/json' \
  -d '{"persona_slug":"sarah_acme"}' \
  | python3 -c 'import json,sys;print(json.load(sys.stdin)["bearer"])')
echo $TOKEN
```

List the personas the dev IdP knows about:

```bash
curl -sS http://127.0.0.1:8200/v1/dev/personas | jq
```

Today the fixtures cover at least:

| slug                 | role     | org           | typical use                                |
| -------------------- | -------- | ------------- | ------------------------------------------ |
| `sarah_acme`         | employee | `org_acme`    | default user — popover, runs, MCP install. |
| `marcus_admin`       | admin    | `org_acme`    | members, audit log, admin-managed gates.   |
| `alex_contoso_admin` | admin    | `org_contoso` | tenant isolation tests.                    |

Run the `personas` endpoint above for the canonical list — it is generated
from `services/backend/dev_personas.yaml` and may evolve.

## curl recipes

Apps must call the facade only — never `backend:8100` or `ai-backend:8000`
directly. The browser, curl, Postman, and native apps all share the same
public surface at `:8200`.

```bash
# Caller's profile
curl -sS http://127.0.0.1:8200/v1/me/profile \
  -H "Authorization: Bearer $TOKEN" | jq

# Caller's workspaces
curl -sS http://127.0.0.1:8200/v1/me/workspaces \
  -H "Authorization: Bearer $TOKEN" | jq

# MCP catalog (read-only, requires MCP_READ scope)
curl -sS http://127.0.0.1:8200/v1/mcp/catalog \
  -H "Authorization: Bearer $TOKEN" | jq

# Servers the caller has installed
curl -sS http://127.0.0.1:8200/v1/mcp/servers \
  -H "Authorization: Bearer $TOKEN" | jq

# Install a 1-click catalog entry
curl -sS -X POST http://127.0.0.1:8200/v1/mcp/servers/install \
  -H "Authorization: Bearer $TOKEN" \
  -H 'content-type: application/json' \
  -d '{"slug":"linear"}' | jq

# Create a conversation, send a message, follow the SSE stream
CONV=$(curl -sS -X POST http://127.0.0.1:8200/v1/agent/conversations \
  -H "Authorization: Bearer $TOKEN" \
  -H 'content-type: application/json' \
  -d '{"title":"smoke"}' | jq -r .conversation_id)

RUN=$(curl -sS -X POST http://127.0.0.1:8200/v1/agent/runs \
  -H "Authorization: Bearer $TOKEN" \
  -H 'content-type: application/json' \
  -d "{\"conversation_id\":\"$CONV\",\"user_input\":\"Hi\"}" \
  | jq -r .run_id)

# -N keeps the connection open for SSE; ?after_sequence resumes mid-stream.
curl -N -H "Authorization: Bearer $TOKEN" \
  "http://127.0.0.1:8200/v1/agent/runs/$RUN/stream"

# Pause Linear for this chat (per-chat connector scope toggle)
curl -sS -X PATCH http://127.0.0.1:8200/v1/agent/conversations/$CONV/connectors \
  -H "Authorization: Bearer $TOKEN" \
  -H 'content-type: application/json' \
  -d '{"scopes":{"seed:linear":null}}' | jq
```

## Postman setup

Recommended: one collection that auto-mints + retries. Walk-through:

1. **Collection variables**: add `persona_slug` (default `sarah_acme`),
   `bearer` (empty), and `bearer_exp` (empty).
2. **Collection → Authorization**: type `Bearer Token`, value `{{bearer}}`.
   Every request inherits it.
3. **Collection → Pre-request Script**:

   ```js
   // Mint a fresh dev bearer if missing or close to expiry.
   const exp = pm.collectionVariables.get("bearer_exp");
   const now = Date.now() / 1000;
   if (!pm.collectionVariables.get("bearer") || !exp || +exp - now < 10) {
     pm.sendRequest(
       {
         url: "http://127.0.0.1:8200/v1/dev/identity/mint",
         method: "POST",
         header: { "content-type": "application/json" },
         body: {
           mode: "raw",
           raw: JSON.stringify({
             persona_slug:
               pm.collectionVariables.get("persona_slug") || "sarah_acme",
           }),
         },
       },
       (err, res) => {
         if (err) throw err;
         const body = res.json();
         pm.collectionVariables.set("bearer", body.bearer);
         pm.collectionVariables.set(
           "bearer_exp",
           Math.floor(Date.parse(body.expires_at) / 1000),
         );
       },
     );
   }
   ```

4. **Switch personas**: change `persona_slug`, then clear `bearer`. The
   next request re-mints.
5. **SSE streaming**: Postman's SSE handling is unreliable; use curl
   (`curl -N "..."`) for `/v1/agent/runs/{id}/stream`.

## Useful tricks

- The browser stores its bearer at `localStorage["enterprise.dev.bearer"]`
  and persona at `localStorage["enterprise.dev.persona_slug"]`. Copy the
  bearer into Postman to mirror exactly what the UI sees.
- If you start hitting 401s after a long break, the bearer may have
  expired — re-mint via `make dev-bearer` or by clearing `bearer` in
  Postman.
- Keep the recipes pinned to the **facade** (`:8200`). `:8100` and `:8000`
  expose `/internal/v1/*` routes that need the service token plus identity
  headers; that surface is for service-to-service traffic only.
- Production behavior is the same shape — only the minting source
  changes. Real bearers come from the IdP integration; verification is
  unchanged.

## Pointers

- Root README — `## Run Locally` section for starting the stack.
- `services/backend-facade/CLAUDE.md` — auth boundary rules.
- `services/backend/CLAUDE.md` — what the dev IdP is allowed to do.
- `apps/frontend/src/features/auth/` — how the AuthContext auto-mints.
