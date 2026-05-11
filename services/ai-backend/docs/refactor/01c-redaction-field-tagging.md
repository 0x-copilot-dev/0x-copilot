# Refactor PRD — `Sensitive[]` field tagging; log emitters introspect it (P11.3)

**Status:** Shipped 2026-05-11
**Parent:** [`01-redaction-subsystem.md`](01-redaction-subsystem.md) §11.3
**Audit reference:** [refactor-audit.md §1.4](../architecture/refactor-audit.md#14-custom-redactor)
**Owner:** Agent runtime team

---

## 1. Problem

After [P11.2](01b-redaction-exact-match-deny-keys.md) the redactor no longer scans string contents at all. The deny-key set protects free-form `metadata: dict[str, JsonScalar]` payloads from credential-shaped key names — but it can't help when the data flowing into a log is a **typed Pydantic model** carrying sensitive fields under benign-looking names (`content`, `output`, `preview`, `text`, `delta`).

Concrete example today: [`ManagedContextPayload`](../../src/agent_runtime/context/memory/contracts.py#L274) is a typed Pydantic model with a `content: str | None` field that carries tool / connector output verbatim. That output flows into the LLM as context (good) and into persistence for replay (good) — but if anyone constructs a `RuntimeLogEvent.metadata` dict from this payload, the `content` value lands in logs whole. No regex, no deny set, no automation catches it because `content` is neither a credential keyword nor a value with a credential shape.

The parent PRD's structural answer (§8.1) is field-level annotation: a Pydantic field carrying sensitive data is **declared** sensitive via `Annotated[T, Sensitive(category)]`. The log emitter introspects the model class once at serialization time and elides the tagged fields. **Sensitivity becomes a property of the field, not the value.**

### What this PRD does

- Introduce `Sensitive(category)` Pydantic annotation marker and `SensitiveCategory` enum.
- Introduce `SafeLogDumper` — a small introspection helper that returns `model_dump()` output with tagged fields removed. Cached per type.
- Route both log emitters' `to_log_dict()` through `SafeLogDumper.dump_safe()`. Behavior is unchanged for untagged fields; tagged fields disappear from the log record.
- Apply one demonstration tag: `ManagedContextPayload.content` and `.preview` become `Sensitive(MODEL_OUTPUT)`. This proves the round trip and gives a working reference for future taggings.

### What this PRD does NOT do

- Touch the existing deny-key behavior. Free-form `metadata` dicts still go through the deny-key filter from [P11.2](01b-redaction-exact-match-deny-keys.md).
- Tag every Pydantic model with sensitive content. Tagging is incremental — future PRs tag models as the relevant code is touched.
- Recurse into nested models. The dumper strips top-level sensitive fields only. Nested-model support is a follow-up (called out in §9).
- Remove `redact_json_object` from non-log paths. That's [P11.5](01e-redaction-remove-from-non-log-paths.md).
- Build a category-based policy (e.g. "drop `MODEL_OUTPUT` in audit logs but keep in debug logs"). Categories exist so policy can be added later; this PRD drops every tagged field uniformly.

---

## 2. Goal and non-goals

### Goal

Land the infrastructure that makes "this field is sensitive; don't log it" a one-line declaration on the Pydantic model. Make the log emitters honor the declaration without breaking any current log record shape.

### Non-goals

- Build a global redaction policy with per-category rules.
- Recurse into nested Pydantic models.
- Tag every sensitive model.
- Change the deny-key set or the `Redactor` Protocol from [P11.1](01a-redaction-protocol.md).

### Success criteria

1. [`agent_runtime/observability/redactor.py`](../../src/agent_runtime/observability/redactor.py) exports:
   - `SensitiveCategory(StrEnum)` with values `SECRET`, `PII`, `FINANCIAL`, `GOVERNMENT_ID`, `MODEL_OUTPUT`, `USER_INPUT`.
   - `Sensitive` frozen dataclass with a `category: SensitiveCategory` field.
   - `SafeLogDumper` class with `dump_safe(model, **dump_kwargs)` and `sensitive_field_names(model_cls)` classmethods. Introspection is cached per `model_cls`.
2. `RuntimeLogEvent.to_log_dict()` and `HttpLogEvent.to_log_dict()` go through `SafeLogDumper.dump_safe(self, mode="json", exclude_none=True)`.
3. [`ManagedContextPayload`](../../src/agent_runtime/context/memory/contracts.py#L274) `content` and `preview` fields are annotated `Sensitive(SensitiveCategory.MODEL_OUTPUT)`.
4. New test file `tests/unit/agent_runtime/observability/test_field_tagging.py` covers: marker construction, introspection (tagged vs untagged fields, multiple markers), caching, dump output, mode/exclude_none passthrough, untagged model dumps identically to `model_dump`.
5. All existing tests pass. Tagged `ManagedContextPayload` round-trips correctly through Pydantic but its `content` is absent from the log dict.
6. No callsite outside `observability/`, `context/memory/contracts.py`, and tests is touched.

---

## 3. Systems touched

### 3.1 Files changed

| File                                                                                                   | Change                                                                                                                                                                                               |
| ------------------------------------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [`agent_runtime/observability/redactor.py`](../../src/agent_runtime/observability/redactor.py)         | Add `SensitiveCategory` enum, `Sensitive` dataclass, `SafeLogDumper` class. Existing `Redactor` / `RegexRedactor` / `RedactorRegistry` / `DENY_KEYS` left intact.                                    |
| [`agent_runtime/observability/__init__.py`](../../src/agent_runtime/observability/__init__.py)         | Re-export `SafeLogDumper`, `Sensitive`, `SensitiveCategory`.                                                                                                                                         |
| [`agent_runtime/observability/logging.py`](../../src/agent_runtime/observability/logging.py)           | `RuntimeLogEvent.to_log_dict()` returns `SafeLogDumper.dump_safe(self, mode="json", exclude_none=True)`.                                                                                             |
| [`agent_runtime/observability/http_logging.py`](../../src/agent_runtime/observability/http_logging.py) | `HttpLogEvent.to_log_dict()` same swap.                                                                                                                                                              |
| [`agent_runtime/context/memory/contracts.py`](../../src/agent_runtime/context/memory/contracts.py)     | `ManagedContextPayload.content` and `ManagedContextPayload.preview` annotations gain `Sensitive(SensitiveCategory.MODEL_OUTPUT)`. Validator decorators stay; markers compose with existing metadata. |

### 3.2 Files added

| File                                                           | Purpose                                                                                                                                                                |
| -------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `tests/unit/agent_runtime/observability/test_field_tagging.py` | Pin marker semantics, introspection result, cache behavior, dumper output for tagged + untagged models, integration with `to_log_dict()` of the two log event classes. |

### 3.3 Files **not** touched

- The 19 redaction callsites. They go through `ObservabilityRedactor` / `redact_json_object` which is untouched.
- `MemoryRedactor`, `_MetadataRedactor` instances. They handle `dict` shapes — field tagging applies to typed Pydantic models, a different surface.
- Any consumer of `ManagedContextPayload` that reads its `content` field. Tagging is metadata only; the field still exists and the value is still accessible via attribute access. Only `model_dump()` paths going through `SafeLogDumper` strip it.

---

## 4. Design

### 4.1 `Sensitive` marker shape

```python
from dataclasses import dataclass
from enum import StrEnum


class SensitiveCategory(StrEnum):
    SECRET = "secret"
    PII = "pii"
    FINANCIAL = "financial"
    GOVERNMENT_ID = "government_id"
    MODEL_OUTPUT = "model_output"
    USER_INPUT = "user_input"


@dataclass(frozen=True)
class Sensitive:
    """Pydantic ``Annotated[]`` marker: this field is sensitive; the log
    emitter elides it from log records. Categories drive future policy;
    today every tagged field is dropped uniformly."""

    category: SensitiveCategory
```

Usage:

```python
from typing import Annotated
from agent_runtime.observability.redactor import Sensitive, SensitiveCategory

class ManagedContextPayload(RuntimeContract):
    content: Annotated[str | None, Sensitive(SensitiveCategory.MODEL_OUTPUT)] = None
```

`Sensitive` is `frozen=True` so two `Sensitive(SensitiveCategory.SECRET)` instances compare equal (handy for tests) and the marker is hashable.

### 4.2 `SafeLogDumper`

```python
class SafeLogDumper:
    """Pydantic model dumper that elides ``Sensitive``-tagged fields.

    Introspection is cached per ``BaseModel`` subclass so the hot path
    is one ``frozenset`` lookup per dump. Top-level fields only —
    nested Pydantic models inside the dump are not currently inspected
    (see PRD §9 for the follow-up scope).
    """

    _cache: ClassVar[dict[type[BaseModel], frozenset[str]]] = {}

    @classmethod
    def sensitive_field_names(cls, model_cls: type[BaseModel]) -> frozenset[str]:
        cached = cls._cache.get(model_cls)
        if cached is not None:
            return cached
        names: set[str] = set()
        for name, field in model_cls.model_fields.items():
            for meta in field.metadata:
                if isinstance(meta, Sensitive):
                    names.add(name)
                    break
        result = frozenset(names)
        cls._cache[model_cls] = result
        return result

    @classmethod
    def dump_safe(cls, model: BaseModel, **dump_kwargs: Any) -> dict[str, Any]:
        sensitive = cls.sensitive_field_names(type(model))
        dumped = model.model_dump(**dump_kwargs)
        if not sensitive:
            return dumped
        return {k: v for k, v in dumped.items() if k not in sensitive}

    @classmethod
    def reset_cache(cls) -> None:
        """Test-only hook. Production code never invalidates the cache —
        a model class's field annotations don't change at runtime."""

        cls._cache.clear()
```

### 4.3 Log emitter integration

`RuntimeLogEvent.to_log_dict()`:

```python
def to_log_dict(self) -> dict[str, object]:
    return SafeLogDumper.dump_safe(self, mode="json", exclude_none=True)
```

`HttpLogEvent.to_log_dict()`: same swap.

Behavior is identical for untagged fields (the empty `frozenset` short-circuit returns the unchanged `model_dump()` result). Tagged fields disappear from the dict.

### 4.4 The demonstration tag

[`ManagedContextPayload`](../../src/agent_runtime/context/memory/contracts.py) carries tool / connector output after inline / offload / summary handling. The `content` and `preview` fields hold raw text from external sources — frequently echoing user data. Tagging both:

```python
class ManagedContextPayload(RuntimeContract):
    strategy: ContextCompressionStrategy
    content: Annotated[
        str | None, Sensitive(SensitiveCategory.MODEL_OUTPUT)
    ] = None
    reference: str | None = None  # storage pointer — not sensitive
    preview: Annotated[
        str | None, Sensitive(SensitiveCategory.MODEL_OUTPUT)
    ] = None
    event: ContextCompressionEvent
```

Why these two and not `reference` or `strategy`:

- `content` and `preview` carry the actual text. Sensitive.
- `reference` is a storage path / blob ID. Not sensitive.
- `strategy` is an enum value. Not sensitive.
- `event` is a structured `ContextCompressionEvent`. Has its own metadata, which already goes through `MemoryRedactor`. Untagged.

The validator decorators on `content` / `reference` stay — Pydantic's `Annotated[]` chain composes correctly: `Annotated[T, validator_marker, Sensitive(...)]` keeps both.

---

## 5. Behaviors preserved

| Behavior                                                                                               | After P11.3                                                                                                                                     |
| ------------------------------------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------- |
| `RuntimeLogEvent.to_log_dict()` returns a JSON-safe dict with `exclude_none=True` semantics            | Unchanged — `SafeLogDumper` passes the kwargs through to `model_dump()`.                                                                        |
| Every existing field on `RuntimeLogEvent` / `HttpLogEvent` shows up in the log dict                    | Unchanged — none of those fields are tagged.                                                                                                    |
| `ManagedContextPayload.content` is accessible via attribute access (`payload.content`)                 | Unchanged — tagging is metadata only; the field is a normal Pydantic field at runtime.                                                          |
| `ManagedContextPayload` validates the same inputs as before                                            | Unchanged — `Annotated[T, validator, Sensitive(...)]` composes; validators run as today.                                                        |
| `ManagedContextPayload.model_dump()` (direct call) includes `content`                                  | Unchanged — only `SafeLogDumper.dump_safe()` strips. Direct `model_dump()` callers see the full dict. Persistence / SSE paths are not affected. |
| Untagged Pydantic models dumped via `SafeLogDumper.dump_safe` produce identical output to `model_dump` | Yes — the `if not sensitive: return dumped` short-circuit makes this trivially true.                                                            |
| The `Redactor` Protocol + `DENY_KEYS` from earlier phases                                              | Unchanged. Tagging is additive; the dict-shaped redaction layer still runs on `metadata` fields.                                                |

---

## 6. Risks and mitigations

| Risk                                                                                                                         | Likelihood | Impact | Mitigation                                                                                                                                                                                                                         |
| ---------------------------------------------------------------------------------------------------------------------------- | ---------- | ------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `SafeLogDumper._cache` grows unbounded as new Pydantic classes load                                                          | Low        | Low    | Pydantic model classes are module-level — the cache is bounded by the number of model classes defined in the process. No teardown needed in normal operation. `reset_cache()` exists for tests.                                    |
| A field has multiple markers (e.g. validator + `Sensitive` + something else)                                                 | Low        | Low    | Introspection iterates `field.metadata` and `break`s on the first `Sensitive` match — extra markers don't interfere.                                                                                                               |
| Caller passes a tagged Pydantic model to `RuntimeLogEvent.metadata` (a `dict[str, JsonScalar]` field) and the tag is ignored | Medium     | Medium | Documented limitation: tagging is honored by `to_log_dict()` of the log event itself, not by free-form `metadata` dicts. Convert the model with `SafeLogDumper.dump_safe(model)` at the call site if needed.                       |
| Nested Pydantic model with a tagged field is not stripped (e.g. `ContextCompressionEvent.metadata`)                          | Medium     | Medium | Top-level only in P11.3. Add nested support as a follow-up if a real use case appears. The `MemoryRedactor` on `ContextCompressionEvent.metadata` still runs as a fallback.                                                        |
| Annotation order matters for some Pydantic v2 behaviors                                                                      | Low        | Medium | Place `Sensitive(...)` AFTER `Field(...)` and any validator markers in `Annotated[]`. Test covers the `ManagedContextPayload` case where validators co-exist.                                                                      |
| Someone tags a field by mistake and breaks observability                                                                     | Low        | Medium | Tagging is reviewable as a single-line addition. CI runs the safe-dump tests so a stripped field that was previously visible in logs will surface as a missing-key failure if the test exercises it.                               |
| `model_dump(mode="json")` serializes a tagged field's metadata into JSON-Schema output and leaks the category                | Low        | Low    | `Sensitive` is metadata only — Pydantic doesn't include `Annotated[]` metadata in JSON output. Confirmed by tests. (Pydantic's JSON Schema can expose `Annotated[]` metadata; the `mode="json"` dump is data-only and unaffected.) |

---

## 7. Test requirements

### 7.1 New tests (`test_field_tagging.py`)

- `test_sensitive_marker_is_frozen_dataclass` — `Sensitive(SECRET)` equals itself across constructions; `frozen=True` makes it hashable.
- `test_sensitive_field_names_for_untagged_model_is_empty` — a model with no annotated fields returns `frozenset()`.
- `test_sensitive_field_names_returns_tagged_field` — a model with one `Annotated[T, Sensitive(...)]` field returns `{field_name}`.
- `test_sensitive_field_names_handles_multiple_tags` — model with three tagged fields returns all three names.
- `test_sensitive_field_names_ignores_other_metadata` — a field with `Annotated[T, Field(...), validator, Sensitive(...)]` returns the field name; a field with only `Field(...)` and a validator does not.
- `test_sensitive_field_names_is_cached` — call twice on the same class; second call doesn't iterate metadata (use a sentinel or `_cache` inspection).
- `test_dump_safe_strips_tagged_field` — `SafeLogDumper.dump_safe(instance)` returns a dict without the tagged field key.
- `test_dump_safe_preserves_untagged_fields` — every untagged field shows up in the dump.
- `test_dump_safe_passes_through_dump_kwargs` — `exclude_none=True` honored; `mode="json"` produces JSON-compatible primitives.
- `test_dump_safe_untagged_model_matches_model_dump` — for a model with zero tags, `dump_safe(m, **kw) == m.model_dump(**kw)`.
- `test_managed_context_payload_content_is_stripped` — instantiate `ManagedContextPayload(strategy=INLINE, content="echoed user PII", event=...)`, call `SafeLogDumper.dump_safe(...)`, assert `"content"` and `"preview"` not in the dict.
- `test_runtime_log_event_to_log_dict_strips_sensitive` — build a small subclass / monkeypatched `RuntimeLogEvent` with a tagged field, assert it's missing from `to_log_dict()`. (Or: assert `RuntimeLogEvent.to_log_dict()` produces the same shape as before for the untagged base class — proves the integration didn't break anything.)
- `test_http_log_event_to_log_dict_strips_sensitive` — same idea for `HttpLogEvent`.
- `test_safe_log_dumper_reset_cache_clears_internal_state` — test hook for fixture isolation.

### 7.2 Tests updated

None expected. Existing log emitter tests assert on field presence in `to_log_dict()`; none of those fields are tagged, so output is identical.

### 7.3 Regression suite

- All `agent_runtime/`, `runtime_api/`, `runtime_adapters/` unit suites green.
- `ManagedContextPayload` consumers (memory, context flow tests) continue to pass — they read `.content` directly via attribute access, untouched.

---

## 8. Rollout / rollback

### 8.1 Rollout

Single PR. No feature flag. Behavior change is bounded: only `ManagedContextPayload.content` and `.preview` are tagged, and the only behavior change is "these fields disappear from `to_log_dict()` output." No code today logs `ManagedContextPayload` directly via `to_log_dict()`, so the user-observable diff is nil — the infrastructure is in place for future tagging.

### 8.2 Rollback

Revert the PR. Tags come off; `SafeLogDumper` goes away; `to_log_dict()` reverts to direct `model_dump()`.

---

## 9. Open questions / future scope

- **Nested model recursion.** When a model has a `nested: Annotated[Inner, Sensitive(...)]`-style top-level tag, the inner model is dropped entirely. When a model has a `nested: Inner` (untagged) where `Inner` has tagged fields, the inner model's tags are NOT honored by today's top-level dumper. Decide: extend the dumper to recurse, or document the limitation and require explicit `SafeLogDumper.dump_safe` calls at each level.
- **Per-category policy.** Today every tagged field is dropped uniformly. A future PR could let a `RedactionPolicy` say "drop `MODEL_OUTPUT` in audit logs, keep in debug logs." Infrastructure for it lives in the `SensitiveCategory` enum — no work needed in P11.3.
- **Tagging the rest of the codebase.** P11.3 tags one model as a demonstration. Each subsequent PR should tag sensitive fields on the model it touches. Open question: should there be a CI lint that flags new `str` fields under names like `content`, `output`, `message`, `delta`, `text` without a `Sensitive` tag? Probably overkill; defer until a real leak shows up.
- **Conversion helper for dicts.** Many callers construct `metadata: dict[str, JsonScalar]` by hand. A `SafeLogDumper.dump_safe_dict(values: dict, sensitive_keys: Iterable[str]) -> dict` could provide a symmetric helper for dict-shaped data. Probably not needed; the deny-key set already covers credential-shaped dict keys.
- **Schema export.** Should the `Sensitive` annotation propagate into OpenAPI / JSON Schema as a vendor extension (e.g. `x-sensitive: model_output`)? Could help downstream consumers (the frontend, audit tooling). Not required for redaction; consider after a buyer asks.

---

## 10. Acceptance criteria

- [x] `SensitiveCategory` enum + `Sensitive` dataclass + `SafeLogDumper` class added to [`agent_runtime/observability/redactor.py`](../../src/agent_runtime/observability/redactor.py).
- [x] Re-exported from [`agent_runtime/observability/__init__.py`](../../src/agent_runtime/observability/__init__.py) (`Sensitive`, `SensitiveCategory`, `SafeLogDumper`, `DENY_KEYS`).
- [x] `RuntimeLogEvent.to_log_dict()` and `HttpLogEvent.to_log_dict()` use `SafeLogDumper.dump_safe`.
- [x] `ManagedContextPayload.content` and `.preview` annotated `Sensitive(SensitiveCategory.MODEL_OUTPUT)`.
- [x] New test file [`test_field_tagging.py`](../../tests/unit/agent_runtime/observability/test_field_tagging.py) exists with **20 tests** across 5 test classes (marker, introspection, dump-safe, log-event integration, ManagedContextPayload tagging).
- [x] Existing tests pass unchanged.
- [x] Full regression suite green — **1181 tests passing, 0 failures**.

---

## 11. Done definition

- Tests in §10 green.
- Parent PRD's phase table marks P11.3 as **Shipped**.
- This PRD's Status header flipped to **Shipped 2026-05-11**.
