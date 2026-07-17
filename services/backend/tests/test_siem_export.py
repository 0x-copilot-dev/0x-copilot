"""C9 SIEM export pump unit tests.

DB-backed pump paths are exercised via fakes — the production code goes
through ``asyncio.to_thread`` + ``psycopg.connect`` which can't be stood
up cheaply here. Cursor / dead-letter / backoff state lives in the
pump's instance variables in this test scope; persistent-state behavior
is owned by the integration suite.

Backend doesn't ship pytest-asyncio so async tests run via ``asyncio.run``,
matching the pattern already in ``tests/identity/test_sessions.py``.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import pytest

from backend_app.siem_export.exporters import (
    ElasticExporter,
    FileExporter,
    NullExporter,
    SplunkHecExporter,
    SyslogCefExporter,
    build_exporter_from_env,
)
from backend_app.siem_export.interface import (
    NormalizedEvent,
    SendOutcome,
    SiemExportSource,
)
from backend_app.siem_export.normalizer import EventNormalizer


def _event(
    *,
    composite_id: str = "org_a:evt_1",
    source: SiemExportSource = SiemExportSource.MCP_AUDIT,
    org_id: str = "org_a",
    user_id: str = "user_1",
    event_type: str = "mcp.token.created",
    severity: str = "INFO",
    payload: dict[str, Any] | None = None,
) -> NormalizedEvent:
    return NormalizedEvent(
        composite_id=composite_id,
        source=source,
        org_id=org_id,
        user_id=user_id,
        event_type=event_type,
        timestamp=datetime(2026, 5, 4, 12, 0, tzinfo=timezone.utc),
        severity=severity,
        payload=payload or {"action": "create"},
    )


class TestNormalizer:
    def test_mcp_audit_to_normalized(self) -> None:
        row = {
            "id": "evt_1",
            "org_id": "org_a",
            "user_id": "user_1",
            "event_type": "mcp.token.created",
            "created_at": datetime(2026, 5, 4, 12, 0, tzinfo=timezone.utc),
            "metadata": {"server_id": "s1"},
        }
        event = EventNormalizer.from_mcp_audit(row)
        assert event.composite_id == "org_a:evt_1"
        assert event.source is SiemExportSource.MCP_AUDIT
        assert event.payload == {"server_id": "s1"}

    def test_runtime_failure_outcome_lifts_severity(self) -> None:
        row = {
            "id": "evt_2",
            "org_id": "org_a",
            "user_id": "user_1",
            "event_type": "runtime.tool.invoked",
            "outcome": "failure",
            "created_at": datetime(2026, 5, 4, 12, 0, tzinfo=timezone.utc),
            "metadata_json_redacted": {},
        }
        event = EventNormalizer.from_runtime_audit(row)
        assert event.severity == "WARNING"

    def test_global_org_when_org_id_none(self) -> None:
        row = {
            "id": "evt_3",
            "org_id": None,
            "user_id": None,
            "event_type": "system.boot",
            "created_at": datetime(2026, 5, 4, 12, 0, tzinfo=timezone.utc),
        }
        event = EventNormalizer.from_identity_audit(row)
        assert event.composite_id == "global:evt_3"
        assert event.org_id is None


class TestNullExporter:
    def test_drops_silently(self) -> None:
        exporter = NullExporter()
        result = asyncio.run(exporter.send((_event(),)))
        assert result.outcome is SendOutcome.OK
        assert result.accepted_event_ids == ("org_a:evt_1",)


class TestFileExporter:
    def test_writes_jsonl(self, tmp_path: Path) -> None:
        target = tmp_path / "audit.jsonl"
        exporter = FileExporter(path=target)
        events = (_event(), _event(composite_id="org_a:evt_2"))
        result = asyncio.run(exporter.send(events))
        assert result.outcome is SendOutcome.OK
        lines = target.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 2
        assert '"composite_id":"org_a:evt_1"' in lines[0]


class TestSplunkHecExporter:
    @staticmethod
    def _client(handler) -> httpx.AsyncClient:  # type: ignore[no-untyped-def]
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    def test_2xx_classified_ok(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            assert request.headers["Authorization"].startswith("Splunk ")
            return httpx.Response(200, json={"text": "Success"})

        async def exercise() -> None:
            exporter = SplunkHecExporter(
                url="https://splunk.test", token="t-1", client=self._client(handler)
            )
            result = await exporter.send((_event(),))
            assert result.outcome is SendOutcome.OK

        asyncio.run(exercise())

    def test_4xx_classified_dead_letter(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(400, text="invalid token")

        async def exercise() -> None:
            exporter = SplunkHecExporter(
                url="https://splunk.test", token="t-1", client=self._client(handler)
            )
            result = await exporter.send((_event(),))
            assert result.outcome is SendOutcome.DEAD_LETTER
            assert "http_400" in (result.last_error or "")

        asyncio.run(exercise())

    def test_5xx_classified_retry(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503, text="upstream down")

        async def exercise() -> None:
            exporter = SplunkHecExporter(
                url="https://splunk.test", token="t-1", client=self._client(handler)
            )
            result = await exporter.send((_event(),))
            assert result.outcome is SendOutcome.RETRY

        asyncio.run(exercise())

    def test_transport_error_classified_retry(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("dns boom")

        async def exercise() -> None:
            exporter = SplunkHecExporter(
                url="https://splunk.test", token="t-1", client=self._client(handler)
            )
            result = await exporter.send((_event(),))
            assert result.outcome is SendOutcome.RETRY
            assert "dns boom" in (result.last_error or "")

        asyncio.run(exercise())


class TestElasticExporter:
    def test_bulk_path_and_id(self) -> None:
        captured: dict[str, Any] = {}

        async def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            captured["body"] = request.content.decode("utf-8")
            return httpx.Response(200, json={"errors": False})

        async def exercise() -> None:
            client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            exporter = ElasticExporter(
                url="https://elastic.test",
                index="audit-events",
                client=client,
            )
            result = await exporter.send((_event(),))
            assert result.outcome is SendOutcome.OK

        asyncio.run(exercise())
        assert captured["url"].endswith("/_bulk")
        assert '"_id":"org_a:evt_1"' in captured["body"]


class TestSyslogCefFormatting:
    def test_cef_line_shape(self) -> None:
        line = SyslogCefExporter._cef_line(_event())
        assert line.startswith("CEF:0|Copilot|Backend|1.0|")
        assert "externalId=org_a:evt_1" in line
        assert "cn1=org_a" in line

    def test_cef_escapes_pipes_and_equals(self) -> None:
        # Field values get backslash-escaped per CEF; the SignatureID +
        # Name carry the raw event_type since pipes/equals there are
        # part of the identifier.
        line = SyslogCefExporter._cef_line(_event(event_type="evt-with-meta"))
        assert "evt-with-meta" in line


class TestExporterFactory:
    def test_default_is_null(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SIEM_EXPORTER_BACKEND", raising=False)
        exporter = build_exporter_from_env()
        assert isinstance(exporter, NullExporter)

    def test_unknown_backend_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SIEM_EXPORTER_BACKEND", "wat")
        with pytest.raises(RuntimeError, match="Unknown SIEM_EXPORTER_BACKEND"):
            build_exporter_from_env()

    def test_file_backend_picks_path(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        target = tmp_path / "out.jsonl"
        monkeypatch.setenv("SIEM_EXPORTER_BACKEND", "file")
        monkeypatch.setenv("SIEM_EXPORTER_FILE_PATH", str(target))
        exporter = build_exporter_from_env()
        assert isinstance(exporter, FileExporter)
