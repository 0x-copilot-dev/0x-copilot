"""Dev IdP HTTP routes — env-gated, never registered in production.

Two endpoints:

- ``GET  /v1/dev/personas``        — list available personas (FE switcher).
- ``POST /v1/dev/identity/mint``   — mint an HMAC bearer for the named persona.

The mint endpoint reuses the same signing scheme the facade verifies, so the
dev path exercises the production verification code unchanged. Production
images do not register these routes — ``register_dev_idp_routes`` is a no-op
unless ``BACKEND_ENVIRONMENT=development``.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, FastAPI, HTTPException, Request, status
from pydantic import BaseModel, Field

from backend_app.contracts import OrganizationRecord, UserRecord
from backend_app.dev_idp._sign import sign_identity_token
from backend_app.dev_idp.personas import (
    DevOrg,
    DevPersona,
    PersonaDirectory,
    PersonaLoader,
)
from backend_app.identity.store import IdentityStore


_DEV_BEARER_TTL = timedelta(days=365)
"""Dev bearers don't expire in any practical sense. Verification still
checks the HMAC; the long TTL just keeps the FE from re-minting on every
page load. Routes don't exist in prod, so the long TTL is harmless."""


class DevPersonaSummary(BaseModel):
    slug: str
    display_name: str
    primary_email: str
    org_id: str
    org_slug: str
    roles: tuple[str, ...]
    permission_scopes: tuple[str, ...]


class DevPersonaListResponse(BaseModel):
    personas: tuple[DevPersonaSummary, ...]


class DevMintRequest(BaseModel):
    persona_slug: str = Field(min_length=1, max_length=128)


class DevMintIdentity(BaseModel):
    org_id: str
    user_id: str
    display_name: str
    primary_email: str
    roles: tuple[str, ...]
    permission_scopes: tuple[str, ...]


class DevMintResponse(BaseModel):
    bearer: str
    expires_at: datetime
    persona_slug: str
    identity: DevMintIdentity


def _is_development() -> bool:
    return os.environ.get("BACKEND_ENVIRONMENT", "").strip().lower() == "development"


def _summarise(directory: PersonaDirectory, persona: DevPersona) -> DevPersonaSummary:
    org = directory.org_by_id(persona.org_id)
    return DevPersonaSummary(
        slug=persona.slug,
        display_name=persona.display_name,
        primary_email=persona.primary_email,
        org_id=org.id,
        org_slug=org.slug,
        roles=persona.roles,
        permission_scopes=persona.permission_scopes,
    )


def _build_router(loader: PersonaLoader) -> APIRouter:
    router = APIRouter(prefix="/v1/dev", tags=["dev-idp"])

    @router.get("/personas", response_model=DevPersonaListResponse)
    def list_personas() -> DevPersonaListResponse:
        directory = loader.load()
        return DevPersonaListResponse(
            personas=tuple(_summarise(directory, p) for p in directory.personas)
        )

    @router.post("/identity/mint", response_model=DevMintResponse)
    def mint_identity(payload: DevMintRequest, request: Request) -> DevMintResponse:
        directory = loader.load()
        try:
            persona = directory.persona(payload.persona_slug)
        except KeyError as exc:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                f"persona_slug not found: {payload.persona_slug}",
            ) from exc
        secret = os.environ.get("ENTERPRISE_AUTH_SECRET", "").strip()
        if not secret:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "ENTERPRISE_AUTH_SECRET is not configured",
            )
        # First-mint side-effect: ensure the org and user records the
        # bearer references actually exist in the identity store. Without
        # this, every persona authenticates successfully but anything that
        # reads from identity_store (profile, preferences, member chips,
        # …) 404s with "user_not_found". Idempotent — a re-mint is a
        # no-op once the records exist. Dev-only path; the route itself is
        # only mounted when BACKEND_ENVIRONMENT=development.
        identity_store: IdentityStore | None = getattr(
            request.app.state, "identity_store", None
        )
        if identity_store is not None:
            org = directory.org_by_id(persona.org_id)
            _ensure_persona_seeded(identity_store, org=org, persona=persona)
        now = datetime.now(timezone.utc)
        expires_at = now + _DEV_BEARER_TTL
        token_payload = {
            "org_id": persona.org_id,
            "user_id": persona.user_id,
            "roles": list(persona.roles),
            "permission_scopes": list(persona.permission_scopes),
            "connector_scopes": {},
            "iat": int(now.timestamp()),
            "exp": int(expires_at.timestamp()),
        }
        bearer = sign_identity_token(token_payload, secret)
        return DevMintResponse(
            bearer=bearer,
            expires_at=expires_at,
            persona_slug=persona.slug,
            identity=DevMintIdentity(
                org_id=persona.org_id,
                user_id=persona.user_id,
                display_name=persona.display_name,
                primary_email=persona.primary_email,
                roles=persona.roles,
                permission_scopes=persona.permission_scopes,
            ),
        )

    return router


def register_dev_idp_routes(
    app: FastAPI,
    *,
    persona_path: Path | None = None,
) -> bool:
    """Mount ``/v1/dev/*`` iff ``BACKEND_ENVIRONMENT=development``.

    Returns ``True`` when routes were registered, ``False`` otherwise. The
    return value is for tests and operability — production images get
    ``False`` and the routes do not exist in the OpenAPI surface.
    """

    if not _is_development():
        return False
    path = persona_path or _default_persona_path()
    if not path.exists():
        # Fail loud in dev so a missing fixture isn't silent.
        raise FileNotFoundError(f"dev persona fixture missing: {path}")
    loader = PersonaLoader(path)
    app.state.dev_persona_loader = loader
    app.include_router(_build_router(loader))
    return True


def _ensure_persona_seeded(
    identity_store: IdentityStore,
    *,
    org: DevOrg,
    persona: DevPersona,
) -> None:
    """Idempotently insert the org + user the persona refers to.

    The dev IdP signs bearers from a YAML fixture; the rest of the
    backend assumes the corresponding identity rows exist. Without this
    seed the bearer is valid but every read against identity_store 404s.
    """

    if identity_store.get_organization(org_id=org.id) is None:
        try:
            identity_store.create_organization(
                OrganizationRecord(
                    org_id=org.id,
                    display_name=org.display_name,
                    slug=org.slug,
                )
            )
        except ValueError:
            # Lost a race against a concurrent mint of the same persona;
            # the row exists now, that's all we needed.
            pass
    if identity_store.get_user(org_id=persona.org_id, user_id=persona.user_id) is None:
        try:
            identity_store.create_user(
                UserRecord(
                    user_id=persona.user_id,
                    org_id=persona.org_id,
                    primary_email=persona.primary_email,
                    display_name=persona.display_name,
                )
            )
        except ValueError:
            # Either a race against a concurrent mint, or the persona
            # YAML re-used an email already taken by another user in the
            # org. Idempotency is the goal; tolerate both.
            pass


def _default_persona_path() -> Path:
    """Locate ``dev_personas.yaml`` next to the service root.

    The default search walks up from this file two levels (``dev_idp``
    package → ``backend_app`` package → ``src`` → service root).
    """

    here = Path(__file__).resolve()
    candidate = here.parents[3] / "dev_personas.yaml"
    return candidate
