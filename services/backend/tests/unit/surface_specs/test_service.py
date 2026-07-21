"""Unit tests for the SurfaceSpec registry service + in-memory store (PRD-08)."""

from __future__ import annotations

import pytest

from backend_app.surface_specs import (
    InMemorySurfaceSpecStore,
    SurfaceSpecOrigin,
    SurfaceSpecSchemaError,
    SurfaceSpecService,
    SurfaceSpecUpsert,
)


def _spec(*, server: str = "linear", tool: str = "get_issue") -> dict[str, object]:
    return {
        "spec_version": 1,
        "archetype": "record",
        "source": {"server": server, "tool": tool},
        "title_path": "issue.title",
        "fields": [{"label": "State", "path": "issue.state.name"}],
    }


def _upsert(
    *,
    server: str = "linear",
    tool: str = "get_issue",
    shape: str = "h1",
    origin: SurfaceSpecOrigin = SurfaceSpecOrigin.GENERATED,
    spec: dict[str, object] | None = None,
) -> SurfaceSpecUpsert:
    return SurfaceSpecUpsert(
        server=server,
        tool=tool,
        output_shape_hash=shape,
        origin=origin,
        generator_model="haiku-test",
        spec=spec or _spec(server=server, tool=tool),
    )


@pytest.fixture
def service() -> SurfaceSpecService:
    return SurfaceSpecService(store=InMemorySurfaceSpecStore())


class TestRoundTrip:
    def test_put_then_get_by_tool(self, service: SurfaceSpecService) -> None:
        service.put_spec(org_id="orgA", user_id="u1", upsert=_upsert())
        got = service.get_spec(org_id="orgA", server="linear", tool="get_issue")
        assert got is not None
        assert got.spec["archetype"] == "record"
        assert got.origin is SurfaceSpecOrigin.GENERATED
        assert got.generator_model == "haiku-test"

    def test_put_then_get_by_full_key(self, service: SurfaceSpecService) -> None:
        service.put_spec(org_id="orgA", user_id="u1", upsert=_upsert())
        got = service.get_spec(
            org_id="orgA",
            server="linear",
            tool="get_issue",
            output_shape_hash="h1",
            spec_schema_version=1,
            skill_version=1,
        )
        assert got is not None
        assert got.output_shape_hash == "h1"

    def test_full_key_miss_returns_none(self, service: SurfaceSpecService) -> None:
        service.put_spec(org_id="orgA", user_id="u1", upsert=_upsert(shape="h1"))
        assert (
            service.get_spec(
                org_id="orgA",
                server="linear",
                tool="get_issue",
                output_shape_hash="different",
                spec_schema_version=1,
                skill_version=1,
            )
            is None
        )

    def test_upsert_is_idempotent_on_key(self, service: SurfaceSpecService) -> None:
        first = service.put_spec(org_id="orgA", user_id="u1", upsert=_upsert())
        second = service.put_spec(org_id="orgA", user_id="u1", upsert=_upsert())
        # Same identity -> same row id, replaced in place (not a duplicate).
        assert first.spec_id == second.spec_id


class TestOrgIsolation:
    def test_org_b_cannot_read_org_a(self, service: SurfaceSpecService) -> None:
        service.put_spec(org_id="orgA", user_id="u1", upsert=_upsert())
        assert (
            service.get_spec(org_id="orgB", server="linear", tool="get_issue") is None
        )

    def test_same_key_distinct_across_orgs(self, service: SurfaceSpecService) -> None:
        service.put_spec(org_id="orgA", user_id="u1", upsert=_upsert(shape="hA"))
        service.put_spec(org_id="orgB", user_id="u2", upsert=_upsert(shape="hB"))
        a = service.get_spec(org_id="orgA", server="linear", tool="get_issue")
        b = service.get_spec(org_id="orgB", server="linear", tool="get_issue")
        assert a is not None and b is not None
        assert a.output_shape_hash == "hA"
        assert b.output_shape_hash == "hB"
        assert a.spec_id != b.spec_id


class TestOverridePrecedence:
    def test_curated_override_wins_on_full_key(
        self, service: SurfaceSpecService
    ) -> None:
        service.put_spec(org_id="orgA", user_id="u1", upsert=_upsert())
        service.put_spec(
            org_id="orgA",
            user_id="op",
            upsert=_upsert(origin=SurfaceSpecOrigin.CURATED_OVERRIDE),
        )
        best = service.get_spec(
            org_id="orgA",
            server="linear",
            tool="get_issue",
            output_shape_hash="h1",
            spec_schema_version=1,
            skill_version=1,
        )
        assert best is not None
        assert best.origin is SurfaceSpecOrigin.CURATED_OVERRIDE

    def test_curated_override_wins_on_tool_read(
        self, service: SurfaceSpecService
    ) -> None:
        service.put_spec(org_id="orgA", user_id="u1", upsert=_upsert())
        service.put_spec(
            org_id="orgA",
            user_id="op",
            upsert=_upsert(origin=SurfaceSpecOrigin.CURATED_OVERRIDE),
        )
        best = service.get_spec(org_id="orgA", server="linear", tool="get_issue")
        assert best is not None
        assert best.origin is SurfaceSpecOrigin.CURATED_OVERRIDE

    def test_generated_and_override_coexist(self, service: SurfaceSpecService) -> None:
        gen = service.put_spec(org_id="orgA", user_id="u1", upsert=_upsert())
        ovr = service.put_spec(
            org_id="orgA",
            user_id="op",
            upsert=_upsert(origin=SurfaceSpecOrigin.CURATED_OVERRIDE),
        )
        # Two distinct rows for the same key (one per origin).
        assert gen.spec_id != ovr.spec_id


class TestValidation:
    def test_invalid_spec_raises(self, service: SurfaceSpecService) -> None:
        bad = _spec()
        bad["spec_version"] = 2
        with pytest.raises(SurfaceSpecSchemaError):
            service.put_spec(org_id="orgA", user_id="u1", upsert=_upsert(spec=bad))

    def test_invalid_spec_not_persisted(self, service: SurfaceSpecService) -> None:
        bad = _spec()
        bad["archetype"] = "carousel"
        with pytest.raises(SurfaceSpecSchemaError):
            service.put_spec(org_id="orgA", user_id="u1", upsert=_upsert(spec=bad))
        assert (
            service.get_spec(org_id="orgA", server="linear", tool="get_issue") is None
        )


class TestDelete:
    def test_delete_removes_spec(self, service: SurfaceSpecService) -> None:
        view = service.put_spec(org_id="orgA", user_id="u1", upsert=_upsert())
        assert service.delete_spec(org_id="orgA", spec_id=view.spec_id) is True
        assert (
            service.get_spec(org_id="orgA", server="linear", tool="get_issue") is None
        )

    def test_delete_is_org_scoped(self, service: SurfaceSpecService) -> None:
        view = service.put_spec(org_id="orgA", user_id="u1", upsert=_upsert())
        # Another org cannot delete orgA's row.
        assert service.delete_spec(org_id="orgB", spec_id=view.spec_id) is False
        assert (
            service.get_spec(org_id="orgA", server="linear", tool="get_issue")
            is not None
        )

    def test_delete_missing_returns_false(self, service: SurfaceSpecService) -> None:
        assert service.delete_spec(org_id="orgA", spec_id="sspec_nope") is False
