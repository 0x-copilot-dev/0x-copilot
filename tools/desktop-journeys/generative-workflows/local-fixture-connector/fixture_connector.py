"""Deterministic, fixture-only connector domain for Desktop journey tests.

The production application never imports this module.  It is an in-memory
simulation behind an MCP stdio adapter so supervised Desktop journeys can prove
read, stage, approval, retry, and receipt behaviour without reaching a real
mailbox, social account, Discord guild, or filesystem.
"""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Final
from urllib.parse import quote

Json = dict[str, Any]

FIXTURE_SCHEME: Final = "fixture://"
SECRET_FIELD_PARTS: Final = frozenset(
    {
        "apikey",
        "authorization",
        "bearer",
        "credential",
        "password",
        "privatekey",
        "secret",
        "token",
    }
)


class FixtureError(RuntimeError):
    """Typed, JSON-safe fixture failure; never contains secret input."""

    def __init__(self, code: str, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable

    def as_dict(self) -> Json:
        return {"code": self.code, "message": self.message, "retryable": self.retryable}


@dataclass(frozen=True)
class AuditEntry:
    """Append-only audit row stored as canonical JSON plus a hash-chain link."""

    sequence: int
    generation: int
    operation: str
    payload_json: str
    previous_hash: str
    entry_hash: str

    def public(self) -> Json:
        return {
            "sequence": self.sequence,
            "generation": self.generation,
            "operation": self.operation,
            "payload": json.loads(self.payload_json),
            "previous_hash": self.previous_hash,
            "entry_hash": self.entry_hash,
        }


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


class FixtureStore:
    """In-memory scenario store with staged-only effects and an immutable audit chain.

    ``FixtureStore`` receives data only from the checked-in scenario JSON.  It
    neither reads nor writes user paths after construction, and it exposes no
    URL, token, key, or credential input.  Every mutable operation requires an
    exact target under one of the scenario's known ``fixture://`` roots.
    """

    def __init__(self, scenario: Json):
        if scenario.get("schema_version") != 1:
            raise ValueError("unsupported fixture scenario schema")
        namespace = scenario.get("namespace")
        workspace = scenario.get("workspace")
        if not isinstance(namespace, str) or not namespace.startswith(FIXTURE_SCHEME):
            raise ValueError("scenario namespace must use fixture://")
        if not isinstance(workspace, dict) or not isinstance(
            workspace.get("root"), str
        ):
            raise ValueError("scenario workspace root is required")
        if not workspace["root"].startswith(FIXTURE_SCHEME):
            raise ValueError("scenario workspace root must use fixture://")

        self._scenario = copy.deepcopy(scenario)
        self._namespace = namespace.rstrip("/")
        self._workspace_root = workspace["root"].rstrip("/")
        self._generation = 0
        self._audit: list[AuditEntry] = []
        self._reset_state()

    @classmethod
    def from_path(cls, path: Path) -> "FixtureStore":
        return cls(json.loads(path.read_text(encoding="utf-8")))

    @property
    def namespace(self) -> str:
        return self._namespace

    @property
    def workspace_root(self) -> str:
        return self._workspace_root

    def _reset_state(self) -> None:
        self._state = copy.deepcopy(self._scenario)
        self._stages: dict[str, Json] = {}
        self._effects: dict[str, Json] = {}
        self._sequence = 0
        self._faults = {
            "expired_grant_once": bool(
                self._scenario["faults"].get("expired_grant_once")
            ),
            "discord_publish_failed_once": False,
        }

    # -- safe identity / audit -------------------------------------------------
    def _append_audit(self, operation: str, payload: Json) -> AuditEntry:
        payload_json = _canonical(payload)
        previous_hash = self._audit[-1].entry_hash if self._audit else "0" * 64
        sequence = len(self._audit) + 1
        entry_hash = _digest(
            {
                "sequence": sequence,
                "generation": self._generation,
                "operation": operation,
                "payload": json.loads(payload_json),
                "previous_hash": previous_hash,
            }
        )
        entry = AuditEntry(
            sequence=sequence,
            generation=self._generation,
            operation=operation,
            payload_json=payload_json,
            previous_hash=previous_hash,
            entry_hash=entry_hash,
        )
        self._audit.append(entry)
        return entry

    def audit_log(self) -> tuple[Json, ...]:
        """Return detached audit values; callers cannot mutate the chain."""
        return tuple(copy.deepcopy(entry.public()) for entry in self._audit)

    def verify_audit(self) -> bool:
        previous_hash = "0" * 64
        for expected_sequence, entry in enumerate(self._audit, start=1):
            if (
                entry.sequence != expected_sequence
                or entry.previous_hash != previous_hash
            ):
                return False
            expected_hash = _digest(
                {
                    "sequence": entry.sequence,
                    "generation": entry.generation,
                    "operation": entry.operation,
                    "payload": json.loads(entry.payload_json),
                    "previous_hash": entry.previous_hash,
                }
            )
            if entry.entry_hash != expected_hash:
                return False
            previous_hash = entry.entry_hash
        return True

    def reset(self) -> Json:
        """Reset mutable scenario state while retaining the append-only audit log."""
        self._generation += 1
        self._reset_state()
        self._append_audit("fixture.reset", {"namespace": self._namespace})
        return {
            "namespace": self._namespace,
            "generation": self._generation,
            "reset": True,
        }

    def _stage_id(self, domain: str) -> str:
        self._sequence += 1
        return f"stg_{domain}_{self._generation:02d}_{self._sequence:04d}"

    def _receipt(self, domain: str, effect_id: str) -> str:
        return f"{self._namespace}/receipts/{domain}/{quote(effect_id, safe='._-')}"

    def _domain_target(self, domain: str, identifier: str) -> str:
        return f"{self._namespace}/{domain}/{quote(identifier, safe='@._-')}"

    def _workspace_target(self, path: str) -> str:
        return f"{self._workspace_root}/{quote(path, safe='/._-')}"

    def _safe_path(self, path: object) -> str:
        if not isinstance(path, str) or not path:
            raise FixtureError("invalid_path", "workspace path is required")
        parsed = PurePosixPath(path)
        if parsed.is_absolute() or ".." in parsed.parts or "." in parsed.parts:
            raise FixtureError(
                "invalid_path", "workspace path must be a relative fixture path"
            )
        normalized = str(parsed)
        if normalized in {"", "."}:
            raise FixtureError("invalid_path", "workspace path is required")
        return normalized

    def _known_workspace_path(self, path: str) -> None:
        files = self._state["workspace"]["files"]
        expected = set(self._state["workspace"].get("expected_artifacts", []))
        if path not in files and path not in expected:
            raise FixtureError(
                "unknown_file", "path is not part of this fixture scenario"
            )

    def _require_target(self, target: object, expected: str) -> None:
        if not isinstance(target, str) or not target.startswith(FIXTURE_SCHEME):
            raise FixtureError(
                "invalid_target", "only fixture:// targets are permitted"
            )
        if target != expected:
            raise FixtureError(
                "invalid_target", "target does not match the fixture resource"
            )

    def _reject_secret_fields(self, value: object) -> None:
        if isinstance(value, dict):
            for key, nested in value.items():
                normalized = "".join(
                    char for char in str(key).lower() if char.isalnum()
                )
                if any(part in normalized for part in SECRET_FIELD_PARTS):
                    raise FixtureError(
                        "secret_input_rejected",
                        "fixture connector does not accept credentials",
                    )
                if normalized in {
                    "target",
                    "destination",
                    "url",
                    "endpoint",
                } and isinstance(nested, str):
                    if not nested.startswith(FIXTURE_SCHEME):
                        raise FixtureError(
                            "invalid_target", "only fixture:// targets are permitted"
                        )
                self._reject_secret_fields(nested)
        elif isinstance(value, list):
            for nested in value:
                self._reject_secret_fields(nested)

    def _require_text(self, args: Json, key: str, *, limit: int = 32_000) -> str:
        value = args.get(key)
        if not isinstance(value, str) or not value:
            raise FixtureError("invalid_argument", f"{key} is required")
        if len(value) > limit:
            raise FixtureError("invalid_argument", f"{key} exceeds fixture limit")
        return value

    def _require_stage(self, stage_id: object, kind: str) -> Json:
        if not isinstance(stage_id, str) or stage_id not in self._stages:
            raise FixtureError("unknown_stage", "staged operation was not found")
        stage = self._stages[stage_id]
        if stage["kind"] != kind:
            raise FixtureError("invalid_stage", "stage is not valid for this operation")
        return stage

    def _commit_stage(self, stage: Json, *, operation: str, apply: callable) -> Json:
        if stage.get("effect") is not None:
            return {**copy.deepcopy(stage["effect"]), "idempotent": True}
        if stage.get("status") != "staged":
            raise FixtureError("invalid_stage", "stage is no longer commit-ready")
        effect = apply()
        stage["status"] = "applied"
        stage["effect"] = copy.deepcopy(effect)
        self._effects[stage["id"]] = copy.deepcopy(effect)
        self._append_audit(
            operation, {"stage_id": stage["id"], **copy.deepcopy(effect)}
        )
        return effect

    # -- read operations -------------------------------------------------------
    def fixture_manifest(self) -> Json:
        return {
            "namespace": self._namespace,
            "workspace_root": self._workspace_root,
            "schema_version": self._scenario["schema_version"],
            "principals": copy.deepcopy(self._state["principals"]),
            "network": "disabled",
            "accepts_credentials": False,
        }

    def mail_list_threads(self) -> Json:
        threads = []
        for thread in self._state["mail"]["threads"]:
            threads.append(
                {
                    "id": thread["id"],
                    "subject": thread["subject"],
                    "participants": list(thread["participants"]),
                    "message_count": len(thread["messages"]),
                    "target": self._domain_target("mail/threads", thread["id"]),
                }
            )
        self._append_audit("mail.list_threads", {"count": len(threads)})
        return {
            "account": copy.deepcopy(self._state["mail"]["account"]),
            "threads": threads,
        }

    def _mail_thread(self, thread_id: object) -> Json:
        for thread in self._state["mail"]["threads"]:
            if thread["id"] == thread_id:
                return thread
        raise FixtureError("unknown_thread", "mail thread is not part of this fixture")

    def mail_get_thread(self, args: Json) -> Json:
        thread = self._mail_thread(args.get("thread_id"))
        target = self._domain_target("mail/threads", thread["id"])
        self._require_target(args.get("target"), target)
        self._append_audit(
            "mail.get_thread", {"thread_id": thread["id"], "target": target}
        )
        return {**copy.deepcopy(thread), "target": target}

    def timeline_list_posts(self) -> Json:
        posts = []
        for post in self._state["timeline"]["posts"]:
            posts.append(
                {
                    **copy.deepcopy(post),
                    "target": self._domain_target("timeline/posts", post["id"]),
                }
            )
        self._append_audit("timeline.list_posts", {"count": len(posts)})
        return {
            "account": copy.deepcopy(self._state["timeline"]["account"]),
            "posts": posts,
        }

    def _timeline_post(self, post_id: object) -> Json:
        for post in self._state["timeline"]["posts"]:
            if post["id"] == post_id:
                return post
        raise FixtureError("unknown_post", "timeline post is not part of this fixture")

    def timeline_get_post(self, args: Json) -> Json:
        post = self._timeline_post(args.get("post_id"))
        target = self._domain_target("timeline/posts", post["id"])
        self._require_target(args.get("target"), target)
        self._append_audit(
            "timeline.get_post", {"post_id": post["id"], "target": target}
        )
        return {**copy.deepcopy(post), "target": target}

    def discord_list_channels(self) -> Json:
        channels = []
        for channel in self._state["discord"]["channels"]:
            channels.append(
                {
                    "id": channel["id"],
                    "name": channel["name"],
                    "message_count": len(channel["messages"]),
                    "target": self._domain_target("discord/channels", channel["id"]),
                }
            )
        self._append_audit("discord.list_channels", {"count": len(channels)})
        return {
            "guild": copy.deepcopy(self._state["discord"]["guild"]),
            "channels": channels,
        }

    def _discord_channel(self, channel_id: object) -> Json:
        for channel in self._state["discord"]["channels"]:
            if channel["id"] == channel_id:
                return channel
        raise FixtureError(
            "unknown_channel", "Discord channel is not part of this fixture"
        )

    def discord_get_messages(self, args: Json) -> Json:
        channel = self._discord_channel(args.get("channel_id"))
        target = self._domain_target("discord/channels", channel["id"])
        self._require_target(args.get("target"), target)
        self._append_audit(
            "discord.get_messages", {"channel_id": channel["id"], "target": target}
        )
        return {
            "channel": {"id": channel["id"], "name": channel["name"]},
            "messages": copy.deepcopy(channel["messages"]),
            "target": target,
        }

    def workspace_list(self, args: Json) -> Json:
        self._require_target(args.get("target"), self._workspace_root)
        paths = sorted(self._state["workspace"]["files"])
        self._append_audit(
            "workspace.list", {"count": len(paths), "target": self._workspace_root}
        )
        return {
            "root": self._workspace_root,
            "files": [
                {"path": path, "target": self._workspace_target(path)} for path in paths
            ],
        }

    def workspace_read(self, args: Json) -> Json:
        # Validate the resource before simulating a reconnectable grant fault.
        # A malformed/escaping target must never learn anything about grant state.
        path = self._safe_path(args.get("path"))
        self._known_workspace_path(path)
        self._require_target(args.get("target"), self._workspace_target(path))
        if self._faults["expired_grant_once"]:
            self._faults["expired_grant_once"] = False
            self._append_audit("workspace.read.grant_expired", {"path": path})
            raise FixtureError(
                "grant_expired",
                "fixture workspace grant expired; retry after reconnect",
                retryable=True,
            )
        files = self._state["workspace"]["files"]
        if path not in files:
            raise FixtureError("not_found", "fixture artifact has not been created")
        content = files[path]
        self._append_audit("workspace.read", {"path": path, "sha256": _digest(content)})
        return {
            "path": path,
            "content": content,
            "sha256": _digest(content),
            "target": self._workspace_target(path),
        }

    def workspace_stat(self, args: Json) -> Json:
        path = self._safe_path(args.get("path"))
        self._known_workspace_path(path)
        self._require_target(args.get("target"), self._workspace_target(path))
        files = self._state["workspace"]["files"]
        if path not in files:
            raise FixtureError("not_found", "fixture artifact has not been created")
        content = files[path]
        self._append_audit("workspace.stat", {"path": path, "sha256": _digest(content)})
        return {
            "path": path,
            "bytes": len(content.encode("utf-8")),
            "sha256": _digest(content),
            "target": self._workspace_target(path),
        }

    # -- stage operations ------------------------------------------------------
    def mail_draft_reply(self, args: Json) -> Json:
        thread = self._mail_thread(args.get("thread_id"))
        target = self._domain_target("mail/threads", thread["id"])
        self._require_target(args.get("target"), target)
        recipient = self._require_text(args, "recipient")
        if recipient not in thread["participants"]:
            raise FixtureError(
                "invalid_recipient",
                "recipient is not a participant in this fixture thread",
            )
        stage_id = self._stage_id("mail")
        stage = {
            "id": stage_id,
            "kind": "mail_reply",
            "status": "staged",
            "thread_id": thread["id"],
            "recipient": recipient,
            "body": self._require_text(args, "body"),
            "target": target,
        }
        self._stages[stage_id] = stage
        self._append_audit(
            "mail.draft_reply",
            {
                "stage_id": stage_id,
                "thread_id": thread["id"],
                "recipient": recipient,
                "target": target,
            },
        )
        return {
            "stage_id": stage_id,
            "status": "staged",
            "target": target,
            "revision": _digest(stage["body"]),
        }

    def mail_send_draft(self, args: Json) -> Json:
        stage = self._require_stage(args.get("stage_id"), "mail_reply")
        self._require_target(args.get("target"), stage["target"])
        if args.get("approved") is not True:
            raise FixtureError(
                "approval_required", "fixture effects require explicit approved=true"
            )

        def apply() -> Json:
            message_id = f"msg_sent_{stage['id']}"
            thread = self._mail_thread(stage["thread_id"])
            thread["messages"].append(
                {
                    "id": message_id,
                    "from": self._state["mail"]["account"]["address"],
                    "to": stage["recipient"],
                    "sent_at": "2026-07-26T00:00:00Z",
                    "body": stage["body"],
                }
            )
            return {
                "status": "applied",
                "receipt": self._receipt("mail", stage["id"]),
                "thread_id": stage["thread_id"],
                "recipient": stage["recipient"],
                "revision": _digest(stage["body"]),
            }

        return self._commit_stage(stage, operation="mail.send_draft", apply=apply)

    def timeline_draft_reply_post(self, args: Json) -> Json:
        post = self._timeline_post(args.get("post_id"))
        target = self._domain_target("timeline/posts", post["id"])
        self._require_target(args.get("target"), target)
        stage_id = self._stage_id("timeline")
        stage = {
            "id": stage_id,
            "kind": "timeline_reply",
            "status": "staged",
            "post_id": post["id"],
            "body": self._require_text(args, "body"),
            "target": target,
        }
        self._stages[stage_id] = stage
        self._append_audit(
            "timeline.draft_reply_post",
            {"stage_id": stage_id, "post_id": post["id"], "target": target},
        )
        return {
            "stage_id": stage_id,
            "status": "staged",
            "target": target,
            "revision": _digest(stage["body"]),
        }

    def timeline_publish_draft(self, args: Json) -> Json:
        stage = self._require_stage(args.get("stage_id"), "timeline_reply")
        self._require_target(args.get("target"), stage["target"])
        if args.get("approved") is not True:
            raise FixtureError(
                "approval_required", "fixture effects require explicit approved=true"
            )

        def apply() -> Json:
            post_id = f"post_published_{stage['id']}"
            self._state["timeline"]["posts"].append(
                {
                    "id": post_id,
                    "author": self._state["timeline"]["account"]["handle"],
                    "published_at": "2026-07-26T00:00:00Z",
                    "body": stage["body"],
                    "in_reply_to": stage["post_id"],
                }
            )
            return {
                "status": "applied",
                "receipt": self._receipt("timeline", stage["id"]),
                "post_id": post_id,
                "in_reply_to": stage["post_id"],
                "revision": _digest(stage["body"]),
            }

        return self._commit_stage(
            stage, operation="timeline.publish_draft", apply=apply
        )

    def discord_draft_announcement(self, args: Json) -> Json:
        channel = self._discord_channel(args.get("channel_id"))
        target = self._domain_target("discord/channels", channel["id"])
        self._require_target(args.get("target"), target)
        mentions = args.get("mentions")
        if (
            not isinstance(mentions, list)
            or not mentions
            or not all(
                isinstance(item, str) and item.startswith("@") for item in mentions
            )
        ):
            raise FixtureError(
                "invalid_argument", "mentions must be a non-empty list of local handles"
            )
        stage_id = self._stage_id("discord")
        stage = {
            "id": stage_id,
            "kind": "discord_announcement",
            "status": "staged",
            "channel_id": channel["id"],
            "body": self._require_text(args, "body"),
            "mentions": sorted(set(mentions)),
            "target": target,
        }
        self._stages[stage_id] = stage
        self._append_audit(
            "discord.draft_announcement",
            {
                "stage_id": stage_id,
                "channel_id": channel["id"],
                "mentions": stage["mentions"],
                "target": target,
            },
        )
        return {
            "stage_id": stage_id,
            "status": "staged",
            "target": target,
            "revision": _digest(stage["body"]),
            "mentions": stage["mentions"],
        }

    def discord_publish_announcement(self, args: Json) -> Json:
        stage = self._require_stage(args.get("stage_id"), "discord_announcement")
        self._require_target(args.get("target"), stage["target"])
        if args.get("approved") is not True:
            raise FixtureError(
                "approval_required", "fixture effects require explicit approved=true"
            )
        if not self._faults["discord_publish_failed_once"]:
            self._faults["discord_publish_failed_once"] = True
            self._append_audit(
                "discord.publish_announcement.retryable_failure",
                {"stage_id": stage["id"], "channel_id": stage["channel_id"]},
            )
            return {
                "status": "retryable_failure",
                "retryable": True,
                "stage_id": stage["id"],
                "reason": "fixture injected first publish failure",
            }

        def apply() -> Json:
            message_id = f"dc_published_{stage['id']}"
            channel = self._discord_channel(stage["channel_id"])
            channel["messages"].append(
                {
                    "id": message_id,
                    "author": "@aria",
                    "sent_at": "2026-07-26T00:00:00Z",
                    "body": stage["body"],
                    "mentions": stage["mentions"],
                    "pinned": True,
                }
            )
            return {
                "status": "applied",
                "receipt": self._receipt("discord", stage["id"]),
                "channel_id": stage["channel_id"],
                "message_id": message_id,
                "mentions": stage["mentions"],
                "revision": _digest(stage["body"]),
            }

        return self._commit_stage(
            stage, operation="discord.publish_announcement", apply=apply
        )

    def workspace_write_revision(self, args: Json) -> Json:
        """Create a revision stage or commit one with its exact stage id.

        This single operation maps cleanly to a future staged-write adapter:
        `{path, content, target}` stages; `{stage_id, target, approved:true}`
        performs the deliberately local effect.
        """
        if "stage_id" in args:
            stage = self._require_stage(args.get("stage_id"), "workspace_revision")
            self._require_target(args.get("target"), stage["target"])
            if args.get("approved") is not True:
                raise FixtureError(
                    "approval_required",
                    "fixture effects require explicit approved=true",
                )

            def apply() -> Json:
                self._state["workspace"]["files"][stage["path"]] = stage["content"]
                return {
                    "status": "applied",
                    "receipt": self._receipt("workspace", stage["id"]),
                    "path": stage["path"],
                    "sha256": _digest(stage["content"]),
                    "revision": _digest(stage["content"]),
                }

            return self._commit_stage(
                stage, operation="workspace.write_revision", apply=apply
            )
        path = self._safe_path(args.get("path"))
        self._known_workspace_path(path)
        target = self._workspace_target(path)
        self._require_target(args.get("target"), target)
        content = self._require_text(args, "content")
        stage_id = self._stage_id("workspace")
        stage = {
            "id": stage_id,
            "kind": "workspace_revision",
            "status": "staged",
            "path": path,
            "content": content,
            "target": target,
        }
        self._stages[stage_id] = stage
        self._append_audit(
            "workspace.write_revision.staged",
            {
                "stage_id": stage_id,
                "path": path,
                "target": target,
                "revision": _digest(content),
            },
        )
        return {
            "stage_id": stage_id,
            "status": "staged",
            "path": path,
            "target": target,
            "revision": _digest(content),
        }

    def workspace_apply_rowset(self, args: Json) -> Json:
        """Stage CSV changes or apply an approved non-held subset of those rows."""
        if "stage_id" in args:
            stage = self._require_stage(args.get("stage_id"), "workspace_rowset")
            self._require_target(args.get("target"), stage["target"])
            if args.get("approved") is not True:
                raise FixtureError(
                    "approval_required",
                    "fixture effects require explicit approved=true",
                )
            requested = args.get("row_keys", sorted(stage["changes"]))
            if (
                not isinstance(requested, list)
                or not requested
                or not all(isinstance(item, str) for item in requested)
            ):
                raise FixtureError(
                    "invalid_argument", "row_keys must be a non-empty list"
                )
            if not set(requested).issubset(stage["changes"]):
                raise FixtureError(
                    "invalid_row", "requested row is not part of this staged rowset"
                )
            held = set(stage["holds"])
            if held.intersection(requested):
                raise FixtureError("held_row", "held fixture rows cannot be applied")

            def apply() -> Json:
                rows = self._csv_rows(stage["path"])
                for row in rows:
                    key = row[stage["row_key"]]
                    if key in requested:
                        row.update(stage["changes"][key])
                content = self._csv_encode(rows, tuple(rows[0]) if rows else ())
                self._state["workspace"]["files"][stage["path"]] = content
                return {
                    "status": "partial"
                    if set(requested) != set(stage["changes"])
                    else "applied",
                    "receipt": self._receipt("workspace-rowset", stage["id"]),
                    "path": stage["path"],
                    "applied_rows": sorted(requested),
                    "held_rows": sorted(held),
                    "sha256": _digest(content),
                }

            return self._commit_stage(
                stage, operation="workspace.apply_rowset", apply=apply
            )
        path = self._safe_path(args.get("path"))
        self._known_workspace_path(path)
        target = self._workspace_target(path)
        self._require_target(args.get("target"), target)
        row_key = self._require_text(args, "row_key", limit=128)
        changes = args.get("changes")
        holds = args.get("holds", [])
        if not isinstance(changes, dict) or not changes:
            raise FixtureError(
                "invalid_argument", "changes must be a non-empty object keyed by row"
            )
        if not isinstance(holds, list) or not all(
            isinstance(item, str) for item in holds
        ):
            raise FixtureError("invalid_argument", "holds must be a list of row keys")
        rows = self._csv_rows(path)
        available = {row.get(row_key) for row in rows}
        if (
            None in available
            or not set(changes).issubset(available)
            or not set(holds).issubset(available)
        ):
            raise FixtureError(
                "invalid_row", "changes and holds must reference fixture rows"
            )
        normalized_changes: Json = {}
        for key, update in changes.items():
            if not isinstance(key, str) or not isinstance(update, dict) or not update:
                raise FixtureError(
                    "invalid_argument", "each row change must be a non-empty object"
                )
            if not all(
                isinstance(column, str) and isinstance(value, str)
                for column, value in update.items()
            ):
                raise FixtureError("invalid_argument", "row values must be strings")
            normalized_changes[key] = dict(update)
        stage_id = self._stage_id("rowset")
        stage = {
            "id": stage_id,
            "kind": "workspace_rowset",
            "status": "staged",
            "path": path,
            "target": target,
            "row_key": row_key,
            "changes": normalized_changes,
            "holds": sorted(set(holds)),
        }
        self._stages[stage_id] = stage
        self._append_audit(
            "workspace.apply_rowset.staged",
            {
                "stage_id": stage_id,
                "path": path,
                "row_key": row_key,
                "row_keys": sorted(normalized_changes),
                "holds": stage["holds"],
                "target": target,
            },
        )
        return {
            "stage_id": stage_id,
            "status": "staged",
            "path": path,
            "target": target,
            "row_keys": sorted(normalized_changes),
            "holds": stage["holds"],
        }

    def _csv_rows(self, path: str) -> list[dict[str, str]]:
        import csv
        from io import StringIO

        content = self._state["workspace"]["files"].get(path)
        if not isinstance(content, str):
            raise FixtureError("not_found", "fixture CSV artifact has not been created")
        return list(csv.DictReader(StringIO(content)))

    def _csv_encode(self, rows: list[dict[str, str]], columns: tuple[str, ...]) -> str:
        import csv
        from io import StringIO

        stream = StringIO()
        writer = csv.DictWriter(stream, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
        return stream.getvalue()

    # -- dispatch --------------------------------------------------------------
    def call(self, operation: str, arguments: Json | None = None) -> Json:
        """Invoke one fixture operation after rejecting credentials/remote targets."""
        args = copy.deepcopy(arguments or {})
        if not isinstance(args, dict):
            raise FixtureError("invalid_argument", "tool arguments must be an object")
        self._reject_secret_fields(args)
        handlers = {
            "fixture_manifest": lambda: self.fixture_manifest(),
            "fixture_reset": lambda: self.reset(),
            "fixture_audit": lambda: {
                "entries": list(self.audit_log()),
                "valid": self.verify_audit(),
            },
            "mail_list_threads": self.mail_list_threads,
            "mail_get_thread": lambda: self.mail_get_thread(args),
            "mail_draft_reply": lambda: self.mail_draft_reply(args),
            "mail_send_draft": lambda: self.mail_send_draft(args),
            "timeline_list_posts": self.timeline_list_posts,
            "timeline_get_post": lambda: self.timeline_get_post(args),
            "timeline_draft_reply_post": lambda: self.timeline_draft_reply_post(args),
            "timeline_publish_draft": lambda: self.timeline_publish_draft(args),
            "discord_list_channels": self.discord_list_channels,
            "discord_get_messages": lambda: self.discord_get_messages(args),
            "discord_draft_announcement": lambda: self.discord_draft_announcement(args),
            "discord_publish_announcement": lambda: self.discord_publish_announcement(
                args
            ),
            "workspace_list": lambda: self.workspace_list(args),
            "workspace_read": lambda: self.workspace_read(args),
            "workspace_stat": lambda: self.workspace_stat(args),
            "workspace_write_revision": lambda: self.workspace_write_revision(args),
            "workspace_apply_rowset": lambda: self.workspace_apply_rowset(args),
        }
        handler = handlers.get(operation)
        if handler is None:
            self._append_audit("fixture.unknown_operation", {"operation": operation})
            raise FixtureError(
                "unknown_operation", "fixture connector does not provide this operation"
            )
        result = handler()
        return copy.deepcopy(result)
