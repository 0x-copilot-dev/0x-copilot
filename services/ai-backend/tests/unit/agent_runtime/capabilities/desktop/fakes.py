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

    # --- write ops (mirror host-fs.ts WRITE semantics) ---

    def write(self, path: str, content: bytes) -> dict[str, object] | tuple[str, None]:
        """Create-or-overwrite ``path`` (rejects clobbering a directory)."""
        if path in self._dirs() and path not in self.files:
            return ("not_a_file", None)
        created = path not in self.files
        self.files[path] = content
        return {"path": path, "bytesWritten": len(content), "created": created}

    def edit(self, path: str, content: bytes) -> dict[str, object] | tuple[str, None]:
        """Full-content replace of an EXISTING file (``not_found`` when absent)."""
        if path not in self.files:
            return ("not_found", None)
        self.files[path] = content
        return {"path": path, "bytesWritten": len(content)}

    def mkdir(self, path: str) -> dict[str, object] | tuple[str, None]:
        """Create a single directory (idempotent; collides with a file)."""
        if path in self.files:
            return ("not_a_directory", None)
        created = path not in self._dirs()
        # A directory materializes once it holds a child; record a marker child
        # so subsequent ``list`` reflects the new (empty) directory.
        self.files.setdefault(f"{path}/.keep", b"")
        return {"path": path, "created": created}

    def delete(self, path: str) -> dict[str, object] | tuple[str, None]:
        """Unlink a file, or rmdir an EMPTY directory."""
        if path in self.files:
            del self.files[path]
            return {"path": path, "type": "file"}
        if path in self._dirs():
            children = [p for p in self.files if p.startswith(f"{path}/")]
            if children:
                return ("invalid_request", None)
            return {"path": path, "type": "dir"}
        return ("not_found", None)

    def move(
        self, from_path: str, to_path: str
    ) -> dict[str, object] | tuple[str, None]:
        """Rename an existing file (dir move not modelled — not backend-reachable)."""
        if from_path not in self.files:
            return ("not_found", None)
        self.files[to_path] = self.files.pop(from_path)
        return {"from": from_path, "to": to_path, "type": "file"}


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
    #: Minted run-capability contexts → the grant-mode snapshot pinned at begin.
    run_contexts: dict[str, dict[str, str]] = field(default_factory=dict)
    _rcx_counter: int = 0

    _GRANTS_SNAPSHOT = "/v1/grants/snapshot"
    _RUNS_BEGIN = "/v1/runs/begin"
    _RUNS_END = "/v1/runs/end"
    _ROUTES = {
        "/v1/fs/stat": "stat",
        "/v1/fs/list": "list",
        "/v1/fs/read": "read",
        "/v1/fs/glob": "glob",
        "/v1/fs/grep": "grep",
        "/v1/fs/write": "write",
        "/v1/fs/edit": "edit",
        "/v1/fs/mkdir": "mkdir",
        "/v1/fs/delete": "delete",
        "/v1/fs/move": "move",
    }
    # Minimum grant mode each route requires (mirrors broker.ts).
    _ROUTE_REQUIRED_MODE = {
        "write": "read_write_no_delete",
        "edit": "read_write_no_delete",
        "mkdir": "read_write_no_delete",
        "delete": "read_write",
        "move": "read_write",
    }
    _MODE_RANK = {"read_only": 0, "read_write_no_delete": 1, "read_write": 2}
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
        if route == self._RUNS_BEGIN:
            return httpx.Response(200, json=self._begin_run())
        if route == self._RUNS_END:
            released = self.run_contexts.pop(body.get("run_capability_context"), None)
            return httpx.Response(200, json={"released": released is not None})
        op = self._ROUTES.get(route)
        if op is None:
            return httpx.Response(404, json={"error": "not_found"})
        grant_id = body.get("grant_id")
        fs = self.grants.get(grant_id)
        if fs is None:
            return httpx.Response(403, json={"error": "grant_required"})
        # Mode-gate the mutating routes exactly as the broker does, resolving the
        # grant mode from the run's PINNED snapshot when a context is supplied.
        required = self._ROUTE_REQUIRED_MODE.get(op)
        if required is not None:
            mode = self._resolve_mode(grant_id, body.get("run_capability_context"))
            if mode is None:
                return httpx.Response(403, json={"error": "grant_required"})
            if self._MODE_RANK.get(mode, -1) < self._MODE_RANK[required]:
                return httpx.Response(403, json={"error": "permission_denied"})
        result = self._dispatch(fs, op, body)
        if isinstance(result, tuple):
            code, _ = result
            return httpx.Response(
                self._ERROR_STATUS.get(code, 500), json={"error": code}
            )
        return httpx.Response(200, json=result)

    def _resolve_mode(self, grant_id: str, run_context: str | None) -> str | None:
        """Resolve the grant's mode against the pinned snapshot or live state."""
        if run_context is not None:
            pinned = self.run_contexts.get(run_context)
            if pinned is None:
                return None
            return pinned.get(grant_id)
        return self.grant_meta.get(grant_id, {}).get("mode", "read_only")

    def _begin_run(self) -> dict[str, object]:
        """Pin the CURRENT active grant modes under a fresh, opaque context id."""
        self._rcx_counter += 1
        rcx = f"rcx_fake_{self._rcx_counter}"
        self.run_contexts[rcx] = {
            grant_id: self.grant_meta.get(grant_id, {}).get("mode", "read_only")
            for grant_id in self.grants
        }
        return {
            "runCapabilityContext": rcx,
            "snapshotId": "snap-fake",
            "capturedAt": 1000,
            "grants": self._snapshot()["grants"],
        }

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
        if op == "grep":
            return fs.grep(body["pattern"], body.get("path_glob"))
        if op == "write":
            return fs.write(body["path"], base64.b64decode(body["content_base64"]))
        if op == "edit":
            return fs.edit(body["path"], base64.b64decode(body["content_base64"]))
        if op == "mkdir":
            return fs.mkdir(body["path"])
        if op == "delete":
            return fs.delete(body["path"])
        return fs.move(body["from"], body["to"])

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
