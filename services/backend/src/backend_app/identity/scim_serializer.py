"""SCIM 2.0 resource serialization (A7).

Translates internal records (UserRecord + ScimGroupRecord) into the
canonical SCIM resource shapes from RFC 7643. Kept tiny — anything more
involved than the User / Group / ListResponse / Error shapes belongs in
its own module.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

from backend_app.contracts import (
    ScimGroupMemberRecord,
    ScimGroupRecord,
    UserRecord,
)


_USER_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:User"
_GROUP_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:Group"
_LIST_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:ListResponse"
_ERROR_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:Error"


def user_to_scim(
    user: UserRecord,
    *,
    base_url: str,
    external_id: str | None = None,
    groups: Sequence[ScimGroupRecord] = (),
) -> dict[str, Any]:
    """Project a :class:`UserRecord` into a SCIM ``User`` resource.

    ``base_url`` is the SCIM API root the IdP is calling (so resource
    references can be absolute).
    """

    resource: dict[str, Any] = {
        "schemas": [_USER_SCHEMA],
        "id": user.user_id,
        "userName": user.primary_email,
        "displayName": user.display_name,
        "emails": [
            {
                "value": user.primary_email,
                "primary": True,
            }
        ],
        "active": user.deleted_at is None and user.status.value == "active",
        "meta": {
            "resourceType": "User",
            "created": user.created_at.isoformat(),
            "lastModified": user.updated_at.isoformat(),
            "location": f"{base_url.rstrip('/')}/Users/{user.user_id}",
        },
    }
    if external_id:
        resource["externalId"] = external_id
    if groups:
        resource["groups"] = [
            {
                "value": group.group_id,
                "display": group.display_name,
                "$ref": f"{base_url.rstrip('/')}/Groups/{group.group_id}",
            }
            for group in groups
        ]
    return resource


def group_to_scim(
    group: ScimGroupRecord,
    *,
    base_url: str,
    members: Sequence[ScimGroupMemberRecord] = (),
    member_users: dict[str, UserRecord] | None = None,
) -> dict[str, Any]:
    """Project a :class:`ScimGroupRecord` into a SCIM ``Group`` resource.

    When ``member_users`` is supplied, member ``display`` values are
    populated from the local user records — the IdP would otherwise need
    a second round-trip to render group memberships.
    """

    resource: dict[str, Any] = {
        "schemas": [_GROUP_SCHEMA],
        "id": group.group_id,
        "displayName": group.display_name,
        "meta": {
            "resourceType": "Group",
            "created": group.created_at.isoformat(),
            "lastModified": group.updated_at.isoformat(),
            "location": f"{base_url.rstrip('/')}/Groups/{group.group_id}",
        },
        "members": [
            _member_dict(
                member=member,
                base_url=base_url,
                member_users=member_users or {},
            )
            for member in members
        ],
    }
    if group.external_id:
        resource["externalId"] = group.external_id
    return resource


def list_response(
    resources: Iterable[dict[str, Any]],
    *,
    total_results: int,
    start_index: int = 1,
    items_per_page: int | None = None,
) -> dict[str, Any]:
    materialized = list(resources)
    return {
        "schemas": [_LIST_SCHEMA],
        "totalResults": total_results,
        "startIndex": start_index,
        "itemsPerPage": items_per_page
        if items_per_page is not None
        else len(materialized),
        "Resources": materialized,
    }


def error_response(
    *,
    detail: str,
    status_code: int,
    scim_type: str | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "schemas": [_ERROR_SCHEMA],
        "status": str(status_code),
        "detail": detail,
    }
    if scim_type:
        body["scimType"] = scim_type
    return body


def service_provider_config(*, base_url: str) -> dict[str, Any]:
    return {
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:ServiceProviderConfig"],
        "documentationUri": ("https://datatracker.ietf.org/doc/html/rfc7644"),
        "patch": {"supported": True},
        "bulk": {"supported": False, "maxOperations": 0, "maxPayloadSize": 0},
        "filter": {"supported": True, "maxResults": 200},
        "changePassword": {"supported": False},
        "sort": {"supported": False},
        "etag": {"supported": False},
        "authenticationSchemes": [
            {
                "name": "OAuth Bearer Token",
                "description": "Per-org SCIM bearer token, minted via admin API",
                "specUri": "https://datatracker.ietf.org/doc/html/rfc6750",
                "type": "oauthbearertoken",
            }
        ],
        "meta": {
            "location": f"{base_url.rstrip('/')}/ServiceProviderConfig",
            "resourceType": "ServiceProviderConfig",
        },
    }


def schemas_listing() -> dict[str, Any]:
    return list_response(
        [
            {
                "id": _USER_SCHEMA,
                "name": "User",
                "description": "SCIM User resource",
            },
            {
                "id": _GROUP_SCHEMA,
                "name": "Group",
                "description": "SCIM Group resource",
            },
        ],
        total_results=2,
    )


def resource_types_listing(*, base_url: str) -> dict[str, Any]:
    return list_response(
        [
            {
                "schemas": ["urn:ietf:params:scim:schemas:core:2.0:ResourceType"],
                "id": "User",
                "name": "User",
                "endpoint": "/Users",
                "schema": _USER_SCHEMA,
                "meta": {
                    "location": f"{base_url.rstrip('/')}/ResourceTypes/User",
                    "resourceType": "ResourceType",
                },
            },
            {
                "schemas": ["urn:ietf:params:scim:schemas:core:2.0:ResourceType"],
                "id": "Group",
                "name": "Group",
                "endpoint": "/Groups",
                "schema": _GROUP_SCHEMA,
                "meta": {
                    "location": f"{base_url.rstrip('/')}/ResourceTypes/Group",
                    "resourceType": "ResourceType",
                },
            },
        ],
        total_results=2,
    )


def _member_dict(
    *,
    member: ScimGroupMemberRecord,
    base_url: str,
    member_users: dict[str, UserRecord],
) -> dict[str, Any]:
    user = member_users.get(member.user_id)
    return {
        "value": member.user_id,
        "display": user.display_name if user else member.user_id,
        "$ref": f"{base_url.rstrip('/')}/Users/{member.user_id}",
        "type": "User",
    }


__all__ = [
    "error_response",
    "group_to_scim",
    "list_response",
    "resource_types_listing",
    "schemas_listing",
    "service_provider_config",
    "user_to_scim",
]
