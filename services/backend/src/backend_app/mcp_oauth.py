"""OAuth discovery, registration, and token exchange for remote MCP servers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from backend_app.contracts import (
    McpAuthSessionRecord,
    McpServerRecord,
    OAuthTokenRequest,
)
from backend_app.token_vault import TokenVault


class McpOAuthError(ValueError):
    """Safe OAuth setup or token exchange failure."""


class Keys:
    """Stable OAuth metadata, request, and HTTP keys."""

    class Encoding:
        UTF_8 = "utf-8"

    class Header:
        ACCEPT = "accept"
        CONTENT_TYPE = "content-type"

    class HttpMethod:
        GET = "GET"
        POST = "POST"

    class OAuth:
        ACCESS_TOKEN = "access_token"
        AUTHORIZATION_ENDPOINT = "authorization_endpoint"
        AUTHORIZATION_SERVER_METADATA = "authorization_server_metadata"
        AUTHORIZATION_SERVERS = "authorization_servers"
        CLIENT_ID = "client_id"
        CLIENT_NAME = "client_name"
        CLIENT_SECRET = "client_secret"
        CODE = "code"
        CODE_CHALLENGE = "code_challenge"
        CODE_CHALLENGE_METHOD = "code_challenge_method"
        CODE_VERIFIER = "code_verifier"
        DISCOVERED_AT = "discovered_at"
        ENCRYPTED_CLIENT_SECRET = "encrypted_client_secret"
        EXPIRES_IN = "expires_in"
        GRANT_TYPE = "grant_type"
        GRANT_TYPES = "grant_types"
        ISSUER = "issuer"
        OAUTH_CLIENT = "oauth_client"
        REDIRECT_URI = "redirect_uri"
        REDIRECT_URIS = "redirect_uris"
        REFRESH_TOKEN = "refresh_token"
        REGISTRATION_ENDPOINT = "registration_endpoint"
        REGISTERED_AT = "registered_at"
        REQUIRED_SCOPES = "required_scopes"
        RESOURCE = "resource"
        RESOURCE_METADATA = "resource_metadata"
        RESPONSE_TYPE = "response_type"
        RESPONSE_TYPES = "response_types"
        SCOPE = "scope"
        SCOPES_REQUIRED = "scopes_required"
        SCOPES_SUPPORTED = "scopes_supported"
        STATE = "state"
        TOKEN_ENDPOINT = "token_endpoint"
        TOKEN_ENDPOINT_AUTH_METHOD = "token_endpoint_auth_method"
        TOKEN_TYPE = "token_type"


class Values:
    """Stable OAuth string values and endpoint suffixes."""

    class ContentType:
        FORM = "application/x-www-form-urlencoded"
        JSON = "application/json"

    class GrantType:
        AUTHORIZATION_CODE = "authorization_code"
        REFRESH_TOKEN = "refresh_token"

    class OAuth:
        CLIENT_ID = "enterprise-search"
        CLIENT_NAME = "Enterprise Search"
        CODE = "code"
        CODE_CHALLENGE_METHOD = "S256"
        DEFAULT_SCOPE = "mcp"
        TOKEN_TYPE = "Bearer"
        TOKEN_ENDPOINT_AUTH_METHOD = "client_secret_post"
        TOKEN_ENDPOINT_AUTH_NONE = "none"

    class WellKnown:
        AUTHORIZATION_SERVER = "/.well-known/oauth-authorization-server"
        PROTECTED_RESOURCE = "/.well-known/oauth-protected-resource"


@dataclass(frozen=True)
class McpAuthorization:
    auth_url: str
    discovery: dict[str, Any]
    required_scopes: tuple[str, ...]


class RemoteMcpOAuthClient:
    """Small OAuth 2.1 client for remote MCP protected resources."""

    CLIENT_NAME = Values.OAuth.CLIENT_NAME
    DEFAULT_SCOPE = Values.OAuth.DEFAULT_SCOPE

    def __init__(self, *, timeout_seconds: float = 10) -> None:
        self.timeout_seconds = timeout_seconds

    def authorization(
        self,
        *,
        record: McpServerRecord,
        redirect_uri: str,
        state: str,
        code_challenge: str,
        token_vault: TokenVault,
    ) -> McpAuthorization:
        discovery = self._ensure_client_registration(
            discovery=self.discover(record),
            redirect_uri=redirect_uri,
            token_vault=token_vault,
        )
        client = self._client(discovery)
        auth_endpoint = self._required_url(discovery, Keys.OAuth.AUTHORIZATION_ENDPOINT)
        scopes = self._required_scopes(discovery)
        query = {
            Keys.OAuth.CLIENT_ID: str(client[Keys.OAuth.CLIENT_ID]),
            Keys.OAuth.REDIRECT_URI: redirect_uri,
            Keys.OAuth.RESPONSE_TYPE: Values.OAuth.CODE,
            Keys.OAuth.SCOPE: " ".join(scopes),
            Keys.OAuth.STATE: state,
            Keys.OAuth.CODE_CHALLENGE: code_challenge,
            Keys.OAuth.CODE_CHALLENGE_METHOD: Values.OAuth.CODE_CHALLENGE_METHOD,
        }
        resource = discovery.get(Keys.OAuth.RESOURCE)
        if isinstance(resource, str) and resource.strip():
            query[Keys.OAuth.RESOURCE] = resource
        return McpAuthorization(
            auth_url=f"{auth_endpoint}?{urlencode(query)}",
            discovery=discovery,
            required_scopes=scopes,
        )

    def exchange_code(
        self,
        *,
        record: McpServerRecord,
        session: McpAuthSessionRecord,
        code: str,
        token_vault: TokenVault,
    ) -> OAuthTokenRequest:
        discovery = self._validated_discovery(record)
        body: dict[str, str] = {
            Keys.OAuth.GRANT_TYPE: Values.GrantType.AUTHORIZATION_CODE,
            Keys.OAuth.CLIENT_ID: str(self._client(discovery)[Keys.OAuth.CLIENT_ID]),
            Keys.OAuth.CODE: code,
            Keys.OAuth.REDIRECT_URI: session.redirect_uri,
            Keys.OAuth.CODE_VERIFIER: session.code_verifier,
        }
        self._apply_client_auth(body, discovery=discovery, token_vault=token_vault)
        return self._token_request(discovery, body)

    def refresh_token(
        self,
        *,
        record: McpServerRecord,
        refresh_token: str,
        token_vault: TokenVault,
    ) -> OAuthTokenRequest:
        discovery = self._validated_discovery(record)
        body: dict[str, str] = {
            Keys.OAuth.GRANT_TYPE: Values.GrantType.REFRESH_TOKEN,
            Keys.OAuth.CLIENT_ID: str(self._client(discovery)[Keys.OAuth.CLIENT_ID]),
            Keys.OAuth.REFRESH_TOKEN: refresh_token,
        }
        self._apply_client_auth(body, discovery=discovery, token_vault=token_vault)
        return self._token_request(discovery, body)

    def discover(self, record: McpServerRecord) -> dict[str, Any]:
        cached = record.last_discovery
        if self._has_required_metadata(cached):
            return dict(cached)

        resource_metadata = self._fetch_first_json(
            self._protected_resource_metadata_urls(record.url)
        )
        auth_server = self._auth_server_from_resource(resource_metadata, record.url)
        auth_metadata = self._fetch_first_json(
            self._authorization_server_metadata_urls(auth_server)
        )
        if not auth_metadata:
            auth_metadata = self._legacy_metadata(record.url)

        merged = {
            **dict(cached),
            Keys.OAuth.RESOURCE: resource_metadata.get(Keys.OAuth.RESOURCE, record.url),
            Keys.OAuth.RESOURCE_METADATA: resource_metadata,
            Keys.OAuth.AUTHORIZATION_SERVER_METADATA: auth_metadata,
            Keys.OAuth.AUTHORIZATION_ENDPOINT: auth_metadata.get(
                Keys.OAuth.AUTHORIZATION_ENDPOINT
            ),
            Keys.OAuth.TOKEN_ENDPOINT: auth_metadata.get(Keys.OAuth.TOKEN_ENDPOINT),
            Keys.OAuth.REGISTRATION_ENDPOINT: auth_metadata.get(
                Keys.OAuth.REGISTRATION_ENDPOINT
            ),
            Keys.OAuth.ISSUER: auth_metadata.get(Keys.OAuth.ISSUER, auth_server),
            Keys.OAuth.SCOPES_SUPPORTED: auth_metadata.get(
                Keys.OAuth.SCOPES_SUPPORTED, ()
            ),
            Keys.OAuth.REQUIRED_SCOPES: self._scopes_from_metadata(
                resource_metadata, auth_metadata
            ),
            Keys.OAuth.DISCOVERED_AT: datetime.now(UTC).isoformat(),
        }
        if not self._has_required_metadata(merged):
            raise McpOAuthError("MCP OAuth discovery did not return required endpoints")
        return merged

    def _ensure_client_registration(
        self,
        *,
        discovery: dict[str, Any],
        redirect_uri: str,
        token_vault: TokenVault,
    ) -> dict[str, Any]:
        client = discovery.get(Keys.OAuth.OAUTH_CLIENT)
        if isinstance(client, dict) and client.get(Keys.OAuth.CLIENT_ID):
            redirect_uris = client.get(Keys.OAuth.REDIRECT_URIS)
            if not isinstance(redirect_uris, list) or redirect_uri in redirect_uris:
                return discovery

        registration_endpoint = discovery.get(Keys.OAuth.REGISTRATION_ENDPOINT)
        if isinstance(registration_endpoint, str) and registration_endpoint.strip():
            registered = self._register_client(registration_endpoint, redirect_uri)
            secret = registered.get(Keys.OAuth.CLIENT_SECRET)
            client_record: dict[str, Any] = {
                Keys.OAuth.CLIENT_ID: self._required_text(
                    registered, Keys.OAuth.CLIENT_ID
                ),
                Keys.OAuth.TOKEN_ENDPOINT_AUTH_METHOD: registered.get(
                    Keys.OAuth.TOKEN_ENDPOINT_AUTH_METHOD,
                    Values.OAuth.TOKEN_ENDPOINT_AUTH_METHOD,
                ),
                Keys.OAuth.REDIRECT_URIS: registered.get(
                    Keys.OAuth.REDIRECT_URIS, [redirect_uri]
                ),
                Keys.OAuth.REGISTERED_AT: datetime.now(UTC).isoformat(),
            }
            if isinstance(secret, str) and secret.strip():
                client_record[Keys.OAuth.ENCRYPTED_CLIENT_SECRET] = token_vault.encrypt(
                    secret
                )
            return {**discovery, Keys.OAuth.OAUTH_CLIENT: client_record}

        return {
            **discovery,
            Keys.OAuth.OAUTH_CLIENT: {
                Keys.OAuth.CLIENT_ID: Values.OAuth.CLIENT_ID,
                Keys.OAuth.TOKEN_ENDPOINT_AUTH_METHOD: Values.OAuth.TOKEN_ENDPOINT_AUTH_NONE,
                Keys.OAuth.REDIRECT_URIS: [redirect_uri],
                Keys.OAuth.REGISTERED_AT: datetime.now(UTC).isoformat(),
            },
        }

    def _register_client(
        self, registration_endpoint: str, redirect_uri: str
    ) -> dict[str, Any]:
        payload = {
            Keys.OAuth.CLIENT_NAME: self.CLIENT_NAME,
            Keys.OAuth.REDIRECT_URIS: [redirect_uri],
            Keys.OAuth.GRANT_TYPES: [
                Values.GrantType.AUTHORIZATION_CODE,
                Values.GrantType.REFRESH_TOKEN,
            ],
            Keys.OAuth.RESPONSE_TYPES: [Values.OAuth.CODE],
            Keys.OAuth.TOKEN_ENDPOINT_AUTH_METHOD: Values.OAuth.TOKEN_ENDPOINT_AUTH_METHOD,
        }
        return self._post_json(registration_endpoint, payload)

    def _token_request(
        self, discovery: dict[str, Any], body: dict[str, str]
    ) -> OAuthTokenRequest:
        payload = self._post_form(
            self._required_url(discovery, Keys.OAuth.TOKEN_ENDPOINT), body
        )
        expires_at = self._expires_at(payload.get(Keys.OAuth.EXPIRES_IN))
        return OAuthTokenRequest(
            access_token=self._required_text(payload, Keys.OAuth.ACCESS_TOKEN),
            refresh_token=self._optional_text(payload.get(Keys.OAuth.REFRESH_TOKEN)),
            token_type=str(
                payload.get(Keys.OAuth.TOKEN_TYPE) or Values.OAuth.TOKEN_TYPE
            ),
            expires_at=expires_at,
            scope=self._optional_text(payload.get(Keys.OAuth.SCOPE)),
        )

    def _apply_client_auth(
        self,
        body: dict[str, str],
        *,
        discovery: dict[str, Any],
        token_vault: TokenVault,
    ) -> None:
        client = self._client(discovery)
        encrypted_secret = client.get(Keys.OAuth.ENCRYPTED_CLIENT_SECRET)
        if isinstance(encrypted_secret, str) and encrypted_secret:
            body[Keys.OAuth.CLIENT_SECRET] = token_vault.decrypt(encrypted_secret)

    @classmethod
    def _validated_discovery(cls, record: McpServerRecord) -> dict[str, Any]:
        discovery = dict(record.last_discovery)
        if not cls._has_required_metadata(discovery):
            raise McpOAuthError("MCP OAuth discovery is missing for this server")
        cls._client(discovery)
        return discovery

    @classmethod
    def _has_required_metadata(cls, discovery: dict[str, Any]) -> bool:
        return bool(
            discovery.get(Keys.OAuth.AUTHORIZATION_ENDPOINT)
            and discovery.get(Keys.OAuth.TOKEN_ENDPOINT)
        )

    @classmethod
    def _client(cls, discovery: dict[str, Any]) -> dict[str, Any]:
        client = discovery.get(Keys.OAuth.OAUTH_CLIENT)
        if not isinstance(client, dict) or not client.get(Keys.OAuth.CLIENT_ID):
            raise McpOAuthError("MCP OAuth client registration is missing")
        return client

    @classmethod
    def _required_scopes(cls, discovery: dict[str, Any]) -> tuple[str, ...]:
        scopes = discovery.get(Keys.OAuth.REQUIRED_SCOPES)
        if isinstance(scopes, list | tuple) and scopes:
            return tuple(str(scope) for scope in scopes)
        return (cls.DEFAULT_SCOPE,)

    @classmethod
    def _scopes_from_metadata(
        cls, resource_metadata: dict[str, Any], auth_metadata: dict[str, Any]
    ) -> list[str]:
        for key in (
            Keys.OAuth.SCOPES_REQUIRED,
            Keys.OAuth.REQUIRED_SCOPES,
            Keys.OAuth.SCOPES_SUPPORTED,
        ):
            value = resource_metadata.get(key)
            if isinstance(value, list) and value:
                return [str(scope) for scope in value]
        value = auth_metadata.get(Keys.OAuth.SCOPES_SUPPORTED)
        if isinstance(value, list) and cls.DEFAULT_SCOPE in value:
            return [cls.DEFAULT_SCOPE]
        return [cls.DEFAULT_SCOPE]

    @classmethod
    def _auth_server_from_resource(
        cls, resource_metadata: dict[str, Any], server_url: str
    ) -> str:
        auth_servers = resource_metadata.get(Keys.OAuth.AUTHORIZATION_SERVERS)
        if isinstance(auth_servers, list) and auth_servers:
            first = auth_servers[0]
            if isinstance(first, str) and first.strip():
                return first
        parsed = urlsplit(server_url)
        return urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))

    @classmethod
    def _protected_resource_metadata_urls(cls, server_url: str) -> tuple[str, ...]:
        parsed = urlsplit(server_url)
        path = parsed.path.rstrip("/")
        origin = urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
        urls = []
        if path:
            urls.append(f"{origin}{Values.WellKnown.PROTECTED_RESOURCE}{path}")
        urls.append(f"{origin}{Values.WellKnown.PROTECTED_RESOURCE}")
        return tuple(urls)

    @classmethod
    def _authorization_server_metadata_urls(cls, auth_server: str) -> tuple[str, ...]:
        parsed = urlsplit(auth_server)
        origin = urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
        path = parsed.path.rstrip("/")
        urls = []
        if path:
            urls.append(f"{origin}{Values.WellKnown.AUTHORIZATION_SERVER}{path}")
        urls.append(f"{origin}{Values.WellKnown.AUTHORIZATION_SERVER}")
        return tuple(urls)

    @classmethod
    def _legacy_metadata(cls, server_url: str) -> dict[str, str]:
        base = server_url.rstrip("/")
        return {
            Keys.OAuth.ISSUER: base,
            Keys.OAuth.AUTHORIZATION_ENDPOINT: f"{base}/oauth/authorize",
            Keys.OAuth.TOKEN_ENDPOINT: f"{base}/oauth/token",
        }

    def _fetch_first_json(self, urls: tuple[str, ...]) -> dict[str, Any]:
        for url in urls:
            try:
                payload = self._get_json(url)
            except (HTTPError, URLError, TimeoutError, ValueError):
                continue
            if payload:
                return payload
        return {}

    def _get_json(self, url: str) -> dict[str, Any]:
        request = Request(
            url,
            headers={Keys.Header.ACCEPT: Values.ContentType.JSON},
            method=Keys.HttpMethod.GET,
        )
        with urlopen(request, timeout=self.timeout_seconds) as response:
            return self._decode_json_response(response.read())

    def _post_json(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        request = Request(
            url,
            data=json.dumps(payload).encode(Keys.Encoding.UTF_8),
            headers={
                Keys.Header.ACCEPT: Values.ContentType.JSON,
                Keys.Header.CONTENT_TYPE: Values.ContentType.JSON,
            },
            method=Keys.HttpMethod.POST,
        )
        with urlopen(request, timeout=self.timeout_seconds) as response:
            return self._decode_json_response(response.read())

    def _post_form(self, url: str, body: dict[str, str]) -> dict[str, Any]:
        request = Request(
            url,
            data=urlencode(body).encode(Keys.Encoding.UTF_8),
            headers={
                Keys.Header.ACCEPT: Values.ContentType.JSON,
                Keys.Header.CONTENT_TYPE: Values.ContentType.FORM,
            },
            method=Keys.HttpMethod.POST,
        )
        with urlopen(request, timeout=self.timeout_seconds) as response:
            return self._decode_json_response(response.read())

    @staticmethod
    def _decode_json_response(raw: bytes) -> dict[str, Any]:
        payload = json.loads(raw.decode(Keys.Encoding.UTF_8))
        if not isinstance(payload, dict):
            raise ValueError("OAuth endpoint returned an invalid response")
        return payload

    @staticmethod
    def _required_url(payload: dict[str, Any], key: str) -> str:
        value = payload.get(key)
        if not isinstance(value, str) or not value.strip():
            raise McpOAuthError(f"OAuth discovery response missing {key}")
        return value

    @staticmethod
    def _required_text(payload: dict[str, Any], key: str) -> str:
        value = payload.get(key)
        if not isinstance(value, str) or not value.strip():
            raise McpOAuthError(f"OAuth response missing {key}")
        return value

    @staticmethod
    def _optional_text(value: object) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str) or not value.strip():
            raise McpOAuthError("OAuth response has an invalid string field")
        return value

    @staticmethod
    def _expires_at(value: object) -> datetime | None:
        if value is None:
            return None
        if not isinstance(value, int) or value <= 0:
            raise McpOAuthError("OAuth token response has invalid expires_in")
        return datetime.now(UTC) + timedelta(seconds=value)
