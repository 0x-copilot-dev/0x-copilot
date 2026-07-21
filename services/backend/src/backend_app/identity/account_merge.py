"""Account-merge engine (PRD docs/plan/account-linking §6.3 — linking PR6).

One account = one personal org, so merging two accounts is an org
consolidation: every backend row keyed to the ABSORBED ``(org_id, user_id)``
is re-keyed to the SURVIVOR, the runtime (ai-backend) re-keys its own rows
via HTTP (the service boundary forbids importing it), the absorbed sessions
are revoked, and the absorbed user is soft-disabled with lineage — never
hard-deleted (NFR-6).

The saga (NFR-3/8): ``account_merges.state`` is the last COMPLETED
checkpoint; each step is idempotent, a failure records ``error`` and leaves
the checkpoint, and ``resume`` re-enters at the next step. Nothing is
destructive before the re-key it depends on is confirmed:

    pending           → backend re-key done   (backend_done)
    backend_done      → runtime re-key done   (runtime_done)
    runtime_done      → absorbed sessions revoked (sessions_revoked)
    sessions_revoked  → absorbed disabled + audited (completed)

Execution context (Postgres): the re-key SQL runs on the backend's own pool
connection, exactly like the migration runner — the backend connects as the
schema owner, which per-org RLS policies do not constrain. Deployments that
run the backend as a restricted role must grant the merge role BYPASSRLS
(deployment control; PRD §7).
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Protocol

import httpx
from copilot_service_contracts.headers import (
    ORG_HEADER,
    SERVICE_TOKEN_HEADER,
    USER_HEADER,
)

from backend_app.contracts import (
    AccountMergeRecord,
    AccountMergeState,
    IdentityAuditEventRecord,
    UserStatus,
)
from backend_app.identity.account_merge_store import AccountMergeStore
from backend_app.identity.sessions import SessionService
from backend_app.identity.store import IdentityStore

_LOGGER = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class AccountMergeError(RuntimeError):
    """Base class; ``detail`` is the stable wire code."""

    detail = "account_merge_error"


class MergeNotAllowed(AccountMergeError):
    """Preconditions failed (same account / non-personal org / missing user)."""

    detail = "merge_not_allowed"

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class MergeRuntimeFailed(AccountMergeError):
    """The ai-backend re-key call failed — the saga stops at its checkpoint."""

    detail = "merge_runtime_failed"


# ---------------------------------------------------------------------------
# Runtime (ai-backend) port — HTTP across the service boundary
# ---------------------------------------------------------------------------


class RuntimeMergePort(Protocol):
    def merge(
        self,
        *,
        merge_id: str,
        absorbed_org_id: str,
        absorbed_user_id: str,
        survivor_org_id: str,
        survivor_user_id: str,
    ) -> dict[str, Any]:
        """Re-key the runtime's rows; returns its counts payload. Idempotent."""


