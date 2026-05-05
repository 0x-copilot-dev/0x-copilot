"""A10 — RBAC permission-scope catalog.

Single source of truth for the string scope identifiers carried inside
session bearer tokens (``permission_scopes`` claim) and inside the
``x-enterprise-permission-scopes`` header on internal service-to-service
calls. ``services/backend`` and ``services/ai-backend`` both read this
module so a typo on either side fails at import time, not at the
moment a 403 lands in production.

Adding a scope:

  1. Add the constant here.
  2. Add it to :data:`ALL_SCOPES`.
  3. Annotate the route(s) that require it.
  4. Update the per-route table in
     :doc:`docs/roadmap/29-a10-rbac-enforcement` and the auth specs
     under ``services/backend/docs/specs/auth/``.
"""

from __future__ import annotations

from typing import Final


# -- Runtime / chat ---------------------------------------------------------

RUNTIME_USE: Final = "runtime:use"
"""Use the runtime: open a conversation, post a message, stream events."""


# -- MCP registry -----------------------------------------------------------

MCP_READ: Final = "mcp:read"
"""List MCP servers + read their card metadata."""

MCP_WRITE: Final = "mcp:write"
"""Create / update / delete MCP servers + their OAuth client config."""

CONNECTORS_AUTH: Final = "connectors:auth"
"""Drive an MCP server through its OAuth dance on the user's behalf."""


# -- Skills registry --------------------------------------------------------

SKILLS_READ: Final = "skills:read"
"""List skills available to the caller (system + org + user)."""

SKILLS_WRITE: Final = "skills:write"
"""Create / update / delete user-owned and org-owned skills."""


# -- Admin --------------------------------------------------------------

ADMIN_USERS: Final = "admin:users"
"""Manage users, sessions, lockouts, role assignments. Sensitive."""

ADMIN_IDP: Final = "admin:idp"
"""Manage SAML / OIDC / SCIM provider configurations."""

ADMIN_AUDIT_EXPORT: Final = "admin:audit_export"
"""Configure SIEM export cursor + dead-letter management."""

ADMIN_BUDGETS: Final = "admin:budgets"
"""Mint / rotate / inspect per-org and per-user spend budgets."""

ADMIN_RETENTION: Final = "admin:retention"
"""Manage retention policies and run on-demand sweeps."""

ADMIN_SIEM: Final = "admin:siem"
"""Drive the SIEM-export pump (start / stop / replay)."""


# -- Audit --------------------------------------------------------------

AUDIT_READ: Final = "audit:read"
"""Read identity + runtime audit log entries (no export)."""


# -- Lifecycle markers (NOT real permissions) -------------------------------

MFA_PENDING: Final = "mfa:pending"
"""Internal: present on a session that minted before MFA verify ran.
A route that needs *any* scope refuses an mfa-pending session unless
the route itself explicitly opts in (e.g. POST /v1/auth/mfa/verify)."""


ALL_SCOPES: frozenset[str] = frozenset(
    {
        RUNTIME_USE,
        MCP_READ,
        MCP_WRITE,
        CONNECTORS_AUTH,
        SKILLS_READ,
        SKILLS_WRITE,
        ADMIN_USERS,
        ADMIN_IDP,
        ADMIN_AUDIT_EXPORT,
        ADMIN_BUDGETS,
        ADMIN_RETENTION,
        ADMIN_SIEM,
        AUDIT_READ,
        MFA_PENDING,
    }
)


__all__ = [
    "ADMIN_AUDIT_EXPORT",
    "ADMIN_BUDGETS",
    "ADMIN_IDP",
    "ADMIN_RETENTION",
    "ADMIN_SIEM",
    "ADMIN_USERS",
    "ALL_SCOPES",
    "AUDIT_READ",
    "CONNECTORS_AUTH",
    "MCP_READ",
    "MCP_WRITE",
    "MFA_PENDING",
    "RUNTIME_USE",
    "SKILLS_READ",
    "SKILLS_WRITE",
]
