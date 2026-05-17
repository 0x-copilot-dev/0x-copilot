# Phase 7C — Tier-2 Review Pipeline

Status: draft · Phase: 7 (Tier-2 sharing & server-side registry) · Owner: 7C agent

Implements the admin-facing review queue for agent-generated SaaS renderer
adapters (tier-2) that have met the Phase 6C success criteria and been
submitted by 7B for promotion to the shared registry (7A). The reviewer
approves, rejects, or requests-changes against a candidate without ever
seeing tenant-private data.

Parent reading: [PRD.md](../PRD.md) §3.4 (renderer tiers), §5 row 7C
(scope), §9.5.3 (sharing model), §9.5.2 (security model), R8 (community
trust model).

## 1. Scope

In:

- `apps/frontend/src/admin/adapter-review/*` — admin queue + detail view +
  smoke-render preview against synthetic samples.
- `services/backend-facade/src/backend_facade/adapter_review_routes.py` —
  facade proxy to 7A's `/internal/v1/adapter_registry/*` endpoints.
- Route mount under `/admin/adapter-review` (queue) and
  `/admin/adapter-review/:candidateId` (detail), gated by admin role.

Out:

- The registry itself (7A — backend storage, decision audit, promote/
  demote). 7C only consumes its public read/write surface.
- Client-side harvest / download / opt-out (7B).
- Sandbox primitives — Phase 6A's `Tier2Loader` is reused. 7C calls it
  inside an iframe-sandbox with a no-network CSP for review-time preview.

## 2. Contract with 7A (assumed)

7A owns these `/internal/v1/adapter_registry/*` routes; the facade
proxies them at `/v1/admin/adapter_registry/*`. Until 7A merges, the
frontend tests stub these on the transport boundary.

```
GET  /v1/admin/adapter_registry/candidates
  ?status=submitted|in-review|changes-requested|approved|rejected (optional)
  ?layout=form|table|kanban|definition-list (optional)
  ?scheme=<string> (optional)
  ?cursor=<string> (optional)
  ?limit=<int 1..200, default 50>
  → 200 {
      candidates: [{
        candidate_id: string,
        scheme: string,
        layout_template: "form"|"table"|"kanban"|"definition-list",
        origin_tenant_redacted: string,   # tenant-anonymized ID (e.g. "tenant_<hash8>")
        generator_model: string,
        submitted_at: string,             # ISO 8601
        status: "submitted"|"in-review"|"changes-requested"|"approved"|"rejected",
        session_count: number,            # successful sessions before submission
      }],
      next_cursor: string | null,
      has_more: boolean,
    }

GET  /v1/admin/adapter_registry/candidates/{candidate_id}
  → 200 {
      candidate_id, scheme, layout_template, origin_tenant_redacted,
      generator_model, submitted_at, status,
      candidate_source: string,           # the anonymized adapter source (text)
      schema_version: number,
      history: [{
        decided_at: string,
        decided_by_user_id: string,
        action: "approve"|"reject"|"request-changes",
        notes: string,
      }],
    }

POST /v1/admin/adapter_registry/candidates/{candidate_id}/decisions
  body: {
    action: "approve" | "reject" | "request-changes",
    notes: string,
  }
  → 200 {
      candidate_id, status, decided_at, decided_by_user_id,
      action, notes,
    }
```

7A enforces the `admin:adapter_registry_review` scope on every route.
The facade does **not** duplicate the check (same pattern as
`audit_routes.py` — defence-in-depth would race with role updates).

## 3. Compliance rules

**Reviewer never sees tenant-private data.** The candidate's tenant-of-
origin is redacted upstream (7A produces a hashed handle, not the org
id). The candidate's source string is assumed to be tenant-anonymized at
submit time (7B's responsibility — strip identifiers, sample state,
hard-coded URLs). 7C's UI:

- Never displays the raw `org_id` of the originating tenant.
- Never fetches real tenant state to drive the smoke render. The preview
  pane mounts the candidate against a **synthetic state** built by
  `SyntheticStateFactory.ts` (well-known dummy values — `acme.example.com`,
  `Test User`, `2026-01-01`).
- The synthetic state is asserted by test to contain no real-PII
  patterns (no `@` outside `example.com`, no SSN-shaped numbers, etc.).

