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

from fastapi import APIRouter, FastAPI, HTTPException, status
from pydantic import BaseModel, Field

from backend_app.dev_idp._sign import sign_identity_token
from backend_app.dev_idp.personas import (
    DevPersona,
    PersonaDirectory,
    PersonaLoader,
)


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
    def mint_identity(payload: DevMintRequest) -> DevMintResponse:
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


def _default_persona_path() -> Path:
    """Locate ``dev_personas.yaml`` next to the service root.

    The default search walks up from this file two levels (``dev_idp``
    package → ``backend_app`` package → ``src`` → service root).
    """

    here = Path(__file__).resolve()
    candidate = here.parents[3] / "dev_personas.yaml"
    return candidate
