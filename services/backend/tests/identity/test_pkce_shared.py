"""Verify MCP OAuth and OIDC SSO share the same PKCE primitive.

Spec A3 §3.2 ``test_pkce_helper_shared_between_mcp_and_oidc``: the OIDC
state machine and the MCP OAuth flow must derive their code_challenge
through the same module so a future hardening (e.g. wider verifier, FIPS
RNG) lands in exactly one place.
"""

from __future__ import annotations

import hashlib
import inspect
import re

import backend_app.identity._pkce as pkce
import backend_app.identity.oidc as oidc_module
import backend_app.service as mcp_service


def test_compute_challenge_is_rfc7636_s256() -> None:
    verifier = "Z" * 64
    expected = (
        hashlib.sha256(verifier.encode("ascii")).digest().hex()  # sanity
    )
    actual = pkce.compute_challenge(verifier)
    # Decode S256 challenge back to digest hex for the comparison.
    import base64

    digest = base64.urlsafe_b64decode(actual + "=" * (-len(actual) % 4)).hex()
    assert digest == expected


def test_generate_verifier_meets_rfc7636_length_window() -> None:
    # RFC 7636 §4.1: verifier MUST be 43..128 unreserved chars.
    verifier = pkce.generate_verifier()
    assert 43 <= len(verifier) <= 128
    assert re.fullmatch(r"[A-Za-z0-9_\-]+", verifier)


def test_generate_state_and_nonce_are_distinct_per_call() -> None:
    samples = {pkce.generate_state() for _ in range(50)}
    assert len(samples) == 50
    samples = {pkce.generate_nonce() for _ in range(50)}
    assert len(samples) == 50


def test_mcp_service_imports_compute_challenge_from_pkce() -> None:
    """The MCP OAuth path must source ``compute_challenge`` from
    ``backend_app.identity._pkce`` — not re-implement it inline."""

    src = inspect.getsource(mcp_service)
    assert (
        "from backend_app.identity._pkce import compute_challenge, generate_verifier"
        in src
    )
    # And it must NOT re-implement the S256 transformation locally.
    assert "_code_challenge" not in src
    assert "hashlib.sha256(verifier" not in src


def test_oidc_service_imports_from_pkce() -> None:
    """The OIDC service must reach the helpers through the same module."""

    src = inspect.getsource(oidc_module)
    assert "from backend_app.identity._pkce import" in src or (
        "from backend_app.identity import _pkce" in src
    )
