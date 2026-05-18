"""Agents service — CRUD + ACL + audit (Phase 8 P8-A1).

Authorization rules (agents-prd §6.2 + cross-audit §1.3, binding):

* ``system`` + ``community`` agents — readable by every tenant member;
  PATCH responds 409 ``agent_origin_immutable`` (must duplicate first).
* ``custom`` agents — owner-only writes; non-owner non-installer 404
  (existence not leaked); admins get a tenant-wide compliance read.
* All reads return ``viewer_install_status`` derived per caller (the
  caller's install row drives it; absent install = ``available``).
* Non-readers see 404 (cross-audit §1.3 master rule), NEVER 403.

The route layer (``routes.py``) is presentation-only: every authorization
decision lives here so the in-memory ``InMemoryAgentsStore`` and the
Postgres adapter share one set of checks, invariants, and audit hooks.

P8-A1 owns CRUD; install / uninstall / version-snapshot operational
endpoints land in P8-A2 (versions) and P8-A3 (installs). The service
class exposes the supporting predicates (``resolve_agent_view``,
``viewer_install_status_for``) those sub-PRDs will compose with.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Iterable

from backend_app.agents.store import (
    AgentAuditRecord,
    AgentInstallRecord,
    AgentRecord,
    AgentsStore,
)
from backend_app.identity.store import IdentityStore


def _now() -> datetime:
    return datetime.now(timezone.utc)


# Tenant-admin roles. Treated as untrusted unless the verified
# ``ScopedIdentity.roles`` tuple set them — the route layer passes
# through what the auth middleware verified.
_ADMIN_ROLES = frozenset({"admin", "owner"})


_VALID_ORIGINS = frozenset({"system", "community", "custom"})
_VALID_STATUSES = frozenset({"installed", "available", "disabled", "draft"})
_VALID_REASONING = frozenset({"fast", "balanced", "deep"})
_VALID_AUTONOMY = frozenset({"manual_approval", "auto_apply"})

_NAME_MAX = 80
_SLUG_MAX = 80
_DESCRIPTION_MAX = 400
_INSTRUCTIONS_MAX = 64_000  # 64KB; PRD doesn't pin a number, this is a guard.
_HUE_MIN = 0
_HUE_MAX = 359

_DEFAULT_PERMISSIONS: dict[str, Any] = {
    "autonomy": "manual_approval",
    "max_tool_calls_per_run": 20,
    "max_output_tokens": 8000,
    "read_only": False,
}
_DEFAULT_MODEL_ID = "anthropic:claude-sonnet-4-7-1m"
_DEFAULT_REASONING = "balanced"

_SLUG_PATTERN = re.compile(r"[^a-z0-9]+")


class AgentNotFound(Exception):
    """Raised when an agent doesn't exist OR the caller has no read rights.

    The 404-not-403 rule (cross-audit §1.3) collapses both branches to
    one exception so the route layer cannot accidentally distinguish
    them — the response is always 404.
    """


class AgentForbidden(Exception):
    """Raised when the caller can READ but cannot WRITE.

    Used after read access has already been established (so 404-not-403
    still applies for the read-doesn't-exist case). The route layer
    translates this to 403.
    """


class AgentInvalidRequest(Exception):
    """Raised for client-fixable invariant violations (400)."""


class AgentConflict(Exception):
    """Raised for state-conflict violations (409).

    Used for: duplicate slug, mutation on origin-immutable agent
    (``system``/``community``), and (forward-compat) hard-delete blocked
    by a routine version pin.
    """

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class AgentsService:
    """Composition of the agents store + identity store with ACL + audit."""

    def __init__(
        self,
        *,
        store: AgentsStore,
        identity_store: IdentityStore,
    ) -> None:
        self._store = store
        self._identity = identity_store

    # =================================================================
    # Reads
    # =================================================================

    def get_agent(
        self,
        *,
        tenant_id: str,
        caller_user_id: str,
        caller_roles: Iterable[str],
        agent_id: str,
    ) -> tuple[AgentRecord, "AgentInstallRecord | None"]:
        """Authorise + return a single agent plus the caller's install row.

        Returns ``(record, install)``. The route layer composes the wire
        ``viewer_install_status`` from these two values via
        :meth:`viewer_install_status_for`.

        Raises :class:`AgentNotFound` if the caller can't see the agent
        (404-not-403; the route never distinguishes "not found" from
        "not authorised").
        """

        record = self._store.get_agent(tenant_id=tenant_id, agent_id=agent_id)
        if record is None:
            raise AgentNotFound(agent_id)
        if not self._can_read(record, caller_user_id, caller_roles):
            raise AgentNotFound(agent_id)
        install = self._store.get_install(
            tenant_id=tenant_id, agent_id=agent_id, user_id=caller_user_id
        )
        return record, install

    def list_agents(
        self,
        *,
        tenant_id: str,
        caller_user_id: str,
        caller_roles: Iterable[str],
        origins: tuple[str, ...] | None = None,
        statuses: tuple[str, ...] | None = None,
        skill_ids: tuple[str, ...] | None = None,
        connector_ids: tuple[str, ...] | None = None,
        owner_user_id: str | None = None,
        q: str | None = None,
        cursor: str | None = None,
        limit: int = 50,
        sort: str = "updated_at:desc",
    ) -> tuple[tuple[tuple[AgentRecord, "AgentInstallRecord | None"], ...], str | None]:
        """List agents the caller can see.

        ACL gate (agents-prd §6.2):

        * ``system`` + ``community`` rows are always included.
        * ``custom`` rows: only the caller's own customs + customs they
          have installed. Non-admin can NEVER see another user's draft
          custom (404-not-403 enforced at the store-side
          ``visible_to_user_id`` filter).
        * Admin caller (tenant role ``admin``/``owner``): the
          visibility filter is dropped — admins see every row in the
          tenant for the compliance read path.
        * ``owner_user_id`` filter is admin-only — non-admins requesting
          ``filter[owner_user_id]=<other>`` are rejected at the route
          layer (membership-graph harvesting protection, same shape
          as projects-prd §4.4).
        """

        admin = _is_admin(caller_roles)
        visible_filter = None if admin else caller_user_id
        page, next_cursor = self._store.list_agents(
            tenant_id=tenant_id,
            origins=origins,
            statuses=statuses,
            skill_ids=skill_ids,
            connector_ids=connector_ids,
            owner_user_id=owner_user_id,
            visible_to_user_id=visible_filter,
            q=q,
            cursor=cursor,
            limit=limit,
            sort=sort,
        )
        enriched: list[tuple[AgentRecord, "AgentInstallRecord | None"]] = []
        for record in page:
            install = self._store.get_install(
                tenant_id=tenant_id,
                agent_id=record.id,
                user_id=caller_user_id,
            )
            enriched.append((record, install))
        return tuple(enriched), next_cursor

    # =================================================================
    # Writes — agent lifecycle
    # =================================================================

    def create_custom_agent(
        self,
        *,
        tenant_id: str,
        caller_user_id: str,
        payload: dict[str, Any],
    ) -> AgentRecord:
        """Create an ``origin='custom'`` agent owned by the caller.

        Any tenant member can create a custom agent (Wave 6 may add a
        tenant-admin quota; see agents-prd §11 Q1). The owner of the
        record is the caller; the slug is derived from the name when
        not supplied.

        The new row is created with ``status='draft'`` — the first
        explicit install (P8-A3) flips it to ``installed``.
        """

        validated = self._validate_create_payload(payload)
        slug = validated["slug"]

        # Slug uniqueness — case-insensitive, scoped to live rows.
        if self._store.get_agent_by_slug(tenant_id=tenant_id, slug=slug) is not None:
            raise AgentConflict("duplicate_slug")

        record = AgentRecord(
            tenant_id=tenant_id,
            name=validated["name"],
            slug=slug,
            description=validated.get("description", ""),
            icon_emoji=validated.get("icon_emoji", "🤖"),
            color_hue=int(validated.get("color_hue", 220)),
            version=1,
            status="draft",
            origin="custom",
            owner_user_id=caller_user_id,
            instructions=validated.get("instructions", ""),
            model_id=validated.get("model_id", _DEFAULT_MODEL_ID),
            reasoning_depth=validated.get("reasoning_depth", _DEFAULT_REASONING),
            skills=list(validated.get("skills", [])),
            connectors_default=list(validated.get("connectors_default", [])),
            permissions=dict(validated.get("permissions", _DEFAULT_PERMISSIONS)),
            memory_ref=validated.get("memory_ref"),
        )

        with self._store.transaction():
            stored = self._store.insert_agent(record)
            self._store.append_audit(
                AgentAuditRecord(
                    tenant_id=tenant_id,
                    actor_user_id=caller_user_id,
                    action="agent.create",
                    target_id=stored.id,
                    after_state=_safe_dump(stored),
                    context={
                        "name": stored.name,
                        "slug": stored.slug,
                        "origin": stored.origin,
                        "agent_id": stored.id,
                    },
                )
            )
        return stored

    def update_agent(
        self,
        *,
        tenant_id: str,
        caller_user_id: str,
        caller_roles: Iterable[str],
        agent_id: str,
        patch: dict[str, Any],
    ) -> AgentRecord:
        """Owner-only edit on the live record (agents-prd §4.4).

        ``system``/``community`` agents are origin-immutable — the PATCH
        responds 409 ``agent_origin_immutable``. Caller must duplicate
        (§4.10) to fork into a custom they own.

        PATCH does NOT bump ``version`` — explicit POST /versions does
        (§3.2).
        """

        existing = self._store.get_agent(tenant_id=tenant_id, agent_id=agent_id)
        if existing is None:
            raise AgentNotFound(agent_id)
        if not self._can_read(existing, caller_user_id, caller_roles):
            raise AgentNotFound(agent_id)
        if existing.origin != "custom":
            raise AgentConflict("agent_origin_immutable")
        if existing.owner_user_id != caller_user_id:
            raise AgentForbidden(agent_id)

        updates = self._validate_patch_payload(existing, patch)
        if not updates:
            # No-op PATCH — return the existing record unchanged.
            return existing

        # Slug uniqueness on rename — case-insensitive, scoped to the
        # same tenant, ignoring this row.
        new_slug = updates.get("slug")
        if new_slug is not None and new_slug.lower() != existing.slug.lower():
            collision = self._store.get_agent_by_slug(
                tenant_id=tenant_id, slug=new_slug
            )
            if collision is not None and collision.id != existing.id:
                raise AgentConflict("duplicate_slug")

        new_record = existing.model_copy(update={**updates, "updated_at": _now()})
        before = _safe_dump(existing)
        after = _safe_dump(new_record)
        with self._store.transaction():
            stored = self._store.update_agent(new_record)
            self._store.append_audit(
                AgentAuditRecord(
                    tenant_id=tenant_id,
                    actor_user_id=caller_user_id,
                    action="agent.update",
                    target_id=stored.id,
                    before_state=before,
                    after_state=after,
                    context={
                        "changed_fields": sorted(updates.keys()),
                        "agent_id": stored.id,
                    },
                )
            )
        return stored

    def delete_agent(
        self,
        *,
        tenant_id: str,
        caller_user_id: str,
        caller_roles: Iterable[str],
        agent_id: str,
    ) -> None:
        """Soft-delete a custom agent. Owner-only.

        Soft-delete sets ``deleted_at`` — the row stays visible to
        compliance reads but disappears from the public list/get paths.
        Hard-delete is the 90-day retention cleanup job (agents-prd §5.4)
        or a tenant-GDPR delete; neither lives in this routes-CRUD path.

        ``system``/``community`` agents cannot be deleted via this route
        (the catalog seeder owns retirement — see agents-prd §11 Q5).
        """

        existing = self._store.get_agent(tenant_id=tenant_id, agent_id=agent_id)
        if existing is None:
            raise AgentNotFound(agent_id)
        if not self._can_read(existing, caller_user_id, caller_roles):
            raise AgentNotFound(agent_id)
        if existing.origin != "custom":
            raise AgentConflict("agent_origin_immutable")
        if existing.owner_user_id != caller_user_id:
            raise AgentForbidden(agent_id)

        before = _safe_dump(existing)
        with self._store.transaction():
            self._store.soft_delete_agent(tenant_id=tenant_id, agent_id=agent_id)
            self._store.append_audit(
                AgentAuditRecord(
                    tenant_id=tenant_id,
                    actor_user_id=caller_user_id,
                    action="agent.soft_delete",
                    target_id=agent_id,
                    before_state=before,
                    context={"agent_id": agent_id, "soft": True},
                )
            )

    # =================================================================
    # Helpers
    # =================================================================

    def viewer_install_status_for(
        self,
        record: AgentRecord,
        install: "AgentInstallRecord | None",
        caller_user_id: str,
    ) -> str:
        """Derive the caller-relative ``viewer_install_status`` field.

        Per agents-prd §1.6:

          * No install row + caller is the owner of a draft custom →
            ``draft`` (the owner's first edit-cycle state).
          * No install row otherwise → ``available``.
          * Install row + ``disabled=true`` → ``disabled``.
          * Install row + ``disabled=false`` → ``installed``.
        """

        if install is None:
            if record.origin == "custom" and record.owner_user_id == caller_user_id:
                # The owner of a custom that hasn't been self-installed
                # yet sees the draft state regardless of the row's
                # underlying ``status`` field (which may be 'draft' or
                # 'installed' depending on whether the install row was
                # ever written).
                if record.status == "draft":
                    return "draft"
            return "available"
        if install.disabled:
            return "disabled"
        return "installed"

    def _can_read(
        self,
        record: AgentRecord,
        caller_user_id: str,
        caller_roles: Iterable[str],
    ) -> bool:
        """ACL gate per agents-prd §6.2.

        ``system`` + ``community`` are tenant-readable. ``custom`` is
        readable by owner OR by users who installed it. Admins (tenant
        role ``admin``/``owner``) get a compliance read on everything.
        """

        if _is_admin(caller_roles):
            return True
        if record.origin in ("system", "community"):
            # Drafts on system/community shouldn't happen but if they do,
            # treat them like custom drafts (server-side seeder owners).
            if record.status == "draft":
                return False
            return True
        # origin == 'custom'
        if record.owner_user_id == caller_user_id:
            return True
        install = self._store.get_install(
            tenant_id=record.tenant_id,
            agent_id=record.id,
            user_id=caller_user_id,
        )
        if install is None:
            return False
        # Drafts: visible to owner only.
        if record.status == "draft":
            return False
        return True

    # ----- validation ----------------------------------------------------

    def _validate_create_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise AgentInvalidRequest("invalid_payload")
        name = payload.get("name")
        if not isinstance(name, str) or not name.strip():
            raise AgentInvalidRequest("name_required")
        name = name.strip()
        if len(name) > _NAME_MAX:
            raise AgentInvalidRequest("name_too_long")

        slug_raw = payload.get("slug")
        if slug_raw is None or (isinstance(slug_raw, str) and not slug_raw.strip()):
            slug = _slugify(name)
        elif isinstance(slug_raw, str):
            slug = _slugify(slug_raw.strip())
        else:
            raise AgentInvalidRequest("slug_invalid")
        if not slug or len(slug) > _SLUG_MAX:
            raise AgentInvalidRequest("slug_invalid")

        description = payload.get("description", "")
        if description is None:
            description = ""
        if not isinstance(description, str):
            raise AgentInvalidRequest("description_invalid")
        if len(description) > _DESCRIPTION_MAX:
            raise AgentInvalidRequest("description_too_long")

        icon = payload.get("icon_emoji")
        if icon is not None:
            if not isinstance(icon, str) or not icon:
                raise AgentInvalidRequest("icon_invalid")
            if len(icon) > 16:
                raise AgentInvalidRequest("icon_too_long")

        hue = payload.get("color_hue", 220)
        if not isinstance(hue, int) or not (_HUE_MIN <= hue <= _HUE_MAX):
            raise AgentInvalidRequest("color_hue_invalid")

        instructions = payload.get("instructions", "")
        if instructions is None:
            instructions = ""
        if not isinstance(instructions, str):
            raise AgentInvalidRequest("instructions_invalid")
        if len(instructions) > _INSTRUCTIONS_MAX:
            raise AgentInvalidRequest("instructions_too_long")

        result: dict[str, Any] = {
            "name": name,
            "slug": slug,
            "description": description,
            "icon_emoji": icon or "🤖",
            "color_hue": hue,
            "instructions": instructions,
        }

        # Optional model + reasoning depth.
        model_default = payload.get("model_default")
        if model_default is not None:
            if not isinstance(model_default, dict):
                raise AgentInvalidRequest("model_default_invalid")
            model_id = model_default.get("model_id")
            depth = model_default.get("reasoning_depth")
            if not isinstance(model_id, str) or not model_id:
                raise AgentInvalidRequest("model_id_invalid")
            if depth not in _VALID_REASONING:
                raise AgentInvalidRequest("reasoning_depth_invalid")
            result["model_id"] = model_id
            result["reasoning_depth"] = depth

        # Skills + connectors (lists of strings).
        skills = payload.get("skills")
        if skills is not None:
            result["skills"] = _validate_string_list(skills, "skills")
        connectors = payload.get("connectors_default")
        if connectors is not None:
            result["connectors_default"] = _validate_string_list(
                connectors, "connectors_default"
            )

        permissions = payload.get("permissions")
        if permissions is not None:
            result["permissions"] = _validate_permissions(permissions)

        memory_ref = payload.get("memory_ref")
        if memory_ref is not None:
            if not isinstance(memory_ref, dict):
                raise AgentInvalidRequest("memory_ref_invalid")
            result["memory_ref"] = memory_ref

        return result

    def _validate_patch_payload(
        self, existing: AgentRecord, patch: dict[str, Any]
    ) -> dict[str, Any]:
        if not isinstance(patch, dict):
            raise AgentInvalidRequest("invalid_payload")
        updates: dict[str, Any] = {}

        if "name" in patch:
            name = patch["name"]
            if not isinstance(name, str) or not name.strip():
                raise AgentInvalidRequest("name_required")
            name = name.strip()
            if len(name) > _NAME_MAX:
                raise AgentInvalidRequest("name_too_long")
            updates["name"] = name
        if "slug" in patch:
            slug_raw = patch["slug"]
            if not isinstance(slug_raw, str):
                raise AgentInvalidRequest("slug_invalid")
            slug = _slugify(slug_raw.strip())
            if not slug or len(slug) > _SLUG_MAX:
                raise AgentInvalidRequest("slug_invalid")
            updates["slug"] = slug
        if "description" in patch:
            description = patch["description"]
            if description is None:
                description = ""
            if not isinstance(description, str):
                raise AgentInvalidRequest("description_invalid")
            if len(description) > _DESCRIPTION_MAX:
                raise AgentInvalidRequest("description_too_long")
            updates["description"] = description
        if "icon_emoji" in patch:
            icon = patch["icon_emoji"]
            if not isinstance(icon, str) or not icon:
                raise AgentInvalidRequest("icon_invalid")
            if len(icon) > 16:
                raise AgentInvalidRequest("icon_too_long")
            updates["icon_emoji"] = icon
        if "color_hue" in patch:
            hue = patch["color_hue"]
            if not isinstance(hue, int) or not (_HUE_MIN <= hue <= _HUE_MAX):
                raise AgentInvalidRequest("color_hue_invalid")
            updates["color_hue"] = hue
        if "instructions" in patch:
            instructions = patch["instructions"]
            if instructions is None:
                instructions = ""
            if not isinstance(instructions, str):
                raise AgentInvalidRequest("instructions_invalid")
            if len(instructions) > _INSTRUCTIONS_MAX:
                raise AgentInvalidRequest("instructions_too_long")
            updates["instructions"] = instructions
        if "model_default" in patch:
            model_default = patch["model_default"]
            if not isinstance(model_default, dict):
                raise AgentInvalidRequest("model_default_invalid")
            model_id = model_default.get("model_id")
            depth = model_default.get("reasoning_depth")
            if not isinstance(model_id, str) or not model_id:
                raise AgentInvalidRequest("model_id_invalid")
            if depth not in _VALID_REASONING:
                raise AgentInvalidRequest("reasoning_depth_invalid")
            updates["model_id"] = model_id
            updates["reasoning_depth"] = depth
        if "skills" in patch:
            updates["skills"] = _validate_string_list(patch["skills"], "skills")
        if "connectors_default" in patch:
            updates["connectors_default"] = _validate_string_list(
                patch["connectors_default"], "connectors_default"
            )
        if "permissions" in patch:
            updates["permissions"] = _validate_permissions(patch["permissions"])
        if "memory_ref" in patch:
            memory_ref = patch["memory_ref"]
            if memory_ref is not None and not isinstance(memory_ref, dict):
                raise AgentInvalidRequest("memory_ref_invalid")
            updates["memory_ref"] = memory_ref
        if "status" in patch:
            status_val = patch["status"]
            if status_val not in _VALID_STATUSES:
                raise AgentInvalidRequest("status_invalid")
            # Draft can flip to installed/disabled; can't go back to draft
            # via PATCH (use POST /uninstall for the disable path —
            # P8-A3 owns that route). Owner is the only PATCHer (gated
            # above) so the state machine is permissive here.
            updates["status"] = status_val

        return updates


# ---------------------------------------------------------------------------
# Helpers (module-private)
# ---------------------------------------------------------------------------


def _is_admin(caller_roles: Iterable[str]) -> bool:
    return any(role in _ADMIN_ROLES for role in caller_roles)


def _slugify(value: str) -> str:
    """Lowercase, hyphenate, strip leading/trailing hyphens.

    Mirrors the projects' name-uniqueness shape — the slug is the natural
    handle used in URLs and `@slug` mentions; multiple agents sharing a
    case-insensitive slug is rejected at write time.
    """

    if not isinstance(value, str):
        return ""
    cleaned = _SLUG_PATTERN.sub("-", value.lower()).strip("-")
    return cleaned


def _validate_string_list(value: Any, field_name: str) -> list[str]:
    if not isinstance(value, list):
        raise AgentInvalidRequest(f"{field_name}_invalid")
    out: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise AgentInvalidRequest(f"{field_name}_invalid_entry")
        stripped = item.strip()
        if stripped in seen:
            continue
        seen.add(stripped)
        out.append(stripped)
    return out


def _validate_permissions(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise AgentInvalidRequest("permissions_invalid")
    out: dict[str, Any] = {}
    autonomy = value.get("autonomy")
    if autonomy is not None:
        if autonomy not in _VALID_AUTONOMY:
            raise AgentInvalidRequest("autonomy_invalid")
        out["autonomy"] = autonomy
    if "max_tool_calls_per_run" in value:
        n = value["max_tool_calls_per_run"]
        if not isinstance(n, int) or n < 0:
            raise AgentInvalidRequest("max_tool_calls_per_run_invalid")
        out["max_tool_calls_per_run"] = n
    if "max_output_tokens" in value:
        n = value["max_output_tokens"]
        if not isinstance(n, int) or n < 0:
            raise AgentInvalidRequest("max_output_tokens_invalid")
        out["max_output_tokens"] = n
    if "read_only" in value:
        ro = value["read_only"]
        if not isinstance(ro, bool):
            raise AgentInvalidRequest("read_only_invalid")
        out["read_only"] = ro
    if "allowed_skill_ids" in value:
        out["allowed_skill_ids"] = _validate_string_list(
            value["allowed_skill_ids"], "allowed_skill_ids"
        )
    if "blocked_tool_families" in value:
        out["blocked_tool_families"] = _validate_string_list(
            value["blocked_tool_families"], "blocked_tool_families"
        )
    # Backfill required fields from default if absent (route may PATCH
    # the autonomy alone). The field-wise merge of agents-prd §3.3 lives
    # at the install-override layer (P8-A3); the canonical permissions on
    # the agent record must carry every key the runtime depends on.
    for k, v in _DEFAULT_PERMISSIONS.items():
        out.setdefault(k, v)
    return out


def _safe_dump(record: AgentRecord) -> dict[str, Any]:
    """Dump an agent record to a JSON-serialisable dict for audit rows.

    The ``instructions`` field can grow large (system-prompt body). When
    it's over 4KB we keep a hash + length in the audit row per
    agents-prd §6.1 (full text retained on the live row); under 4KB we
    embed verbatim.
    """

    dumped = record.model_dump(mode="json")
    instructions = dumped.get("instructions") or ""
    if isinstance(instructions, str) and len(instructions) > 4096:
        import hashlib

        digest = hashlib.sha256(instructions.encode("utf-8")).hexdigest()
        dumped["instructions"] = {
            "__hashed__": True,
            "sha256": digest,
            "length": len(instructions),
        }
    return dumped


__all__ = [
    "AgentConflict",
    "AgentForbidden",
    "AgentInvalidRequest",
    "AgentNotFound",
    "AgentsService",
]
