"""The MCP config document: projection, reconcile, and secret round-tripping.

The property that matters most here is not "a save works" — it is that a save
of an UNRELATED edit does not destroy a credential the user supplied earlier.
A GET renders stored secrets as `${input:...}` placeholders and never the
plaintext, so a naive PUT of the document it just returned would write empty
secrets over real ones, and every server would start failing auth for a reason
nothing on screen could explain.
"""

from __future__ import annotations

import pytest
from backend_app.contracts import (
    CreateMcpServerRequest,
    McpAuthMode,
    McpTransport,
)
from backend_app.mcp_config import (
    REDACTED,
    McpConfigDocument,
    McpConfigServer,
    McpConfigWriteRequest,
)
from backend_app.service import McpRegistryService
from backend_app.store import InMemoryMcpStore
from backend_app.token_vault import LocalTokenVault

_VAULT_SECRET = "unit-test-secret-value-at-least-32-chars"
_ORG = "org_123"
_USER = "user_123"


@pytest.fixture(autouse=True)
def _desktop(monkeypatch) -> None:
    # stdio + local URLs are desktop-only; most of these tests need them.
    monkeypatch.setenv("ENTERPRISE_DEPLOYMENT_PROFILE", "single_user_desktop")
    monkeypatch.delenv("BACKEND_ENVIRONMENT", raising=False)


@pytest.fixture()
def service() -> McpRegistryService:
    return McpRegistryService(
        store=InMemoryMcpStore(), token_vault=LocalTokenVault(secret=_VAULT_SECRET)
    )


def _read(service: McpRegistryService) -> McpConfigDocument:
    return service.read_config(org_id=_ORG, user_id=_USER)


def _write(service: McpRegistryService, document: McpConfigDocument):
    return service.write_config(
        org_id=_ORG,
        user_id=_USER,
        request=McpConfigWriteRequest(document=document),
    )


def _github_document(token: str = "Bearer ghp_real") -> McpConfigDocument:
    """A config with the credential typed straight in, as a user would."""

    return McpConfigDocument(
        servers={
            "github": McpConfigServer(
                type=McpTransport.HTTP,
                url="https://api.githubcopilot.com/mcp/",
                headers={"Authorization": token},
            )
        }
    )


def test_creates_a_server_from_a_pasted_document(service) -> None:
    result = _write(service, _github_document())

    assert result.created == ("github",)
    servers = service.list_servers(org_id=_ORG, user_id=_USER).servers
    assert len(servers) == 1
    assert servers[0].url == "https://api.githubcopilot.com/mcp/"
    # A configured credential IS the auth; defaulting to OAuth would leave it
    # "Disconnected" forever behind a Connect button that cannot succeed.
    assert servers[0].auth_mode is McpAuthMode.API_KEY


def test_document_never_contains_the_secret(service) -> None:
    _write(service, _github_document())

    document = _read(service)

    assert "ghp_real" not in document.model_dump_json()
    assert document.servers["github"].headers["Authorization"] == REDACTED


def test_resaving_the_document_verbatim_preserves_the_secret(service) -> None:
    """The round-trip property the whole editor depends on."""

    _write(service, _github_document())

    # Exactly what a GET returns, sent straight back with NO secrets map —
    # which is what happens whenever the user edits anything else.
    _write(service, _read(service))

    stored = service.store.list_servers(org_id=_ORG, user_id=_USER)[0]
    header = stored.headers[0]
    assert header.encrypted_value is not None
    assert service.token_vault.decrypt(header.encrypted_value) == "Bearer ghp_real"


def test_editing_one_server_preserves_another_servers_secret(service) -> None:
    _write(service, _github_document())

    document = _read(service)
    document.servers["files"] = McpConfigServer(
        type=McpTransport.STDIO,
        command="npx",
        args=("-y", "@modelcontextprotocol/server-filesystem", "/tmp"),
    )
    result = _write(service, document)

    assert result.created == ("files",)
    assert result.unchanged == ("github",)
    stored = {r.name: r for r in service.store.list_servers(org_id=_ORG, user_id=_USER)}
    assert (
        service.token_vault.decrypt(stored["github"].headers[0].encrypted_value)
        == "Bearer ghp_real"
    )


