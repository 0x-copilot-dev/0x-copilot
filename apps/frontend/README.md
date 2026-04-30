# Frontend

Web work surface for Enterprise Search. It talks to `backend-facade`; it must
not call `backend` or `ai-backend` directly.

See `ARCHITECTURE.md` for runtime boundaries and `TESTING.md` for the current
testing strategy.

## Local Environment

The frontend has its own Node workspace environment. It does not use a Python
venv or any backend service venv; `requirements.txt` is intentionally empty to
make that explicit.

```bash
cd enterprise-search
npm install
npm run dev --workspace @enterprise-search/frontend
```

Typecheck and build:

```bash
npm run typecheck --workspace @enterprise-search/frontend
npm run build --workspace @enterprise-search/frontend
```

Build the frontend image from the repository root so Docker can copy workspace
packages used by the app:

```bash
docker build -f apps/frontend/Dockerfile -t enterprise-search-frontend .
```

The Docker build context must include every workspace package imported by the
frontend, including `packages/api-types` and `packages/design-system`, before
`npm ci` runs.

## Routing

Local Vite development proxies `/v1/*` to `backend-facade` on
`http://127.0.0.1:8200`.

The nginx image only serves static frontend assets and the SPA fallback. It does
not proxy `/v1/*`; production ingress must route those requests to
`backend-facade`.

## API Layer

Use `src/api/*` for new HTTP and SSE clients. Shared request and response shapes
should come from `@enterprise-search/api-types`. Root-level API helpers are
legacy compatibility files and should not gain new feature callers.

## Boundary Rule

Do not import service implementation code into the frontend. Use
`@enterprise-search/api-types` for stable contracts and call `backend-facade`
over HTTP/SSE.

This app owns its own `package.json`, Vite/build config, Dockerfile, local
workspace dependency environment, and deploy path.
