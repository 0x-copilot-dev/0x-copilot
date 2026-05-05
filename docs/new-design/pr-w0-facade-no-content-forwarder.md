# PR W0.2 — Facade No-Content Forwarder

> **Status:** Draft (PRD + Spec + Architecture)
> **Owner:** `services/backend-facade`
> **Size:** **XS**. ~30 LoC change. Zero migrations. Zero new contracts. Zero schema changes.
> **Reads alongside:** [`services/backend-facade/CLAUDE.md`](../../services/backend-facade/CLAUDE.md) · [`services/ai-backend/src/runtime_api/http/routes.py`](../../services/ai-backend/src/runtime_api/http/routes.py) (DELETE returns 204) · [PR W0.1 — Dev Identity Framework](./pr-w0-dev-identity-framework.md) (independent; can land in any order).

---

## 0 · TL;DR

`DELETE /v1/agent/conversations/{cid}` returns **HTTP 500** through the facade today. ai-backend correctly returns `204 No Content` with an empty body; the facade's [`_forward_json`](../../services/backend-facade/src/backend_facade/app.py) helper unconditionally calls `response.json()`, which raises `JSONDecodeError` on empty content.

The fix is two lines:

1. Make `_forward_json` HTTP-aware — if `204` or empty body, return `{}` instead of trying to parse JSON.
2. Collapse the duplicate `forward_json` and `forward_json_to_ai` helpers (90% identical, only differ in `base_url`) into one `forward_json(target=...)`.

Net change ≈ 30 LoC. Zero ai-backend / frontend impact. Closes Bug 2 from the QA report.

---

## 1 · PRD

### 1.1 Problem

Concrete trace from QA against `make dev`:

```
DELETE /v1/agent/conversations/566da7…  -> 500
   Internal Server Error

… backend-facade/.venv/.../starlette/_exception_handler.py … raise exc
… backend-facade/src/backend_facade/app.py:373 in delete_conversation
… backend-facade/src/backend_facade/app.py:962 in forward_json_to_ai
… backend-facade/src/backend_facade/app.py:1009 in _forward_json
… httpx/_models.py:832 in json
… python3.13/json/__init__.py:346 in loads
… python3.13/json/decoder.py:363 in raw_decode
json.decoder.JSONDecodeError: Expecting value: line 1 column 1 (char 0)
```

Two issues are visible in this stack:

1. **Empty response body crash.** `_forward_json` calls `response.json()` with no guard against `204 No Content`. ai-backend's DELETE route correctly returns 204 (per HTTP semantics for delete-side-effect operations) — the facade should pass that through, not crash on it.
2. **Helper duplication.** `forward_json` (target = backend) and `forward_json_to_ai` (target = ai-backend) are 90% identical — they differ only in `base_url`. The duplication is why one of them (`forward_json_to_ai`) lacks the `expect_json` flag that already exists on the other. The bug is what duplication looks like in production.

Soft-delete persistence works correctly — the upstream DELETE succeeds, the facade just fails to forward the empty response. From the QA run: `list_after_delete` returns the row hidden by default, and `?include_deleted=true` reveals it. The fix is purely facade-internal.

### 1.2 Goals

1. `DELETE /v1/agent/conversations/{cid}` returns **204 with empty body** end-to-end.
2. The facade forward helper handles `204 No Content` and `Content-Length: 0` responses without exception.
3. `forward_json` and `forward_json_to_ai` collapse to one helper. The `target` is a parameter, not a function-name suffix.
4. No regression in any existing JSON-returning route.

### 1.3 Non-goals

