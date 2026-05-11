"""Tests for the P11.3 field-tagging system.

Contract:

- ``Sensitive(category)`` is a frozen dataclass usable as a Pydantic
  ``Annotated[]`` metadata marker.
- ``SafeLogDumper.sensitive_field_names(cls)`` returns the set of
  field names annotated ``Sensitive(...)`` on a Pydantic model class.
  The result is cached per class.
- ``SafeLogDumper.dump_safe(instance, **kwargs)`` returns
  ``instance.model_dump(**kwargs)`` with sensitive field keys removed.
- Untagged fields and untagged models behave identically to
  ``model_dump``.
- ``RuntimeLogEvent.to_log_dict()`` and ``HttpLogEvent.to_log_dict()``
  go through ``SafeLogDumper`` so future taggings on those classes
  would auto-elide. Untagged fields today produce the same shape.
- The first real-world tag, ``ManagedContextPayload.content`` and
  ``.preview``, gets stripped by ``SafeLogDumper.dump_safe`` but is
  still accessible via attribute access and direct ``model_dump``.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, Field

from agent_runtime.context.memory.contracts import (
    ContextCompressionEvent,
    ContextCompressionStrategy,
    ManagedContextPayload,
)
from agent_runtime.observability.http_logging import HttpLogEvent
from agent_runtime.observability.logging import RuntimeLogEvent, RuntimeLogLevel
from agent_runtime.observability.redactor import (
    SafeLogDumper,
    Sensitive,
    SensitiveCategory,
)


class _FieldTaggingFixtures:
    """Shared models + sample inputs for the test classes below."""

    class Untagged(BaseModel):
        name: str
        count: int

    class TaggedSingle(BaseModel):
        name: str
        secret: Annotated[str, Sensitive(SensitiveCategory.SECRET)]

    class TaggedMulti(BaseModel):
        name: str
        token: Annotated[str, Sensitive(SensitiveCategory.SECRET)]
        email: Annotated[str, Sensitive(SensitiveCategory.PII)]
        emirates_id: Annotated[str, Sensitive(SensitiveCategory.GOVERNMENT_ID)]

    class ComposedMarkers(BaseModel):
        """Multiple ``Annotated[]`` markers co-exist: Pydantic ``Field``,
        a validator description, and ``Sensitive``. The dumper must
        still find the sensitive tag."""

        name: str
        payload: Annotated[
            str,
            Field(description="model output"),
            Sensitive(SensitiveCategory.MODEL_OUTPUT),
        ]

    @staticmethod
    def make_compression_event() -> ContextCompressionEvent:
        return ContextCompressionEvent(
            before_tokens=100,
            after_tokens=20,
            strategy=ContextCompressionStrategy.SUMMARIZE,
            files_written=(),
            trace_id="trace_123",
            metadata={"safe": "visible"},
        )


class TestSensitiveMarker:
    def test_marker_is_frozen_dataclass(self) -> None:
        marker = Sensitive(SensitiveCategory.SECRET)

        assert marker.category is SensitiveCategory.SECRET
        assert hash(marker) == hash(Sensitive(SensitiveCategory.SECRET))
        assert marker == Sensitive(SensitiveCategory.SECRET)

    def test_categories_are_distinct(self) -> None:
        assert Sensitive(SensitiveCategory.SECRET) != Sensitive(
            SensitiveCategory.MODEL_OUTPUT
        )

    def test_all_documented_categories_exist(self) -> None:
        # PRD §4.1 — the six initial categories.
        assert {c.value for c in SensitiveCategory} == {
            "secret",
            "pii",
            "financial",
            "government_id",
            "model_output",
            "user_input",
        }


class TestSensitiveFieldIntrospection:
    def setup_method(self) -> None:
        SafeLogDumper.reset_cache()

    def test_untagged_model_returns_empty_set(self) -> None:
        assert (
            SafeLogDumper.sensitive_field_names(_FieldTaggingFixtures.Untagged)
            == frozenset()
        )

    def test_single_tagged_field(self) -> None:
        assert SafeLogDumper.sensitive_field_names(
            _FieldTaggingFixtures.TaggedSingle
        ) == frozenset({"secret"})

    def test_multiple_tagged_fields(self) -> None:
        assert SafeLogDumper.sensitive_field_names(
            _FieldTaggingFixtures.TaggedMulti
        ) == frozenset({"token", "email", "emirates_id"})

    def test_composed_annotation_metadata_is_still_detected(self) -> None:
        # The tag co-exists with ``Field(...)``; introspection must
        # iterate past unrelated markers.
        assert SafeLogDumper.sensitive_field_names(
            _FieldTaggingFixtures.ComposedMarkers
        ) == frozenset({"payload"})

    def test_introspection_result_is_cached(self) -> None:
        # First call populates the cache; second call returns the
        # cached frozenset (identity check confirms no rebuild).
        first = SafeLogDumper.sensitive_field_names(_FieldTaggingFixtures.TaggedSingle)
        second = SafeLogDumper.sensitive_field_names(_FieldTaggingFixtures.TaggedSingle)

        assert first is second

    def test_reset_cache_clears_state(self) -> None:
        SafeLogDumper.sensitive_field_names(_FieldTaggingFixtures.TaggedSingle)
        assert _FieldTaggingFixtures.TaggedSingle in SafeLogDumper._cache

        SafeLogDumper.reset_cache()
        assert SafeLogDumper._cache == {}


class TestDumpSafe:
    def setup_method(self) -> None:
        SafeLogDumper.reset_cache()

    def test_untagged_model_dumps_identically_to_model_dump(self) -> None:
        instance = _FieldTaggingFixtures.Untagged(name="x", count=3)

        assert SafeLogDumper.dump_safe(instance) == instance.model_dump()

    def test_dump_safe_strips_tagged_field(self) -> None:
        instance = _FieldTaggingFixtures.TaggedSingle(name="visible", secret="hunter2")

        result = SafeLogDumper.dump_safe(instance)

        assert result == {"name": "visible"}
        assert "secret" not in result

    def test_dump_safe_strips_all_tagged_fields(self) -> None:
        instance = _FieldTaggingFixtures.TaggedMulti(
            name="visible",
            token="sk-secret",
            email="u@example.com",
            emirates_id="784-1234-5678901-2",
        )

        result = SafeLogDumper.dump_safe(instance)

        assert result == {"name": "visible"}

    def test_dump_safe_passes_through_dump_kwargs(self) -> None:
        # ``exclude_none=True`` and ``mode="json"`` should reach
        # ``model_dump``; the untagged result must honour them.
        class HasOptional(BaseModel):
            a: str
            b: str | None = None

        instance = HasOptional(a="present")

        with_none = SafeLogDumper.dump_safe(instance)
        without_none = SafeLogDumper.dump_safe(instance, exclude_none=True)

        assert with_none == {"a": "present", "b": None}
        assert without_none == {"a": "present"}

    def test_dump_safe_preserves_field_value_via_attribute_access(self) -> None:
        # Tagging does not change the field on the model — only the
        # dumped representation drops it. Persistence / SSE paths read
        # the attribute directly and are unaffected.
        instance = _FieldTaggingFixtures.TaggedSingle(name="x", secret="hunter2")

        assert instance.secret == "hunter2"
        assert "secret" in instance.model_dump()


class TestLogEventIntegration:
    def setup_method(self) -> None:
        SafeLogDumper.reset_cache()

    def test_runtime_log_event_to_log_dict_includes_every_existing_field(
        self,
    ) -> None:
        # ``RuntimeLogEvent`` has no ``Sensitive`` tags today. The
        # dump-safe routing must produce a dict identical to the
        # pre-P11.3 ``model_dump(mode="json", exclude_none=True)``.
        event = RuntimeLogEvent(
            event="runtime.invoke",
            level=RuntimeLogLevel.INFO,
            request_id="req_1",
            run_id="run_1",
            trace_id="trace_1",
            subsystem="runtime",
            operation="invoke",
            status="ok",
        )

        assert event.to_log_dict() == event.model_dump(mode="json", exclude_none=True)

    def test_http_log_event_to_log_dict_includes_every_existing_field(
        self,
    ) -> None:
        event = HttpLogEvent(
            service="ai-backend",
            env="test",
            event="http_request",
        )

        assert event.to_log_dict() == event.model_dump(mode="json", exclude_none=True)


class TestManagedContextPayloadTagging:
    """The first real-world tag: ``ManagedContextPayload.content`` and
    ``.preview`` carry tool/connector output that may echo user PII.
    They must be stripped from ``SafeLogDumper.dump_safe`` output but
    accessible via attribute access and direct ``model_dump``."""

    def setup_method(self) -> None:
        SafeLogDumper.reset_cache()

    def test_managed_context_payload_content_field_is_tagged(self) -> None:
        names = SafeLogDumper.sensitive_field_names(ManagedContextPayload)

        assert "content" in names
        assert "preview" in names

    def test_strategy_and_reference_fields_are_not_tagged(self) -> None:
        names = SafeLogDumper.sensitive_field_names(ManagedContextPayload)

        assert "strategy" not in names
        assert "reference" not in names
        assert "event" not in names

    def test_dump_safe_strips_content_and_preview(self) -> None:
        payload = ManagedContextPayload(
            strategy=ContextCompressionStrategy.INLINE,
            content="echoed user PII: jane@example.com lives at 1 Sheikh Zayed Rd",
            preview="echoed user PII (truncated)",
            event=_FieldTaggingFixtures.make_compression_event(),
        )

        dumped = SafeLogDumper.dump_safe(payload, mode="json")

        assert "content" not in dumped
        assert "preview" not in dumped
        assert dumped["strategy"] == "inline"
        assert "event" in dumped

    def test_content_still_accessible_via_attribute_access(self) -> None:
        # Persistence / SSE / context paths read this attribute. Tagging
        # is for logging; everything else still sees the value.
        payload = ManagedContextPayload(
            strategy=ContextCompressionStrategy.INLINE,
            content="full text the model needs",
            event=_FieldTaggingFixtures.make_compression_event(),
        )

        assert payload.content == "full text the model needs"
        assert payload.model_dump()["content"] == "full text the model needs"
