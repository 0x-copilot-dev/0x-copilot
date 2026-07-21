"""Unit tests for the ``backend-http`` SurfaceSpec store adapter (PRD-08).

Exercises :class:`BackendHttpSurfaceSpecStore` against an in-process fake HTTP
transport (:class:`httpx.MockTransport`) — hit, miss, TTL cache (a second read
within the TTL does not touch HTTP), TTL expiry, PUT-after-generation, and the
best-effort error path — plus the ``SURFACE_SPEC_STORE_BACKEND`` env-selection
factory across ``memory | file | backend`` (acceptance criterion 4). Naming the
flag in each branch keeps the capability out of the dark-capability floor.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx
import pytest

from agent_runtime.capabilities.surfaces.backend_store import (
    BackendHttpSurfaceSpecStore,
    build_surface_spec_store,
)
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

_BASE_URL = "http://backend.test"


def _spec(*, server: str = "linear", tool: str = "get_issue") -> SurfaceSpec:
    return validate_surface_spec(
        {
            "spec_version": 1,
            "archetype": "record",
            "source": {"server": server, "tool": tool},
            "title_path": "issue.title",
        }
    )


def _view(spec: SurfaceSpec, *, shape: str = "h1") -> dict[str, Any]:
    """The wire ``spec`` view the backend registry returns."""

    return {
        "spec_id": "sspec_1",
        "server": spec.source.server,
        "tool": spec.source.tool,
        "output_shape_hash": shape,
        "spec_schema_version": 1,
        "skill_version": 1,
        "origin": "generated",
        "generator_model": "haiku-test",
        "spec": spec.model_dump(mode="json", exclude_none=True),
        "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc).isoformat(),
    }


class _Clock:
    """A hand-cranked monotonic clock for TTL assertions."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class _FakeBackend:
    """Records requests and replies from a scripted spec (or a miss)."""

    def __init__(self, *, spec: SurfaceSpec | None, shape: str = "h1") -> None:
        self._spec = spec
        self._shape = shape
        self.get_calls: list[httpx.Request] = []
        self.put_calls: list[httpx.Request] = []
        self.fail_status: int | None = None

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            self.get_calls.append(request)
            if self.fail_status is not None:
                return httpx.Response(self.fail_status)
            spec_view = _view(self._spec, shape=self._shape) if self._spec else None
            return httpx.Response(200, json={"spec": spec_view})
        if request.method == "PUT":
            self.put_calls.append(request)
            if self.fail_status is not None:
                return httpx.Response(self.fail_status)
            body = _view(self._spec, shape=self._shape) if self._spec else {}
            return httpx.Response(201, json=body)
        return httpx.Response(405)  # pragma: no cover

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handler))


def _store(
    backend: _FakeBackend, *, clock: _Clock | None = None, ttl: float = 600.0
) -> BackendHttpSurfaceSpecStore:
    return BackendHttpSurfaceSpecStore(
        base_url=_BASE_URL,
        org_id="orgA",
        user_id="u1",
        http_client=backend.client(),
        ttl_seconds=ttl,
        clock=clock or _Clock(),
    )


class TestGet:
    def test_hit_returns_spec(self) -> None:
        backend = _FakeBackend(spec=_spec())
        store = _store(backend)
        got = store.get(server="linear", tool="get_issue")
        assert got is not None
        assert got.archetype.value == "record"
        assert len(backend.get_calls) == 1
        # Identity + query params are on the request.
        params = dict(backend.get_calls[0].url.params)
        assert params["org_id"] == "orgA"
        assert params["server"] == "linear"

    def test_miss_returns_none(self) -> None:
        backend = _FakeBackend(spec=None)
        store = _store(backend)
        assert store.get(server="nope", tool="missing") is None

    def test_http_error_degrades_to_none(self) -> None:
        backend = _FakeBackend(spec=_spec())
        backend.fail_status = 500
        store = _store(backend)
        assert store.get(server="linear", tool="get_issue") is None


class TestGetStored:
    def _key(self) -> SpecKey:
        return SpecKey.build(
            server="linear", tool="get_issue", output_shape_hash="h1", skill_version=1
        )

    def test_hit_returns_stored(self) -> None:
        backend = _FakeBackend(spec=_spec())
        store = _store(backend)
        stored = store.get_stored(self._key())
        assert stored is not None
        assert stored.output_shape_hash == "h1"
        assert stored.generator_model == "haiku-test"
        params = dict(backend.get_calls[0].url.params)
        assert params["shape_hash"] == "h1"
        assert params["schema_version"] == "1"

    def test_miss_returns_none(self) -> None:
        backend = _FakeBackend(spec=None)
        store = _store(backend)
        assert store.get_stored(self._key()) is None


class TestCache:
    def test_second_get_within_ttl_does_not_refetch(self) -> None:
        backend = _FakeBackend(spec=_spec())
        clock = _Clock()
        store = _store(backend, clock=clock, ttl=600.0)
        store.get(server="linear", tool="get_issue")
        store.get(server="linear", tool="get_issue")
        assert len(backend.get_calls) == 1  # served from cache the second time

    def test_cached_miss_not_refetched_within_ttl(self) -> None:
        backend = _FakeBackend(spec=None)
        store = _store(backend)
        assert store.get(server="x", tool="y") is None
        assert store.get(server="x", tool="y") is None
        assert len(backend.get_calls) == 1

    def test_refetch_after_ttl_expiry(self) -> None:
        backend = _FakeBackend(spec=_spec())
        clock = _Clock()
        store = _store(backend, clock=clock, ttl=600.0)
        store.get(server="linear", tool="get_issue")
        clock.advance(601.0)
        store.get(server="linear", tool="get_issue")
        assert len(backend.get_calls) == 2

    def test_get_stored_cache_independent_of_get(self) -> None:
        backend = _FakeBackend(spec=_spec())
        store = _store(backend)
        store.get(server="linear", tool="get_issue")
        store.get_stored(
            SpecKey.build(
                server="linear",
                tool="get_issue",
                output_shape_hash="h1",
                skill_version=1,
            )
        )
        assert len(backend.get_calls) == 2  # different cache keys


