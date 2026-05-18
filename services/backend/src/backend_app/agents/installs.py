"""Per-user agent installs, overrides, and the duplicate/fork escape hatch.

This module backs Phase 8's ``agent_installs`` table (PRD §5.3) and the
install / uninstall / disable / overrides-update / duplicate endpoints
(PRD §4.5 — §4.10). The slice is owned entirely by P8-A3 — the
canonical agent catalog (P8-A1) and version snapshots (P8-A2) plug in
through narrow ports so we never import unmerged sibling code.

Fork-vs-overlay rule (PRD §6, the staff-engineer preamble of P8-A3):

  * ``AgentInstall.overrides`` is a **thin** per-user layer. Only the
    ``model_default`` and ``permissions`` fields may be tweaked there —
    not instructions, not skills, not connectors_default. Attempting to
    override ``instructions`` (or ``skills`` / ``connectors_default``)
    yields **HTTP 422** with a helpful pointer: "Instructions edits
    require fork — use POST /v1/agents/<id>/duplicate".
  * The single source of truth for instructions is therefore *either*
    the canonical agent (custom agent edited by its owner) *or* an
    immutable :class:`agent_versions` snapshot — never a per-user
    override row. The fork escape hatch keeps user-level customization
    discoverable without re-introducing a "deep override" layer that
    would split the source-of-truth across two writeable surfaces.

Soft-tombstone semantics:

  * ``POST /v1/agents/<id>/uninstall`` — stamps ``uninstalled_at`` AND
    drops ``overrides`` to ``None``. A subsequent install begins from a
    clean slate, which is what we want when a user says "remove this
    agent from my workspace" (uninstall is a destructive intent).
  * ``POST /v1/agents/<id>/disable`` — stamps ``uninstalled_at`` while
    preserving ``overrides``. Re-enable (a fresh install while the row
    is soft-tombstoned) restores the prior overrides. Disable is a
    "pause" intent — the user expects their tweaks to come back.

Tenant-first invariant: every store call opens with the caller's
``tenant_id`` (from the trusted service-token envelope), never from a
caller-supplied query param. The route handlers translate any
404-shaped failure into a single ``agent_not_found`` error to keep the
existence channel closed across tenants (PRD §6.2 "404-not-403 rule").
"""

from __future__ import annotations

import copy
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Final, Literal, Protocol
from uuid import uuid4

from enterprise_service_contracts.scopes import RUNTIME_USE
from fastapi import Depends, FastAPI, HTTPException, Path, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field

from backend_app.auth import BackendServiceAuthenticator
from backend_app.contracts import IdentityAuditEventRecord
from backend_app.identity.rbac import RequireScopes
from backend_app.identity.store import IdentityStore


# ---------------------------------------------------------------------------
# Override allowlist
# ---------------------------------------------------------------------------

#: The exact set of top-level fields ``AgentInstall.overrides`` may carry.
#: Anything else (in particular ``instructions``, ``skills``, and
#: ``connectors_default``) forces a fork via ``/duplicate``.
ALLOWED_OVERRIDE_FIELDS: Final[frozenset[str]] = frozenset(
    {"model_default", "permissions"}
)

#: Fork-required fields, called out by name so the 422 body can point the
#: caller at the exact culprit. Listed in PRD §3.3 "thin-layer contract".
FORK_REQUIRED_FIELDS: Final[frozenset[str]] = frozenset(
    {"instructions", "skills", "connectors_default"}
)

#: Top-level keys we recognize on ``AgentPermissions`` (PRD §3.1). The
#: override layer accepts any **subset** of these so a user can tweak
#: ``autonomy`` without restating ``max_tool_calls_per_run``.
PERMISSION_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "autonomy",
        "max_tool_calls_per_run",
        "max_output_tokens",
        "read_only",
        "allowed_skill_ids",
        "blocked_tool_families",
    }
)

#: Top-level keys on ``AgentModelDefault`` (PRD §3.1).
MODEL_DEFAULT_FIELDS: Final[frozenset[str]] = frozenset({"model_id", "reasoning_depth"})


