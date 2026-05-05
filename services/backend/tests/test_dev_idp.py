"""W0.1 — Dev IdP routes, persona loader, mint + list semantics."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from pathlib import Path

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend_app.dev_idp import (
    PersonaDirectory,
    register_dev_idp_routes,
)
from backend_app.dev_idp.personas import PersonaLoader


PERSONA_FIXTURE = Path(__file__).resolve().parents[1] / "dev_personas.yaml"


def _make_app(monkeypatch, *, env: str, persona_path: Path | None = None) -> FastAPI:
    monkeypatch.setenv("BACKEND_ENVIRONMENT", env)
    monkeypatch.setenv("ENTERPRISE_AUTH_SECRET", "dev-only-not-for-prod")
    app = FastAPI()
    register_dev_idp_routes(app, persona_path=persona_path)
    return app


def test_routes_registered_in_development(monkeypatch) -> None:
    app = _make_app(monkeypatch, env="development", persona_path=PERSONA_FIXTURE)
    paths = app.openapi()["paths"]
    assert "/v1/dev/personas" in paths
    assert "/v1/dev/identity/mint" in paths


def test_routes_absent_in_production(monkeypatch) -> None:
    app = _make_app(monkeypatch, env="production", persona_path=PERSONA_FIXTURE)
    paths = app.openapi().get("paths", {})
    dev_paths = [p for p in paths if "/dev/" in p]
    assert dev_paths == [], f"dev routes leaked into production: {dev_paths}"


def test_routes_absent_when_env_unset(monkeypatch) -> None:
    monkeypatch.delenv("BACKEND_ENVIRONMENT", raising=False)
    app = FastAPI()
    register_dev_idp_routes(app, persona_path=PERSONA_FIXTURE)
    paths = app.openapi().get("paths", {})
    assert "/v1/dev/personas" not in paths


def test_personas_endpoint_lists_yaml_personas(monkeypatch) -> None:
    app = _make_app(monkeypatch, env="development", persona_path=PERSONA_FIXTURE)
    client = TestClient(app)
    resp = client.get("/v1/dev/personas")
    assert resp.status_code == 200
    body = resp.json()
    slugs = {p["slug"] for p in body["personas"]}
    assert {"sarah_acme", "marcus_admin", "alex_contoso_admin"}.issubset(slugs)
    # Org slug + role propagation visible to FE switcher
    sarah = next(p for p in body["personas"] if p["slug"] == "sarah_acme")
    assert sarah["org_slug"] == "acme"
    assert sarah["roles"] == ["employee"]


def test_mint_returns_signed_bearer_for_known_persona(monkeypatch) -> None:
    app = _make_app(monkeypatch, env="development", persona_path=PERSONA_FIXTURE)
    client = TestClient(app)
    resp = client.post("/v1/dev/identity/mint", json={"persona_slug": "marcus_admin"})
    assert resp.status_code == 200
    body = resp.json()
    bearer = body["bearer"]
    assert "." in bearer
    payload_part, sig_part = bearer.split(".", maxsplit=1)
    # Re-compute signature with the same secret + scheme the facade uses
    expected = (
        base64.urlsafe_b64encode(
            hmac.new(
                b"dev-only-not-for-prod", payload_part.encode("ascii"), hashlib.sha256
            ).digest()
        )
        .rstrip(b"=")
        .decode("ascii")
    )
    assert sig_part == expected, "signature must match facade verification scheme"
    # Decode payload and check identity claims
    padding = "=" * (-len(payload_part) % 4)
    decoded = json.loads(
        base64.urlsafe_b64decode((payload_part + padding).encode()).decode()
    )
    assert decoded["org_id"] == "org_acme"
    assert decoded["user_id"] == "usr_marcus"
    assert "users:admin" in decoded["permission_scopes"]
    # Response surfaces persona metadata for the FE
    assert body["identity"]["display_name"] == "Marcus Johnson"
    assert body["persona_slug"] == "marcus_admin"


def test_mint_404s_on_unknown_persona(monkeypatch) -> None:
    app = _make_app(monkeypatch, env="development", persona_path=PERSONA_FIXTURE)
    client = TestClient(app)
    resp = client.post("/v1/dev/identity/mint", json={"persona_slug": "nope"})
    assert resp.status_code == 404
    assert "persona_slug not found" in resp.json()["detail"]


def test_mint_503s_when_secret_unset(monkeypatch) -> None:
    monkeypatch.setenv("BACKEND_ENVIRONMENT", "development")
    monkeypatch.delenv("ENTERPRISE_AUTH_SECRET", raising=False)
    app = FastAPI()
    register_dev_idp_routes(app, persona_path=PERSONA_FIXTURE)
    client = TestClient(app)
    resp = client.post("/v1/dev/identity/mint", json={"persona_slug": "sarah_acme"})
    assert resp.status_code == 503


def test_persona_loader_reloads_on_mtime_change(tmp_path: Path) -> None:
    p = tmp_path / "personas.yaml"
    p.write_text(
        yaml.safe_dump(
            {
                "orgs": [{"id": "org_a", "slug": "a", "display_name": "A"}],
                "personas": [
                    {
                        "slug": "alpha",
                        "org_id": "org_a",
                        "user_id": "usr_alpha",
                        "display_name": "Alpha",
                        "primary_email": "alpha@a.test",
                    }
                ],
            }
        )
    )
    loader = PersonaLoader(p)
    d1 = loader.load()
    assert {pp.slug for pp in d1.personas} == {"alpha"}
    # Re-write with an additional persona; bump mtime
    import time

    time.sleep(0.01)
    p.write_text(
        yaml.safe_dump(
            {
                "orgs": [{"id": "org_a", "slug": "a", "display_name": "A"}],
                "personas": [
                    {
                        "slug": "alpha",
                        "org_id": "org_a",
                        "user_id": "usr_alpha",
                        "display_name": "Alpha",
                        "primary_email": "alpha@a.test",
                    },
                    {
                        "slug": "beta",
                        "org_id": "org_a",
                        "user_id": "usr_beta",
                        "display_name": "Beta",
                        "primary_email": "beta@a.test",
                    },
                ],
            }
        )
    )
    os.utime(p, (p.stat().st_atime + 1, p.stat().st_mtime + 1))
    d2 = loader.load()
    assert {pp.slug for pp in d2.personas} == {"alpha", "beta"}


def test_persona_directory_rejects_unknown_org_id() -> None:
    with pytest.raises(Exception):
        PersonaDirectory.model_validate(
            {
                "orgs": [{"id": "org_a", "slug": "a", "display_name": "A"}],
                "personas": [
                    {
                        "slug": "x",
                        "org_id": "org_missing",
                        "user_id": "u",
                        "display_name": "X",
                        "primary_email": "x@x.test",
                    }
                ],
            }
        )


def test_persona_directory_rejects_duplicate_slug() -> None:
    with pytest.raises(Exception):
        PersonaDirectory.model_validate(
            {
                "orgs": [{"id": "org_a", "slug": "a", "display_name": "A"}],
                "personas": [
                    {
                        "slug": "x",
                        "org_id": "org_a",
                        "user_id": "u1",
                        "display_name": "X1",
                        "primary_email": "x1@a.test",
                    },
                    {
                        "slug": "x",
                        "org_id": "org_a",
                        "user_id": "u2",
                        "display_name": "X2",
                        "primary_email": "x2@a.test",
                    },
                ],
            }
        )
