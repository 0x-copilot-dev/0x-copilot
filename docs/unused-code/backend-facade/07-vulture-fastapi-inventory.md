# Vulture, FastAPI, and the “wall of unused” report

Running Vulture at **default** or **60%** confidence on `services/backend-facade/src` produces **dozens to hundreds of lines** of output. Almost all of it is **not removable dead code**. This page classifies the noise so audits stay actionable.

## Recommended command (from `services/backend-facade`)

```bash
.venv/bin/pip install -q vulture
.venv/bin/vulture src \
  --min-confidence 60 \
  --ignore-decorators "@app.get,@app.post,@app.patch,@app.delete,@app.put,@app.api_route"
```

With route decorators ignored, Vulture still reports the items below — **by design**.

## Category A — FastAPI / Pydantic field “variables” (false positives)

**Symptom:** `unused variable 'assistant_id'` on [`FacadeConversationRequest`](../../../services/backend-facade/src/backend_facade/app.py) and similar for every field on `FacadeRunRequest`.

**Why:** Vulture performs static analysis. Pydantic v2 model fields are class-body assignments consumed by the metamodel and `model_dump()`; they are never read as plain Python locals. **Do not delete these fields** — they define the public request contract forwarded to ai-backend.

Same pattern for:

- [`DeploymentFeatureToggles`](../../../services/backend-facade/src/backend_facade/deployment_profile.py) / [`DeploymentProfile`](../../../services/backend-facade/src/backend_facade/deployment_profile.py) fields
- [`LogEvent`](../../../services/backend-facade/src/backend_facade/observability/log_event.py) fields (`service`, `error_code`, `safe_message`, `metadata`, …) — populated via `StructuredLogger._build_event` and `fields.pop(...)`; Vulture does not follow that dataflow
- `@field_validator`-decorated `_redact_metadata` in [`log_event.py`](../../../services/backend-facade/src/backend_facade/observability/log_event.py) — invoked by Pydantic, not as a normal call
- `model_config` on [`FacadeSettings`](../../../services/backend-facade/src/backend_facade/settings.py) and deployment profile models

## Category B — OpenTelemetry `SpanProcessor` protocol (false positives)

[`SafeAttributeSpanProcessor`](../../../services/backend-facade/src/backend_facade/observability/otel.py) implements `on_start`, `on_end`, `shutdown`, `force_flush`. Vulture marks `on_start` / `shutdown` / `force_flush` as unused methods and `parent_context` / `timeout_millis` as unused parameters.

**Why:** The SDK invokes these via the protocol. **Do not remove**; renaming parameters to `_parent_context` / `_timeout_millis` is optional style only.

`on_end` **is** implemented and called by the SDK — Vulture still often flags it at 60%; treat as noise.

## Category C — Touch-cache metrics (production vs tests)

[`_TouchCache`](../../../services/backend-facade/src/backend_facade/auth.py) maintains `hits` / `misses`. Vulture on **src only** reports them unused; [`tests/test_session_binding.py`](../../../services/backend-facade/tests/test_session_binding.py) asserts on them.

`FacadeAuthenticator.touch_cache()` is reported “unused” at 60% when scanning **src only** — tests and `invalidate_touch_cache` use it.

**Action:** When interpreting Vulture, pass `tests/` as a second path **or** accept that observability counters are test-targeted.

## Category D — Stdlib logging side effects

[`configure_logging`](../../../services/backend-facade/src/backend_facade/observability/log_config.py) sets `logging.getLogger("uvicorn.access").disabled = True`. Vulture reports `unused attribute 'disabled'` — false positive (stdlib setter).

## Category E — Pytest fixtures and mock signatures (tests only)

With `tests/` included, Vulture may report:

- `@pytest.fixture(autouse=True) def _clear_touch_cache` as unused function — fixtures are discovered by pytest, not by static analysis
- `timeout=None` parameters on fake `httpx` clients — required to match real client signatures; often “unused” in the mock body

## Category F — **Genuine** unused / low-value API (src)

These remain after decorator ignores and are worth tracking in cluster docs:

| Symbol                                                  | Location                                                                                           | Notes                                                                                                                                                                               |
| ------------------------------------------------------- | -------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `TelemetryBootstrap.get_tracer` / `get_meter`           | [`otel.py`](../../../services/backend-facade/src/backend_facade/observability/otel.py)             | No callers in `src/` or `tests/` (repo grep).                                                                                                                                       |
| `StructuredLogger.debug` / `StructuredLogger.exception` | [`log_config.py`](../../../services/backend-facade/src/backend_facade/observability/log_config.py) | No `.debug(` / `.exception(` calls anywhere in facade `src` or `tests`; only `.info` / `.warning` / `.error` are used. Likely parity with sibling services — remove or start using. |
| `create_app(..., deployment= / configure_*=)`           | [`app.py`](../../../services/backend-facade/src/backend_facade/app.py)                             | Keyword parameters never passed from tests; optional injection surface.                                                                                                             |
| `register_health_routes(..., readiness_checkers=)`      | [`app.py`](../../../services/backend-facade/src/backend_facade/app.py)                             | Call site never passes checkers — **unwired feature**.                                                                                                                              |

## Raw volume reference (audit snapshot)

At **60% confidence, src only, no decorator ignore**, Vulture emitted **~95** lines including every nested `async def` route handler under `app.py` and `auth_routes.py`. **That entire handler list is a false positive** for dead-code removal.

Use **Category F** plus the per-cluster markdown files for the real cleanup backlog.
