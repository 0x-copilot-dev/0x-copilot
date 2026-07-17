"""C9 exporters — ship 5: null, file, splunk_hec, elastic, syslog/CEF.

Each exporter implements the same ``send(...)`` shape and distinguishes
2xx/4xx/5xx so the pump can advance the cursor / dead-letter / back off.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
from pathlib import Path

import httpx

from backend_app.siem_export.interface import (
    NormalizedEvent,
    SendOutcome,
    SendResult,
)


_LOGGER = logging.getLogger("backend.siem_export")


class NullExporter:
    """Default exporter — drops events on the floor.

    Used in dev and as the safe default for new deploys; the pump still
    advances cursors so we don't accumulate a backlog the operator has
    to drain on first real-exporter wire-up.
    """

    name = "null"

    async def send(self, events: tuple[NormalizedEvent, ...]) -> SendResult:
        return SendResult(
            outcome=SendOutcome.OK,
            accepted_event_ids=tuple(event.composite_id for event in events),
        )


class FileExporter:
    """JSONL writer for air-gapped Compose deploys.

    Customer ships the file out-of-band. One JSON object per line so
    standard tooling (``jq``, ``logstash`` file input) can ingest.
    """

    name = "file"

    def __init__(self, *, path: str | Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    async def send(self, events: tuple[NormalizedEvent, ...]) -> SendResult:
        if not events:
            return SendResult(outcome=SendOutcome.OK)
        try:
            await asyncio.to_thread(self._append, events)
        except OSError as exc:
            return SendResult(
                outcome=SendOutcome.RETRY,
                last_error=str(exc),
            )
        return SendResult(
            outcome=SendOutcome.OK,
            accepted_event_ids=tuple(event.composite_id for event in events),
        )

    def _append(self, events: tuple[NormalizedEvent, ...]) -> None:
        with self._path.open("a", encoding="utf-8") as fh:
            for event in events:
                fh.write(event.model_dump_json())
                fh.write("\n")


class SplunkHecExporter:
    """Splunk HTTP Event Collector — JSON over HTTPS with token auth."""

    name = "splunk_hec"

    def __init__(
        self,
        *,
        url: str,
        token: str,
        client: httpx.AsyncClient | None = None,
        verify_ssl: bool = True,
    ) -> None:
        self._url = url.rstrip("/") + "/services/collector/event"
        self._token = token
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(10.0), verify=verify_ssl
        )

    async def send(self, events: tuple[NormalizedEvent, ...]) -> SendResult:
        if not events:
            return SendResult(outcome=SendOutcome.OK)
        body = "\n".join(self._splunk_envelope(event) for event in events)
        try:
            response = await self._client.post(
                self._url,
                content=body,
                headers={
                    "Authorization": f"Splunk {self._token}",
                    "Content-Type": "application/json",
                },
            )
        except httpx.HTTPError as exc:
            return SendResult(outcome=SendOutcome.RETRY, last_error=str(exc))
        return self._classify(response, events)

    @staticmethod
    def _splunk_envelope(event: NormalizedEvent) -> str:
        return json.dumps(
            {
                "time": int(event.timestamp.timestamp()),
                "host": event.org_id or "global",
                "source": event.source.value,
                "sourcetype": event.event_type,
                "event": event.model_dump(mode="json"),
            },
            separators=(",", ":"),
        )

    @staticmethod
    def _classify(
        response: httpx.Response, events: tuple[NormalizedEvent, ...]
    ) -> SendResult:
        if 200 <= response.status_code < 300:
            return SendResult(
                outcome=SendOutcome.OK,
                accepted_event_ids=tuple(event.composite_id for event in events),
            )
        if 400 <= response.status_code < 500:
            return SendResult(
                outcome=SendOutcome.DEAD_LETTER,
                last_error=f"http_{response.status_code}: {response.text[:200]}",
                rejected_event_ids=tuple(event.composite_id for event in events),
            )
        return SendResult(
            outcome=SendOutcome.RETRY,
            last_error=f"http_{response.status_code}",
        )


class ElasticExporter:
    """Elastic ``_bulk`` API — newline-delimited action+source pairs."""

    name = "elastic"

    def __init__(
        self,
        *,
        url: str,
        index: str,
        api_key: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._url = url.rstrip("/") + "/_bulk"
        self._index = index
        self._api_key = api_key
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(10.0))

    async def send(self, events: tuple[NormalizedEvent, ...]) -> SendResult:
        if not events:
            return SendResult(outcome=SendOutcome.OK)
        lines = []
        for event in events:
            lines.append(
                json.dumps(
                    {"index": {"_index": self._index, "_id": event.composite_id}},
                    separators=(",", ":"),
                )
            )
            lines.append(event.model_dump_json())
        body = "\n".join(lines) + "\n"
        headers = {"Content-Type": "application/x-ndjson"}
        if self._api_key:
            headers["Authorization"] = f"ApiKey {self._api_key}"
        try:
            response = await self._client.post(self._url, content=body, headers=headers)
        except httpx.HTTPError as exc:
            return SendResult(outcome=SendOutcome.RETRY, last_error=str(exc))
        return SplunkHecExporter._classify(response, events)


class SyslogCefExporter:
    """RFC 5424 syslog frame with an ArcSight CEF-formatted message body."""

    name = "syslog_cef"

    def __init__(
        self,
        *,
        host: str,
        port: int,
        protocol: str = "udp",  # "udp" or "tcp"
    ) -> None:
        if protocol not in {"udp", "tcp"}:
            raise ValueError("protocol must be 'udp' or 'tcp'")
        self._host = host
        self._port = port
        self._protocol = protocol

    async def send(self, events: tuple[NormalizedEvent, ...]) -> SendResult:
        if not events:
            return SendResult(outcome=SendOutcome.OK)
        try:
            await asyncio.to_thread(self._send_blocking, events)
        except OSError as exc:
            return SendResult(outcome=SendOutcome.RETRY, last_error=str(exc))
        return SendResult(
            outcome=SendOutcome.OK,
            accepted_event_ids=tuple(event.composite_id for event in events),
        )

    def _send_blocking(self, events: tuple[NormalizedEvent, ...]) -> None:
        sock_type = socket.SOCK_DGRAM if self._protocol == "udp" else socket.SOCK_STREAM
        with socket.socket(socket.AF_INET, sock_type) as sock:
            if self._protocol == "tcp":
                sock.connect((self._host, self._port))
            for event in events:
                line = self._cef_line(event).encode("utf-8") + b"\n"
                if self._protocol == "udp":
                    sock.sendto(line, (self._host, self._port))
                else:
                    sock.sendall(line)

    @staticmethod
    def _cef_line(event: NormalizedEvent) -> str:
        # CEF:Version|DeviceVendor|DeviceProduct|DeviceVersion|SignatureID|Name|Severity|Extensions
        severity_map = {"INFO": "3", "WARNING": "6", "ERROR": "9"}
        severity = severity_map.get(event.severity.upper(), "3")
        extensions = " ".join(
            f"{key}={SyslogCefExporter._escape(str(value))}"
            for key, value in (
                ("dvc", event.source.value),
                ("suid", event.user_id or ""),
                ("cn1Label", "org_id"),
                ("cn1", event.org_id or ""),
                ("externalId", event.composite_id),
                ("rt", str(int(event.timestamp.timestamp() * 1000))),
            )
            if value
        )
        return (
            f"CEF:0|Copilot|Backend|1.0|"
            f"{event.event_type}|{event.event_type}|{severity}|{extensions}"
        )

    @staticmethod
    def _escape(value: str) -> str:
        # CEF requires escaping pipes, equals, backslashes, and CR/LF.
        return (
            value.replace("\\", "\\\\")
            .replace("|", "\\|")
            .replace("=", "\\=")
            .replace("\n", " ")
            .replace("\r", " ")
        )


def build_exporter_from_env(name: str | None = None) -> "object":
    """Pick an exporter class from ``SIEM_EXPORTER_BACKEND``.

    Default ``null`` keeps existing deploys silent. Real exporters are
    operator-configured per environment.
    """

    backend = (name or os.environ.get("SIEM_EXPORTER_BACKEND", "null")).strip().lower()
    if backend == "null":
        return NullExporter()
    if backend == "file":
        path = os.environ.get("SIEM_EXPORTER_FILE_PATH", "/var/log/siem/audit.jsonl")
        return FileExporter(path=path)
    if backend == "splunk_hec":
        url = os.environ["SIEM_EXPORTER_SPLUNK_URL"]
        token = os.environ["SIEM_EXPORTER_SPLUNK_TOKEN"]
        return SplunkHecExporter(url=url, token=token)
    if backend == "elastic":
        url = os.environ["SIEM_EXPORTER_ELASTIC_URL"]
        index = os.environ.get("SIEM_EXPORTER_ELASTIC_INDEX", "audit-events")
        api_key = os.environ.get("SIEM_EXPORTER_ELASTIC_API_KEY")
        return ElasticExporter(url=url, index=index, api_key=api_key)
    if backend == "syslog_cef":
        host = os.environ["SIEM_EXPORTER_SYSLOG_HOST"]
        port = int(os.environ.get("SIEM_EXPORTER_SYSLOG_PORT", "514"))
        protocol = os.environ.get("SIEM_EXPORTER_SYSLOG_PROTOCOL", "udp")
        return SyslogCefExporter(host=host, port=port, protocol=protocol)
    raise RuntimeError(f"Unknown SIEM_EXPORTER_BACKEND={backend!r}")
