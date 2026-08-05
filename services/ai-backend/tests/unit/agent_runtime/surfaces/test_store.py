"""Unit tests for the SurfaceSpec store adapters (generative-UI PRD-07, AC4).

Covers the in-memory dual store (PRD-02 projector read + PRD-07 generation
methods) and the file store (atomic round-trip, projector pointer, skill_version
in the key, failure recording).
"""

from __future__ import annotations

from pathlib import Path

from agent_runtime.capabilities.surfaces.spec_models import (
    SurfaceSpec,
    validate_surface_spec,
)
from agent_runtime.capabilities.surfaces.store import (
    FileSurfaceSpecStore,
    InMemorySurfaceSpecStore,
    SpecKey,
    StoredSpec,
)


def _record_spec(
    server: str = "seed:customsvc", tool: str = "get_thing"
) -> SurfaceSpec:
    return validate_surface_spec(
        {
            "spec_version": 1,
            "archetype": "record",
            "source": {"server": server, "tool": tool},
            "title_path": "thing.name",
        }
    )


def _key(*, shape: str = "shapehash", skill_version: int = 1) -> SpecKey:
    return SpecKey.build(
        server="customsvc",
        tool="get_thing",
        output_shape_hash=shape,
        skill_version=skill_version,
    )


class TestInMemoryStore:
    def test_prd02_put_spec_and_read(self) -> None:
        store = InMemorySurfaceSpecStore()
        spec = _record_spec()
        store.put(spec)  # PRD-02 overload
        assert store.get(server="customsvc", tool="get_thing") == spec

    def test_generation_put_get_and_projector_read(self) -> None:
        store = InMemorySurfaceSpecStore()
        spec = _record_spec()
        key = _key()
        store.put(
            key, StoredSpec.from_generation(key=key, spec=spec, generator_model="m")
        )
        assert store.get_stored(key).spec == spec
        # A generation put also feeds the coarse projector read.
        assert store.get(server="customsvc", tool="get_thing") == spec

    def test_failure_recording(self) -> None:
        store = InMemorySurfaceSpecStore()
        key = _key()
        assert store.has_failure(key) is False
        store.record_failure(key, "lint failed", '{"bad": true}')
        assert store.has_failure(key) is True
        assert store.get_stored(key) is None


class TestFileStore:
    def test_round_trip_and_projector_pointer(self, tmp_path: Path) -> None:
        store = FileSurfaceSpecStore(tmp_path)
        spec = _record_spec()
        key = _key()
        store.put(
            key, StoredSpec.from_generation(key=key, spec=spec, generator_model="m")
        )

        assert store.get_stored(key).spec == spec
        assert store.get(server="customsvc", tool="get_thing") == spec

    def test_atomic_write_leaves_no_tmp(self, tmp_path: Path) -> None:
        store = FileSurfaceSpecStore(tmp_path)
        key = _key()
        store.put(
            key,
            StoredSpec.from_generation(
                key=key, spec=_record_spec(), generator_model="m"
            ),
        )
        assert list(tmp_path.rglob("*.tmp")) == []
        assert list((tmp_path / "specs").glob("*.json"))

    def test_skill_version_is_part_of_the_key(self, tmp_path: Path) -> None:
        store = FileSurfaceSpecStore(tmp_path)
        spec = _record_spec()
        key_v1 = _key(skill_version=1)
        store.put(
            key_v1,
            StoredSpec.from_generation(key=key_v1, spec=spec, generator_model="m"),
        )
        # Bumping the skill version misses the cache (plan D10).
        assert store.get_stored(_key(skill_version=2)) is None
        assert store.get_stored(key_v1) is not None

    def test_shape_hash_is_part_of_the_key(self, tmp_path: Path) -> None:
        store = FileSurfaceSpecStore(tmp_path)
        key_a = _key(shape="aaa")
        store.put(
            key_a,
            StoredSpec.from_generation(
                key=key_a, spec=_record_spec(), generator_model="m"
            ),
        )
        assert store.get_stored(_key(shape="bbb")) is None

    def test_failure_recording(self, tmp_path: Path) -> None:
        store = FileSurfaceSpecStore(tmp_path)
        key = _key()
        assert store.has_failure(key) is False
        store.record_failure(key, "schema invalid", '{"nope": 1}')
        assert store.has_failure(key) is True

    def test_missing_returns_none(self, tmp_path: Path) -> None:
        store = FileSurfaceSpecStore(tmp_path)
        assert store.get_stored(_key()) is None
        assert store.get(server="customsvc", tool="get_thing") is None

    def test_from_env_prefers_explicit_root(self, tmp_path: Path) -> None:
        store = FileSurfaceSpecStore.from_env(
            {"SURFACE_SPEC_STORE_ROOT": str(tmp_path)}
        )
        assert store is not None
        assert store.root == tmp_path.resolve()

    def test_from_env_nests_under_file_store_root(self, tmp_path: Path) -> None:
        store = FileSurfaceSpecStore.from_env(
            {"RUNTIME_FILE_STORE_ROOT": str(tmp_path)}
        )
        assert store is not None
        assert store.root == (tmp_path / "surfaces").resolve()

    def test_from_env_returns_none_without_root(self) -> None:
        assert FileSurfaceSpecStore.from_env({}) is None