class NullRuntimeMergeClient:
    """Dev/test stand-in: records calls, moves nothing.

    Used when no ai-backend URL is configured (e.g. unit harnesses). The
    saga still records the step so a later real client can be swapped in.
    """

    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []

    def merge(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append({k: str(v) for k, v in kwargs.items()})
        return {
            "status": "skipped",
            "tables": {},
            "warnings": ["runtime_not_configured"],
        }


class UnconfiguredRuntimeMergeClient:
    """Fails CLOSED when a real (Postgres) deployment lacks AI_BACKEND_URL.

    Silently skipping the runtime leg would mark a merge "completed" while
    the absorbed account's conversations/runs/memory stay stranded in
    ai-backend — the exact silent-partial-merge NFR-10 forbids. Raising here
    halts the saga at its resumable ``backend_done`` checkpoint instead.
    """

    def merge(self, **kwargs: Any) -> dict[str, Any]:
        del kwargs
        raise MergeRuntimeFailed(
            "runtime merge is not configured: set AI_BACKEND_URL for the "
            "backend process (the saga resumes from its checkpoint once set)"
        )


class HttpRuntimeMergeClient:
    """Calls the ai-backend internal merge endpoint (PRD §6.4).

    Auth is the shared service token + the SURVIVOR identity headers (the
    endpoint itself is service-token-gated, not tenant-scoped; the explicit
    absorbed/survivor pairs travel in the body).
    """

    def __init__(
        self,
        *,
        base_url: str,
        service_token: str | None,
        timeout_seconds: float = 60.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._service_token = service_token or ""
        self._timeout = timeout_seconds

    def merge(
        self,
        *,
        merge_id: str,
        absorbed_org_id: str,
        absorbed_user_id: str,
        survivor_org_id: str,
        survivor_user_id: str,
    ) -> dict[str, Any]:
        headers = {
            ORG_HEADER: survivor_org_id,
            USER_HEADER: survivor_user_id,
        }
        if self._service_token:
            headers[SERVICE_TOKEN_HEADER] = self._service_token
        try:
            response = httpx.post(
                f"{self._base_url}/internal/v1/admin/account-merge",
                json={
                    "merge_id": merge_id,
                    "absorbed_org_id": absorbed_org_id,
                    "absorbed_user_id": absorbed_user_id,
                    "survivor_org_id": survivor_org_id,
                    "survivor_user_id": survivor_user_id,
                },
                headers=headers,
                timeout=self._timeout,
            )
        except httpx.HTTPError as exc:  # network / timeout
            raise MergeRuntimeFailed(f"runtime merge unreachable: {exc}") from exc
        if response.status_code >= 400:
            raise MergeRuntimeFailed(
                f"runtime merge returned {response.status_code}: {response.text[:500]}"
            )
        payload: dict[str, Any] = response.json()
        return payload


# ---------------------------------------------------------------------------
# Backend data port — the re-key itself
# ---------------------------------------------------------------------------


class MergeDataPort(Protocol):
    def rekey(
        self,
        *,
        absorbed_org_id: str,
        absorbed_user_id: str,
        survivor_org_id: str,
        survivor_user_id: str,
    ) -> dict[str, int]:
        """Move every absorbed-owned backend row to the survivor. Idempotent
        (a second run finds nothing to move). Returns per-table counts."""

    def disable_absorbed_user(
        self,
        *,
        absorbed_org_id: str,
        absorbed_user_id: str,
        survivor_user_id: str,
    ) -> bool:
        """Soft-disable the absorbed user + stamp merge lineage (FR-M7)."""


class InMemoryMergeData:
    """Re-keys the in-memory stores used by tests/dev.

    Takes whichever stores the harness wires; each is optional so partial
    fixtures keep working. TokenVault ciphertext is NOT org-bound (no AAD),
    so moving encrypted rows is a plain re-key — same as Postgres.
    """

    def __init__(
        self,
        *,
        identity_store: Any,
        siwe_store: Any | None = None,
        oidc_store: Any | None = None,
        provider_keys_store: Any | None = None,
        me_store: Any | None = None,
    ) -> None:
        self._identity = identity_store
        self._siwe = siwe_store
        self._oidc = oidc_store
        self._provider_keys = provider_keys_store
        self._me = me_store

    def rekey(
        self,
        *,
        absorbed_org_id: str,
        absorbed_user_id: str,
        survivor_org_id: str,
        survivor_user_id: str,
    ) -> dict[str, int]:
        counts: dict[str, int] = {}

        if self._siwe is not None:
            moved = 0
            for wallet_id, row in list(self._siwe.wallet_identities.items()):
                if row.org_id == absorbed_org_id and row.user_id == absorbed_user_id:
                    self._siwe.wallet_identities[wallet_id] = row.model_copy(
                        update={
                            "org_id": survivor_org_id,
                            "user_id": survivor_user_id,
                        }
                    )
                    moved += 1
            counts["wallet_identities"] = moved

        if self._oidc is not None:
            moved = 0
            for identity_id, row in list(self._oidc.identities.items()):
                if (
                    row.org_id == absorbed_org_id
                    and row.user_id == absorbed_user_id
                    and row.unlinked_at is None
                ):
                    self._oidc.identities[identity_id] = row.model_copy(
                        update={
                            "org_id": survivor_org_id,
                            "user_id": survivor_user_id,
                        }
                    )
                    moved += 1
            counts["oidc_identities"] = moved

        if self._provider_keys is not None:
            moved = 0
            for key, row in list(self._provider_keys.rows.items()):
                row_org, row_user, provider = key
                if row_org == absorbed_org_id and row_user == absorbed_user_id:
                    target = (survivor_org_id, survivor_user_id, provider)
                    # Collision rule (FR-M8): survivor's key wins.
                    if target not in self._provider_keys.rows:
                        self._provider_keys.rows[target] = row.model_copy(
                            update={
                                "org_id": survivor_org_id,
                                "user_id": survivor_user_id,
                            }
                        )
                        moved += 1
                    del self._provider_keys.rows[key]
            counts["provider_api_keys"] = moved

        if self._me is not None:
            for name in ("profiles", "preferences"):
                moved = 0
                table: dict[tuple[str, str], Any] = getattr(self._me, name)
                source = (absorbed_org_id, absorbed_user_id)
                target = (survivor_org_id, survivor_user_id)
                if source in table:
                    # Collision rule (FR-M8): survivor's row wins.
                    if target not in table:
                        row = table[source]
                        table[target] = row.model_copy(
                            update={
                                "org_id": survivor_org_id,
                                "user_id": survivor_user_id,
                            }
                        )
                        moved += 1
                    del table[source]
                counts[f"user_{name}"] = moved

        return counts

    def disable_absorbed_user(
        self,
        *,
        absorbed_org_id: str,
        absorbed_user_id: str,
        survivor_user_id: str,
    ) -> bool:
        user = self._identity.get_user(org_id=absorbed_org_id, user_id=absorbed_user_id)
        if user is None:
            return False
        if user.absorbed_into_user_id == survivor_user_id:
            return True  # idempotent re-run
        stamped = user.model_copy(
            update={
                "status": UserStatus.DISABLED,
                "deleted_at": user.deleted_at or _now(),
                "absorbed_into_user_id": survivor_user_id,
                "merged_at": _now(),
            }
        )
        # In-memory rows are stored by user_id; write the stamped record
        # directly (update_user refuses deleted rows, and postgres has its
        # own SQL path for the lineage columns).
        self._identity.users[absorbed_user_id] = stamped
        return True


class PostgresMergeData:
    """Raw-SQL re-key over the backend's tenant tables (PRD FR-M3/M8).

    A data-migration executor, deliberately NOT per-store methods — the merge
    is one privileged operation over a declared table registry, like the
    migration runner. Tables absent from a deployment are skipped via
    ``to_regclass`` so partial schemas keep working.

    Strategies:
    - RETENANT: plain UPDATE of the tenancy columns (TokenVault ciphertext is
      not org-bound, so encrypted columns move as-is).
    - SURVIVOR_WINS: rows whose (survivor-side) unique key already exists are
      DELETED (the survivor's row is kept); the rest are re-keyed.
    - DROP: absorbed rows that must not follow the user (security material,
      pending tokens) are deleted.
    - Audit/forensic tables (identity_audit_events, *_audit_events,
      login_attempts, sessions) are deliberately NOT re-keyed — history stays
      where it happened (NFR-5); sessions die by revocation, not adoption.
    """

    # Declarative registry — org/user COLUMN NAMES are explicit per table
    # (several tables use tenant_id / owner_user_id / reviewer_* naming) and
    # are verified against the migrations DDL by
    # tests/identity/test_postgres_merge_registry.py, so a schema drift or a
    # wrong column here fails CI instead of aborting a live merge.
    #
    # Spec shape: (table, strategy, org_col, user_col, key_cols)
    #   retenant  — plain UPDATE of the tenancy columns.
    #   singleton — one row per owner: survivor's row wins, else re-key.
    #   keyed     — survivor wins per key value, rest re-keyed.
    #   drop      — security material / pending flow state dies with the
    #               absorbed account (never follows the user).
    _SPECS: tuple[tuple[str, str, str | None, str | None, tuple[str, ...]], ...] = (
        ("wallet_identities", "retenant", "org_id", "user_id", ()),
        ("oidc_identities", "retenant", "org_id", "user_id", ()),
        ("saml_identities", "retenant", "org_id", "user_id", ()),
        # The "Use locally" device edge (baseline schema): rides with the
        # user like every other identity edge. The deployment-wide singleton
        # index is unaffected by a retenant (row count unchanged).
        ("local_accounts", "retenant", "org_id", "user_id", ()),
        ("scim_external_ids", "retenant", "org_id", "user_id", ()),
        ("mcp_servers", "retenant", "org_id", "user_id", ()),
        ("mcp_auth_sessions", "retenant", "org_id", "user_id", ()),
        ("mcp_auth_connections", "retenant", "org_id", "user_id", ()),
        ("api_keys", "retenant", "org_id", "user_id", ()),
        ("todos", "retenant", "tenant_id", "owner_user_id", ()),
        ("todo_series", "retenant", "tenant_id", "owner_user_id", ()),
        ("adapter_candidates", "retenant", "tenant_id", "submitter_user_id", ()),
        ("adapter_reviews", "retenant", "reviewer_org_id", "reviewer_user_id", ()),
        # UNIQUE (org_id, user_id, name): same-named skill → survivor wins.
        ("skills", "keyed", "org_id", "user_id", ("name",)),
        ("provider_api_keys", "keyed", "org_id", "user_id", ("provider",)),
        # Generative-UI spec registry: the unique key (uq_surface_specs_key)
        # is org-scoped, NOT user-scoped, and ``user_id`` is authorship
        # provenance only (reads are org-wide; the API view omits it). So the
        # merge treats it as org-keyed content: survivor wins per spec
        # identity, the rest retenant with provenance left on the absorbed
        # (soft-disabled, never deleted) author. Declaring user_id here would
        # under-match the survivor-wins EXISTS in multi-user survivor orgs and
        # abort the merge on the unique index instead.
        (
            "surface_specs",
            "keyed",
            "org_id",
            None,
            (
                "server",
                "tool",
                "output_shape_hash",
                "spec_schema_version",
                "skill_version",
                "origin",
            ),
        ),
        # user_id-only tables (no org column at all).
        (
            "notification_preferences",
            "keyed",
            None,
            "user_id",
            ("event_kind", "channel"),
        ),
        ("notification_quiet_hours", "singleton", None, "user_id", ()),
        # org-only config, PK (tenant_id, namespace).
        ("tenant_settings", "keyed", "tenant_id", None, ("namespace",)),
        ("user_profiles", "singleton", "org_id", "user_id", ()),
        ("user_preferences", "singleton", "org_id", "user_id", ()),
        ("user_avatars", "singleton", "org_id", "user_id", ()),
        ("privacy_settings", "singleton", "org_id", "user_id", ()),
        ("tool_use_policies", "singleton", "org_id", "user_id", ()),
        # Security material / pending flow state: dies with the absorbed
        # account (the survivor keeps their own). oidc_refresh_tokens are
        # encrypted IdP credentials — exactly the _DROP doctrine.
        ("mfa_factors", "drop", "org_id", "user_id", ()),
        ("mfa_challenges", "drop", "org_id", "user_id", ()),
        ("mfa_recovery_codes", "drop", "org_id", "user_id", ()),
        ("local_credentials", "drop", "org_id", "user_id", ()),
        ("password_reset_tokens", "drop", "org_id", "user_id", ()),
        ("account_lockouts", "drop", "org_id", "user_id", ()),
        ("oidc_refresh_tokens", "drop", "org_id", "user_id", ()),
        ("oidc_authentications", "drop", "org_id", None, ()),
        ("magic_link_tokens", "drop", "org_id", None, ()),
        ("invitations", "drop", "org_id", None, ()),
        ("scim_tokens", "drop", "org_id", None, ()),
    )
    # DELIBERATELY LEFT IN PLACE (the registry test enforces that every
    # tenant table is either in _SPECS or named here with its reason):
    # - identity_audit_events / mcp_audit_events / skill_audit_events /
    #   todo_audit_events / adapter_registry_audit_events / login_attempts:
    #   append-only history stays where it happened (NFR-5).
    # - sessions: die by revocation in the saga's step 3, never adopted.
    # - organizations / users / organization_members / role_assignments:
    #   the absorbed org is retired with its sole member soft-disabled; the
    #   single-org user model forbids moving memberships (PRD §3).
    # - promoted_adapters / adapter_registry state / scim_groups /
    #   scim_group_members / siem_exporter_controls / auth_providers /
    #   auth_provider_domains / identity_policies / lockout_policies /
    #   password_policies / tenant-level org config: org-scoped configuration
    #   of the retired org — inert once no member can sign in (disabled users
    #   fail closed at every ramp).
    # - saml_authentications / siwe_nonces / oidc_jwks_cache: TTL'd flow
    #   state / global cache; completing a pending flow against a disabled
    #   user fails closed at user resolution.
    # - account_merges: the saga's own ledger.
    _LEAVE_IN_PLACE: frozenset[str] = frozenset(
        {
            "identity_audit_events",
            "mcp_audit_events",
            "skill_audit_events",
            "todo_audit_events",
            "adapter_registry_audit_events",
            "login_attempts",
            "sessions",
            "organizations",
            "users",
            "organization_members",
            "roles",
            "role_assignments",
            "promoted_adapters",
            "tenant_adapter_settings",
            "scim_groups",
            "scim_group_members",
            "siem_exporter_controls",
            "auth_providers",
            "auth_provider_domains",
            "identity_policies",
            "lockout_policies",
            "password_policies",
            "saml_authentications",
            "siwe_nonces",
            "oidc_jwks_cache",
            "account_merges",
        }
    )

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    @contextmanager
    def _conn(self) -> Iterator[Any]:
        with self._pool.connection() as conn:
            with conn.transaction():
                yield conn

    @staticmethod
    def _exists(cur: Any, table: str) -> bool:
        cur.execute("SELECT to_regclass(%s)", (table,))
        row = cur.fetchone()
        value = row[0] if isinstance(row, tuple) else next(iter(row.values()))
        return value is not None

    @staticmethod
    def _has_column(cur: Any, table: str, column: str) -> bool:
        cur.execute(
            """
            SELECT 1 FROM information_schema.columns
            WHERE table_name = %s AND column_name = %s
            """,
            (table, column),
        )
        return cur.fetchone() is not None

    @staticmethod
    def _predicates(
        org_col: str | None,
        user_col: str | None,
        org_id: str,
        user_id: str,
        alias: str = "",
    ) -> tuple[str, tuple[str, ...]]:
        """WHERE fragment + params for a table's declared tenancy columns."""

        prefix = f"{alias}." if alias else ""
        clauses: list[str] = []
        params: list[str] = []
        if org_col is not None:
            clauses.append(f"{prefix}{org_col} = %s")
            params.append(org_id)
        if user_col is not None:
            clauses.append(f"{prefix}{user_col} = %s")
            params.append(user_id)
        return " AND ".join(clauses), tuple(params)

    def rekey(
        self,
        *,
        absorbed_org_id: str,
        absorbed_user_id: str,
        survivor_org_id: str,
        survivor_user_id: str,
    ) -> dict[str, int]:
        counts: dict[str, int] = {}
        with self._conn() as conn:
            with conn.cursor() as cur:

                def _retenant(
                    table: str, org_col: str | None, user_col: str | None
                ) -> int:
                    where, where_params = self._predicates(
                        org_col, user_col, absorbed_org_id, absorbed_user_id
                    )
                    sets: list[str] = []
                    set_params: list[str] = []
                    if org_col is not None:
                        sets.append(f"{org_col} = %s")
                        set_params.append(survivor_org_id)
                    if user_col is not None:
                        sets.append(f"{user_col} = %s")
                        set_params.append(survivor_user_id)
                    cur.execute(
                        f"UPDATE {table} SET {', '.join(sets)} WHERE {where}",
                        (*set_params, *where_params),
                    )
                    return cur.rowcount

                # MFA children first: totp_secrets / webauthn_credentials carry
                # only factor_id, so they drop via a join through mfa_factors —
                # and MUST go before the factors row (FK, no CASCADE).
                for child in ("totp_secrets", "webauthn_credentials"):
                    if not self._exists(cur, child) or not self._exists(
                        cur, "mfa_factors"
                    ):
                        continue
                    cur.execute(
                        f"""
                        DELETE FROM {child}
                        WHERE factor_id IN (
                            SELECT factor_id FROM mfa_factors
                            WHERE org_id = %s AND user_id = %s
                        )
                        """,
                        (absorbed_org_id, absorbed_user_id),
                    )
                    counts[f"{child}_dropped"] = cur.rowcount

                for table, strategy, org_col, user_col, key_cols in self._SPECS:
                    if not self._exists(cur, table):
                        continue
                    a_where, a_params = self._predicates(
                        org_col, user_col, absorbed_org_id, absorbed_user_id, "a"
                    )
                    s_where, s_params = self._predicates(
                        org_col, user_col, survivor_org_id, survivor_user_id, "s"
                    )

                    if strategy == "drop":
                        where, params = self._predicates(
                            org_col, user_col, absorbed_org_id, absorbed_user_id
                        )
                        cur.execute(f"DELETE FROM {table} WHERE {where}", params)
                        counts[f"{table}_dropped"] = cur.rowcount
                        continue

                    if strategy in ("singleton", "keyed"):
                        # Survivor wins (FR-M8): drop absorbed rows whose
                        # owner (+ key value) already exists survivor-side.
                        key_match = "".join(
                            f" AND s.{col} = a.{col}" for col in key_cols
                        )
                        cur.execute(
                            f"""
                            DELETE FROM {table} a
                            WHERE {a_where}
                              AND EXISTS (
                                SELECT 1 FROM {table} s
                                WHERE {s_where}{key_match}
                              )
                            """,
                            (*a_params, *s_params),
                        )
                        if cur.rowcount:
                            counts[f"{table}_dropped"] = cur.rowcount

                    counts[table] = _retenant(table, org_col, user_col)
        return counts

    def disable_absorbed_user(
        self,
        *,
        absorbed_org_id: str,
        absorbed_user_id: str,
        survivor_user_id: str,
    ) -> bool:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE users SET
                        status = 'disabled',
                        deleted_at = COALESCE(deleted_at, now()),
                        absorbed_into_user_id = %s,
                        merged_at = COALESCE(merged_at, now()),
                        updated_at = now()
                    WHERE org_id = %s AND user_id = %s
                    """,
                    (survivor_user_id, absorbed_org_id, absorbed_user_id),
                )
                return bool(cur.rowcount)


# ---------------------------------------------------------------------------
# The saga service
# ---------------------------------------------------------------------------


class AccountMergeService:
    """Runs the merge saga (PRD §6.3). Synchronous per step, resumable."""

    def __init__(
        self,
        *,
        identity_store: IdentityStore,
        merge_store: AccountMergeStore,
        sessions: SessionService,
        data_port: MergeDataPort,
        runtime_port: RuntimeMergePort,
    ) -> None:
        self._identity = identity_store
        self._merges = merge_store
        self._sessions = sessions
        self._data = data_port
        self._runtime = runtime_port

    # Entry points ------------------------------------------------------
    def merge_for_conflict(
        self,
        *,
        survivor_org_id: str,
        survivor_user_id: str,
        absorbed_org_id: str,
        absorbed_user_id: str,
        proof_ref: str,
        ip: str | None = None,
        user_agent: str | None = None,
    ) -> AccountMergeRecord:
        """Merge triggered by a proven link conflict (FR-M1/M2, D-01).

        The SURVIVOR is always the authenticated caller; ``proof_ref`` names
        the fresh proof of the absorbed-side identity (the consent record).
        Idempotent: an absorbed account already merged into this survivor
        returns the completed record.
        """

        if absorbed_org_id == survivor_org_id and absorbed_user_id == survivor_user_id:
            raise MergeNotAllowed("cannot merge an account into itself")

        prior = self._merges.find_by_absorbed(
            absorbed_org_id=absorbed_org_id, absorbed_user_id=absorbed_user_id
        )
        for row in prior:
            if row.state == AccountMergeState.COMPLETED:
                if row.survivor_user_id == survivor_user_id:
                    return row  # NFR-8: already merged into this survivor
                raise MergeNotAllowed(
                    "absorbed account was already merged into a different account"
                )
            # An interrupted merge for the same pair resumes; a different
            # survivor mid-merge is refused (the DB partial-unique enforces
            # the same invariant against races).
            if row.survivor_user_id == survivor_user_id:
                return self._run(row, ip=ip, user_agent=user_agent)
            raise MergeNotAllowed("another merge is in progress for this account")

        self._check_personal_org(absorbed_org_id, absorbed_user_id, "absorbed")
        self._check_personal_org(survivor_org_id, survivor_user_id, "survivor")

        record = self._merges.create_merge(
            AccountMergeRecord(
                survivor_org_id=survivor_org_id,
                survivor_user_id=survivor_user_id,
                absorbed_org_id=absorbed_org_id,
                absorbed_user_id=absorbed_user_id,
                proof_ref=proof_ref,
            )
        )
        return self._run(record, ip=ip, user_agent=user_agent)

    def resume(
        self,
        merge_id: str,
        *,
        ip: str | None = None,
        user_agent: str | None = None,
    ) -> AccountMergeRecord:
        """Re-enter an interrupted saga at its next step (NFR-3/8)."""

        record = self._merges.get_merge(merge_id=merge_id)
        if record is None:
            raise MergeNotAllowed(f"unknown merge {merge_id}")
        return self._run(record, ip=ip, user_agent=user_agent)

    # Preconditions -----------------------------------------------------
    def _check_personal_org(self, org_id: str, user_id: str, side: str) -> None:
        user = self._identity.get_user(org_id=org_id, user_id=user_id)
        if user is None:
            raise MergeNotAllowed(f"{side} user does not exist")
        members = [
            m
            for m in self._identity.list_members(org_id=org_id)
            if m.removed_at is None
        ]
        # v1 scope (PRD non-goal): personal orgs only. A shared org is never
        # silently absorbed — other members' data is not the caller's to move.
        if len(members) != 1 or members[0].user_id != user_id:
            raise MergeNotAllowed(f"{side} org is not a single-member personal org")

    # The saga ----------------------------------------------------------
    def _run(
        self,
        record: AccountMergeRecord,
        *,
        ip: str | None,
        user_agent: str | None,
    ) -> AccountMergeRecord:
        try:
            if record.state == AccountMergeState.PENDING:
                counts = self._data.rekey(
                    absorbed_org_id=record.absorbed_org_id,
                    absorbed_user_id=record.absorbed_user_id,
                    survivor_org_id=record.survivor_org_id,
                    survivor_user_id=record.survivor_user_id,
                )
                record = self._merges.update_merge(
                    record.model_copy(
                        update={
                            "state": AccountMergeState.BACKEND_DONE,
                            "counts": {**record.counts, "backend": counts},
                            "error": None,
                        }
                    )
                )

            if record.state == AccountMergeState.BACKEND_DONE:
                runtime_result = self._runtime.merge(
                    merge_id=record.merge_id,
                    absorbed_org_id=record.absorbed_org_id,
                    absorbed_user_id=record.absorbed_user_id,
                    survivor_org_id=record.survivor_org_id,
                    survivor_user_id=record.survivor_user_id,
                )
                record = self._merges.update_merge(
                    record.model_copy(
                        update={
                            "state": AccountMergeState.RUNTIME_DONE,
                            "counts": {**record.counts, "runtime": runtime_result},
                            "error": None,
                        }
                    )
                )

            if record.state == AccountMergeState.RUNTIME_DONE:
                revoked = 0
                for session in self._sessions.list_active(
                    org_id=record.absorbed_org_id,
                    user_id=record.absorbed_user_id,
                ):
                    if self._sessions.revoke(
                        org_id=record.absorbed_org_id,
                        session_id=session.session_id,
                        reason="account_merged",
                    ):
                        revoked += 1
                record = self._merges.update_merge(
                    record.model_copy(
                        update={
                            "state": AccountMergeState.SESSIONS_REVOKED,
                            "counts": {
                                **record.counts,
                                "sessions_revoked": revoked,
                            },
                            "error": None,
                        }
                    )
                )

            if record.state == AccountMergeState.SESSIONS_REVOKED:
                self._data.disable_absorbed_user(
                    absorbed_org_id=record.absorbed_org_id,
                    absorbed_user_id=record.absorbed_user_id,
                    survivor_user_id=record.survivor_user_id,
                )
                self._append_merge_audit(record, ip=ip, user_agent=user_agent)
                record = self._merges.update_merge(
                    record.model_copy(
                        update={
                            "state": AccountMergeState.COMPLETED,
                            "completed_at": _now(),
                            "error": None,
                        }
                    )
                )

            _LOGGER.info(
                "account_merge_completed merge_id=%s absorbed=%s survivor=%s counts=%s",
                record.merge_id,
                record.absorbed_user_id,
                record.survivor_user_id,
                record.counts,
            )
            return record
        except AccountMergeError:
            raise
        except Exception as exc:
            # NFR-3/10: record the failure at its checkpoint; the saga is
            # resumable and nothing is half-owned. Actionable, never silent.
            self._merges.update_merge(record.model_copy(update={"error": str(exc)}))
            _LOGGER.exception(
                "account_merge_failed merge_id=%s state=%s",
                record.merge_id,
                record.state.value,
            )
            raise MergeRuntimeFailed(
                f"merge {record.merge_id} failed at {record.state.value}: {exc}"
            ) from exc

    # Audit -------------------------------------------------------------
    def _append_merge_audit(
        self,
        record: AccountMergeRecord,
        *,
        ip: str | None,
        user_agent: str | None,
    ) -> None:
        """Immutable ``account.merged`` rows on BOTH orgs' trails (FR-M7/NFR-5)."""

        metadata = {
            "merge_id": record.merge_id,
            "absorbed_org_id": record.absorbed_org_id,
            "absorbed_user_id": record.absorbed_user_id,
            "survivor_org_id": record.survivor_org_id,
            "survivor_user_id": record.survivor_user_id,
            "proof_ref": record.proof_ref,
            "counts": record.counts,
        }
        for org_id, actor in (
            (record.survivor_org_id, record.survivor_user_id),
            (record.absorbed_org_id, record.survivor_user_id),
        ):
            self._identity.append_identity_audit(
                IdentityAuditEventRecord(
                    org_id=org_id,
                    actor_user_id=actor,
                    subject_user_id=record.absorbed_user_id,
                    action="account.merged",
                    metadata=metadata,
                    request_ip=ip,
                    user_agent=user_agent,
                )
            )


__all__ = [
    "AccountMergeError",
    "AccountMergeService",
    "HttpRuntimeMergeClient",
    "UnconfiguredRuntimeMergeClient",
    "InMemoryMergeData",
    "MergeDataPort",
    "MergeNotAllowed",
    "MergeRuntimeFailed",
    "NullRuntimeMergeClient",
    "PostgresMergeData",
    "RuntimeMergePort",
]
