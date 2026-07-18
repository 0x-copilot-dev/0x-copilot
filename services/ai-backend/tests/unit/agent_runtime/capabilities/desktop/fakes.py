"""In-memory fake capability broker for desktop workspace-backend tests.

Models the broker's ``/v1/fs/*`` wire contract over a small in-memory tree so
the adapter can be exercised end-to-end without Electron or a real filesystem.
Records every request so tests can assert auth headers and that a **host path
never leaves the process** (requests carry only ``grant_id`` + virtual path).
"""

from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass, field

import httpx

from agent_runtime.capabilities.desktop.broker_client import (
    BrokerClientConfig,
    DesktopBrokerClient,
)

TEST_TOKEN = "fake-broker-token-do-not-log-000000000000000"
TEST_BASE_URL = "http://127.0.0.1:54321"
TEST_PROTOCOL = "1"


def _glob_to_regex(pattern: str) -> re.Pattern[str]:
    """Compile a glob into an anchored regex (mirrors host-fs ``globToRegExp``)."""
    out = ""
    i = 0
    while i < len(pattern):
        char = pattern[i]
        if char == "*" and pattern[i + 1 : i + 2] == "*":
            after = pattern[i + 2 : i + 3]
            if after == "/":
                out += "(?:.*/)?"
                i += 3
                continue
            out += ".*"
            i += 2
            continue
        if char == "*":
            out += "[^/]*"
        elif char == "?":
            out += "[^/]"
        else:
            out += re.escape(char)
        i += 1
    return re.compile(f"^{out}$")


@dataclass
class FakeBrokerFs:
    """A tiny in-memory filesystem addressed by grant-relative POSIX paths."""

    files: dict[str, bytes]

    def _dirs(self) -> set[str]:
        dirs = {""}
        for path in self.files:
            parts = path.split("/")
            for depth in range(1, len(parts)):
                dirs.add("/".join(parts[:depth]))
        return dirs

    def stat(self, path: str) -> dict[str, object] | tuple[str, None]:
        if path in self.files:
            return {
                "type": "file",
                "size": len(self.files[path]),
                "mtimeMs": 1000.0,
                "name": path.split("/")[-1],
            }
        if path in self._dirs():
            return {
                "type": "dir",
                "size": 0,
                "mtimeMs": 1000.0,
                "name": path.split("/")[-1],
            }
        return ("not_found", None)

    def list(self, path: str) -> dict[str, object] | tuple[str, None]:
        dirs = self._dirs()
        if path not in dirs:
            if path in self.files:
                return ("not_a_directory", None)
            return ("not_found", None)
        prefix = f"{path}/" if path else ""
        children: dict[str, str] = {}
        for file_path in self.files:
            if prefix and not file_path.startswith(prefix):
                continue
            if not prefix and "/" not in file_path:
                children[file_path] = "file"
                continue
            rest = file_path[len(prefix) :]
            head = rest.split("/")[0]
            children[head] = "dir" if "/" in rest else "file"
        entries = [
            {"name": name, "type": kind} for name, kind in sorted(children.items())
        ]
        return {"entries": entries, "truncated": False}

    def read(
        self, path: str, offset: int | None, max_bytes: int | None
    ) -> dict[str, object] | tuple[str, None]:
        if path in self._dirs() and path not in self.files:
            return ("not_a_file", None)
        if path not in self.files:
            return ("not_found", None)
        data = self.files[path]
        start = offset or 0
        cap = max_bytes if max_bytes is not None else len(data)
        window = data[start : start + cap]
        return {
            "base64": base64.b64encode(window).decode("ascii"),
            "size": len(data),
            "offset": start,
            "bytesRead": len(window),
            "truncated": start + len(window) < len(data),
        }

    def glob(
        self, pattern: str, max_results: int | None
    ) -> dict[str, object] | tuple[str, None]:
        matcher = _glob_to_regex(pattern)
        paths = sorted(p for p in self.files if matcher.match(p))
        limit = max_results if max_results is not None else len(paths)
        return {
            "paths": paths[:limit],
            "truncated": len(paths) > limit,
            "scanned": len(self.files),
        }

    def grep(
        self, pattern: str, path_glob: str | None
    ) -> dict[str, object] | tuple[str, None]:
        path_matcher = _glob_to_regex(path_glob) if path_glob is not None else None
        hits: list[dict[str, object]] = []
        for file_path in sorted(self.files):
            if path_matcher is not None and not path_matcher.match(file_path):
                continue
            try:
                text = self.files[file_path].decode("utf-8")
            except UnicodeDecodeError:
                continue
            for line_no, line in enumerate(text.split("\n"), start=1):
                col = line.find(pattern)
                if col >= 0:
                    hits.append(
                        {
                            "path": file_path,
                            "line": line_no,
                            "column": col + 1,
                            "preview": line[:240],
                        }
                    )
        return {"hits": hits, "truncated": False, "filesScanned": len(self.files)}