class TestSpecKey:
    def test_normalises_server_and_tool(self) -> None:
        key = SpecKey.build(
            server="seed:Linear",
            tool="Get_Issue",
            output_shape_hash="h",
            skill_version=1,
        )
        assert key.server == "linear"
        assert key.tool == "get_issue"

    def test_digest_is_stable_and_key_sensitive(self) -> None:
        base = SpecKey.build(
            server="s", tool="t", output_shape_hash="h", skill_version=1
        )
        same = SpecKey.build(
            server="s", tool="t", output_shape_hash="h", skill_version=1
        )
        other = SpecKey.build(
            server="s", tool="t", output_shape_hash="h", skill_version=2
        )
        assert base.digest() == same.digest()
        assert base.digest() != other.digest()


class LearnedCacheMixin:
    """Store wiring shared by the shape-keyed learned-cache tests (PRD §3.6)."""

    SHAPE_A = "shape-aaaa"
    SHAPE_B = "shape-bbbb"

    @staticmethod
    def stored_under(store: object, key: SpecKey, spec: SurfaceSpec) -> None:
        store.put(
            key, StoredSpec.from_generation(key=key, spec=spec, generator_model="")
        )

    @classmethod
    def other_tool_key(cls, *, shape: str) -> SpecKey:
        """A key for an entirely different connector and tool, same shape."""

        return SpecKey.build(
            server="othersvc",
            tool="fetch_release",
            output_shape_hash=shape,
            skill_version=1,
        )


class TestInMemoryLearnedCache(LearnedCacheMixin):
    def test_ac14_a_spec_is_readable_by_shape_alone(self) -> None:
        store = InMemorySurfaceSpecStore()
        spec = _record_spec()
        self.stored_under(store, _key(shape=self.SHAPE_A), spec)

        assert store.get_by_shape(output_shape_hash=self.SHAPE_A) == spec

    def test_ac14_the_name_keyed_read_still_wins_when_it_can_answer(self) -> None:
        # Widening the lookup must not cost the ability to prefer an exact
        # (server, tool) entry — the shape index is a fallback, not a takeover.
        store = InMemorySurfaceSpecStore()
        exact = _record_spec()
        self.stored_under(store, _key(shape=self.SHAPE_A), exact)
        self.stored_under(
            store,
            self.other_tool_key(shape=self.SHAPE_A),
            _record_spec(server="seed:othersvc", tool="fetch_release"),
        )

        assert store.get(server="customsvc", tool="get_thing") == exact

    def test_an_unknown_shape_misses(self) -> None:
        store = InMemorySurfaceSpecStore()
        self.stored_under(store, _key(shape=self.SHAPE_A), _record_spec())

        assert store.get_by_shape(output_shape_hash=self.SHAPE_B) is None

    def test_an_empty_shape_hash_is_never_a_hit(self) -> None:
        store = InMemorySurfaceSpecStore()
        self.stored_under(store, _key(shape=""), _record_spec())

        assert store.get_by_shape(output_shape_hash="") is None

    def test_the_prd_02_put_spec_form_does_not_populate_the_shape_index(self) -> None:
        # It carries no shape hash, and inventing one from a spec would key the
        # cache on the spec instead of on the payload it was written for.
        store = InMemorySurfaceSpecStore()
        store.put(_record_spec())

        assert store.get_by_shape(output_shape_hash=self.SHAPE_A) is None

    def test_ac15_a_failure_for_one_shape_does_not_suppress_another(self) -> None:
        # AC15: reads widened to the shape alone; failures did NOT. A recorded
        # failure is durable and suppresses every retry, so widening it would
        # turn one connector's malformed payload into a permanent global mute.
        store = InMemorySurfaceSpecStore()
        store.record_failure(_key(shape=self.SHAPE_A), "bad json", "{")

        assert store.has_failure(_key(shape=self.SHAPE_A)) is True
        assert store.has_failure(_key(shape=self.SHAPE_B)) is False

    def test_ac15_a_failure_never_leaks_into_the_shape_read(self) -> None:
        store = InMemorySurfaceSpecStore()
        store.record_failure(_key(shape=self.SHAPE_A), "bad json", "{")

        assert store.get_by_shape(output_shape_hash=self.SHAPE_A) is None

    def test_ac15_one_connectors_failure_does_not_mute_another(self) -> None:
        store = InMemorySurfaceSpecStore()
        store.record_failure(_key(shape=self.SHAPE_A), "bad json", "{")

        assert store.has_failure(self.other_tool_key(shape=self.SHAPE_A)) is False