class TestPut:
    def test_put_posts_and_populates_cache(self) -> None:
        backend = _FakeBackend(spec=_spec())
        store = _store(backend)
        key = SpecKey.build(
            server="linear", tool="get_issue", output_shape_hash="h1", skill_version=1
        )
        stored = StoredSpec.from_generation(
            key=key, spec=_spec(), generator_model="haiku-test"
        )
        store.put(key, stored)
        assert len(backend.put_calls) == 1
        # A subsequent full-key read is served from the cache (no GET).
        got = store.get_stored(key)
        assert got is not None
        assert len(backend.get_calls) == 0
        # And the coarse (server, tool) read is served from cache too.
        spec = store.get(server="linear", tool="get_issue")
        assert spec is not None
        assert len(backend.get_calls) == 0

    def test_put_body_carries_key_and_spec(self) -> None:
        import json

        backend = _FakeBackend(spec=_spec())
        store = _store(backend)
        key = SpecKey.build(
            server="linear", tool="get_issue", output_shape_hash="hZ", skill_version=3
        )
        stored = StoredSpec.from_generation(key=key, spec=_spec(), generator_model="m")
        store.put(key, stored)
        body = json.loads(backend.put_calls[0].content)
        assert body["output_shape_hash"] == "hZ"
        assert body["skill_version"] == 3
        assert body["origin"] == "generated"
        assert body["spec"]["archetype"] == "record"

    def test_put_error_is_swallowed(self) -> None:
        backend = _FakeBackend(spec=_spec())
        backend.fail_status = 500
        store = _store(backend)
        key = SpecKey.build(
            server="linear", tool="get_issue", output_shape_hash="h1", skill_version=1
        )
        stored = StoredSpec.from_generation(key=key, spec=_spec(), generator_model="m")
        # Must not raise — generation is fire-and-forget.
        store.put(key, stored)


class TestFailureMethods:
    def test_record_failure_is_noop_and_has_failure_false(self) -> None:
        backend = _FakeBackend(spec=_spec())
        store = _store(backend)
        key = SpecKey.build(
            server="linear", tool="get_issue", output_shape_hash="h1", skill_version=1
        )
        store.record_failure(key, "boom", "raw")
        assert store.has_failure(key) is False


class TestEnvSelection:
    """Acceptance criterion 4: SURFACE_SPEC_STORE_BACKEND selects the impl."""

    def test_memory(self) -> None:
        store = build_surface_spec_store(
            environ={"SURFACE_SPEC_STORE_BACKEND": "memory"}
        )
        assert isinstance(store, InMemorySurfaceSpecStore)

    def test_file_with_root(self, tmp_path: Any) -> None:
        store = build_surface_spec_store(
            environ={
                "SURFACE_SPEC_STORE_BACKEND": "file",
                "SURFACE_SPEC_STORE_ROOT": str(tmp_path),
            }
        )
        assert isinstance(store, FileSurfaceSpecStore)

    def test_file_without_root_falls_back_to_memory(self) -> None:
        store = build_surface_spec_store(environ={"SURFACE_SPEC_STORE_BACKEND": "file"})
        assert isinstance(store, InMemorySurfaceSpecStore)

    def test_backend(self) -> None:
        store = build_surface_spec_store(
            environ={
                "SURFACE_SPEC_STORE_BACKEND": "backend",
                "BACKEND_BASE_URL": _BASE_URL,
            },
            org_id="orgA",
            user_id="u1",
            http_client=_FakeBackend(spec=_spec()).client(),
        )
        assert isinstance(store, BackendHttpSurfaceSpecStore)

    def test_backend_end_to_end_via_factory(self) -> None:
        backend = _FakeBackend(spec=_spec())
        store = build_surface_spec_store(
            environ={
                "SURFACE_SPEC_STORE_BACKEND": "backend",
                "BACKEND_BASE_URL": _BASE_URL,
            },
            org_id="orgA",
            user_id="u1",
            http_client=backend.client(),
        )
        assert isinstance(store, BackendHttpSurfaceSpecStore)
        assert store.get(server="linear", tool="get_issue") is not None

    def test_unset_defaults_to_memory_without_file_root(self) -> None:
        store = build_surface_spec_store(environ={})
        assert isinstance(store, InMemorySurfaceSpecStore)


@pytest.mark.parametrize("value", ["memory", "file", "backend"])
def test_env_selection_names_each_value(value: str) -> None:
    """Explicitly reference each SURFACE_SPEC_STORE_BACKEND value (not dark)."""

    env = {"SURFACE_SPEC_STORE_BACKEND": value}
    kwargs: dict[str, Any] = {"environ": env}
    if value == "backend":
        env["BACKEND_BASE_URL"] = _BASE_URL
        kwargs.update(
            org_id="orgA",
            user_id="u1",
            http_client=_FakeBackend(spec=_spec()).client(),
        )
    store = build_surface_spec_store(**kwargs)
    assert store is not None
