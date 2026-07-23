"""``WorkLedgerEmitter`` behaviour (PRD-A3 D3).

Drives the emitter through a recording :data:`EmitFn` (no runtime, no network):
a tool result emits the four events in order; a spec envelope yields a
shaped/registry view + spec-resolved title; a spec-less envelope yields a
generic/schema view + fallback title; a non-mapping (absent) surface yields
classified + read only; ``payload_ref`` is always ``call:<call_id>``;
``class`` is *never* ``"read"`` in A3; a raising ``EmitFn`` is swallowed;
``active()`` is ``None`` when unbound; ``on_spec_generated`` emits the generated
view.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping

from agent_runtime.surfaces_v2.emitter import WorkLedgerEmitter
from agent_runtime.surfaces_v2.ledger_models import LedgerEventType


class RecordingEmitMixin:
    """A recording :data:`EmitFn` + envelope builders for emitter tests."""

    def _make_emitter(self) -> tuple[WorkLedgerEmitter, list[dict[str, object]]]:
        recorded: list[dict[str, object]] = []

        async def _emit(
            event_type_value: str,
            payload: Mapping[str, object],
            summary: str | None,
        ) -> None:
            recorded.append(
                {
                    "event_type": event_type_value,
                    "payload": dict(payload),
                    "summary": summary,
                }
            )

        return WorkLedgerEmitter(emit=_emit), recorded

    @staticmethod
    def _spec_envelope() -> dict[str, object]:
        return {
            "surface_uri": "record://linear/get_issue/issue-1",
            "archetype": "record",
            "state": {
                "spec": {"archetype": "record", "title_path": "issue.title"},
                "data": {"issue": {"title": "ENG-142 Fix streaming reconnect"}},
            },
        }

    @staticmethod
    def _specless_envelope() -> dict[str, object]:
        return {
            "surface_uri": "table://customsvc/list_rows/w-9",
            "archetype": "board",
            "state": {"data": {"rows": [1, 2, 3]}},
        }

    def _run(
        self,
        emitter: WorkLedgerEmitter,
        *,
        surface: object,
        surface_uri: object,
        latency_ms: int | None = 42,
        server: str = "seed:linear",
        tool: str = "Get_Issue",
        call_id: str = "call_01",
        output: object = None,
    ) -> None:
        asyncio.run(
            emitter.on_tool_result(
                server_name=server,
                tool_name=tool,
                call_id=call_id,
                output=output if output is not None else {"k": "v"},
                surface=surface,
                surface_uri=surface_uri,
                latency_ms=latency_ms,
            )
        )


class TestOnToolResult(RecordingEmitMixin):
    def test_spec_envelope_emits_four_events_in_order(self) -> None:
        emitter, recorded = self._make_emitter()
        env = self._spec_envelope()

        self._run(emitter, surface=env, surface_uri=env["surface_uri"])

        assert [row["event_type"] for row in recorded] == [
            LedgerEventType.ACTION_CLASSIFIED.value,
            LedgerEventType.READ_EXECUTED.value,
            LedgerEventType.SURFACE_CREATED.value,
            LedgerEventType.VIEW_DERIVED.value,
        ]

    def test_action_classified_is_always_unknown_default(self) -> None:
        emitter, recorded = self._make_emitter()
        env = self._spec_envelope()

        self._run(emitter, surface=env, surface_uri=env["surface_uri"])

        classified = recorded[0]["payload"]
        assert classified["class"] == "unknown"
        assert classified["basis"] == "default"
        assert classified["connector"] == "linear"  # server_slug strips "seed:"
        assert classified["op"] == "get_issue"  # tool_slug lowercases
        assert classified["v"] == 1

    def test_read_executed_payload_ref_is_call_scheme(self) -> None:
        emitter, recorded = self._make_emitter()
        env = self._spec_envelope()

        self._run(
            emitter, surface=env, surface_uri=env["surface_uri"], call_id="call_XYZ"
        )

        read = recorded[1]["payload"]
        assert read["payload_ref"] == "call:call_XYZ"
        assert read["latency_ms"] == 42
        assert recorded[1]["summary"] == "auto-ran (read)"

    def test_read_executed_omits_latency_when_unavailable(self) -> None:
        emitter, recorded = self._make_emitter()
        env = self._spec_envelope()

        self._run(emitter, surface=env, surface_uri=env["surface_uri"], latency_ms=None)

        assert "latency_ms" not in recorded[1]["payload"]

    def test_spec_envelope_yields_shaped_registry_view_and_title(self) -> None:
        emitter, recorded = self._make_emitter()
        env = self._spec_envelope()

        self._run(emitter, surface=env, surface_uri=env["surface_uri"])

        created = recorded[2]["payload"]
        assert created["surface_id"] == "record://linear/get_issue/issue-1"
        assert created["kind"] == "record"
        assert created["source"] == {"connector": "linear", "op": "get_issue"}
        assert created["title"] == "ENG-142 Fix streaming reconnect"
        assert created["payload_ref"] == "call:call_01"
        derived = recorded[3]["payload"]
        assert derived["tier"] == "shaped"
        assert derived["basis"] == "registry"

    def test_specless_envelope_yields_generic_schema_view_and_fallback_title(
        self,
    ) -> None:
        emitter, recorded = self._make_emitter()
        env = self._specless_envelope()

        self._run(
            emitter,
            surface=env,
            surface_uri=env["surface_uri"],
            server="customsvc",
            tool="list_rows",
        )

        created = recorded[2]["payload"]
        # board archetype maps to table kind (D1).
        assert created["kind"] == "table"
        # No spec ⇒ "<connector> · <op>" fallback title.
        assert created["title"] == "customsvc · list_rows"
        derived = recorded[3]["payload"]
        assert derived["tier"] == "generic"
        assert derived["basis"] == "schema"

    def test_absent_surface_emits_classified_and_read_only(self) -> None:
        emitter, recorded = self._make_emitter()

        self._run(emitter, surface=None, surface_uri=None, server="linear")

        assert [row["event_type"] for row in recorded] == [
            LedgerEventType.ACTION_CLASSIFIED.value,
            LedgerEventType.READ_EXECUTED.value,
        ]

    def test_no_input_ever_yields_class_read(self) -> None:
        # Adversarial: neither a spec, a shaped envelope, nor any output can make
        # A3 claim a policy decision (class == "read").
        emitter, recorded = self._make_emitter()
        env = self._spec_envelope()

        self._run(
            emitter,
            surface=env,
            surface_uri=env["surface_uri"],
            output={"class": "read", "action_class": "read", "basis": "catalog"},
        )

        classes = [
            row["payload"].get("class")
            for row in recorded
            if row["event_type"] == LedgerEventType.ACTION_CLASSIFIED.value
        ]
        assert classes == ["unknown"]
        assert all(row["payload"].get("class") != "read" for row in recorded)

    def test_emit_exception_is_swallowed(self) -> None:
        async def _boom(*_args: object, **_kwargs: object) -> None:
            raise RuntimeError("emit exploded")

        emitter = WorkLedgerEmitter(emit=_boom)
        env = self._spec_envelope()

        # Must not raise — a ledger emit never fails a tool call.
        self._run(emitter, surface=env, surface_uri=env["surface_uri"])


class TestOnSpecGenerated(RecordingEmitMixin):
    def test_emits_generated_view(self) -> None:
        emitter, recorded = self._make_emitter()

        asyncio.run(
            emitter.on_spec_generated(
                payload={
                    "surface_uri": "record://linear/get_issue/issue-1",
                    "generator_model": "gpt-5.4-mini",
                }
            )
        )

        assert len(recorded) == 1
        assert recorded[0]["event_type"] == LedgerEventType.VIEW_DERIVED.value
        payload = recorded[0]["payload"]
        assert payload == {
            "v": 1,
            "surface_id": "record://linear/get_issue/issue-1",
            "tier": "shaped",
            "basis": "generated",
            "gen": {"model": "gpt-5.4-mini"},
        }

    def test_missing_surface_uri_emits_nothing(self) -> None:
        emitter, recorded = self._make_emitter()

        asyncio.run(emitter.on_spec_generated(payload={"generator_model": "m"}))

        assert recorded == []

    def test_spec_generated_emit_exception_swallowed(self) -> None:
        async def _boom(*_args: object, **_kwargs: object) -> None:
            raise RuntimeError("nope")

        emitter = WorkLedgerEmitter(emit=_boom)
        asyncio.run(
            emitter.on_spec_generated(
                payload={"surface_uri": "record://x/y/1", "generator_model": "m"}
            )
        )


class TestBinding(RecordingEmitMixin):
    def test_active_is_none_when_unbound(self) -> None:
        assert WorkLedgerEmitter.active() is None

    def test_bind_and_unbind(self) -> None:
        emitter, _ = self._make_emitter()
        token = WorkLedgerEmitter.bind_for_run(emitter)
        try:
            assert WorkLedgerEmitter.active() is emitter
        finally:
            WorkLedgerEmitter.unbind(token)
        assert WorkLedgerEmitter.active() is None