class TestFileLearnedCache(LearnedCacheMixin):
    def test_ac14_the_learned_cache_survives_a_restart(self, tmp_path: Path) -> None:
        spec = _record_spec()
        self.stored_under(
            FileSurfaceSpecStore(tmp_path), _key(shape=self.SHAPE_A), spec
        )

        # A brand-new store object over the same root: the process restarted.
        assert (
            FileSurfaceSpecStore(tmp_path).get_by_shape(output_shape_hash=self.SHAPE_A)
            == spec
        )

    def test_both_pointers_resolve_to_one_spec_file(self, tmp_path: Path) -> None:
        store = FileSurfaceSpecStore(tmp_path)
        spec = _record_spec()
        self.stored_under(store, _key(shape=self.SHAPE_A), spec)

        assert store.get(server="customsvc", tool="get_thing") == spec
        assert store.get_by_shape(output_shape_hash=self.SHAPE_A) == spec

    def test_an_unknown_shape_misses(self, tmp_path: Path) -> None:
        store = FileSurfaceSpecStore(tmp_path)
        self.stored_under(store, _key(shape=self.SHAPE_A), _record_spec())

        assert store.get_by_shape(output_shape_hash=self.SHAPE_B) is None

    def test_an_empty_shape_hash_is_never_a_hit(self, tmp_path: Path) -> None:
        assert FileSurfaceSpecStore(tmp_path).get_by_shape(output_shape_hash="") is None

    def test_a_traversal_shaped_shape_hash_cannot_escape_the_root(
        self, tmp_path: Path
    ) -> None:
        store = FileSurfaceSpecStore(tmp_path)
        store.put(
            SpecKey.build(
                server="customsvc",
                tool="get_thing",
                output_shape_hash="../../etc/passwd",
                skill_version=1,
            ),
            StoredSpec.from_generation(
                key=_key(), spec=_record_spec(), generator_model=""
            ),
        )

        written = [path for path in tmp_path.rglob("*.json")]
        assert written
        assert all(tmp_path in path.parents for path in written)

    def test_ac15_a_failure_for_one_shape_does_not_suppress_another(
        self, tmp_path: Path
    ) -> None:
        store = FileSurfaceSpecStore(tmp_path)
        store.record_failure(_key(shape=self.SHAPE_A), "bad json", "{")

        assert store.has_failure(_key(shape=self.SHAPE_A)) is True
        assert store.has_failure(_key(shape=self.SHAPE_B)) is False

    def test_ac15_no_shape_keyed_failure_pointer_exists(self, tmp_path: Path) -> None:
        # The structural guarantee behind AC15: there is no by-shape index for
        # failures to be found through.
        store = FileSurfaceSpecStore(tmp_path)
        store.record_failure(_key(shape=self.SHAPE_A), "bad json", "{")

        assert not (tmp_path / "by_shape").exists()
