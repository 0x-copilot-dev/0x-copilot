#!/usr/bin/env python3
"""FS-F — the Linear MCP journey, judged on the OUTCOME rather than the absence of a crash.

FS-E answered "what MCP is configured?" and stopped. This one goes the step
further and asks the only question that matters to a user: **when the agent
reaches for Linear, does real Linear data come back, and if not, is the app
honest about why?**

Three things make this journey different from "run a prompt and see if it
throws".

1. **Ground truth is measured, not assumed.** Before judging any claim the app
   makes, the journey probes ``https://mcp.linear.app/mcp`` itself with an
   anonymous, credential-free ``initialize``. The endpoint answers an
   unauthenticated call with ``401`` + ``www-authenticate: Bearer`` in a few
   hundred milliseconds. That answer is proof the peer is *reachable*: it
   received the request, understood it, and refused it. So if the app then
   reports ``connection_failed`` / "The MCP server could not be reached." /
   ``retryable=true``, the journey is not guessing that the copy is wrong — it
   has the counter-evidence in hand, and it says so in those words rather than
   filing the vague "MCP unavailable" that this program has already filed once.

   That exact collapse is what :class:`McpProxyStatusClassifier`
   (``capabilities/mcp/backend_provider.py``) exists to prevent. It is wired
   into the dispatch/lease/release paths. This journey is the live check on
   whether the path a user's first Linear call actually travels is covered too.

2. **A catalog ENTRY is not a configured SERVER, and an authenticated SERVER is
   not a REACHABLE tool.** Everything reported here comes from the running
   app's own authenticated surfaces (``/v1/mcp/catalog``, ``/v1/mcp/servers``,
   ``/v1/mcp/tools``) — never from the source tree, which can only ever say
   what *could* be installed. The gate is deliberately four-part: a row must
   exist, be ``authenticated``, be ``enabled``, and not be projected
   ``access_mode: off`` — because an ``off`` server is dropped from the model's
   cards entirely, so the agent cannot call it no matter how green the row
   looks.

3. **An empty success is a failure.** A tool that returns 200 with nothing in
   it is the exact shape this program has been fooled by four times. So the
   success assertion is on DATA — an issue identifier, an id+title object —
   not on ``status == "completed"``.

**What this journey will not do.** It never types a secret, and it never
completes an OAuth flow. Connecting Linear means authorizing a third party in
the user's name; a journey that did that would be manufacturing a consent
nobody gave it. So when Linear is not connected the honest result is a REPORT
naming the one click a human has to make — exit ``2`` (blocked), the harness's
existing "a declared capability is absent" code. That is a legitimate outcome,
not a failure of the journey, and it is strictly better than a substituted run
that looks like an MCP call and isn't.

**Why this one journey keeps its profile.** Every other FS journey takes a
throwaway ``userData`` directory, and it is right to. This one cannot: a
brand-new device account has zero installed MCP servers, so a
freshly-provisioned run can only ever report "not connected" and the entire
read-only branch below would be unreachable code that never executed once.
So FS-F reuses ONE named profile — the human completes Linear's OAuth in it
a single time, and every later run finds it still connected. ``FS_F_FRESH=1``
opts back into a virgin install for the narrower question "what does a
brand-new install actually have?".

**The connect MUST happen OUT OF BAND — clicking Connect under the harness
cannot work.** ``tools/cli-testing/harness/driver.mjs`` (lines 149-162)
replaces ``shell.openExternal`` in the Electron main process and merely
RECORDS the URL: "suppress the OS-browser open; the test drives Chrome
itself". Every journey runs through that driver, so the consent screen never
opens, the loopback in ``apps/desktop/main/connectors/oauth-coordinator.ts``
never receives a ``?code``, and the flow dies as a redirect-stage
``ConnectorOAuthError`` — ``connect cancelled`` when anything cancels it, or
``loopback redirect timed out`` when left to expire. This cost real debugging
time once; it is a property of the harness, not a Linear or MCP defect.

So: launch the app YOURSELF (a normal launch has a real ``openExternal``)
against the SAME stage and the SAME profile this journey reuses, connect,
quit — then run the journey.

Step 1, once, by hand — note the subdir is ``journey-<DriverSession
name>-reuse``, and this journey is ``DriverSession(name="fs-f-linear-mcp")``::

    COPILOT_RUNTIME_DIR="$PWD/apps/desktop/resources" \\
    COPILOT_DESKTOP_USER_DATA_SUBDIR=journey-fs-f-linear-mcp-reuse \\
        npm run dev --workspace @0x-copilot/desktop
    # "Tools" in the left rail -> Linear -> Connect -> finish OAuth in the
    # browser -> the row must read Connected -> QUIT the app.

Step 2, the journey itself::

    COPILOT_HOME="$PWD/apps/desktop/resources" \\
    COPILOT_JOURNEY_DOTENV=/path/to/services/ai-backend/.env \\
    python3 tools/desktop-journeys/filesystem-access/jF_linear_mcp.py

``COPILOT_RUNTIME_DIR`` in step 1 must be the same directory as
``COPILOT_HOME`` in step 2 — the connection lives in the supervised Postgres
under that staged runtime, so a different stage is a different database. Do
NOT add ``COPILOT_DEV=1`` / ``COPILOT_AUTH_MODE=dev-mint`` to step 1: a staged
``COPILOT_RUNTIME_DIR`` alone already resolves production posture
(``apps/desktop/main/posture.ts``), matching the driver's ``POSTURE=prod``,
whereas dev-mint would sign in a DIFFERENT persona whose connectors this
journey cannot see.

**And re-stage after backend changes.** ``<COPILOT_HOME>/runtime/**`` is a
SNAPSHOT of ``services/*``; a stale stage answers every question below about
code that is no longer in the checkout. See ``tools/desktop-journeys/README.md``.

Privacy: no key, token, header value, or ``.env`` content is ever written to
evidence or a screenshot. Configured header values are recorded as
``{name, secret_set}`` only, log excerpts pass through :func:`_redact`, and
issue titles — the user's own workspace data, which the task explicitly
permits — are capped and bounded.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Final, Iterator

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _fs_journey_lib import (  # noqa: E402
    DEFAULT_LANE,
    TERMINAL,
    DriverSession,
    PreflightSkip,
    _byok_provider,
    _preflight_staged_runtime,
    approval_events,
    assistant_text,
    dump,
    event_name,
    events,
    lane,
    payload_of,
    result,
    run_status,
    runs_for,
    settle_run,
    tool_calls,
    wait_for_conversation_id,
    wait_for_new_run,
)

JOURNEY: Final = "FS-F"
SLUG: Final = "linear"

#: Addressed by SLUG, not by the visible label. The label is view-dependent —
#: solo desktop renders the `connectors` destination as "Tools" and the `tools`
#: destination as "Skills" (verified live: see `rail_labels` in the evidence) —
#: and it also gains a " (3)" suffix whenever the destination carries a badge.
#: The slug survives all of that, so `connectors` is the surface a user reaches
#: by clicking "Tools".
CONNECTORS_RAIL: Final = '[data-destination="connectors"]'
RUN_RAIL: Final = '[data-destination="run"]'
CHATS_RAIL: Final = '[data-destination="chats"]'
CONNECT_CTA: Final = f'[data-testid="connector-available-connect-{SLUG}"]'
NEW_CHAT: Final = "[data-testid=chats-new-chat]"
SIGN_IN: Final = "[data-testid=sign-in-button]"
ADD_KEY: Final = "[data-testid=first-run-add-key]"
COMPOSER: Final = "[data-testid=composer-textarea]"

#: The first-run surface is a FULL-SCREEN gate: it mounts no nav rail at all,
#: so `[data-destination]` matches nothing until the workspace is open. Its
#: "skip — open the workspace" link is the way out that costs no model call.
FTUE_SKIP: Final = "[data-testid=first-run-skip]"

#: The reused profile — see "Why this one journey keeps its profile" above.
#: A human's OAuth has to survive to the next run or the connected branch is
#: unreachable. ``FS_F_FRESH=1`` restores the throwaway-directory behaviour.
FRESH: Final = os.environ.get("FS_F_FRESH", "").strip() == "1"

CARD_SELECTOR: Final = "[data-testid^=tc-chat-approval-]"
APPROVE_SELECTOR: Final = "[data-testid^=tc-chat-approval-approve-]"
CARD_TEXT_JS: Final = (
    "(() => Array.from(document.querySelectorAll('"
    + CARD_SELECTOR
    + "')).map((el) => ({ testid: el.getAttribute('data-testid'),"
    " text: (el.innerText || '').trim().slice(0, 600) })))()"
)

#: One real, read-only errand. Named tools are avoided on purpose — asking for
#: the OUTCOME lets the runtime pick whatever Linear tool it has, so the
#: journey survives a descriptor rename instead of failing as if Linear broke.
PROMPT: Final = (
    "Using the connected Linear connector, list my Linear teams and the issues "
    "assigned to me. Read only: do not create, update, comment on, or delete "
    "anything. For each issue, report its identifier and title exactly as "
    "Linear returned them. If the connector returns an error, quote the error "
    "verbatim and do not guess or summarise from memory. If it returns nothing "
    "at all, say plainly that it returned nothing rather than describing it as "
    "a success."
)

#: Tool-name prefixes that mutate. Checked against the tool a pending approval
#: names so the journey approves its own read and nothing else. `assign` and
#: `close` are deliberately absent from the free-text fallback below — "issues
#: assigned to me" is a read, and a guard that misfires on it would park every
#: run and report the wrong finding.
WRITE_PREFIXES: Final = (
    "create",
    "update",
    "delete",
    "remove",
    "archive",
    "add",
    "set",
    "move",
    "post",
    "write",
    "comment",
)
WRITE_TEXT_TOKENS: Final = (
    "create",
    "update",
    "delete",
    "archive",
    "comment on",
    "modify",
)

#: The one claim under test, spelled out so the failure message can quote it.
CONNECTION_FAILED_CODE: Final = "connection_failed"
CONNECTION_FAILED_COPY: Final = "The MCP server could not be reached."

#: A Linear issue identifier — TEAM-123.
#:
#: It was described here as proof "a real workspace answered, because nothing in
#: the prompt or the app supplies one". That was FALSE once the MCP catalog
#: became browsable files: the connector's own tool descriptors carry example
#: keys, so a run that read `/mcp/linear/tools/*.json` and returned NO issues at
#: all still yielded `issue_keys: ["ISO-8601", "LIN-123"]` — a doc placeholder
#: and a date standard — and the journey passed on a completely empty errand.
#: The pattern is unchanged; WHERE it is applied is the fix (see
#: `WORKSPACE_DATA_TOOLS`), and this denylist is defence in depth for
#: standards-shaped tokens that are never team prefixes.
ISSUE_KEY: Final = re.compile(r"\b[A-Z][A-Z0-9]{1,9}-\d{1,6}\b")

#: Tools whose output IS workspace data. A filesystem read of the catalog is
#: not: it returns the connector's schema, examples and all.
WORKSPACE_DATA_TOOLS: Final = frozenset({"call_mcp_tool"})

#: Never a Linear team prefix — these are standards and encodings that happen to
#: fit `AAA-123`.
NOT_TEAM_PREFIXES: Final = frozenset(
    {"ISO", "RFC", "UTF", "SHA", "AES", "RSA", "UTC", "HTTP", "IEEE", "ANSI", "ASCII"}
)

#: Anything token-shaped is scrubbed before a log line reaches the run dir.
_REDACTIONS: Final = (
    re.compile(r"(?i)\b(bearer|token|authorization)\b\s*[:=]?\s*\S+"),
    re.compile(r"\b(sk|lin_api|lin_oauth|xoxb|ghp|gho)[-_][A-Za-z0-9_\-]{8,}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{5,}\b"),
)


def _redact(text: str) -> str:
    """Strip anything credential-shaped from a string bound for the run dir."""

    for pattern in _REDACTIONS:
        text = pattern.sub("[REDACTED]", text)
    return text


def _safe(session: DriverSession, path: str) -> Any:
    """An authenticated GET through the app that reports its own failure."""

    try:
        return session.transport("GET", path)
    except Exception as exc:  # noqa: BLE001
        return {"error": _redact(repr(exc))[:300]}


def _entries(value: Any, *keys: str) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        for key in keys:
            found = value.get(key)
            if isinstance(found, list):
                return [item for item in found if isinstance(item, dict)]
    return []


def _walk(value: Any) -> Iterator[dict[str, Any]]:
    """Every dict nested anywhere inside ``value``, itself included."""

    if isinstance(value, dict):
        yield value
        for nested in value.values():
            yield from _walk(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _walk(nested)


# ── ground truth ─────────────────────────────────────────────────────────────
def probe_endpoint(url: str, timeout_s: float = 10.0) -> dict[str, Any]:
    """Ask the Linear MCP endpoint, anonymously, whether it is there.

    No credential is sent and none is accepted: a ``401`` is the EXPECTED and
    fully sufficient answer. The point is not to get in — it is to establish
    that the peer receives, understands, and answers a request, which is the
    thing "could not be reached" denies. ``reachable`` is therefore true for
    ANY HTTP status, and false only when the transport itself never completed.
    """

    body = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "fs-f-reachability-probe", "version": "0"},
            },
        }
    ).encode()
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "content-type": "application/json",
            "accept": "application/json, text/event-stream",
        },
    )
    started = time.time()
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            return {
                "url": url,
                "reachable": True,
                "http_status": response.status,
                "www_authenticate": response.headers.get("www-authenticate"),
                "elapsed_ms": round((time.time() - started) * 1000),
            }
    except urllib.error.HTTPError as exc:
        # The peer ANSWERED. That is the finding.
        return {
            "url": url,
            "reachable": True,
            "http_status": exc.code,
            "www_authenticate": exc.headers.get("www-authenticate"),
            "elapsed_ms": round((time.time() - started) * 1000),
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "url": url,
            "reachable": False,
            "http_status": None,
            "transport_error": _redact(repr(exc))[:200],
            "elapsed_ms": round((time.time() - started) * 1000),
        }


# ── what the app says about itself ───────────────────────────────────────────
def _server_view(entry: dict[str, Any]) -> dict[str, Any]:
    """The fields that decide usability — with header VALUES never copied."""

    return {
        "name": entry.get("name"),
        "display_name": entry.get("display_name"),
        "url": entry.get("url"),
        "transport": entry.get("transport"),
        "auth_mode": entry.get("auth_mode"),
        "auth_state": entry.get("auth_state"),
        "health": entry.get("health"),
        "enabled": entry.get("enabled"),
        "access_mode": entry.get("access_mode"),
        "oauth_client_configured": entry.get("oauth_client_configured"),
        # A plain header value round-trips verbatim by design, so a user who
        # typed a key into one would have it copied here. Name + set-ness only.
        "headers": [
            {"name": h.get("name"), "secret_set": bool(h.get("secret_set"))}
            for h in entry.get("headers") or []
            if isinstance(h, dict)
        ],
    }


def usability(server: dict[str, Any] | None) -> tuple[bool, list[str]]:
    """Can the agent actually call this server, and if not, what must a human do?

    Four independent gates, because each one fails differently and each one has
    a different fix. Collapsing them into "not connected" is what makes a
    misconfiguration look like an outage.
    """

    if server is None:
        return False, [
            "Linear is not installed for this account — the catalog lists it, "
            "but no server row exists. A human must open the app, click "
            '"Tools" in the left rail, find Linear under Available, and click '
            "Connect, which opens Linear's OAuth consent in a browser.",
        ]
    blockers: list[str] = []
    if server.get("auth_state") != "authenticated":
        blockers.append(
            f"Linear is installed but auth_state={server.get('auth_state')!r}. "
            'A human must open "Tools" in the left rail, open the Linear row, '
            "and click Reconnect to complete Linear's OAuth consent in a "
            "browser. No script can do this: it authorizes a third party in "
            "the user's name."
        )
    if server.get("enabled") is False:
        blockers.append(
            'Linear is disabled. A human must re-enable it in "Tools".',
        )
    if server.get("health") == "disabled":
        blockers.append(
            'Linear is health=disabled. A human must re-enable it in "Tools".'
        )
    if server.get("access_mode") == "off":
        blockers.append(
            "Linear is authenticated but access_mode=off, so the runtime drops "
            "it from the model's cards entirely and the agent cannot call it. "
            'A human must set its access to read in "Tools".'
        )
    return not blockers, blockers


# ── judging the run ──────────────────────────────────────────────────────────
def typed_errors(stream: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every typed MCP error in the stream, wherever the shape buried it.

    ``TOOL_RESULT`` hoists ``error_code`` / ``safe_message`` to the top of the
    payload, but ``retryable`` only ever lives on the nested ``McpLoadError``.
    Judging the retry claim means finding the nested object, so this walks
    rather than reading two fixed keys.
    """

    found: list[dict[str, Any]] = []
    seen: set[str] = set()
    for event in stream:
        payload = payload_of(event)
        for node in _walk(payload):
            code = node.get("code") or node.get("error_code")
            message = node.get("safe_message")
            if not isinstance(code, str) or not isinstance(message, str):
                continue
            record = {
                "event": event_name(event),
                "code": code,
                "safe_message": message,
                "retryable": node.get("retryable"),
                "server_name": node.get("server_name"),
                "correlation_id": node.get("correlation_id"),
            }
            key = json.dumps(record, sort_keys=True)
            if key in seen:
                continue
            seen.add(key)
            found.append(record)
    return found


