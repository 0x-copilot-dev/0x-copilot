# Self-hosting 0xCopilot

Run the whole product on one server with Docker Compose. Four published images
(`backend`, `ai-backend`, `backend-facade`, `frontend`) sit behind one nginx
gateway, backed by a single Postgres. Everything is env-driven — no code changes
per install.

## One-line install

```bash
curl -fsSL https://raw.githubusercontent.com/0x-copilot-dev/0x-copilot/main/deploy/self-host/install.sh | bash
```

The installer:

1. checks for Docker + the Compose v2 plugin,
2. creates `~/0x-copilot/`, downloads `docker-compose.yml`,
3. generates a `.env` with strong random secrets (only on first run — re-running
   never rewrites your secrets or orphans the database),
4. `docker compose pull` then `up -d`,
5. waits for the gateway to report healthy and prints the URL.

Then open **http://localhost:8080/**, connect a wallet (or enable Google — see
below), and add a model provider key in **Settings → Models & keys → Provider
keys**.

> The images are published to GHCR. If the packages are still **private**, log in
> first, then re-run the installer:
>
> ```bash
> echo <GITHUB_PAT_with_read:packages> | docker login ghcr.io -u <github-username> --password-stdin
> ```
>
> This login is only needed while the images are private; once the packages are
> public, `docker compose pull` works without authenticating.

## Manual install

```bash
mkdir -p ~/0x-copilot && cd ~/0x-copilot
curl -fsSLO https://raw.githubusercontent.com/0x-copilot-dev/0x-copilot/main/deploy/self-host/docker-compose.prod.yml
curl -fsSLO https://raw.githubusercontent.com/0x-copilot-dev/0x-copilot/main/deploy/self-host/.env.example
cp .env.example .env
# fill in every REPLACE_ME (see below), then:
docker compose -f docker-compose.prod.yml up -d
```

## Configuration (`.env`)

| Variable                                                  | Required | Notes                                                                                                                |
| --------------------------------------------------------- | -------- | -------------------------------------------------------------------------------------------------------------------- |
| `ENTERPRISE_AUTH_SECRET`                                  | yes      | Session bearer HMAC. `openssl rand -hex 64`. Identical across services.                                              |
| `ENTERPRISE_SERVICE_TOKEN`                                | yes      | Service-to-service lane. `openssl rand -hex 32`. Identical across services.                                          |
| `MCP_TOKEN_VAULT_SECRET`                                  | yes      | Local Fernet vault key (≥ 32 chars). `openssl rand -hex 32`.                                                         |
| `AUDIT_HMAC_KEY`                                          | yes      | Tamper-evident audit chain key, hex ≥ 32 bytes. `openssl rand -hex 32`.                                              |
| `POSTGRES_PASSWORD`                                       | yes      | Use a URL-safe value. `openssl rand -hex 24`. Changing it after first boot orphans the volume.                       |
| `SIWE_ORIGIN`                                             | yes      | Exact public origin the browser loads from (`scheme://host[:port]`, no trailing slash). Must match for wallet login. |
| `SIWE_ALLOWED_CHAIN_IDS`                                  | no       | Comma-separated chain ids. Default `1,8453,42161,4663` (Ethereum, Base, Arbitrum One, Robinhood Chain).              |
| `GOOGLE_OAUTH_CLIENT_ID` / `GOOGLE_OAUTH_CLIENT_SECRET`   | no       | Enables "Continue with Google". Register redirect `${SIWE_ORIGIN}/v1/auth/oidc/google/callback`.                     |
| `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` / `GOOGLE_API_KEY` | no       | BYOK — users add keys in Settings; set here only for a shared fallback.                                              |
| `OPENROUTER_API_KEY`                                      | no       | BYOK OpenRouter — users add keys in Settings; set here only for a shared fallback.                                   |
| `LOCAL_MODELS_ENABLED`                                    | no       | `true` to show **Settings → Local models** (download an HF GGUF + run via Ollama). Requires Ollama (see below).      |
| `OLLAMA_BASE_URL`                                         | no       | OpenAI-compatible Ollama endpoint. Default `http://host.docker.internal:11434/v1` (a host-installed Ollama).         |
| `RUNTIME_LOCAL_MODELS_MANAGE_RUNTIME`                     | n/a      | Pinned `false` in the compose file and not settable from `.env` — see "Why this stack never starts Ollama" below.    |
| `GATEWAY_PORT`                                            | no       | Host port for the gateway. Default `8080`.                                                                           |
| `IMAGE_TAG`                                               | no       | Image tag to run. Default `latest`; pin to a commit sha for reproducibility.                                         |
| `AI_BACKEND_WORKERS`                                      | no       | gunicorn workers for the ai-backend API. Default `2`.                                                                |

