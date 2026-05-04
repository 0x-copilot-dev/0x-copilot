"""SAML verification protocol + production wrapper around ``python3-saml``.

Why this abstraction exists:

- ``python3-saml`` is the battle-tested SAML 2.0 library, but installing
  it requires the ``xmlsec1`` system package. That makes it awkward as a
  hard dep of the ``services/backend`` test suite — most CI machines get
  it, most fresh dev boxes don't.
- The :class:`SamlVerifier` Protocol below isolates the *parsing &
  validation* contract. The :class:`SamlService` (saml.py) takes a
  ``SamlVerifier`` in its ctor; production wires :class:`OneLoginSamlVerifier`,
  unit tests wire :class:`FakeSamlVerifier`. The integration test that
  exercises real signed XML uses ``pytest.importorskip("onelogin.saml2")``.

The Protocol surface is intentionally minimal: build SP metadata, build an
authn request, parse + validate a response. Everything specific to
``python3-saml``'s ``OneLogin_Saml2_*`` API lives in
:class:`OneLoginSamlVerifier` and never leaks into the service.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol


# ---------------------------------------------------------------------------
# Errors — the service catches these to map to HTTP status codes.
# ---------------------------------------------------------------------------


class SamlVerifierError(RuntimeError):
    """Base for SAML parsing / validation failures."""


class SamlSignatureError(SamlVerifierError):
    """Assertion signature didn't validate against the IdP cert."""


class SamlAssertionExpired(SamlVerifierError):
    """``NotBefore`` / ``NotOnOrAfter`` window did not include the current time."""


class SamlAudienceMismatch(SamlVerifierError):
    """``AudienceRestriction`` did not match the configured ``sp_entity_id``."""


class SamlInResponseToMismatch(SamlVerifierError):
    """SP-initiated assertion's ``InResponseTo`` did not match a pending request."""


class SamlMissingAssertion(SamlVerifierError):
    """Response contained no usable assertion (decrypt-required, malformed, etc)."""


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SamlProviderConfig:
    """Resolved SAML config for one ``auth_providers`` row.

    Built by :func:`SamlProviderConfig.from_provider`. The verifier never
    sees the raw ``AuthProviderRecord``; it only needs these fields.
    """

    provider_id: str
    idp_entity_id: str
    idp_sso_url: str
    idp_x509_cert: str
    sp_entity_id: str
    sp_acs_url: str
    attribute_map: Mapping[str, str]
    allow_idp_initiated: bool
    auto_provision_user: bool
    group_role_map: Mapping[str, str]
    sp_signing_key_ref: str | None = None
    sp_decryption_key_ref: str | None = None


@dataclass(frozen=True)
class ParsedSamlAssertion:
    """A validated SAML assertion projected into the fields the service uses.

    Anything not in this dataclass is intentionally dropped — we don't want
    the service to grow code paths reading raw IdP XML.
    """

    name_id: str
    name_id_format: str
    assertion_id: str
    in_response_to: str | None
    issuer: str
    attributes: Mapping[str, list[str]] = field(default_factory=dict)


@dataclass(frozen=True)
class BuiltAuthnRequest:
    """SP-initiated authn request built by the verifier.

    ``redirect_url`` is the HTTP-Redirect binding URL the browser should be
    sent to; ``request_xml`` is the canonical AuthnRequest (post-binding).
    Either is sufficient — the service hands both back to the facade for
    rendering.
    """

    request_id: str
    redirect_url: str
    request_xml: str


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


class SamlVerifier(Protocol):
    """Library-independent verification surface for SAML."""

    def build_metadata(self, *, provider: SamlProviderConfig) -> str:
        """Produce SP metadata XML for the IdP admin to consume."""

    def build_authn_request(
        self,
        *,
        provider: SamlProviderConfig,
        relay_state: str | None,
    ) -> BuiltAuthnRequest:
        """Build an SP-initiated AuthnRequest. Raises ``SamlVerifierError``
        on misconfiguration."""

    def parse_response(
        self,
        *,
        provider: SamlProviderConfig,
        saml_response_b64: str,
        expected_in_response_to: str | None,
    ) -> ParsedSamlAssertion:
        """Validate signature, time bounds, audience, and (when supplied)
        InResponseTo. Returns the projected assertion. Raises a subclass of
        :class:`SamlVerifierError` on any validation failure."""