class OverridesValidationError(ValueError):
    """Raised by :func:`validate_overrides` when the payload would force a
    fork. The route handler translates this into HTTP 422 with the
    ``forbidden_field`` + ``hint`` echoed back to the client."""

    def __init__(self, forbidden_field: str, hint: str) -> None:
        super().__init__(f"override field '{forbidden_field}' requires fork")
        self.forbidden_field = forbidden_field
        self.hint = hint


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def validate_overrides(payload: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Validate a proposed overrides payload against the thin-layer rule.

    Returns a deep-copied, validated dict (or ``None`` when the caller
    explicitly cleared the overrides). Raises
    :class:`OverridesValidationError` on any unknown / fork-required key.

    The validation is intentionally strict — only the allowlisted
    top-level fields and their known sub-fields pass through.
    """

    if payload is None:
        return None
    if not isinstance(payload, Mapping):
        raise OverridesValidationError(
            forbidden_field="<root>",
            hint="overrides must be a JSON object (or null to clear)",
        )

    out: dict[str, Any] = {}
    for key, value in payload.items():
        if key in FORK_REQUIRED_FIELDS:
            raise OverridesValidationError(
                forbidden_field=key,
                hint=(
                    f"Cannot override '{key}' on a system/community agent. "
                    "Instructions edits require fork — use POST "
                    "/v1/agents/<id>/duplicate to fork to a custom agent."
                ),
            )
        if key not in ALLOWED_OVERRIDE_FIELDS:
            raise OverridesValidationError(
                forbidden_field=key,
                hint=(
                    f"Unknown override field '{key}'. Allowed: "
                    f"{sorted(ALLOWED_OVERRIDE_FIELDS)}."
                ),
            )
        if key == "model_default":
            if not isinstance(value, Mapping):
                raise OverridesValidationError(
                    forbidden_field="model_default",
                    hint="model_default must be an object",
                )
            unknown = set(value.keys()) - MODEL_DEFAULT_FIELDS
            if unknown:
                raise OverridesValidationError(
                    forbidden_field=f"model_default.{sorted(unknown)[0]}",
                    hint=(
                        "Unknown model_default field. Allowed: "
                        f"{sorted(MODEL_DEFAULT_FIELDS)}."
                    ),
                )
            out["model_default"] = dict(value)
        elif key == "permissions":
            if not isinstance(value, Mapping):
                raise OverridesValidationError(
                    forbidden_field="permissions",
                    hint="permissions must be an object",
                )
            unknown = set(value.keys()) - PERMISSION_FIELDS
            if unknown:
                raise OverridesValidationError(
                    forbidden_field=f"permissions.{sorted(unknown)[0]}",
                    hint=(
                        "Unknown permissions field. Allowed: "
                        f"{sorted(PERMISSION_FIELDS)}."
                    ),
                )
            out["permissions"] = dict(value)

    return out if out else None


# ---------------------------------------------------------------------------
# Row + override wire shapes
# ---------------------------------------------------------------------------


class AgentInstallOverrides(BaseModel):
    """Thin per-user override layer — see PRD §3.3.

    Only ``model_default`` and ``permissions`` may appear. The
    ``permissions`` field is itself a partial — any subset of
    :data:`PERMISSION_FIELDS` is valid.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    model_default: dict[str, Any] | None = None
    permissions: dict[str, Any] | None = None


class AgentInstallRow(BaseModel):
    """One ``agent_installs`` row.

    A row exists when the user has touched the agent. Soft-tombstoned
    rows (``uninstalled_at is not None``) are returned by the row store
    when explicitly asked — they carry the prior ``overrides`` so a
    re-enable can restore them. Uninstall (vs. disable) clears
    ``overrides`` to None before stamping the tombstone.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(default_factory=lambda: f"aginst_{uuid4().hex}")
    tenant_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    agent_id: str = Field(min_length=1)
    installed_at: datetime = Field(default_factory=_utcnow)
    uninstalled_at: datetime | None = None
    overrides: dict[str, Any] | None = None


@dataclass(frozen=True)
class AgentCatalogRecord:
    """Minimal canonical fields the install slice needs from P8-A1's
    catalog store. We don't import P8-A1's full ``AgentRow`` so this
    slice stays independent of unmerged sibling code."""

    id: str
    tenant_id: str
    name: str
    slug: str
    description: str
    icon_emoji: str
    color_hue: int
    origin: Literal["system", "community", "custom"]
    owner_user_id: str | None
    instructions: str
    model_id: str
    reasoning_depth: Literal["fast", "balanced", "deep"]
    skills: tuple[str, ...]
    connectors_default: tuple[str, ...]
    permissions: dict[str, Any]
    memory_ref: dict[str, Any] | None = None
    forked_from_agent_id: str | None = None


@dataclass(frozen=True)
class DuplicateAgentResult:
    """Outcome of a successful fork. ``new_agent_id`` is the freshly
    minted catalog row's id; ``source_version`` is the source agent's
    monotonic version at fork time (for the audit ``context`` field)."""

    new_agent_id: str
    source_agent_id: str
    source_version: int


# ---------------------------------------------------------------------------
# Ports — install row store + catalog source
# ---------------------------------------------------------------------------


class AgentInstallStore(Protocol):
    """Row-level persistence for ``agent_installs``. Tenant + user are
    on every signature — adapters must enforce both."""

    @contextmanager
    def transaction(self) -> Iterator[None]: ...  # pragma: no cover

    def get(
        self,
        *,
        tenant_id: str,
        agent_id: str,
        user_id: str,
        include_tombstoned: bool = False,
    ) -> AgentInstallRow | None: ...

    def upsert(
        self,
        row: AgentInstallRow,
        *,
        conn: Any | None = None,
    ) -> AgentInstallRow:
        """Insert a fresh row OR re-activate a tombstoned row in place
        (preserving its ``id``). Returns the persisted row."""

    def update_overrides(
        self,
        *,
        tenant_id: str,
        agent_id: str,
        user_id: str,
        overrides: dict[str, Any] | None,
        conn: Any | None = None,
    ) -> AgentInstallRow | None: ...

    def soft_tombstone(
        self,
        *,
        tenant_id: str,
        agent_id: str,
        user_id: str,
        drop_overrides: bool,
        conn: Any | None = None,
    ) -> AgentInstallRow | None:
        """Stamp ``uninstalled_at`` on an active row. When
        ``drop_overrides=True`` (uninstall semantics) the ``overrides``
        column is also cleared. Returns None if no active row exists."""

    def list_for_user(
        self,
        *,
        tenant_id: str,
        user_id: str,
        include_tombstoned: bool = False,
    ) -> tuple[AgentInstallRow, ...]: ...


class AgentSourcePort(Protocol):
    """Read-side port over the canonical agent catalog (P8-A1).

    The install slice never needs to *mutate* a canonical row except
    on duplicate/fork; even there it asks the catalog to clone, rather
    than reaching into the catalog's writeable rows.
    """

    def get_agent(
        self,
        *,
        tenant_id: str,
        agent_id: str,
        as_user_id: str,
    ) -> AgentCatalogRecord | None:
        """Return the canonical agent visible to ``as_user_id`` in
        ``tenant_id`` (404-not-403 rule — drafts owned by other users
        and custom-not-owned rows resolve to None). ``None`` becomes
        ``agent_not_found`` at the route layer."""

    def get_agent_version(
        self,
        *,
        tenant_id: str,
        agent_id: str,
    ) -> int:
        """Cheap probe for the catalog's monotonic ``version`` counter
        — recorded in the ``agent.duplicate`` audit context."""

    def duplicate_as_custom(
        self,
        *,
        tenant_id: str,
        source_agent_id: str,
        owner_user_id: str,
        new_name: str | None,
        as_user_id: str,
    ) -> DuplicateAgentResult:
        """Fork the source agent into a new custom row owned by
        ``owner_user_id``. The new row's ``origin`` is ``"custom"``,
        ``status`` is ``"draft"``, and ``forked_from_agent_id`` is the
        source. The catalog implementation is responsible for the
        copy semantics; this port just states the contract."""


# ---------------------------------------------------------------------------
# In-memory adapters (tests + dev)
# ---------------------------------------------------------------------------


@dataclass
class InMemoryAgentInstallStore:
    """Dict-backed adapter. Keyed by ``(tenant_id, agent_id, user_id)``
    to enforce the ``UNIQUE (tenant_id, agent_id, user_id)`` constraint
    from PRD §5.3."""

    rows: dict[tuple[str, str, str], AgentInstallRow] = field(default_factory=dict)

    @contextmanager
    def transaction(self) -> Iterator[None]:
        yield None

    def _key(
        self, *, tenant_id: str, agent_id: str, user_id: str
    ) -> tuple[str, str, str]:
        return (tenant_id, agent_id, user_id)

    def get(
        self,
        *,
        tenant_id: str,
        agent_id: str,
        user_id: str,
        include_tombstoned: bool = False,
    ) -> AgentInstallRow | None:
        row = self.rows.get(
            self._key(tenant_id=tenant_id, agent_id=agent_id, user_id=user_id)
        )
        if row is None:
            return None
        if not include_tombstoned and row.uninstalled_at is not None:
            return None
        return row

    def upsert(
        self,
        row: AgentInstallRow,
        *,
        conn: Any | None = None,
    ) -> AgentInstallRow:
        del conn
        key = self._key(
            tenant_id=row.tenant_id, agent_id=row.agent_id, user_id=row.user_id
        )
        existing = self.rows.get(key)
        if existing is not None:
            # Reactivate-in-place when the existing row is tombstoned.
            # Idempotency on a live row: return the existing row
            # unchanged. The route layer relies on this for idempotent
            # install behavior (second install is a no-op).
            if existing.uninstalled_at is None:
                return existing
            reactivated = existing.model_copy(
                update={
                    "installed_at": row.installed_at,
                    "uninstalled_at": None,
                    # Preserve overrides — disable→install round trip
                    # restores the user's tweaks. (Uninstall already
                    # cleared overrides via soft_tombstone(drop=True);
                    # if those tweaks were dropped, ``existing.overrides``
                    # is None here and reactivation begins from clean.)
                }
            )
            self.rows[key] = reactivated
            return reactivated
        self.rows[key] = row
        return row

    def update_overrides(
        self,
        *,
        tenant_id: str,
        agent_id: str,
        user_id: str,
        overrides: dict[str, Any] | None,
        conn: Any | None = None,
    ) -> AgentInstallRow | None:
        del conn
        key = self._key(tenant_id=tenant_id, agent_id=agent_id, user_id=user_id)
        row = self.rows.get(key)
        if row is None or row.uninstalled_at is not None:
            return None
        # Deep copy so mutations after return can't reach the store.
        updated = row.model_copy(
            update={"overrides": copy.deepcopy(overrides) if overrides else None}
        )
        self.rows[key] = updated
        return updated

    def soft_tombstone(
        self,
        *,
        tenant_id: str,
        agent_id: str,
        user_id: str,
        drop_overrides: bool,
        conn: Any | None = None,
    ) -> AgentInstallRow | None:
        del conn
        key = self._key(tenant_id=tenant_id, agent_id=agent_id, user_id=user_id)
        row = self.rows.get(key)
        if row is None or row.uninstalled_at is not None:
            return None
        update: dict[str, Any] = {"uninstalled_at": _utcnow()}
        if drop_overrides:
            update["overrides"] = None
        tombstoned = row.model_copy(update=update)
        self.rows[key] = tombstoned
        return tombstoned

    def list_for_user(
        self,
        *,
        tenant_id: str,
        user_id: str,
        include_tombstoned: bool = False,
    ) -> tuple[AgentInstallRow, ...]:
        out = [
            r
            for r in self.rows.values()
            if r.tenant_id == tenant_id
            and r.user_id == user_id
            and (include_tombstoned or r.uninstalled_at is None)
        ]
        out.sort(key=lambda r: r.installed_at, reverse=True)
        return tuple(out)


@dataclass
class InMemoryAgentSource:
    """Test-only :class:`AgentSourcePort` — backed by a flat dict of
    pre-seeded :class:`AgentCatalogRecord` rows. Used by P8-A3's tests
    standalone of P8-A1's store."""

    agents: dict[tuple[str, str], AgentCatalogRecord] = field(default_factory=dict)
    versions: dict[tuple[str, str], int] = field(default_factory=dict)
    duplicates: list[DuplicateAgentResult] = field(default_factory=list)

    def add(self, record: AgentCatalogRecord, *, version: int = 1) -> None:
        self.agents[(record.tenant_id, record.id)] = record
        self.versions[(record.tenant_id, record.id)] = version

    def get_agent(
        self,
        *,
        tenant_id: str,
        agent_id: str,
        as_user_id: str,
    ) -> AgentCatalogRecord | None:
        record = self.agents.get((tenant_id, agent_id))
        if record is None:
            return None
        # Mirror the PRD §3.3 visibility rule: a custom agent not owned
        # by the caller is invisible (resolves to 404).
        if record.origin == "custom" and record.owner_user_id != as_user_id:
            return None
        return record

    def get_agent_version(self, *, tenant_id: str, agent_id: str) -> int:
        return self.versions.get((tenant_id, agent_id), 1)

    def duplicate_as_custom(
        self,
        *,
        tenant_id: str,
        source_agent_id: str,
        owner_user_id: str,
        new_name: str | None,
        as_user_id: str,
    ) -> DuplicateAgentResult:
        source = self.get_agent(
            tenant_id=tenant_id, agent_id=source_agent_id, as_user_id=as_user_id
        )
        if source is None:
            raise LookupError("source_agent_not_found")
        new_id = f"agent_{uuid4().hex}"
        name = new_name or f"{source.name} (custom)"
        clone = AgentCatalogRecord(
            id=new_id,
            tenant_id=tenant_id,
            name=name,
            # Slug uniqueness is the catalog's job; the test source mints
            # a unique-enough hex-suffixed value so multiple duplicates
            # of the same source don't collide.
            slug=f"{source.slug}-{new_id[6:14]}",
            description=source.description,
            icon_emoji=source.icon_emoji,
            color_hue=source.color_hue,
            origin="custom",
            owner_user_id=owner_user_id,
            instructions=source.instructions,
            model_id=source.model_id,
            reasoning_depth=source.reasoning_depth,
            skills=source.skills,
            connectors_default=source.connectors_default,
            permissions=dict(source.permissions),
            memory_ref=None,
            forked_from_agent_id=source.id,
        )
        self.agents[(tenant_id, new_id)] = clone
        self.versions[(tenant_id, new_id)] = 1
        result = DuplicateAgentResult(
            new_agent_id=new_id,
            source_agent_id=source.id,
            source_version=self.versions.get((tenant_id, source.id), 1),
        )
        self.duplicates.append(result)
        return result


# ---------------------------------------------------------------------------
# HTTP wire shapes
# ---------------------------------------------------------------------------


class InstallAgentRequest(BaseModel):
    """Body for ``POST /internal/v1/agents/{id}/install``. ``overrides``
    optional — both omitted and ``null`` mean "no overrides", an empty
    object clears prior overrides. Anything that isn't on the allowlist
    fails fast with HTTP 422."""

    model_config = ConfigDict(extra="forbid")

    overrides: dict[str, Any] | None = None


class UpdateInstallRequest(BaseModel):
    """Body for ``PATCH /internal/v1/agents/{id}/install``. The patch is
    a full replacement of the overrides column (per PRD §3.3 "partial
    replacement of canonical fields" — there is no nested merge)."""

    model_config = ConfigDict(extra="forbid")

    overrides: dict[str, Any] | None = None


class DuplicateAgentRequest(BaseModel):
    """Body for ``POST /internal/v1/agents/{id}/duplicate``."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, max_length=200)


class AgentInstallView(BaseModel):
    """Public, wire-safe view of an install row. Mirrors the
    :class:`AgentInstall` shape in ``packages/api-types`` (P8-A5)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    tenant_id: str
    user_id: str
    agent_id: str
    installed_at: str
    uninstalled_at: str | None
    overrides: dict[str, Any] | None


class DuplicateAgentResponse(BaseModel):
    """Wire shape for ``POST /internal/v1/agents/{id}/duplicate``.

    The full :class:`Agent` view is the catalog's responsibility; this
    response carries the **provenance** triple (so the FE can route to
    the new agent's edit page) and not a re-serialized canonical row."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    new_agent_id: str
    source_agent_id: str
    source_version: int


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


def register_agent_install_routes(
    app: FastAPI,
    *,
    install_store: AgentInstallStore,
    agent_source: AgentSourcePort,
    identity_store: IdentityStore,
) -> None:
    """Attach P8-A3's per-user install + duplicate routes to ``app``."""

    @app.post(
        "/internal/v1/agents/{agent_id}/install",
        response_model=AgentInstallView,
        status_code=status.HTTP_200_OK,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def install_agent(
        request: Request,
        payload: InstallAgentRequest,
        agent_id: str = Path(..., min_length=1, max_length=128),
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> AgentInstallView:
        identity = BackendServiceAuthenticator.internal_scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        # Look up the agent through the visible-to-caller filter; a
        # missing row becomes ``agent_not_found`` regardless of why.
        record = agent_source.get_agent(
            tenant_id=identity.org_id,
            agent_id=agent_id,
            as_user_id=identity.user_id,
        )
        if record is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "agent_not_found")
        # Validate overrides BEFORE the store touch so an invalid
        # payload doesn't create an empty row on first install.
        try:
            validated = validate_overrides(payload.overrides)
        except OverridesValidationError as exc:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "error": "override_field_requires_fork"
                    if exc.forbidden_field in FORK_REQUIRED_FIELDS
                    else "override_field_not_allowed",
                    "forbidden_field": exc.forbidden_field,
                    "hint": exc.hint,
                },
            ) from exc

        with install_store.transaction() as conn:
            existing = install_store.get(
                tenant_id=identity.org_id,
                agent_id=agent_id,
                user_id=identity.user_id,
                include_tombstoned=True,
            )
            is_reactivation = (
                existing is not None and existing.uninstalled_at is not None
            )
            is_noop_idempotent = (
                existing is not None
                and existing.uninstalled_at is None
                and validated is None
            )
            now = _utcnow()
            row = AgentInstallRow(
                tenant_id=identity.org_id,
                user_id=identity.user_id,
                agent_id=agent_id,
                installed_at=now,
                uninstalled_at=None,
                overrides=validated,
            )
            saved = install_store.upsert(row, conn=conn)
            # If the caller explicitly provided overrides on a fresh
            # install OR a re-enable, write them through. The upsert
            # path preserves prior overrides on re-enable; an explicit
            # caller-provided payload replaces them.
            if validated is not None and (
                existing is None
                or is_reactivation
                or (
                    existing is not None
                    and existing.uninstalled_at is None
                    and existing.overrides != validated
                )
            ):
                with_overrides = install_store.update_overrides(
                    tenant_id=identity.org_id,
                    agent_id=agent_id,
                    user_id=identity.user_id,
                    overrides=validated,
                    conn=conn,
                )
                if with_overrides is not None:
                    saved = with_overrides

            # Audit: skip on the truly-idempotent no-op path; otherwise
            # stamp ``agent.install`` (or ``agent.reinstall`` on a
            # disable→install round trip).
            if not is_noop_idempotent:
                action = "agent.reinstall" if is_reactivation else "agent.install"
                identity_store.append_identity_audit(
                    IdentityAuditEventRecord(
                        org_id=identity.org_id,
                        actor_user_id=identity.user_id,
                        subject_user_id=identity.user_id,
                        action=action,
                        metadata={
                            "agent_id": agent_id,
                            "install_id": saved.id,
                            "has_overrides": saved.overrides is not None,
                        },
                        request_ip=_request_ip(request),
                        user_agent=request.headers.get("user-agent"),
                    ),
                    conn=conn,
                )
        return _to_view(saved)

    @app.post(
        "/internal/v1/agents/{agent_id}/uninstall",
        response_model=AgentInstallView,
        status_code=status.HTTP_200_OK,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def uninstall_agent(
        request: Request,
        agent_id: str = Path(..., min_length=1, max_length=128),
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> AgentInstallView:
        return _tombstone_route(
            request=request,
            install_store=install_store,
            identity_store=identity_store,
            agent_source=agent_source,
            org_id=org_id,
            user_id=user_id,
            agent_id=agent_id,
            drop_overrides=True,
            audit_action="agent.uninstall",
        )

    @app.post(
        "/internal/v1/agents/{agent_id}/disable",
        response_model=AgentInstallView,
        status_code=status.HTTP_200_OK,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def disable_agent(
        request: Request,
        agent_id: str = Path(..., min_length=1, max_length=128),
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> AgentInstallView:
        return _tombstone_route(
            request=request,
            install_store=install_store,
            identity_store=identity_store,
            agent_source=agent_source,
            org_id=org_id,
            user_id=user_id,
            agent_id=agent_id,
            drop_overrides=False,
            audit_action="agent.disable",
        )

    @app.patch(
        "/internal/v1/agents/{agent_id}/install",
        response_model=AgentInstallView,
        status_code=status.HTTP_200_OK,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def patch_install_overrides(
        request: Request,
        payload: UpdateInstallRequest,
        agent_id: str = Path(..., min_length=1, max_length=128),
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> AgentInstallView:
        identity = BackendServiceAuthenticator.internal_scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        # Visibility check first — never let a 422 leak the agent's
        # existence to a caller who can't see it.
        record = agent_source.get_agent(
            tenant_id=identity.org_id,
            agent_id=agent_id,
            as_user_id=identity.user_id,
        )
        if record is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "agent_not_found")
        try:
            validated = validate_overrides(payload.overrides)
        except OverridesValidationError as exc:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "error": "override_field_requires_fork"
                    if exc.forbidden_field in FORK_REQUIRED_FIELDS
                    else "override_field_not_allowed",
                    "forbidden_field": exc.forbidden_field,
                    "hint": exc.hint,
                },
            ) from exc
        with install_store.transaction() as conn:
            updated = install_store.update_overrides(
                tenant_id=identity.org_id,
                agent_id=agent_id,
                user_id=identity.user_id,
                overrides=validated,
                conn=conn,
            )
            if updated is None:
                # Either no install row, or the row is soft-tombstoned.
                # Either way the caller hasn't a live install to patch.
                raise HTTPException(
                    status.HTTP_404_NOT_FOUND, "agent_install_not_found"
                )
            identity_store.append_identity_audit(
                IdentityAuditEventRecord(
                    org_id=identity.org_id,
                    actor_user_id=identity.user_id,
                    subject_user_id=identity.user_id,
                    action="agent.override_update",
                    metadata={
                        "agent_id": agent_id,
                        "install_id": updated.id,
                        "has_overrides": updated.overrides is not None,
                    },
                    request_ip=_request_ip(request),
                    user_agent=request.headers.get("user-agent"),
                ),
                conn=conn,
            )
        return _to_view(updated)

    @app.post(
        "/internal/v1/agents/{agent_id}/duplicate",
        response_model=DuplicateAgentResponse,
        status_code=status.HTTP_201_CREATED,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def duplicate_agent(
        request: Request,
        payload: DuplicateAgentRequest,
        agent_id: str = Path(..., min_length=1, max_length=128),
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> DuplicateAgentResponse:
        identity = BackendServiceAuthenticator.internal_scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        # Visibility check — a fork is a read operation against the
        # source, plus an owner-scoped write of the new record.
        record = agent_source.get_agent(
            tenant_id=identity.org_id,
            agent_id=agent_id,
            as_user_id=identity.user_id,
        )
        if record is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "agent_not_found")
        try:
            result = agent_source.duplicate_as_custom(
                tenant_id=identity.org_id,
                source_agent_id=agent_id,
                # Fork is **always** owned by the requesting user — the
                # body cannot redirect ownership to another tenant
                # member. This is the §3 invariant.
                owner_user_id=identity.user_id,
                new_name=(payload.name.strip() if payload.name else None),
                as_user_id=identity.user_id,
            )
        except LookupError as exc:
            # Catalog reported the source disappeared between the
            # visibility check and the clone. Surface as 404.
            raise HTTPException(status.HTTP_404_NOT_FOUND, "agent_not_found") from exc
        identity_store.append_identity_audit(
            IdentityAuditEventRecord(
                org_id=identity.org_id,
                actor_user_id=identity.user_id,
                subject_user_id=identity.user_id,
                action="agent.duplicate",
                metadata={
                    "source_agent_id": result.source_agent_id,
                    "source_version": result.source_version,
                    "new_agent_id": result.new_agent_id,
                },
                request_ip=_request_ip(request),
                user_agent=request.headers.get("user-agent"),
            ),
        )
        return DuplicateAgentResponse(
            new_agent_id=result.new_agent_id,
            source_agent_id=result.source_agent_id,
            source_version=result.source_version,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _tombstone_route(
    *,
    request: Request,
    install_store: AgentInstallStore,
    identity_store: IdentityStore,
    agent_source: AgentSourcePort,
    org_id: str,
    user_id: str,
    agent_id: str,
    drop_overrides: bool,
    audit_action: str,
) -> AgentInstallView:
    """Shared body for ``uninstall`` (drop overrides) and ``disable``
    (preserve overrides). Both end at the same soft-tombstone shape."""

    identity = BackendServiceAuthenticator.internal_scoped_identity(
        request, org_id=org_id, user_id=user_id
    )
    record = agent_source.get_agent(
        tenant_id=identity.org_id,
        agent_id=agent_id,
        as_user_id=identity.user_id,
    )
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "agent_not_found")
    with install_store.transaction() as conn:
        tombstoned = install_store.soft_tombstone(
            tenant_id=identity.org_id,
            agent_id=agent_id,
            user_id=identity.user_id,
            drop_overrides=drop_overrides,
            conn=conn,
        )
        if tombstoned is None:
            # No active install to tombstone — keep the existence
            # channel closed and use the standard not-found error.
            raise HTTPException(status.HTTP_404_NOT_FOUND, "agent_install_not_found")
        identity_store.append_identity_audit(
            IdentityAuditEventRecord(
                org_id=identity.org_id,
                actor_user_id=identity.user_id,
                subject_user_id=identity.user_id,
                action=audit_action,
                metadata={
                    "agent_id": agent_id,
                    "install_id": tombstoned.id,
                    "preserved_overrides": (
                        not drop_overrides and tombstoned.overrides is not None
                    ),
                },
                request_ip=_request_ip(request),
                user_agent=request.headers.get("user-agent"),
            ),
            conn=conn,
        )
    return _to_view(tombstoned)


def _to_view(row: AgentInstallRow) -> AgentInstallView:
    return AgentInstallView(
        id=row.id,
        tenant_id=row.tenant_id,
        user_id=row.user_id,
        agent_id=row.agent_id,
        installed_at=row.installed_at.isoformat(),
        uninstalled_at=(
            row.uninstalled_at.isoformat() if row.uninstalled_at is not None else None
        ),
        overrides=copy.deepcopy(row.overrides) if row.overrides else None,
    )


def _request_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip() or None
    return request.client.host if request.client else None


__all__ = [
    "ALLOWED_OVERRIDE_FIELDS",
    "AgentCatalogRecord",
    "AgentInstallOverrides",
    "AgentInstallRow",
    "AgentInstallStore",
    "AgentInstallView",
    "AgentSourcePort",
    "DuplicateAgentRequest",
    "DuplicateAgentResponse",
    "DuplicateAgentResult",
    "FORK_REQUIRED_FIELDS",
    "InMemoryAgentInstallStore",
    "InMemoryAgentSource",
    "InstallAgentRequest",
    "MODEL_DEFAULT_FIELDS",
    "OverridesValidationError",
    "PERMISSION_FIELDS",
    "UpdateInstallRequest",
    "register_agent_install_routes",
    "validate_overrides",
]