`ENTERPRISE_AUTH_SECRET` and `ENTERPRISE_SERVICE_TOKEN` **must be byte-identical**
across `backend`, `ai-backend`, and `backend-facade` — they sign and verify the
same bearers and the internal service lane. The compose file wires this for you
from a single `.env`.

### Local models (Ollama) — optional

To let users download a Hugging Face GGUF and run it locally, install
[Ollama](https://ollama.com/download) on the host, then set
`LOCAL_MODELS_ENABLED=true`. The container reaches the host Ollama via
`OLLAMA_BASE_URL` (default `http://host.docker.internal:11434/v1`); on Linux add
`extra_hosts: ["host.docker.internal:host-gateway"]` to the `ai-backend` service
(or point `OLLAMA_BASE_URL` at wherever Ollama runs). Downloaded models appear in
the chat model picker. Left off, **Settings → Local models** is hidden.

#### Why this stack never starts Ollama

`RUNTIME_LOCAL_MODELS_MANAGE_RUNTIME` is pinned to `false` in
`docker-compose.prod.yml` and is deliberately not wired to `.env`. That flag
authorises `ai-backend` to look for the Ollama **binary on its own filesystem**
and spawn it. Here `ai-backend` is a container and `OLLAMA_BASE_URL` points at
the **host** — so the container cannot see the host's binary, and a spawn would
only start a second Ollama inside the container that nothing ever talks to.

With the flag off, `GET /v1/local-models/status` reports
`runtime_state: "unknown"` rather than claiming a host filesystem it cannot
inspect, and the UI shows install/start instructions instead of a "Restart
Ollama" button that could not work. Start and stop Ollama on the host yourself
(`ollama serve`, or your service manager). Only the desktop app sets this flag
true — there `ai-backend` is a child process on the user's own machine talking
to a loopback Ollama.

### Going public (TLS + a domain)

Put the gateway behind your own reverse proxy / load balancer terminating TLS,
forwarding to `GATEWAY_PORT`. Set `SIWE_ORIGIN=https://your.domain` (wallet login
binds the EIP-4361 message to this exact origin), then `docker compose up -d`.

## What runs

| Service                                  | Image                | Role                                                                          |
| ---------------------------------------- | -------------------- | ----------------------------------------------------------------------------- |
| `postgres`                               | `postgres:17`        | Two logical DBs: `atlas_backend` + `atlas_ai`. Named volume `pgdata`.         |
| `backend-migrate` / `ai-backend-migrate` | backend / ai-backend | One-shot `scripts/migrate.py apply`, gated on Postgres healthy.               |
| `backend`                                | `…-backend`          | Core backend on the `single_user_desktop` Postgres composition root.          |
| `ai-backend` + `ai-backend-worker`       | `…-ai-backend`       | Agent runtime API + queued worker, Postgres store + LISTEN/NOTIFY bus.        |
| `backend-facade`                         | `…-backend-facade`   | The only surface apps call; proxies `/v1/*`.                                  |
| `frontend`                               | `…-frontend`         | Static SPA.                                                                   |
| `gateway`                                | `nginx:1.29-alpine`  | Ingress: serves the SPA, proxies `/v1` → facade. Published on `GATEWAY_PORT`. |

## Why the `single_user_desktop` deployment profile

A public multi-user self-host wants `allow_self_signup=True`. Only two profiles
carry that toggle: `saas_multi_tenant` and `single_user_desktop`. We use
**`single_user_desktop`**, and the reasoning is deliberate:

- **It is the only profile with a shipping Postgres composition root.** The
  backend image's default entrypoint (`backend_app.app:app`) wires _in-memory_
  stores; the only env-driven Postgres wiring in the repo is
  `backend_app.desktop_app:app`, which this compose points uvicorn at. There is
  no `saas_multi_tenant` composition root in the codebase today.
- **It boots without cloud infrastructure.** `saas_multi_tenant` sets
  `require_kms_token_vault=True` and `siem_export_required=True`; on the stock
  image that means the token vault fails closed (no KMS adapter ships), which
  drops OIDC/Google login, and there is no SIEM sink. `single_user_desktop` uses
  a local Fernet vault and skips SIEM/RLS — so a plain server can run it.
- **It persists.** Identity, sessions, settings, provider keys, and MCP
  registrations are Postgres-backed, so data survives restarts (verified below).
- **It keeps self-signup on.** Users create their own workspace via Google or
  wallet on first visit — exactly the self-host onboarding we want.

**Trade-off, stated honestly:** this profile does **not** enforce row-level
security, KMS-managed secrets, or SIEM export. The trust boundary is the server
instance, not the database row. That is appropriate for a personal or
trusted-team self-host. It is **not** a hostile-multi-tenant SaaS posture — that
would need a `saas_multi_tenant` composition root plus KMS + SIEM adapters that
are not yet in this repository.

## Data & lifecycle

- All state lives in the `pgdata` named volume.
- `docker compose down` stops the stack and keeps your data.
- `docker compose down -v` **deletes the volume** — every conversation, run,
  user, and setting is gone.
- Migrations run as one-shot containers before the services start; on upgrade
  (`docker compose pull && up -d`) they re-apply any new migrations first.

## Verified

Built from source and brought up end-to-end with real images (see
`docker-compose.local-build.yml`):

- gateway `/v1/health` returns 200 on the published port;
- `POST /v1/auth/siwe/nonce` through the gateway returns 200 (proves
  gateway → facade → backend);
- a full SIWE wallet login mints a session, a `PUT /v1/me/preferences`
  (`theme: light`) write survives `docker compose restart backend`, and the same
  bearer still authenticates afterwards — proving Postgres-backed persistence,
  not in-memory.

### Reproduce the local build + verification

```bash
cd deploy/self-host
cp .env.example .env   # fill secrets, set IMAGE_TAG=local
docker compose -f docker-compose.prod.yml -f docker-compose.local-build.yml build
docker compose -f docker-compose.prod.yml -f docker-compose.local-build.yml up -d
curl -fsS http://localhost:${GATEWAY_PORT:-8080}/v1/health
# teardown, including data:
docker compose -f docker-compose.prod.yml -f docker-compose.local-build.yml down -v
```

## Troubleshooting

### `backend` / `ai-backend` crash-loop with exit code 132 on Apple Silicon

Exit 132 is `SIGILL`. On some **Apple Silicon Macs running Docker Desktop**,
OpenSSL (bundled in the `cryptography` wheel) probes for an ARM crypto
instruction that Docker Desktop's virtual CPU doesn't expose, then executes it
and traps. This is a Docker-Desktop-VM quirk, **not** an issue on real Linux
servers — the published images are `linux/amd64` and run cleanly on amd64 hosts,
and native arm64 servers (AWS Graviton, Ampere) are unaffected.

If you hit it while self-hosting on an Apple Silicon Mac, add this to `.env`
(it disables OpenSSL's optional hardware-crypto instructions):

```bash
OPENSSL_armcap=0
```

Then reference it in the `backend`, `ai-backend`, `ai-backend-worker`, and
`backend-facade` service `environment:` blocks, or run the amd64 images with
`--platform linux/amd64`. Do not set this on production Linux hosts — it needlessly
turns off hardware crypto acceleration.
