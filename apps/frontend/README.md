# Frontend

Web work surface for Enterprise Search. It talks to `backend-facade`; it must
not call `backend` or `ai-backend` directly.

## Local Environment

The frontend has its own Node workspace environment. It does not use a Python
venv; `requirements.txt` is intentionally empty to make that explicit.

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

Build the frontend image from the repository root so Docker can copy the shared
API types package:

```bash
docker build -f apps/frontend/Dockerfile -t enterprise-search-frontend .
```

## Boundary Rule

Do not import service implementation code into the frontend. Use
`@enterprise-search/api-types` for stable contracts and call `backend-facade`
over HTTP/SSE.
