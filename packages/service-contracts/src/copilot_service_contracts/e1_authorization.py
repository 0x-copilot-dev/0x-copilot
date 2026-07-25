"""Closed inventory for E1-sensitive product routes.

The inventory is deliberately a constants-only cross-service contract.  The
runtime API tests use it to prove strict identity and resource opacity, while
the facade tests use the same entries to prove that every product-facing path
is forwarded through the facade.  It is not an authorization mechanism by
itself: each owner service remains responsible for enforcing the policy.

Adding a path in one of these families without adding a row here makes the
inventory guards fail.  That turns the E1 requirement from a best-effort test
checklist into an additive contract.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class E1SensitiveRoute:
    """One authenticated E1 route and its public facade counterpart."""

    route_id: str
    method: str
    runtime_path: str
    facade_path: str
    family: str
    identity_class: str
    parent_scope: str
    foreign_status: int | None


def _route(
    route_id: str,
    method: str,
    path: str,
    *,
    family: str,
    identity_class: str,
    parent_scope: str,
    foreign_status: int | None,
) -> E1SensitiveRoute:
    return E1SensitiveRoute(
        route_id=route_id,
        method=method,
        runtime_path=path,
        facade_path=path,
        family=family,
        identity_class=identity_class,
        parent_scope=parent_scope,
        foreign_status=foreign_status,
    )


# Keep this tuple in API-registration order where that matters.  It covers the
# compatibility Source feed and D4/D5's canonical artifact-only source opener.
E1_SENSITIVE_ROUTES: tuple[E1SensitiveRoute, ...] = (
    _route(
        "artifact.promote",
        "POST",
        "/v1/agent/artifacts:promote",
        family="artifact",
        identity_class="member",
        parent_scope="opaque_source",
        foreign_status=404,
    ),
    _route(
        "artifact.list_run",
        "GET",
        "/v1/agent/runs/{run_id}/artifacts",
        family="artifact",
        identity_class="member",
        parent_scope="run",
        foreign_status=404,
    ),
    _route(
        "artifact.create_run",
        "POST",
        "/v1/agent/runs/{run_id}/artifacts",
        family="artifact",
        identity_class="member",
        parent_scope="run",
        foreign_status=404,
    ),
    _route(
        "artifact.content_download",
        "GET",
        "/v1/agent/artifacts/{artifact_id}/revisions/{revision}/content",
        family="artifact",
        identity_class="member",
        parent_scope="artifact",
        foreign_status=404,
    ),
    _route(
        "artifact.get_revision",
        "GET",
        "/v1/agent/artifacts/{artifact_id}/revisions/{revision}",
        family="artifact",
        identity_class="member",
        parent_scope="artifact",
        foreign_status=404,
    ),
    _route(
        "artifact.append_revision",
        "POST",
        "/v1/agent/artifacts/{artifact_id}/revisions",
        family="artifact",
        identity_class="member",
        parent_scope="artifact",
        foreign_status=404,
    ),
    _route(
        "artifact.get",
        "GET",
        "/v1/agent/artifacts/{artifact_id}",
        family="artifact",
        identity_class="member",
        parent_scope="artifact",
        foreign_status=404,
    ),
    _route(
        "artifact.delete",
        "DELETE",
        "/v1/agent/artifacts/{artifact_id}",
        family="artifact",
        identity_class="member",
        parent_scope="artifact",
        foreign_status=404,
    ),
    _route(
        "stage.get",
        "GET",
        "/v1/agent/stages/{stage_id}",
        family="stage",
        identity_class="member",
        parent_scope="run_stage",
        foreign_status=404,
    ),
    _route(
        "stage.revision",
        "POST",
        "/v1/agent/stages/{stage_id}/revisions",
        family="stage",
        identity_class="member",
        parent_scope="run_stage",
        foreign_status=404,
    ),
    _route(
        "stage.decision",
        "POST",
        "/v1/agent/stages/{stage_id}/decisions",
        family="stage",
        identity_class="member",
        parent_scope="run_stage",
        foreign_status=404,
    ),
    _route(
        "stage.apply",
        "POST",
        "/v1/agent/stages/{stage_id}/apply",
        family="stage",
        identity_class="member",
        parent_scope="run_stage",
        foreign_status=404,
    ),
    _route(
        "effect.workspace_decision",
        "POST",
        "/v1/agent/effect-stages/{stage_id}/decisions",
        family="effect",
        identity_class="member",
        parent_scope="run_stage",
        foreign_status=404,
    ),
    _route(
        "receipt.export_v1",
        "GET",
        "/v1/agent/runs/{run_id}/receipt/export",
        family="receipt",
        identity_class="member",
        parent_scope="run",
        foreign_status=404,
    ),
    _route(
        "receipt.export_v2",
        "GET",
        "/v1/agent/runs/{run_id}/receipt/export-v2",
        family="receipt",
        identity_class="member",
        parent_scope="run",
        foreign_status=404,
    ),
    _route(
        "usage.me",
        "GET",
        "/v1/usage/me",
        family="usage",
        identity_class="member",
        parent_scope="self",
        foreign_status=None,
    ),
    _route(
        "usage.me_conversations",
        "GET",
        "/v1/usage/me/conversations",
        family="usage",
        identity_class="member",
        parent_scope="self",
        foreign_status=None,
    ),
    _route(
        "usage.run",
        "GET",
        "/v1/usage/runs/{run_id}",
        family="usage",
        identity_class="member",
        parent_scope="run",
        foreign_status=404,
    ),
    _route(
        "usage.run_calls",
        "GET",
        "/v1/usage/runs/{run_id}/calls",
        family="usage",
        identity_class="member",
        parent_scope="run",
        foreign_status=404,
    ),
    _route(
        "usage.conversation",
        "GET",
        "/v1/usage/conversations/{conversation_id}",
        family="usage",
        identity_class="member",
        parent_scope="conversation",
        foreign_status=404,
    ),
    _route(
        "usage.org",
        "GET",
        "/v1/usage/org",
        family="usage",
        identity_class="audit_or_admin",
        parent_scope="tenant",
        foreign_status=None,
    ),
    _route(
        "usage.org_subagents",
        "GET",
        "/v1/usage/org/subagents",
        family="usage",
        identity_class="audit_or_admin",
        parent_scope="tenant",
        foreign_status=None,
    ),
    _route(
        "usage.org_purpose",
        "GET",
        "/v1/usage/org/purpose",
        family="usage",
        identity_class="audit_or_admin",
        parent_scope="tenant",
        foreign_status=None,
    ),
    _route(
        "source_open",
        "POST",
        "/v1/agent/runs/{run_id}/sources/{source_id}/open",
        family="source",
        identity_class="member",
        parent_scope="artifact_revision",
        foreign_status=404,
    ),
    _route(
        "sources.compatibility_list",
        "GET",
        "/v1/agent/conversations/{conversation_id}/sources",
        family="source",
        identity_class="member",
        parent_scope="conversation",
        foreign_status=404,
    ),
    _route(
        "pending.compatibility",
        "GET",
        "/v1/agent/pending-work",
        family="pending",
        identity_class="member",
        parent_scope="self",
        foreign_status=None,
    ),
    _route(
        "pending.canonical",
        "GET",
        "/v1/agent/pending-work-v2",
        family="pending",
        identity_class="member",
        parent_scope="self",
        foreign_status=None,
    ),
    _route(
        "legal_hold.list",
        "GET",
        "/v1/retention/legal-holds",
        family="legal_hold",
        identity_class="retention_admin",
        parent_scope="tenant",
        foreign_status=None,
    ),
    _route(
        "legal_hold.create",
        "POST",
        "/v1/retention/legal-holds",
        family="legal_hold",
        identity_class="retention_admin",
        parent_scope="target",
        foreign_status=404,
    ),
    _route(
        "legal_hold.release",
        "POST",
        "/v1/retention/legal-holds/{hold_id}/release",
        family="legal_hold",
        identity_class="retention_admin",
        parent_scope="hold",
        foreign_status=404,
    ),
)

# A deliberate review checkpoint: adding a sensitive public route must update
# this canonical inventory and its independent runtime/facade registration
# tests, rather than silently expanding one side of the boundary.
E1_SENSITIVE_ROUTE_COUNT = 30

E1_SENSITIVE_ROUTE_IDS = frozenset(route.route_id for route in E1_SENSITIVE_ROUTES)
E1_SENSITIVE_ROUTE_KEYS = frozenset(
    (route.method, route.runtime_path) for route in E1_SENSITIVE_ROUTES
)

if len(E1_SENSITIVE_ROUTE_IDS) != len(E1_SENSITIVE_ROUTES):  # pragma: no cover
    raise RuntimeError("E1 authorization route ids must be unique")
if len(E1_SENSITIVE_ROUTE_KEYS) != len(E1_SENSITIVE_ROUTES):  # pragma: no cover
    raise RuntimeError("E1 authorization method/path pairs must be unique")
if len(E1_SENSITIVE_ROUTES) != E1_SENSITIVE_ROUTE_COUNT:  # pragma: no cover
    raise RuntimeError("E1 authorization route count must match the reviewed inventory")


def is_e1_sensitive_path(path: str) -> bool:
    """Return whether a registered path belongs to the E1 guard surface.

    This intentionally uses public route-shape families rather than the
    inventory itself: a newly registered route in an existing E1 family is
    therefore reported as *uncovered* instead of being silently excluded.
    """

    if path == "/v1/agent/artifacts:promote":
        return True
    if path.startswith("/v1/agent/artifacts/"):
        return True
    if path == "/v1/agent/runs/{run_id}/artifacts":
        return True
    if path.startswith("/v1/agent/stages/"):
        return True
    if path.startswith("/v1/agent/effect-stages/"):
        return True
    if path.startswith("/v1/agent/runs/{run_id}/receipt/"):
        return True
    if path.startswith("/v1/agent/runs/{run_id}/sources/"):
        return True
    if path == "/v1/agent/conversations/{conversation_id}/sources":
        return True
    if path in {"/v1/agent/pending-work", "/v1/agent/pending-work-v2"}:
        return True
    if path.startswith("/v1/retention/legal-holds"):
        return True
    if path.startswith("/v1/usage/") and not path.startswith("/v1/usage/org/agent/"):
        return True
    return False


__all__ = (
    "E1SensitiveRoute",
    "E1_SENSITIVE_ROUTE_COUNT",
    "E1_SENSITIVE_ROUTES",
    "E1_SENSITIVE_ROUTE_IDS",
    "E1_SENSITIVE_ROUTE_KEYS",
    "is_e1_sensitive_path",
)
