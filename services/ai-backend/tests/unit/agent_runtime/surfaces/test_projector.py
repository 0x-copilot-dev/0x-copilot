"""Unit tests for :class:`SurfaceProjector` (generative-UI PRD-02).

Covers the spec-acquisition ladder (builtin → store → miss), the URI grammar
+ id derivation, no-spec archetype inference, and the two short-circuits
(non-mapping output, emission disabled).
"""

from __future__ import annotations

from agent_runtime.capabilities.surfaces import builtin
from agent_runtime.capabilities.surfaces.projector import (
    InMemorySurfaceSpecStore,
    SurfaceProjector,
)
from agent_runtime.capabilities.surfaces.spec_models import (
    SurfaceArchetype,
    validate_surface_spec,
)


def _linear_issue_output() -> dict[str, object]:
    return {
        "issue": {
            "id": "issue-uuid-1",
            "identifier": "ENG-1421",
            "title": "Fix login redirect loop",
            "url": "https://linear.app/acme/issue/ENG-1421",
        }
    }


class TestSurfaceProjectorBuiltinRung:
    def test_builtin_spec_binds_record_archetype_and_uri(self) -> None:
        envelope = SurfaceProjector().resolve(
            "linear", "get_issue", _linear_issue_output(), call_id="call_1"
        )

        assert envelope is not None
        assert envelope.archetype is SurfaceArchetype.RECORD
        # id precedence: ``id`` beats ``identifier`` — nested one wrapper deep.
        assert envelope.surface_uri == "record://linear/get_issue/issue-uuid-1"
        assert envelope.state.spec == builtin.lookup("linear", "get_issue")
        assert envelope.state.data == _linear_issue_output()

    def test_seed_prefixed_server_name_resolves_same_builtin(self) -> None:
        envelope = SurfaceProjector().resolve(
            "seed:linear", "get_issue", _linear_issue_output()
        )

        assert envelope is not None
        assert envelope.surface_uri == "record://linear/get_issue/issue-uuid-1"
        assert envelope.state.spec == builtin.lookup("linear", "get_issue")

    def test_list_shaped_builtin_binds_table(self) -> None:
        output = {"repository": {"full_name": "acme/web"}, "issues": [{"number": 1}]}
        envelope = SurfaceProjector().resolve("github", "list_issues", output)

        assert envelope is not None
        assert envelope.archetype is SurfaceArchetype.TABLE
        assert envelope.surface_uri.startswith("table://github/list_issues/")


class TestSurfaceProjectorStoreRung:
    def test_store_resolves_when_builtin_misses(self) -> None:
        spec = validate_surface_spec(
            {
                "spec_version": 1,
                "archetype": "record",
                "source": {"server": "seed:customsvc", "tool": "get_thing"},
                "title_path": "thing.name",
            }
        )
        store = InMemorySurfaceSpecStore()
        store.put(spec)
        projector = SurfaceProjector(store=store)

        envelope = projector.resolve(
            "customsvc", "get_thing", {"thing": {"id": "t-7", "name": "Widget"}}
        )

        assert envelope is not None
        assert envelope.state.spec == spec
        assert envelope.surface_uri == "record://customsvc/get_thing/t-7"

    def test_builtin_wins_over_store(self) -> None:
        # A store entry for a curated (server, tool) must not shadow the builtin.
        shadow = validate_surface_spec(
            {
                "spec_version": 1,
                "archetype": "table",
                "source": {"server": "seed:linear", "tool": "get_issue"},
                "title_path": "issue.title",
            }
        )
        store = InMemorySurfaceSpecStore()
        store.put(shadow)

        envelope = SurfaceProjector(store=store).resolve(
            "linear", "get_issue", _linear_issue_output()
        )

        assert envelope is not None
        assert envelope.state.spec == builtin.lookup("linear", "get_issue")
        assert envelope.archetype is SurfaceArchetype.RECORD


class TestSurfaceProjectorMiss:
    def test_uncurated_output_ships_data_only(self) -> None:
        output = {"widget": {"id": "w-9", "label": "Ready"}}
        envelope = SurfaceProjector().resolve("customsvc", "do_thing", output)

        assert envelope is not None
        assert envelope.state.spec is None
        assert envelope.state.data == output
        assert envelope.surface_uri == "record://customsvc/do_thing/w-9"

    def test_uncurated_list_infers_table_scheme(self) -> None:
        output = {"rows": [{"a": 1}, {"a": 2}]}
        envelope = SurfaceProjector().resolve("customsvc", "list_rows", output)

        assert envelope is not None
        assert envelope.state.spec is None
        assert envelope.archetype is SurfaceArchetype.TABLE
        assert envelope.surface_uri.startswith("table://customsvc/list_rows/")


class TestSurfaceProjectorIdDerivation:
    def test_top_level_id_field_used(self) -> None:
        envelope = SurfaceProjector().resolve("svc", "t", {"id": "abc", "x": 1})
        assert envelope is not None
        assert envelope.surface_uri.endswith("/abc")

    def test_id_field_precedence_over_identifier(self) -> None:
        envelope = SurfaceProjector().resolve(
            "svc", "t", {"identifier": "IDF-1", "key": "K1"}
        )
        # ``key`` precedes ``identifier`` in the probe order.
        assert envelope is not None
        assert envelope.surface_uri.endswith("/K1")

    def test_unsafe_id_is_sanitised(self) -> None:
        envelope = SurfaceProjector().resolve("svc", "t", {"id": "a/b c:d"})
        assert envelope is not None
        segment = envelope.surface_uri.rsplit("/", 1)[1]
        assert "/" not in segment
        assert " " not in segment
        assert segment == "a-b-c-d"

    def test_hash_fallback_uses_call_id_and_is_stable(self) -> None:
        projector = SurfaceProjector()
        output = {"no": {"identifier": "here"}, "also": {"nope": True}}
        a = projector.resolve("svc", "t", output, call_id="call_zzz")
        b = projector.resolve("svc", "t", output, call_id="call_zzz")
        assert a is not None and b is not None
        assert a.surface_uri == b.surface_uri
        # Different call ids → different fallback segment.
        c = projector.resolve("svc", "t", output, call_id="call_yyy")
        assert c is not None
        assert c.surface_uri != a.surface_uri

    def test_hash_fallback_uses_output_when_no_call_id(self) -> None:
        projector = SurfaceProjector()
        output = {"payload": {"nested": "value"}}
        a = projector.resolve("svc", "t", output)
        b = projector.resolve("svc", "t", output)
        assert a is not None and b is not None
        assert a.surface_uri == b.surface_uri


class TestSurfaceProjectorShortCircuits:
    def test_non_mapping_output_returns_none(self) -> None:
        projector = SurfaceProjector()
        assert projector.resolve("linear", "get_issue", "a string") is None
        assert projector.resolve("linear", "get_issue", None) is None
        assert projector.resolve("linear", "get_issue", [1, 2, 3]) is None

    def test_disabled_projector_returns_none(self) -> None:
        projector = SurfaceProjector(enabled=False)
        assert projector.resolve("linear", "get_issue", _linear_issue_output()) is None