def judge_taxonomy(
    errors: list[dict[str, Any]], probe: dict[str, Any]
) -> list[dict[str, Any]]:
    """Score each typed error against what the endpoint measurably did.

    A failed errand and a DISHONEST failed errand are different defects with
    different owners, and a report that blurs them sends the wrong team
    looking. So each error is labelled:

    ``misclassified`` the app said ``connection_failed`` about a peer that
                      demonstrably answered. This is the classifier claim
                      failing, not an outage.
    ``correct``       the app said ``auth_failure`` about a peer that answered
                      ``401``. Right code, right non-retryability, right
                      remedy — the errand still failed, but honestly.
    ``unjudged``      no measurement contradicts or confirms it.
    """

    status = probe.get("http_status")
    reachable = bool(probe.get("reachable"))
    judged: list[dict[str, Any]] = []
    for error in errors:
        code = error.get("code")
        connection_claim = (
            code == CONNECTION_FAILED_CODE
            or error.get("safe_message") == CONNECTION_FAILED_COPY
        )
        if connection_claim and reachable:
            label = "misclassified"
        elif code == "auth_failure" and status in {401, 403}:
            label = "correct"
        elif code == "timeout" and reachable:
            # The probe answered in a few hundred ms; a timeout claim about the
            # same endpoint is at least worth naming rather than accepting.
            label = "suspect"
        else:
            label = "unjudged"
        judged.append({**error, "taxonomy": label})
    return judged