def test_rotating_a_secret_replaces_it(service) -> None:
    _write(service, _github_document("Bearer old"))

    _write(service, _github_document("Bearer new"))

    stored = service.store.list_servers(org_id=_ORG, user_id=_USER)[0]
    assert (
        service.token_vault.decrypt(stored.headers[0].encrypted_value) == "Bearer new"
    )


def test_removing_a_server_from_the_document_deletes_it(service) -> None:
    _write(service, _github_document())

    result = _write(service, McpConfigDocument(servers={}))

    assert result.deleted == ("github",)
    assert service.list_servers(org_id=_ORG, user_id=_USER).servers == ()


def test_editing_a_server_keeps_its_server_id(service) -> None:
    """Editing must not be a way to silently sign yourself out.

    `server_id` is what connector rows, OAuth tokens, and live session leases
    point at. Delete-and-recreate would give the same server a new identity and
    drop an already-authorized token on the floor.
    """

    _write(service, _github_document())
    original_id = service.list_servers(org_id=_ORG, user_id=_USER).servers[0].server_id

    document = _read(service)
    document.servers["github"].headers["X-Api-Version"] = "2"
    result = _write(service, document)

    assert result.updated == ("github",)
    servers = service.list_servers(org_id=_ORG, user_id=_USER).servers
    assert servers[0].server_id == original_id
    assert {h.name for h in servers[0].headers} == {"Authorization", "X-Api-Version"}


def test_unchanged_document_reports_no_writes(service) -> None:
    # Cheap but load-bearing: a save that rewrote every row on every keystroke
    # would invalidate every pooled session and re-auth the world.
    _write(service, _github_document())

    result = _write(service, _read(service))

    assert result.unchanged == ("github",)
    assert result.updated == ()
    assert result.created == ()
    assert result.deleted == ()


def test_stdio_server_round_trips(service) -> None:
    document = McpConfigDocument(
        servers={
            "files": McpConfigServer(
                type=McpTransport.STDIO,
                command="npx",
                args=("-y", "@modelcontextprotocol/server-filesystem", "/tmp"),
                env={"API_TOKEN": "s3cr3t"},
                cwd="/tmp",
            )
        }
    )

    _write(service, document)
    reread = _read(service)

    entry = reread.servers["files"]
    assert entry.command == "npx"
    assert entry.args == ("-y", "@modelcontextprotocol/server-filesystem", "/tmp")
    assert entry.cwd == "/tmp"
    assert entry.env["API_TOKEN"] == REDACTED
    assert "s3cr3t" not in reread.model_dump_json()


def test_literal_values_round_trip_verbatim(service) -> None:
    # A non-secret must NOT be masked: the editor has to show what it saves.
    document = McpConfigDocument(
        servers={
            "svc": McpConfigServer(
                url="https://mcp.example.com/", headers={"X-Api-Version": "2"}
            )
        }
    )

    _write(service, document)

    assert _read(service).servers["svc"].headers == {"X-Api-Version": "2"}


def test_stdio_entry_without_a_command_is_rejected(service) -> None:
    document = McpConfigDocument(
        servers={"broken": McpConfigServer(type=McpTransport.STDIO)}
    )

    with pytest.raises(ValueError, match="no command"):
        _write(service, document)


def test_a_malformed_entry_rejects_the_whole_save(service) -> None:
    """One bad entry must not half-apply the document.

    There is no cross-row transaction available, so the document is validated
    end-to-end before anything is written. Without that, a typo in the second
    server would leave the first one created and the editor describing a state
    the registry no longer matched.
    """

    _write(service, _github_document())
    document = _read(service)
    document.servers["fresh"] = McpConfigServer(url="https://new.example.com/mcp")
    document.servers["broken"] = McpConfigServer(type=McpTransport.STDIO)

    with pytest.raises(ValueError):
        _write(service, document)

    names = {s.name for s in service.list_servers(org_id=_ORG, user_id=_USER).servers}
    assert names == {"github"}


def test_local_servers_rejected_when_hosted(service, monkeypatch) -> None:
    # The config route reaches the registry directly, so it needs the same
    # gate as the single-server create path — a gate on one of two doors is
    # not a gate.
    monkeypatch.setenv("ENTERPRISE_DEPLOYMENT_PROFILE", "saas_multi_tenant")
    document = McpConfigDocument(
        servers={"files": McpConfigServer(type=McpTransport.STDIO, command="npx")}
    )

    with pytest.raises(ValueError, match="not permitted"):
        _write(service, document)