- Refactor of facade route handlers beyond the helper consolidation.
- Streaming endpoints (`/stream` uses `StreamingResponse`, doesn't go through `_forward_json`).
- Any change to ai-backend, backend, or frontend.
- Any change to error handling for upstream 4xx / 5xx (existing `HTTPException` re-raise is fine).
- Any new contract in `packages/api-types`.

### 1.4 Acceptance criteria

| #    | Criterion                                                                                                                                             | Verified by                                        |
| ---- | ----------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------- |
| AC-1 | `DELETE /v1/agent/conversations/{cid}` returns `204` with empty body in `make dev`.                                                                   | curl + integration test                            |
| AC-2 | Unit test: `_forward_json` returns `{}` when upstream responds `204` or `Content-Length: 0`.                                                          | `tests/unit/test_forwarder.py`                     |
| AC-3 | Unit test: `_forward_json` returns `None` when called with `expect_json=False` and upstream responds 2xx with empty body.                             | `tests/unit/test_forwarder.py`                     |
| AC-4 | One helper exists. `forward_json(app, method, path, *, target: Literal["backend","ai_backend"], …)`. `forward_json_to_ai` is gone.                    | grep + ruff                                        |
| AC-5 | All ~40 existing call sites compile and pass tests after the rename.                                                                                  | full facade test suite                             |
| AC-6 | `DELETE /v1/agent/history` (which already returns 204) also no longer 500s.                                                                           | `tests/integration/test_history_delete.py`         |
| AC-7 | The QA flow `create conversation → DELETE → list (default = hidden) → list (?include_deleted=true) → restore → list` succeeds end-to-end with no 5xx. | `tests/integration/test_conversation_lifecycle.py` |

### 1.5 Risks / mitigations

| Risk                                                           | Mitigation                                                                                                                                                             |
| -------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Collapsing two helpers breaks an esoteric caller.              | Type-checked refactor (mypy clean), full facade test suite green before merge. Ruff catches missing `target=` kwarg.                                                   |
| Future routes expect a `dict` but get `None`.                  | Helper returns `{}` for 2xx no-content **by default** (`expect_json=True`); callers can opt into `None` with `expect_json=False`. Defaults preserve existing behavior. |
| Some upstream returns 200 with empty body (rare but possible). | Treated identically to 204 — `{}` (or `None` if `expect_json=False`). Existing `HTTPException(502)` for "200 with non-empty non-JSON body" is unchanged.               |
| Merge conflicts with PR W0.1 (which also touches `app.py`).    | The two PRs touch disjoint sections (W0.1 deletes auth blocks, W0.2 collapses forwarder). Trivial to land in either order.                                             |

### 1.6 Unit testing requirements

- Forwarder unit test for `204` path → returns `{}`.
- Forwarder unit test for `Content-Length: 0` path → returns `{}`.
- Forwarder unit test for `expect_json=False` + empty body → returns `None`.
- Forwarder unit test for normal JSON path → unchanged behavior.
- Integration test: full DELETE flow against `make dev` (or test stack).
- Regression test: at least one GET, one POST, one PATCH route through the renamed helper to confirm no caller broke.

---

## 2 · Spec

### 2.1 Helper signature

```python
# services/backend-facade/src/backend_facade/app.py
from typing import Literal, Mapping

ForwardTarget = Literal["backend", "ai_backend"]

async def forward_json(
    app: FastAPI,
    method: str,
    path: str,
    *,
    target: ForwardTarget,
    params: Mapping[str, object] | None = None,
    json: Mapping[str, object] | None = None,
    expect_json: bool = True,
    identity: AuthenticatedIdentity,
) -> dict[str, object] | None:
    """Forward an authenticated request to the named upstream service.

    Returns the parsed JSON body (or {} for 2xx no-content responses).
    Pass expect_json=False to receive None instead of {} on no-content.
    """
    base_url = (
        settings_for(app).backend_url
        if target == "backend"
        else settings_for(app).ai_backend_url
    )
    return await _forward_json(
        base_url=base_url,
        method=method,
        path=path,
        params=params,
        json=json,
        expect_json=expect_json,
        headers=_outbound_headers(identity),
    )
```

`forward_json_to_ai` is **deleted**. Every call site updates:

```python
# Before
await forward_json_to_ai(app, "GET", path, params=identity.scoped_params(), identity=identity)

# After
await forward_json(app, "GET", path, target="ai_backend", params=identity.scoped_params(), identity=identity)
```

### 2.2 No-content handling inside `_forward_json`

The single behavioral change. New branch added at the top of the post-response section:

```python
async def _forward_json(
    *,
    base_url: str,
    method: str,
    path: str,
    params: Mapping[str, object] | None = None,
    json: Mapping[str, object] | None = None,
    expect_json: bool = True,
    headers: Mapping[str, str] | None = None,
) -> dict[str, object] | None:
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.request(
            method,
            f"{base_url}{path}",
            params=params,
            json=json,
            headers=headers,
        )
    if response.status_code >= 400:
        raise HTTPException(response.status_code, _upstream_error_detail(response))

    # NEW — HTTP-aware no-content handling. Some upstreams (DELETE,
    # idempotent POST/PUT) correctly return 204 No Content with empty
    # body. Calling response.json() on an empty body raises
    # JSONDecodeError, which the bug-2 trace caught in production.
    if (
        response.status_code == 204
        or response.headers.get("content-length") == "0"
        or not response.content
    ):
        return None if not expect_json else {}

    payload = response.json()
    if not isinstance(payload, dict):
        raise HTTPException(502, "Upstream returned non-object JSON.")
    return payload
```

The three-condition check is intentionally explicit:

- `status_code == 204` is the canonical signal.
- `content-length: 0` covers proxies that strip the status code or rewrite to 200.
- `not response.content` is a final defense for buggy upstreams that return 200 with empty body.

### 2.3 Caller updates

Every facade route file replaces calls. Examples:

```python
# Before
@app.delete("/v1/agent/conversations/{conversation_id}")
async def delete_conversation(...):
    await forward_json_to_ai(
        app, "DELETE",
        f"/v1/agent/conversations/{conversation_id}",
        params=identity.scoped_params(),
        identity=identity,
    )
    return Response(status_code=204)

# After
@app.delete("/v1/agent/conversations/{conversation_id}")
async def delete_conversation(...):
    await forward_json(
        app, "DELETE",
        f"/v1/agent/conversations/{conversation_id}",
        target="ai_backend",
        params=identity.scoped_params(),
        identity=identity,
    )
    return Response(status_code=204)
```

The DELETE handler keeps `Response(status_code=204)` — the helper now returns `{}` (which we ignore), and the route surfaces 204 to the client. No double-body. No body-shape inconsistency.

The full migration is a mechanical rename across `services/backend-facade/src/backend_facade/app.py` (~40 sites) plus any sibling files. `forward_json` already accepted `target=` implicitly via two functions; we now make it explicit.

### 2.4 Files changed

| Path                                                                       | Change                                                                                                                       | LoC delta |
| -------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------- | --------- |
| `services/backend-facade/src/backend_facade/app.py`                        | Add `target` param to `forward_json`. Delete `forward_json_to_ai`. Add 204 branch in `_forward_json`. Update ~40 call sites. | +20 / −25 |
| `services/backend-facade/tests/unit/test_forwarder.py` _(new)_             | Unit tests for 204 / Content-Length: 0 / expect_json=False / normal JSON.                                                    | +50       |
| `services/backend-facade/tests/integration/test_conversation_lifecycle.py` | Add: DELETE returns 204 end-to-end; soft-delete + restore round-trip.                                                        | +30       |
| `services/backend-facade/tests/integration/test_history_delete.py` _(new)_ | Bulk history delete returns 204.                                                                                             | +20       |

**Total:** ≈ +120 / −25 LoC including tests; ≈ −5 LoC excluding tests.

### 2.5 Streaming impact — none

The stream endpoints (`GET /v1/agent/runs/{id}/stream`, `GET /v1/agent/runs/{id}/events`) use `StreamingResponse` — they do not call `_forward_json` and are entirely untouched.

### 2.6 Frontend / api-types impact — none

No contract change. `DELETE /v1/agent/conversations/{cid}` already returned 204 in the OpenAPI surface; the bug was just that the actual response was 500. Frontend code that already expects 204 will start working. No `packages/api-types` change.

### 2.7 Edge cases

| Case                                                                  | Behavior                                                                                              |
| --------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------- |
| Upstream returns 200 with empty body                                  | Treated identically to 204 → `{}` (or `None` if `expect_json=False`).                                 |
| Upstream returns 200 with `Content-Length: 0` but non-empty `content` | The `content-length` header takes precedence — `{}` is returned. (Defensive against header lies.)     |
| Upstream returns 200 with non-JSON body                               | Existing `HTTPException(502, "Upstream returned non-object JSON.")` — unchanged.                      |
| Upstream returns 204 with a JSON body (RFC 7231 forbids this)         | The `status_code == 204` branch wins. Body is silently discarded — correct per HTTP.                  |
| `expect_json=False` + 200 with JSON body                              | Existing call sites already used `expect_json=False` only for fire-and-forget routes. Returns `None`. |

---

## 3 · Architecture

### 3.1 Where this lives

Pure facade-internal change. `services/ai-backend`, `services/backend`, `apps/frontend`, `packages/*` — all untouched.

### 3.2 Why this is small

- The bug surface is a single function (`_forward_json`).
- The DRY win (`forward_json` + `forward_json_to_ai` → one) is a mechanical rename: ~40 call sites change from `forward_json_to_ai(…, …)` to `forward_json(…, …, target="ai_backend")`.
- Net LoC ≈ −5 (excluding tests).

### 3.3 No third-party / new middleware

| Considered                           | Rejected because                                                                                                                                                                           |
| ------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Replace `httpx` with `aiohttp`       | Out of scope; just one bug.                                                                                                                                                                |
| Use FastAPI's `Response` adapter     | The helper is already adapter-shaped. Switching frameworks is a refactor, not a fix.                                                                                                       |
| Add a generic upstream-proxy library | The facade is intentionally **not** a generic reverse proxy — it's a small set of typed forwarders with auth header injection. Generic libraries cost more than the helper they'd replace. |

We **do** use:

- `httpx.Response.status_code`, `.headers`, `.content`, `.json()` — already imported.
- `typing.Literal` — already used elsewhere in the codebase.
- `Mapping` — already used elsewhere.

No new imports.

### 3.4 Streaming impact — explicitly **none**

The streaming endpoints (`/stream`, `/events`) use `StreamingResponse` and bypass `_forward_json`. They are untouched. The bug only ever manifested on `_forward_json` callers (DELETE, bulk-history-delete, etc.).

### 3.5 Agent harness / DB schema impact — none

The bug is at the facade ↔ ai-backend HTTP boundary. ai-backend's behavior is already correct (it returns 204 per HTTP semantics). No event types change. No tables change. `runtime_events`, `conversations.deleted_at`, `runtime_audit_log` — all unchanged.

### 3.6 DRY — what we delete

- `forward_json_to_ai` function (one of two near-identical forwarders).
- The implicit assumption that "every upstream response has a JSON body".

### 3.7 DRY — what we reuse

- `_forward_json` body (one place to change for any future cross-cutting forward concern: retries, backoff, timeouts).
- `_outbound_headers(identity)` — unchanged.
- Existing `HTTPException` propagation for 4xx / 5xx upstream — unchanged.

### 3.8 Sequence — the fixed DELETE

```
client → DELETE /v1/agent/conversations/{cid}    (facade)
   facade verifies bearer (PR W0.1) → identity
   facade.forward_json(app, "DELETE", path, target="ai_backend", identity=...)
     → _forward_json
       → httpx DELETE upstream
       ← upstream 204 No Content (empty body)
       → status_code == 204 → return {}
   facade returns Response(status_code=204) to client
```

### 3.9 Test plan

- **Unit** (`tests/unit/test_forwarder.py`)
  - `test_204_returns_empty_dict`
  - `test_content_length_zero_returns_empty_dict`
  - `test_empty_content_returns_empty_dict`
  - `test_expect_json_false_returns_none_on_no_content`
  - `test_normal_json_path_unchanged`
  - `test_target_routes_to_correct_base_url`
- **Integration**
  - `test_delete_conversation_returns_204`
  - `test_delete_history_returns_204`
  - `test_full_lifecycle_create_delete_list_restore_no_5xx`
- **Regression**: re-run the full facade suite — every existing GET/POST/PATCH route passes after the helper rename.

### 3.10 Rollout

Single PR. No flag. Merge.

Pre-merge:

- `npm run typecheck --workspace @enterprise-search/api-types` — confirms no contract drift (none expected).
- Full facade test suite green.
- QA flow reproducible via `python3 /tmp/qa_run_inspect.py` against `make dev` returns 204 for the DELETE step.

Post-merge:

- No CLAUDE.md changes required.
- No README changes required.

### 3.11 Open questions

| #   | Question                                             | Default                                                                                                                |
| --- | ---------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------- |
| Q-1 | Should the helper also handle `205 Reset Content`?   | **No** — not used anywhere in our routes. Add when a real caller needs it.                                             |
| Q-2 | Should the helper retry on transient network errors? | **No** — that's a separate concern, not this PR. Existing `httpx.AsyncClient(timeout=30)` is the only resilience knob. |
| Q-3 | Should we add a `head_request` / `trace` helper too? | **No** — only build helpers we have callers for.                                                                       |

---

## 4 · Acceptance checklist

- [ ] `_forward_json` returns `{}` for `204` / `Content-Length: 0` / empty body when `expect_json=True`.
- [ ] `_forward_json` returns `None` for the same conditions when `expect_json=False`.
- [ ] `forward_json_to_ai` deleted; all callers updated to `forward_json(…, target="ai_backend")`.
- [ ] Unit tests cover the four no-content cases.
- [ ] `DELETE /v1/agent/conversations/{cid}` returns `204` end-to-end against `make dev`.
- [ ] `DELETE /v1/agent/history` returns `204` end-to-end.
- [ ] Full conversation lifecycle E2E passes (create → DELETE → list-default-hidden → list-include-deleted-shows → restore → list-shown).
- [ ] No regression in any other facade route.
- [ ] mypy + ruff clean.

---

## 5 · References

- [`services/backend-facade/src/backend_facade/app.py`](../../services/backend-facade/src/backend_facade/app.py) — `forward_json`, `forward_json_to_ai`, `_forward_json`, DELETE handlers.
- [`services/backend-facade/CLAUDE.md`](../../services/backend-facade/CLAUDE.md) — facade boundaries.
- [`services/ai-backend/src/runtime_api/http/routes.py`](../../services/ai-backend/src/runtime_api/http/routes.py) — DELETE handler that correctly returns 204.
- [PR 1.6 — Workspace defaults + conversation lifecycle](./pr-1.6-workspace-defaults-conversation-lifecycle.md) — introduced the DELETE/restore endpoints.
- [PR W0.1 — Dev Identity Framework](./pr-w0-dev-identity-framework.md) — sibling fix; lands independently.
- The original Bug 2 traceback: `json.decoder.JSONDecodeError: Expecting value: line 1 column 1 (char 0)` at `app.py:1009`.