#: ``tool_result`` is where the OUTPUT lives; ``tool_call_completed`` carries
#: only the terminal status. Both are collected so a runtime that emits one
#: without the other cannot be mistaken for "the agent never called anything".
RESULT_EVENTS: Final = ("tool_result", "tool_call_completed")


def _tool_results(stream: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [event for event in stream if event_name(event) in RESULT_EVENTS]


def data_returned(stream: list[dict[str, Any]], answer: str) -> dict[str, Any]:
    """Did real workspace data come back, or a successful-looking nothing?

    Two independent witnesses, both of which a fabricating model cannot
    produce: a Linear issue identifier (``TEAM-123``), and objects carrying
    both an id and a human name. Counted over TOOL OUTPUT, not over the
    assistant's prose — prose is exactly what a model writes when the tool
    gave it nothing.
    """

    keys: set[str] = set()
    items: list[dict[str, Any]] = []
    succeeded = 0
    failed = 0
    for event in _tool_results(stream):
        payload = payload_of(event)
        status = str(payload.get("status") or "")
        if status in {"failed", "error"}:
            failed += 1
        else:
            succeeded += 1
        if str(payload.get("tool_name") or "") not in WORKSPACE_DATA_TOOLS:
            # A catalog read is not workspace data. Harvesting keys from it is
            # what let a zero-issue run report two issue keys and pass.
            continue
        blob = json.dumps(payload.get("output"), default=str)
        keys.update(
            key
            for key in ISSUE_KEY.findall(blob)
            if key.split("-", 1)[0] not in NOT_TEAM_PREFIXES
        )
        for node in _walk(payload.get("output")):
            identifier = node.get("id") or node.get("identifier")
            label = node.get("title") or node.get("name")
            if isinstance(identifier, str) and isinstance(label, str) and label:
                items.append({"id": identifier[:80], "label": label[:120]})
    return {
        "tool_results_ok": succeeded,
        "tool_results_failed": failed,
        "issue_keys": sorted(keys)[:25],
        "items": items[:15],
        "item_count": len(items),
        "answer_has_issue_key": any(
            key.split("-", 1)[0] not in NOT_TEAM_PREFIXES
            for key in ISSUE_KEY.findall(answer)
        ),
        "has_real_data": bool(keys or items),
    }


# ── the trace a capability owes ──────────────────────────────────────────────
def log_excerpt(session: DriverSession, run_id: str | None = None) -> dict[str, Any]:
    """Pull the MCP-relevant log lines for this run out of the supervised services.

    A previous live attempt produced NO MCP lines at all. A capability that can
    fail without leaving a trace is its own defect — naming the failure then
    costs a live reproduction and a database read instead of one grep — so
    silence is recorded as a finding rather than skipped over.

    ``ai-backend`` is called out separately because it is the service that
    owns the loader, the classifier, and every typed MCP error. ``backend``
    logs an ``http_request`` row for each ``/v1/mcp/*`` read this journey
    makes, so it is never silent and cannot stand in as evidence that the
    runtime said anything.
    """

    logs = session._user_data_dir / "logs"  # noqa: SLF001 — the app's own log dir
    out: dict[str, Any] = {"logs_dir": str(logs), "files": {}}
    # An empty run_id would match every line ever written; drop it.
    wanted = tuple(token for token in (SLUG, "mcp", run_id) if token)
    for name in ("ai-backend.log", "backend.log"):
        path = logs / name
        if not path.is_file():
            out["files"][name] = {"present": False, "matched_lines": 0}
            continue
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError as exc:
            out["files"][name] = {
                "present": True,
                "matched_lines": 0,
                "unreadable": repr(exc)[:120],
            }
            continue
        matched = [
            _redact(line)[:1200]
            for line in lines
            if any(token.lower() in line.lower() for token in wanted)
        ]
        out["files"][name] = {
            "present": True,
            "total_lines": len(lines),
            "matched_lines": len(matched),
            # The grep McpLoadFailureLog was added to make possible.
            "load_failure_lines": sum(
                1 for line in matched if "MCP load failed" in line
            ),
            "lines": matched[-120:],
        }
    out["ai_backend_mcp_silent"] = (
        out["files"]["ai-backend.log"].get("matched_lines", 0) == 0
    )
    out["mcp_log_silence"] = all(
        entry.get("matched_lines", 0) == 0 for entry in out["files"].values()
    )
    return out


def log_findings(logs: dict[str, Any]) -> list[str]:
    """Turn log silence into a stated finding rather than an absent section."""

    findings: list[str] = []
    if logs.get("mcp_log_silence"):
        findings.append(
            "TOTAL SILENCE: neither ai-backend.log nor backend.log recorded a "
            "single line mentioning MCP, linear, or this run. A capability "
            "that fails without leaving a trace is its own defect — naming "
            "the failure costs a live reproduction and a database read "
            "instead of one grep."
        )
    elif logs.get("ai_backend_mcp_silent"):
        findings.append(
            "RUNTIME SILENCE: backend.log shows the /v1/mcp/* reads, but "
            "ai-backend.log — the service that owns the loader, the "
            "classifier, and every typed MCP error — wrote nothing about MCP "
            "at all. The HTTP rows are not evidence that the runtime spoke."
        )
    return findings


def run_is_terminal(session: DriverSession, run_id: str) -> bool:
    """Stop card-polling once the run is over, instead of burning the budget."""

    return run_status(session, run_id).get("status") in TERMINAL


def _write_intent(approvals: list[dict[str, Any]], cards: list[dict[str, Any]]) -> bool:
    """Refuse to approve anything that is not the read this journey asked for."""

    for approval in approvals:
        for node in _walk(approval.get("payload")):
            for key in ("tool_name", "name", "operation"):
                value = node.get(key)
                if isinstance(value, str) and value.lower().startswith(WRITE_PREFIXES):
                    return True
    text = " ".join(str(card.get("text", "")) for card in cards).lower()
    return any(token in text for token in WRITE_TEXT_TOKENS)


def report_configuration(session: DriverSession, evidence: dict[str, Any]) -> None:
    """Record what is ACTUALLY configured, from the running app's own surfaces."""

    catalog = _safe(session, "/v1/mcp/catalog")
    servers = _safe(session, "/v1/mcp/servers")
    tools = _safe(session, "/v1/mcp/tools")
    catalog_entries = _entries(catalog, "entries", "catalog", "servers")
    server_entries = _entries(servers, "servers", "items")
    tool_entries = _entries(tools, "tools", "items")

    catalog_entry = next(
        (entry for entry in catalog_entries if str(entry.get("slug")) == SLUG), None
    )
    server_entry = next(
        (
            entry
            for entry in server_entries
            if str(entry.get("name") or "").lower() == SLUG
            or str(entry.get("display_name") or "").lower() == SLUG
        ),
        None,
    )
    evidence["catalog_has_linear"] = catalog_entry is not None
    evidence["catalog_linear_url"] = catalog_entry.get("url") if catalog_entry else None
    evidence["installed_server_count"] = len(server_entries)
    evidence["installed_servers"] = [_server_view(entry) for entry in server_entries]
    evidence["linear_server"] = _server_view(server_entry) if server_entry else None
    evidence["mcp_tool_count"] = len(tool_entries)
    evidence["mcp_tool_names"] = [
        str(entry.get("name") or entry.get("tool_name")) for entry in tool_entries
    ][:60]

    # Ground truth, measured out-of-band and without credentials.
    evidence["endpoint_probe"] = probe_endpoint(
        evidence["catalog_linear_url"] or "https://mcp.linear.app/mcp"
    )

    # The surface a human is told to click. Best-effort on purpose: the
    # authenticated API reads above are the load-bearing evidence, and a rail
    # that failed to render must not throw away a report they already earned.
    evidence["rail_labels"] = session.evaluate(
        "(() => Array.from(document.querySelectorAll('[data-destination]'))"
        ".map((el) => ({ slug: el.getAttribute('data-destination'),"
        " label: el.getAttribute('aria-label') })))()"
    )
    if not session.wait_for(CONNECTORS_RAIL, 30):
        evidence["connectors_surface_error"] = (
            "the Connectors rail never rendered; see rail_labels for what did"
        )
        session.shot("f-01-no-connectors-rail")
        return
    session.click(CONNECTORS_RAIL)
    time.sleep(3)
    session.shot("f-01-connectors")
    evidence["connect_cta_present"] = session.present(CONNECT_CTA)
    evidence["connectors_surface_text"] = _redact(
        str(
            session.evaluate(
                "((document.querySelector('main') || document.body)"
                ".innerText || '').trim().slice(0, 2500)"
            )
            or ""
        )
    )


def bootstrap(session: DriverSession, provider: str, key: str) -> dict[str, Any]:
    """Clear the first-run gates — but only the ones that are actually there.

    ``sign_in_local`` / ``ftue_add_key`` assert their gate exists, which is
    correct for a throwaway profile and wrong for a reused one: on the second
    run the device account is already signed in and already keyed, and the
    hard assert would fail the journey for being in exactly the state it needs
    to be in.
    """

    state = {"signed_in_now": False, "key_added_now": False}
    if session.wait_for(SIGN_IN, 25):
        session.click(SIGN_IN)
        state["signed_in_now"] = True
    if session.wait_for(ADD_KEY, 25):
        session.ftue_add_key(provider, key)  # the key itself is never logged
        state["key_added_now"] = True
    else:
        assert session.wait_for(COMPOSER, 60), (
            "no first-run key gate and no composer — the app did not reach a "
            "usable state, so nothing below would be measuring MCP"
        )
    # Leave the full-screen first-run gate so the nav rail exists. Skipping
    # costs no model call; sending a throwaway message to get the same effect
    # would spend one and pollute the conversation this journey then reads.
    if session.present(FTUE_SKIP):
        session.click(FTUE_SKIP)
        state["skipped_ftue"] = True
    state["rail_mounted"] = session.wait_for("[data-destination]", 30)
    return state


def _thread_baseline(session: DriverSession) -> tuple[str | None, int]:
    """The conversation already on screen and how many runs it already has.

    A reused profile restores the last thread, so sending into it and then
    asking for "the newest run" can hand back a run from a previous session —
    the journey would judge yesterday's evidence and never notice. Counting
    first makes ``wait_for_new_run`` mean what its name says.
    """

    match = re.fullmatch(
        r"#/convo/([^/?#]+)(?:[?#].*)?",
        str(session.evaluate("window.location.hash") or ""),
    )
    if match is None:
        return None, 0
    conversation_id = match.group(1)
    try:
        return conversation_id, len(runs_for(session, conversation_id))
    except Exception:  # noqa: BLE001 — an unreadable baseline is just zero
        return conversation_id, 0


def drive_read_only_errand(session: DriverSession, evidence: dict[str, Any]) -> None:
    """Ask for one real Linear read, then record what came back — good or bad."""

    # Prefer a brand-new thread; fall back to the Run cockpit's composer.
    session.click(CHATS_RAIL)
    time.sleep(2)
    if session.present(NEW_CHAT):
        session.click(NEW_CHAT)
        time.sleep(2)
    else:
        session.click(RUN_RAIL)
        time.sleep(2)
    previous_id, before = _thread_baseline(session)

    session.send_first_run_message(PROMPT)
    conversation_id = wait_for_conversation_id(session)
    run_id = wait_for_new_run(
        session, conversation_id, before if conversation_id == previous_id else 0
    )
    evidence["conversation_id"] = conversation_id
    evidence["reused_thread"] = conversation_id == previous_id
    evidence["run_id"] = run_id

    # The approval card, if the connector's policy asks for one.
    cards: list[dict[str, Any]] = []
    deadline = time.time() + 150
    while time.time() < deadline:
        found = session.evaluate(CARD_TEXT_JS)
        if isinstance(found, list) and found:
            cards = found
            break
        if run_is_terminal(session, run_id):
            break
        time.sleep(0.25)
    evidence["cards_seen"] = cards
    if cards:
        session.shot("f-02-approval-card")
        if _write_intent(approval_events(events(session, run_id)), cards):
            # The prompt asked for a read. A card offering a write is the
            # finding, not something to click past.
            evidence["refused_to_approve_a_write"] = True
            evidence["user_clicked_approve"] = False
        elif session.present(APPROVE_SELECTOR):
            session.click(APPROVE_SELECTOR)
            evidence["user_clicked_approve"] = True
        else:
            evidence["user_clicked_approve"] = False

    final = settle_run(session, run_id)
    evidence["run_status"] = final.get("status")
    evidence["safe_error"] = final.get("safe_error")
    time.sleep(2)
    session.shot("f-03-outcome")

    stream = events(session, run_id)
    answer = assistant_text(session, run_id)
    evidence["event_count"] = len(stream)
    evidence["tools"] = tool_calls(stream)
    evidence["mcp_auth_required"] = sum(
        1 for event in stream if event_name(event) == "mcp_auth_required"
    )
    evidence["typed_errors"] = typed_errors(stream)
    evidence["data"] = data_returned(stream, answer)
    evidence["answer"] = _redact(answer)[-2500:]

    # The tool result, verbatim — the primary artefact of this journey.
    dump(
        session.run_dir,
        "fs-f-tool-results.json",
        [
            {
                "event": event_name(event),
                "sequence_no": event.get("sequence_no"),
                "payload": json.loads(
                    _redact(json.dumps(payload_of(event), default=str))
                ),
            }
            for event in _tool_results(stream)
        ],
    )
    evidence["log_excerpt"] = log_excerpt(session, run_id)


def verdict(evidence: dict[str, Any]) -> tuple[str, dict[str, Any], int]:
    """Turn the gathered evidence into an outcome, a report, and an exit code.

    Pure on purpose: every claim this journey makes is decided here from data
    alone, so the wording of a failure can be exercised without booting the
    app — and so no verdict can quietly depend on something only a live
    session knows.
    """

    findings = log_findings(evidence.get("log_excerpt") or {})

    if not evidence.get("linear_usable"):
        # Blocked, not failed: the capability is absent, the journey is intact.
        # `2` is the harness's existing "a declared capability is absent" code.
        return (
            "blocked",
            {
                "linear_in_catalog": evidence.get("catalog_has_linear"),
                "linear_connected": False,
                "linear_server": evidence.get("linear_server"),
                "endpoint_probe": evidence.get("endpoint_probe"),
                "human_action_required": evidence.get("blockers"),
                "connect_cta_present": evidence.get("connect_cta_present"),
                "findings": findings,
            },
            2,
        )

    probe = evidence.get("endpoint_probe") or {}
    data = evidence.get("data") or {}
    errors = judge_taxonomy(evidence.get("typed_errors") or [], probe)
    status = probe.get("http_status")
    failures: list[str] = []

    # 3a. Judge the error copy against the measured ground truth.
    misclassified = [e for e in errors if e["taxonomy"] == "misclassified"]
    if misclassified:
        retryable = next(
            (e.get("retryable") for e in misclassified if e.get("retryable")), False
        )
        failures.append(
            f"CLASSIFIER FAIL: the endpoint answered HTTP {status} in "
            f"{probe.get('elapsed_ms')}ms, so it was reached — yet the app "
            f"reported code={CONNECTION_FAILED_CODE!r}, "
            f"safe_message={CONNECTION_FAILED_COPY!r}, "
            f"retryable={retryable!r}. A {status} surfacing as "
            "connection_failed/retryable=true is a FAIL of the "
            "McpProxyStatusClassifier claim: the peer received, understood "
            "and refused the request, so 'could not be reached' is false, "
            "'temporary' is false, and 'try again in a moment' is an "
            "instruction that cannot ever succeed. This is NOT 'MCP "
            "unavailable' and must not be reported as one."
        )
    if suspect := [e for e in errors if e["taxonomy"] == "suspect"]:
        failures.append(
            f"the app reported code={suspect[0].get('code')!r} for an endpoint "
            f"that answered the journey's own probe in {probe.get('elapsed_ms')}ms"
        )
    honest = [e for e in errors if e["taxonomy"] == "correct"]
    unjudged = [e for e in errors if e["taxonomy"] == "unjudged"]
    if not misclassified and (honest or unjudged):
        # Still a failed errand — but say so without impugning the taxonomy the
        # evidence does not contradict.
        failures.append(
            "the Linear read failed, with the error taxonomy "
            + ("CORRECT: " if honest and not unjudged else "recorded as: ")
            + "; ".join(
                f"code={e.get('code')!r} retryable={e.get('retryable')!r} "
                f"safe_message={e.get('safe_message')!r}"
                for e in (honest + unjudged)[:3]
            )
        )

    # 3b. Judge the success on DATA. An empty success is a failure.
    if not errors:
        if data.get("tool_results_ok", 0) == 0:
            failures.append(
                "no MCP tool result was ever recorded — the agent never reached "
                "Linear, so nothing about the connector was exercised and the "
                "run proves nothing either way"
            )
        elif not data.get("has_real_data"):
            failures.append(
                "EMPTY SUCCESS: an MCP tool returned without error but carried "
                "no Linear data at all — no issue identifier, no id+title "
                "object. A successful-looking nothing is the exact failure "
                "shape this program has already hit four times, and it must "
                "not be reported as a pass."
            )

    if evidence.get("refused_to_approve_a_write"):
        failures.append(
            "the approval card offered a WRITE for a strictly read-only "
            "prompt; the journey refused to approve it"
        )
    if evidence.get("run_status") != "completed":
        failures.append(
            f"run did not complete: status={evidence.get('run_status')!r} "
            f"safe_error={evidence.get('safe_error')!r}"
        )

    if failures:
        return (
            "FAILED",
            {
                "reasons": failures,
                "findings": findings,
                "run_id": evidence.get("run_id"),
                "endpoint_probe": probe,
                "typed_errors": errors,
                "approval_seen": bool(evidence.get("cards_seen")),
            },
            1,
        )

    return (
        "passed",
        {
            "run_id": evidence.get("run_id"),
            "issue_keys": data.get("issue_keys"),
            "item_count": data.get("item_count"),
            "approval_seen": bool(evidence.get("cards_seen")),
            "user_clicked_approve": evidence.get("user_clicked_approve"),
            "findings": findings,
        },
        0,
    )


def main() -> int:
    try:
        _preflight_staged_runtime()
        provider, key = _byok_provider()
    except PreflightSkip as exc:
        result(JOURNEY, "skipped", reason=str(exc))
        return 3

    evidence: dict[str, Any] = {}
    with lane(DEFAULT_LANE):
        session = DriverSession(name="fs-f-linear-mcp", fresh=FRESH)
        try:
            with session:
                evidence["target"] = session.rpc("status").get("target")
                evidence["fresh_profile"] = FRESH
                evidence["user_data_subdir"] = session.user_data_subdir
                evidence["byok_provider"] = provider  # never the key itself
                evidence["bootstrap"] = bootstrap(session, provider, key)

                report_configuration(session, evidence)
                usable, blockers = usability(evidence["linear_server"])
                evidence["linear_usable"] = usable
                evidence["blockers"] = blockers

                if usable:
                    drive_read_only_errand(session, evidence)
                else:
                    # Not connected is a REPORT, not a fabricated run.
                    evidence["log_excerpt"] = log_excerpt(session)
        finally:
            out = dump(session.run_dir, "fs-f-evidence.json", evidence)
            print(f"[fs-f] evidence -> {out}", flush=True)

    outcome, report, exit_code = verdict(evidence)
    result(JOURNEY, outcome, **report)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