@dataclass
class RecordingBroker:
    """MockTransport-backed fake broker: dispatches ``/v1/fs/*`` and records requests.

    ``grant_meta`` optionally overrides the path-free grant projection returned
    by ``/v1/grants/snapshot`` (``mount`` / ``label`` / ``mode`` / ``status``)
    per grant id; grants without an override default to an active read-only
    grant whose ``label`` and ``mount`` derive from the grant id. This lets a
    test drive the full workspace wiring — grant snapshot → mount table → fs
    reads — against one in-memory broker.
    """

    grants: dict[str, FakeBrokerFs]
    grant_meta: dict[str, dict[str, str]] = field(default_factory=dict)
    requests: list[tuple[str, dict[str, str], dict[str, object]]] = field(
        default_factory=list
    )

    _GRANTS_SNAPSHOT = "/v1/grants/snapshot"
    _ROUTES = {
        "/v1/fs/stat": "stat",
        "/v1/fs/list": "list",
        "/v1/fs/read": "read",
        "/v1/fs/glob": "glob",
        "/v1/fs/grep": "grep",
    }
    _ERROR_STATUS = {
        "grant_required": 403,
        "not_found": 404,
        "not_a_directory": 400,
        "not_a_file": 400,
        "permission_denied": 403,
        "invalid_path": 400,
        "invalid_request": 400,
        "too_large": 413,
        "unsupported": 404,
    }

    def _handler(self, request: httpx.Request) -> httpx.Response:
        route = request.url.path
        body = json.loads(request.content) if request.content else {}
        self.requests.append((route, dict(request.headers), body))
        if route == self._GRANTS_SNAPSHOT:
            return httpx.Response(200, json=self._snapshot())
        op = self._ROUTES.get(route)
        if op is None:
            return httpx.Response(404, json={"error": "not_found"})
        grant_id = body.get("grant_id")
        fs = self.grants.get(grant_id)
        if fs is None:
            return httpx.Response(403, json={"error": "grant_required"})
        result = self._dispatch(fs, op, body)
        if isinstance(result, tuple):
            code, _ = result
            return httpx.Response(
                self._ERROR_STATUS.get(code, 500), json={"error": code}
            )
        return httpx.Response(200, json=result)

    @staticmethod
    def _dispatch(
        fs: FakeBrokerFs, op: str, body: dict[str, object]
    ) -> dict[str, object] | tuple[str, None]:
        if op == "stat":
            return fs.stat(body["path"])
        if op == "list":
            return fs.list(body["path"])
        if op == "read":
            return fs.read(body["path"], body.get("offset"), body.get("max_bytes"))
        if op == "glob":
            return fs.glob(body["pattern"], body.get("max_results"))
        return fs.grep(body["pattern"], body.get("path_glob"))

    def _snapshot(self) -> dict[str, object]:
        """Build the ``/v1/grants/snapshot`` body (path-free ``BrokerGrant``s)."""
        grants: list[dict[str, object]] = []
        for grant_id in self.grants:
            meta = self.grant_meta.get(grant_id, {})
            grants.append(
                {
                    "grantId": grant_id,
                    "mode": meta.get("mode", "read_only"),
                    "label": meta.get("label", grant_id),
                    "status": meta.get("status", "active"),
                    "mount": meta.get("mount", f"mnt_{grant_id}"),
                }
            )
        return {"snapshotId": "snap-fake", "capturedAt": 1000, "grants": grants}

    def transport(self) -> httpx.MockTransport:
        """An httpx transport wired to this fake broker."""
        return httpx.MockTransport(self._handler)

    def client(self) -> DesktopBrokerClient:
        """A :class:`DesktopBrokerClient` bound to this fake broker."""
        return DesktopBrokerClient(
            BrokerClientConfig(
                base_url=TEST_BASE_URL,
                token=TEST_TOKEN,
                protocol_version=TEST_PROTOCOL,
            ),
            http_client=httpx.AsyncClient(transport=self.transport()),
        )
