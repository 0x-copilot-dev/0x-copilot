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