# ---------------------------------------------------------------------------
# Production wrapper around python3-saml
# ---------------------------------------------------------------------------


class OneLoginSamlVerifier:
    """Wraps OneLogin's ``OneLogin_Saml2_*`` API.

    The ``python3-saml`` import is lazy so this module can be imported on a
    machine without ``xmlsec1`` (the production wiring will only construct
    the OneLogin verifier when SAML is actually enabled).
    """

    def __init__(self) -> None:
        self._lib = self._import_lib()

    @staticmethod
    def _import_lib() -> Any:
        try:
            import onelogin.saml2  # noqa: F401  (probe import)
            from onelogin.saml2.auth import OneLogin_Saml2_Auth
            from onelogin.saml2.idp_metadata_parser import (
                OneLogin_Saml2_IdPMetadataParser,
            )
            from onelogin.saml2.settings import OneLogin_Saml2_Settings
            from onelogin.saml2.utils import OneLogin_Saml2_Utils
            from onelogin.saml2.xml_utils import OneLogin_Saml2_XML
        except ImportError as exc:
            raise SamlVerifierError(
                "python3-saml is required for SAML SSO; install via 'pip install "
                "python3-saml' (also requires the xmlsec1 system library)"
            ) from exc
        return {
            "Auth": OneLogin_Saml2_Auth,
            "IdPMetadataParser": OneLogin_Saml2_IdPMetadataParser,
            "Settings": OneLogin_Saml2_Settings,
            "Utils": OneLogin_Saml2_Utils,
            "XML": OneLogin_Saml2_XML,
        }

    def _settings(self, provider: SamlProviderConfig) -> dict[str, Any]:
        # OneLogin's settings dict — minimal. ``python3-saml`` validates the
        # rest server-side.
        return {
            "strict": True,
            "debug": False,
            "sp": {
                "entityId": provider.sp_entity_id,
                "assertionConsumerService": {
                    "url": provider.sp_acs_url,
                    "binding": ("urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST"),
                },
                "NameIDFormat": (
                    "urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress"
                ),
                "x509cert": "",
                "privateKey": "",
            },
            "idp": {
                "entityId": provider.idp_entity_id,
                "singleSignOnService": {
                    "url": provider.idp_sso_url,
                    "binding": ("urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect"),
                },
                "x509cert": _normalize_pem(provider.idp_x509_cert),
            },
        }

    def build_metadata(self, *, provider: SamlProviderConfig) -> str:
        Settings = self._lib["Settings"]
        settings = Settings(self._settings(provider), sp_validation_only=True)
        metadata = settings.get_sp_metadata()
        if isinstance(metadata, bytes):
            metadata = metadata.decode("utf-8")
        return metadata

    def build_authn_request(
        self,
        *,
        provider: SamlProviderConfig,
        relay_state: str | None,
    ) -> BuiltAuthnRequest:
        Auth = self._lib["Auth"]
        request_data = {
            "https": "on",
            "http_host": _host_from_url(provider.sp_acs_url),
            "script_name": "/v1/auth/saml/{provider_id}/start",
            "get_data": {},
            "post_data": {},
        }
        auth = Auth(request_data, self._settings(provider))
        login_url = auth.login(
            return_to=relay_state,
            force_authn=False,
            is_passive=False,
            set_nameid_policy=True,
        )
        request_id = auth.get_last_request_id()
        return BuiltAuthnRequest(
            request_id=request_id,
            redirect_url=login_url,
            request_xml=auth.get_last_request_xml() or "",
        )

    def parse_response(
        self,
        *,
        provider: SamlProviderConfig,
        saml_response_b64: str,
        expected_in_response_to: str | None,
    ) -> ParsedSamlAssertion:
        Auth = self._lib["Auth"]
        request_data = {
            "https": "on",
            "http_host": _host_from_url(provider.sp_acs_url),
            "script_name": "/v1/auth/saml/{provider_id}/acs",
            "get_data": {},
            "post_data": {"SAMLResponse": saml_response_b64},
        }
        auth = Auth(request_data, self._settings(provider))
        try:
            auth.process_response(request_id=expected_in_response_to)
        except Exception as exc:
            raise SamlVerifierError(f"SAML response parse failed: {exc}") from exc

        errors = auth.get_errors() or []
        if errors:
            reason = auth.get_last_error_reason() or ", ".join(errors)
            classified = _classify_onelogin_error(reason)
            raise classified(reason)
        if not auth.is_authenticated():
            raise SamlSignatureError("SAML response did not authenticate user")

        attributes_raw = auth.get_attributes() or {}
        attributes: dict[str, list[str]] = {
            str(key): [str(v) for v in values] for key, values in attributes_raw.items()
        }
        return ParsedSamlAssertion(
            name_id=str(auth.get_nameid() or ""),
            name_id_format=str(auth.get_nameid_format() or ""),
            assertion_id=str(auth.get_last_assertion_id() or ""),
            in_response_to=str(auth.get_last_message_id() or "") or None,
            issuer=str(provider.idp_entity_id),
            attributes=attributes,
        )