**Sandboxed preview.** The candidate runs inside an `<iframe>` whose
`sandbox` attribute is `allow-scripts` only (no `allow-same-origin`, no
`allow-popups`, no `allow-forms`). The iframe document is built with a
strict CSP meta tag:

```
default-src 'none';
script-src 'unsafe-inline';
style-src 'unsafe-inline';
connect-src 'none';
img-src 'none';
font-src 'none';
frame-src 'none';
```

`connect-src 'none'` forbids `fetch` / `XMLHttpRequest` / `WebSocket` /
`EventSource`. `img-src 'none'` forbids `<img>` exfil. Combined with
no-same-origin, the iframe cannot reach the parent's storage, cookies,
network, or DOM.

**Fail closed on adapter throw.** The host wraps the candidate's
`renderCurrent` / `renderDiff` call in try/catch. Any throw collapses
the preview into an error placeholder — never falls back silently to
tier-3. The reviewer must see that the candidate broke.

**Audit trail.** Every decision is written to the backend audit table
(7A's responsibility). The detail view shows the candidate's prior
decisions inline (the `history` array on the GET response) so the
reviewer sees the full chain before deciding.

## 4. Files

```
apps/frontend/src/admin/adapter-review/
├── AdapterReviewQueue.tsx        # table: candidates + filters
├── AdapterReviewDetail.tsx       # 3-pane: source / template+state / preview
├── AdapterPreview.tsx            # iframe-sandboxed render of candidate
├── SyntheticStateFactory.ts      # synthetic state per LayoutTemplate
├── adapterReviewApi.ts           # frontend API client
├── types.ts                      # frontend types mirroring 7A contract
├── index.tsx                     # route table for /admin/adapter-review/*
├── AdapterReviewQueue.test.tsx
├── AdapterReviewDetail.test.tsx
├── AdapterPreview.test.tsx
└── SyntheticStateFactory.test.ts

services/backend-facade/src/backend_facade/
└── adapter_review_routes.py      # /v1/admin/adapter_registry/* proxies

apps/frontend/src/app/
├── App.tsx                       # mounts /admin route when admin role
├── HashRouter.ts                 # adds "admin" route shape
└── routes.ts                     # AppRoute union widened
```

## 5. Route mount

Hash router gains `/admin/adapter-review` and
`/admin/adapter-review/<candidate_id>` paths. `App.tsx` renders the
admin tree only when `identity.roles` includes `"admin"` — otherwise
the route resolves back to chat (defence-in-depth; the facade enforces
the real gate).

## 6. Tests

Each component is paired with a vitest + jsdom test:

- `AdapterReviewQueue.test.tsx` — renders rows from the API, sorts by
  `submitted_at`, filters by status / layout / scheme, opens detail on
  click.
- `AdapterReviewDetail.test.tsx` — fetches detail, renders source pane,
  template-and-state pane, preview pane, fires the correct
  approve/reject/request-changes API call with notes.
- `AdapterPreview.test.tsx` — preview fails closed when the candidate
  source throws; preview iframe carries the documented CSP.
- `SyntheticStateFactory.test.ts` — output contains no real-PII
  patterns (regex check for non-example email domains and SSN-shaped
  digit runs); each `LayoutTemplate` returns both `state` and `diff`.
- `adapterReviewApi.test.ts` — sends correct queries / bodies via
  `httpGet` / `httpPost`.

## 7. Out-of-scope

- Visual regression on the preview (PRD §9.5.1 defers full-screenshot
  diffing post-Phase 7).
- Editing the candidate source (reviewers can only approve / reject /
  request-changes — the agent regenerates on request-changes).
- Bulk decisions (single-candidate flow only in v1).

## 8. Exit criteria

- Admin user lands on `/admin/adapter-review`, sees the candidate
  queue, opens a candidate, sees source + synthetic state + preview,
  approves with notes, sees the decision in the candidate's history on
  the next fetch.
- Non-admin user is redirected back to chat (the facade returns 403;
  the UI hides the route from the navigation).
- A deliberately-throwing candidate source produces an error placeholder
  in the preview pane — never silently degrades.
- The synthetic state under every supported `LayoutTemplate` passes the
  no-real-PII test.