def test_existing_servers_appear_in_the_document(service) -> None:
    # A server added by any other route must show up here, or "Manage MCP"
    # would be a partial view that silently deletes what it failed to render.
    service.create_server(
        CreateMcpServerRequest(
            org_id=_ORG,
            user_id=_USER,
            url="https://linear.app/mcp",
            display_name="Linear",
        )
    )

    document = _read(service)

    assert "linear" in document.servers
    assert document.servers["linear"].url == "https://linear.app/mcp"


# ---------------------------------------------------------------------------
# Credential classification — the rule that replaced `${input:...}`
# ---------------------------------------------------------------------------
#
# There is no placeholder syntax and no side-channel secrets map any more. A
# value is sealed or shown based on what it IS, decided once on write, so these
# pin both directions: a credential must never come back, and a plain value
# must never be hidden (a config editor that redacts `X-Api-Version: 2` can no
# longer tell you what your config says).


def test_typed_credential_is_sealed_and_never_returned(service) -> None:
    _write(service, _github_document("Bearer ghp_typed_in"))

    document = _read(service)

    assert document.servers["github"].headers["Authorization"] == REDACTED
    assert "ghp_typed_in" not in document.model_dump_json()
    stored = service.store.list_servers(org_id=_ORG, user_id=_USER)[0]
    assert stored.headers[0].value is None
    assert (
        service.token_vault.decrypt(stored.headers[0].encrypted_value)
        == "Bearer ghp_typed_in"
    )


def test_plain_value_stays_visible(service) -> None:
    _write(
        service,
        McpConfigDocument(
            servers={
                "svc": McpConfigServer(
                    url="https://mcp.example.com/",
                    headers={"X-Api-Version": "2", "Accept-Language": "en-US"},
                )
            }
        ),
    )

    headers = _read(service).servers["svc"].headers

    assert headers == {"X-Api-Version": "2", "Accept-Language": "en-US"}


def test_a_url_carrying_a_password_is_sealed_whatever_it_is_called(service) -> None:
    # `DATABASE_URL` reads as plain by name, and is the single most common way
    # to hand a service a password.
    _write(
        service,
        McpConfigDocument(
            servers={
                "pg": McpConfigServer(
                    type=McpTransport.STDIO,
                    command="npx",
                    args=("-y", "@modelcontextprotocol/server-postgres"),
                    env={"DATABASE_URL": "postgres://u:hunter2@db/app"},
                )
            }
        ),
    )

    document = _read(service)

    assert document.servers["pg"].env["DATABASE_URL"] == REDACTED
    assert "hunter2" not in document.model_dump_json()


def test_a_url_without_a_password_stays_visible(service) -> None:
    _write(
        service,
        McpConfigDocument(
            servers={
                "pg": McpConfigServer(
                    type=McpTransport.STDIO,
                    command="npx",
                    args=("-y", "@modelcontextprotocol/server-postgres"),
                    env={"DATABASE_URL": "postgres://localhost:5432/app"},
                )
            }
        ),
    )

    assert (
        _read(service).servers["pg"].env["DATABASE_URL"]
        == "postgres://localhost:5432/app"
    )


def test_an_unresolved_placeholder_is_never_stored_as_a_credential(service) -> None:
    """A pasted `${input:...}` must not become the bearer token.

    Storing that literal would mean sending
    `Authorization: ${input:github_mcp_pat}` to the server and getting a 401
    the user could not explain from anything on screen. With nothing on file
    the header is dropped entirely, so the server answers its own honest
    "unauthenticated" instead.
    """

    _write(service, _github_document("${input:github_mcp_pat}"))

    stored = service.store.list_servers(org_id=_ORG, user_id=_USER)[0]
    assert stored.headers == ()
    assert _read(service).servers["github"].headers is None


def test_a_placeholder_does_not_clobber_a_stored_credential(service) -> None:
    # Same treatment as the marker: "no value supplied" means keep what is on
    # file, so a half-edited paste cannot sign the user out.
    _write(service, _github_document("Bearer ghp_real"))

    _write(service, _github_document("${input:github_mcp_pat}"))

    stored = service.store.list_servers(org_id=_ORG, user_id=_USER)[0]
    assert (
        service.token_vault.decrypt(stored.headers[0].encrypted_value)
        == "Bearer ghp_real"
    )