# ---------------------------------------------------------------------------
# Test double — pure Python, no xmlsec dependency.
# ---------------------------------------------------------------------------


@dataclass
class FakeSamlVerifier:
    """In-memory verifier for unit tests.

    Configure ``next_assertion`` to return a successful parse, or set
    ``next_error`` to a ``SamlVerifierError`` subclass to simulate the
    matching trust-model failure mode.
    """

    next_assertion: ParsedSamlAssertion | None = None
    next_error: SamlVerifierError | None = None
    last_request_id: str = "fake-req-1"

    def build_metadata(self, *, provider: SamlProviderConfig) -> str:
        return (
            f'<?xml version="1.0"?>'
            f'<EntityDescriptor entityID="{provider.sp_entity_id}">'
            f"<SPSSODescriptor>"
            f'<AssertionConsumerService Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST" '
            f'Location="{provider.sp_acs_url}" />'
            f"</SPSSODescriptor></EntityDescriptor>"
        )

    def build_authn_request(
        self,
        *,
        provider: SamlProviderConfig,
        relay_state: str | None,
    ) -> BuiltAuthnRequest:
        del relay_state
        return BuiltAuthnRequest(
            request_id=self.last_request_id,
            redirect_url=f"{provider.idp_sso_url}?SAMLRequest=test",
            request_xml='<samlp:AuthnRequest ID="fake-req-1"/>',
        )

    def parse_response(
        self,
        *,
        provider: SamlProviderConfig,
        saml_response_b64: str,
        expected_in_response_to: str | None,
    ) -> ParsedSamlAssertion:
        del provider, saml_response_b64, expected_in_response_to
        if self.next_error is not None:
            error = self.next_error
            self.next_error = None
            raise error
        if self.next_assertion is None:
            raise SamlMissingAssertion("FakeSamlVerifier has no assertion staged")
        result = self.next_assertion
        self.next_assertion = None
        return result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _classify_onelogin_error(reason: str) -> type[SamlVerifierError]:
    text = reason.lower()
    if "signature" in text or "validation" in text:
        return SamlSignatureError
    if "expired" in text or "notonorafter" in text or "notbefore" in text:
        return SamlAssertionExpired
    if "audience" in text:
        return SamlAudienceMismatch
    if "inresponseto" in text:
        return SamlInResponseToMismatch
    return SamlVerifierError


def _normalize_pem(pem: str) -> str:
    """Strip PEM headers if present — OneLogin wants the raw base64 body."""
    body = pem.strip()
    if body.startswith("-----BEGIN"):
        lines = [
            line for line in body.splitlines() if line and not line.startswith("-----")
        ]
        return "".join(lines)
    return body


def _host_from_url(url: str) -> str:
    from urllib.parse import urlsplit

    parsed = urlsplit(url)
    return parsed.netloc or "localhost"


__all__ = [
    "BuiltAuthnRequest",
    "FakeSamlVerifier",
    "OneLoginSamlVerifier",
    "ParsedSamlAssertion",
    "SamlAssertionExpired",
    "SamlAudienceMismatch",
    "SamlInResponseToMismatch",
    "SamlMissingAssertion",
    "SamlProviderConfig",
    "SamlSignatureError",
    "SamlVerifier",
    "SamlVerifierError",
]
