"""Tests for the ``single_user_desktop`` composition root.

No Postgres here — the pool is a fake. What we verify:

* env validation fails fast with one error naming EVERY missing var;
* a foreign ``ENTERPRISE_DEPLOYMENT_PROFILE`` value is a packaging error;
* ``build_create_app_kwargs`` wires every existing Postgres adapter (no
  silent in-memory fallback for a store that has an adapter);
* the assembled kwargs actually build a working app whose health route
  reports the desktop profile — proving ``create_app`` accepts the full
  desktop wiring without touching the database at construction time.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend_app.adapter_registry.store import PostgresAdapterRegistryStore
from backend_app.api_keys.store import PostgresApiKeyStore
from backend_app.app import create_app
from backend_app.deployment_profile import DeploymentProfileLoader
from backend_app.desktop_app import DesktopComposer, DesktopEnvironmentError
from backend_app.identity import SessionService
from backend_app.identity.avatar_store import PostgresAvatarStore
from backend_app.identity.invitation_store import PostgresInvitationStore
from backend_app.identity.lockout_store import PostgresLockoutStore
from backend_app.identity.login_email_first_store import (
    PostgresAuthProviderDomainStore,
    PostgresMagicLinkTokenStore,
)
from backend_app.identity.me_store import PostgresMeStore
from backend_app.identity.mfa_store import PostgresMfaStore
from backend_app.identity.oidc_store import PostgresOidcStore
from backend_app.identity.password_store import PostgresPasswordStore
from backend_app.identity.saml_store import PostgresSamlStore
from backend_app.identity.scim_store import PostgresScimStore
from backend_app.identity.store import PostgresIdentityStore
from backend_app.notifications.store import PostgresNotificationPrefsStore
from backend_app.policies.store import PostgresToolUsePolicyStore
from backend_app.privacy.store import PostgresPrivacySettingsStore
from backend_app.provider_keys import PostgresProviderApiKeyStore
from backend_app.settings.store import PostgresSettingsStore
from backend_app.store import PostgresMcpStore, PostgresSkillStore
from backend_app.token_vault import LocalTokenVault


_SECRET_32 = "0123456789abcdef0123456789abcdef"


class DesktopEnvMixin:
    """Env builders shared by the desktop composition tests."""

    @staticmethod
    def full_env() -> dict[str, str]:
        return {
            "DATABASE_URL": "postgresql://postgres:pw@127.0.0.1:5432/desktop",
            "ENTERPRISE_AUTH_SECRET": _SECRET_32,
            "ENTERPRISE_SERVICE_TOKEN": "service-token-for-tests",
            "MCP_TOKEN_VAULT_SECRET": _SECRET_32,
            "AUDIT_HMAC_KEY": _SECRET_32.encode("utf-8").hex(),
        }

    @staticmethod
    def desktop_profile():
        return DeploymentProfileLoader.load(
            env={"ENTERPRISE_DEPLOYMENT_PROFILE": "single_user_desktop"}
        )

    def patch_secrets(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """SessionService + LocalTokenVault read os.environ directly."""

        for name, value in self.full_env().items():
            monkeypatch.setenv(name, value)
        monkeypatch.delenv("MCP_TOKEN_VAULT_BACKEND", raising=False)
        monkeypatch.delenv("BACKEND_API_KEY_PEPPER", raising=False)


class _FakePool:
    """Stands in for PostgresConnectionPool; never touched at build time."""


class TestDesktopEnvValidation(DesktopEnvMixin):
    def test_all_missing_lists_every_required_var(self) -> None:
        with pytest.raises(DesktopEnvironmentError) as exc:
            DesktopComposer.validate_env({})

        message = str(exc.value)
        for name in (
            "DATABASE_URL",
            "ENTERPRISE_AUTH_SECRET",
            "ENTERPRISE_SERVICE_TOKEN",
            "MCP_TOKEN_VAULT_SECRET",
            "AUDIT_HMAC_KEY",
        ):
            assert name in message

    def test_partial_missing_lists_only_missing(self) -> None:
        env = self.full_env()
        del env["MCP_TOKEN_VAULT_SECRET"]
        env["ENTERPRISE_SERVICE_TOKEN"] = "   "  # whitespace-only is missing

        with pytest.raises(DesktopEnvironmentError) as exc:
            DesktopComposer.validate_env(env)

        missing_list = str(exc.value).split("environment variables: ")[1].split(". ")[0]
        assert "MCP_TOKEN_VAULT_SECRET" in missing_list
        assert "ENTERPRISE_SERVICE_TOKEN" in missing_list
        assert "DATABASE_URL" not in missing_list
        assert "ENTERPRISE_AUTH_SECRET" not in missing_list

    def test_full_env_passes(self) -> None:
        DesktopComposer.validate_env(self.full_env())


class TestDesktopProfileResolution(DesktopEnvMixin):
    def test_unset_profile_defaults_to_desktop(self) -> None:
        profile = DesktopComposer.resolve_profile(self.full_env())

        assert profile.name == "single_user_desktop"
        assert profile.toggles.require_kms_token_vault is False
        assert profile.toggles.dev_auth_bypass_allowed is False

    def test_matching_profile_env_accepted(self) -> None:
        env = {
            **self.full_env(),
            "ENTERPRISE_DEPLOYMENT_PROFILE": "single_user_desktop",
        }

        profile = DesktopComposer.resolve_profile(env)

        assert profile.name == "single_user_desktop"

    def test_foreign_profile_env_is_a_packaging_error(self) -> None:
        env = {
            **self.full_env(),
            "ENTERPRISE_DEPLOYMENT_PROFILE": "saas_multi_tenant",
        }

        with pytest.raises(DesktopEnvironmentError) as exc:
            DesktopComposer.resolve_profile(env)

        assert "saas_multi_tenant" in str(exc.value)
        assert "single_user_desktop" in str(exc.value)


class TestDesktopKwargsWiring(DesktopEnvMixin):
    _EXPECTED_ADAPTERS: tuple[tuple[str, type], ...] = (
        ("identity_store", PostgresIdentityStore),
        ("oidc_store", PostgresOidcStore),
        ("password_store", PostgresPasswordStore),
        ("lockout_store", PostgresLockoutStore),
        ("mfa_store", PostgresMfaStore),
        ("saml_store", PostgresSamlStore),
        ("scim_store", PostgresScimStore),
        ("me_store", PostgresMeStore),
        ("avatar_store", PostgresAvatarStore),
        ("tool_use_policy_store", PostgresToolUsePolicyStore),
        ("notification_prefs_store", PostgresNotificationPrefsStore),
        ("privacy_settings_store", PostgresPrivacySettingsStore),
        ("api_key_store", PostgresApiKeyStore),
        ("invitation_store", PostgresInvitationStore),
        ("auth_provider_domain_store", PostgresAuthProviderDomainStore),
        ("magic_link_token_store", PostgresMagicLinkTokenStore),
        ("adapter_registry_store", PostgresAdapterRegistryStore),
        ("settings_store", PostgresSettingsStore),
        ("provider_api_keys_store", PostgresProviderApiKeyStore),
    )

    def _kwargs(self, monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
        self.patch_secrets(monkeypatch)
        return DesktopComposer.build_create_app_kwargs(
            pool=_FakePool(),
            profile=self.desktop_profile(),
            env=self.full_env(),
        )

    def test_every_postgres_adapter_is_wired(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        kwargs = self._kwargs(monkeypatch)

        for kwarg_name, adapter_type in self._EXPECTED_ADAPTERS:
            assert isinstance(kwargs[kwarg_name], adapter_type), kwarg_name
        assert isinstance(kwargs["service"].store, PostgresMcpStore)
        assert isinstance(kwargs["skill_service"].store, PostgresSkillStore)

    def test_magic_link_is_globally_disabled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        kwargs = self._kwargs(monkeypatch)

        assert kwargs["magic_link_globally_enabled"] is False

    def test_token_vault_is_local_fernet_and_shared_with_mcp(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        kwargs = self._kwargs(monkeypatch)

        assert isinstance(kwargs["token_vault"], LocalTokenVault)
        assert kwargs["service"].token_vault is kwargs["token_vault"]

    def test_session_service_disallows_dev_mint(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        kwargs = self._kwargs(monkeypatch)

        session_service = kwargs["session_service"]
        assert isinstance(session_service, SessionService)
        assert session_service._dev_mint_allowed is False  # noqa: SLF001

    def test_api_key_pepper_is_derived_not_dev_fallback(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        kwargs = self._kwargs(monkeypatch)

        pepper = kwargs["api_key_pepper"]
        assert isinstance(pepper, bytes)
        assert len(pepper) >= 16
        assert pepper != b"dev-only-pepper-NOT-FOR-PROD!"

    def test_explicit_pepper_env_wins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.patch_secrets(monkeypatch)
        env = {**self.full_env(), "BACKEND_API_KEY_PEPPER": "x" * 24}

        kwargs = DesktopComposer.build_create_app_kwargs(
            pool=_FakePool(), profile=self.desktop_profile(), env=env
        )

        assert kwargs["api_key_pepper"] == b"x" * 24

    def test_kwargs_build_a_booting_app_with_desktop_profile(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """create_app must accept the full desktop wiring without a DB.

        Route registration never queries Postgres, so a fake pool is
        enough to prove the composition is structurally sound end to end.
        """

        kwargs = self._kwargs(monkeypatch)

        app = create_app(**kwargs)  # type: ignore[arg-type]
        client = TestClient(app)

        response = client.get("/v1/health")

        assert response.status_code == 200
        body = response.json()
        assert body["deployment_profile"] == "single_user_desktop"
