# Cluster 05 — Observability

## Modules in scope

- [`services/backend-facade/src/backend_facade/observability/__init__.py`](../../../services/backend-facade/src/backend_facade/observability/__init__.py)
- [`services/backend-facade/src/backend_facade/observability/log_config.py`](../../../services/backend-facade/src/backend_facade/observability/log_config.py)
- [`services/backend-facade/src/backend_facade/observability/log_event.py`](../../../services/backend-facade/src/backend_facade/observability/log_event.py)
- [`services/backend-facade/src/backend_facade/observability/otel.py`](../../../services/backend-facade/src/backend_facade/observability/otel.py)
- [`services/backend-facade/src/backend_facade/observability/request_context.py`](../../../services/backend-facade/src/backend_facade/observability/request_context.py)

## Related route (implemented in `app.py`)

- `POST /v1/telemetry/otlp/v1/traces` — forwards browser OTLP to `OTEL_COLLECTOR_HTTP_URL` / `v1/traces`. Code lives in [`app.py`](../../../services/backend-facade/src/backend_facade/app.py); narrative is here to keep telemetry concerns in one audit doc.

## Unused / unexercised (confidence)

| Item                                                                | Confidence      | Notes                                                                                                                                                                                                                                                                                   |
| ------------------------------------------------------------------- | --------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `SafeAttributeSpanProcessor.on_start` parameter `parent_context`    | **N/A**         | Vulture reports “unused variable” at 100% confidence. Parameter is part of the OpenTelemetry `SpanProcessor` protocol; **must not be removed** — prefix with `_` if you want to silence tools without changing behavior.                                                                |
| `SafeAttributeSpanProcessor.force_flush` parameter `timeout_millis` | **N/A**         | Same as above: protocol signature; implementation intentionally no-ops.                                                                                                                                                                                                                 |
| `TelemetryBootstrap.get_tracer`                                     | **High**        | Defined in [`otel.py`](../../../services/backend-facade/src/backend_facade/observability/otel.py); **no references** in `src/` or `tests/` (repo-wide grep). Dead public API unless reserved for future callers.                                                                        |
| `TelemetryBootstrap.get_meter`                                      | **High**        | Same: **no references**.                                                                                                                                                                                                                                                                |
| `TelemetryBootstrap.reset_for_tests`                                | **High** (used) | Used from [`tests/test_otel.py`](../../../services/backend-facade/tests/test_otel.py).                                                                                                                                                                                                  |
| `StructuredLogger.debug`                                            | **High**        | Method exists on [`StructuredLogger`](../../../services/backend-facade/src/backend_facade/observability/log_config.py); **no** `.debug(` invocations in facade `src` or `tests` (only `.info` / `.warning` / `.error` are used). Dead API surface unless you plan to emit DEBUG events. |
| `StructuredLogger.exception`                                        | **High**        | Same: **no** `.exception(` calls anywhere in this service; errors use `.error` without attaching `exc_info` through this wrapper.                                                                                                                                                       |
| `logging.getLogger("uvicorn.access").disabled` assignment           | **N/A**         | Inside [`configure_logging`](../../../services/backend-facade/src/backend_facade/observability/log_config.py); Vulture reports `disabled` as unused attribute — false positive (stdlib). [07-vulture-fastapi-inventory.md](./07-vulture-fastapi-inventory.md) category D.               |

Other exports (`configure_logging`, `emit_access_log`, `RequestContextMiddleware`, `current_context`, `TelemetryBootstrap.configure`, etc.) are used from `app.py`, middleware, or tests.

## Smells

1. **Vulture noise on interface methods** — See unused-parameter hits above; use underscores or read [07-vulture-fastapi-inventory.md](./07-vulture-fastapi-inventory.md) before adding a large whitelist file.

2. **Browser OTLP path vs. service OTLP** — `TelemetryBootstrap.configure` reads `OTEL_EXPORTER_OTLP_ENDPOINT` for **service** export; the **browser** forwarder uses `FacadeSettings.otel_collector_url` (`OTEL_COLLECTOR_HTTP_URL`). Two channels are correct but easy to misconfigure in ops; worth a single sentence in runbooks (documentation smell).

## Recommended follow-ups (optional)

- Remove `TelemetryBootstrap.get_tracer` / `get_meter` if no roadmap needs them, or add a thin internal caller (e.g. future custom spans) so they are not dead weight.
- Rename unused protocol parameters to `_parent_context` and `_timeout_millis` to match convention and silence static “unused” reports.
