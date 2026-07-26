#!/usr/bin/env python3
"""Stdio-only MCP adapter for the local Generative Workflows fixture store.

It intentionally implements the compact MCP surface needed by the supervised
journeys: initialize, tools/list, and tools/call.  There is no listener, HTTP
client, credential option, or background network task.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

try:  # Supports both ``python -m`` and direct stdio command execution.
    from .fixture_connector import FixtureError, FixtureStore
except ImportError:  # pragma: no cover - direct script path is the Desktop form
    from fixture_connector import FixtureError, FixtureStore

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SCENARIO = ROOT / "scenarios" / "local-communications.json"

TOOL_NAMES = (
    "fixture_manifest",
    "fixture_reset",
    "fixture_audit",
    "mail_list_threads",
    "mail_get_thread",
    "mail_draft_reply",
    "mail_send_draft",
    "timeline_list_posts",
    "timeline_get_post",
    "timeline_draft_reply_post",
    "timeline_publish_draft",
    "discord_list_channels",
    "discord_get_messages",
    "discord_draft_announcement",
    "discord_publish_announcement",
    "workspace_list",
    "workspace_read",
    "workspace_stat",
    "workspace_write_revision",
    "workspace_apply_rowset",
)


def tool_definitions() -> list[dict[str, Any]]:
    """Return MCP tool metadata. Full argument validation is domain-owned."""
    return [
        {
            "name": name,
            "description": "Local fixture-only Generative Workflows operation; never reaches external services.",
            "inputSchema": {"type": "object", "additionalProperties": True},
        }
        for name in TOOL_NAMES
    ]


def _error(
    request_id: object, code: int, message: str, *, data: dict[str, Any] | None = None
) -> dict[str, Any]:
    response: dict[str, Any] = {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message},
    }
    if data is not None:
        response["error"]["data"] = data
    return response


def handle(store: FixtureStore, request: dict[str, Any]) -> dict[str, Any] | None:
    """Handle one JSON-RPC message. Notifications deliberately have no reply."""
    request_id = request.get("id")
    method = request.get("method")
    if request.get("jsonrpc") != "2.0" or not isinstance(method, str):
        return _error(request_id, -32600, "invalid JSON-RPC request")
    if method == "notifications/initialized":
        return None
    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {
                    "name": "generative-workflows-local-fixture",
                    "version": "1.0.0-test",
                },
            },
        }
    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {"tools": tool_definitions()},
        }
    if method == "tools/call":
        params = request.get("params")
        if not isinstance(params, dict) or not isinstance(params.get("name"), str):
            return _error(request_id, -32602, "tools/call requires params.name")
        arguments = params.get("arguments", {})
        if not isinstance(arguments, dict):
            return _error(request_id, -32602, "tools/call arguments must be an object")
        try:
            result = store.call(params["name"], arguments)
        except FixtureError as error:
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(error.as_dict(), sort_keys=True),
                        }
                    ],
                    "isError": True,
                },
            }
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "content": [
                    {"type": "text", "text": json.dumps(result, sort_keys=True)}
                ],
                "structuredContent": result,
                "isError": False,
            },
        }
    return _error(request_id, -32601, "method not found")


def main() -> int:
    # The scenario path is deliberately fixed; accepting a CLI path or URL would
    # weaken reproducibility and make fixture selection a data-exfiltration seam.
    store = FixtureStore.from_path(DEFAULT_SCENARIO)
    for raw in sys.stdin:
        try:
            request = json.loads(raw)
            if not isinstance(request, dict):
                response = _error(None, -32600, "invalid JSON-RPC request")
            else:
                response = handle(store, request)
        except json.JSONDecodeError:
            response = _error(None, -32700, "parse error")
        if response is not None:
            sys.stdout.write(
                json.dumps(response, sort_keys=True, separators=(",", ":")) + "\n"
            )
            sys.stdout.flush()
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised by subprocess test
    raise SystemExit(main())
