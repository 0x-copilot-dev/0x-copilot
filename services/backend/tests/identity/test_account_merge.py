"""Account-merge saga tests (PRD docs/plan/account-linking §6.3 — PR6).

Service-level, over the in-memory adapters: the saga's happy path, tenant
isolation (a decoy third account is never touched, NFR-2), collision rules
(FR-M8), session revocation (FR-M5), soft-disable + lineage + dual audit
(FR-M7/NFR-5), idempotency (NFR-8), and failure/resume at a checkpoint
(NFR-3). The Postgres re-key executor shares the same strategy tables and is
gated on the live-stack integration run (PRD §8).
"""

from __future__ import annotations

from typing import Any

import pytest

from backend_app.contracts import (
    AccountMergeState,
    OidcIdentityRecord,
    OrganizationMemberRecord,
    OrganizationMemberSource,
    OrganizationRecord,
    UserRecord,
    UserStatus,
    WalletIdentityRecord,
)
from backend_app.identity import (
    InMemoryIdentityStore,
    InMemoryOidcStore,
    InMemorySessionStore,
    InMemorySiweStore,
    SessionService,
)
from backend_app.identity.account_merge import (
    AccountMergeService,
    InMemoryMergeData,
    MergeNotAllowed,
    MergeRuntimeFailed,
    NullRuntimeMergeClient,
)
from backend_app.identity.account_merge_store import InMemoryAccountMergeStore
from backend_app.identity.me_store import InMemoryMeStore
from backend_app.provider_keys.store import (
    InMemoryProviderApiKeyStore,
    ProviderApiKeyRecord,
)

_AUTH_SECRET = "test-auth-secret-merge-0123456789"
_ADDR = "0x5aaeb6053f3e94c9b9a09f33669435e7ef1beaed"


class _FlakyRuntime:
    """Fails the first call, succeeds after — drives the resume test."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.fail_next = True

    def merge(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs["merge_id"])
        if self.fail_next:
            self.fail_next = False
            raise RuntimeError("runtime unavailable")
        return {"status": "completed", "tables": {"agent_conversations": 2}}


def _seed_account(
    identity: InMemoryIdentityStore, *, org_id: str, user_id: str, email: str
) -> None:
    identity.create_organization(
        OrganizationRecord(org_id=org_id, display_name=org_id, slug=org_id)
    )
    identity.create_user(
        UserRecord(
            user_id=user_id,
            org_id=org_id,
            primary_email=email,
            display_name=user_id,
        )
    )
    identity.add_member(
        OrganizationMemberRecord(
            org_id=org_id,
            user_id=user_id,
            source=OrganizationMemberSource.SIWE,
        )
    )


def _build(runtime: Any | None = None) -> dict[str, Any]:
    identity = InMemoryIdentityStore()
    siwe = InMemorySiweStore()
    oidc = InMemoryOidcStore()
    provider_keys = InMemoryProviderApiKeyStore()
    me = InMemoryMeStore()
    sessions = SessionService(
        store=InMemorySessionStore(),
        auth_secret=_AUTH_SECRET,
        dev_mint_allowed=True,
    )
    _seed_account(
        identity, org_id="org_surv", user_id="usr_surv", email="surv@acme.com"
    )
    _seed_account(
        identity,
        org_id="org_abs",
        user_id="usr_abs",
        email=f"{_ADDR}@wallet.invalid",
    )
    _seed_account(
        identity, org_id="org_decoy", user_id="usr_decoy", email="decoy@acme.com"
    )
    runtime_port = runtime or NullRuntimeMergeClient()
    service = AccountMergeService(
        identity_store=identity,
        merge_store=InMemoryAccountMergeStore(),
        sessions=sessions,
        data_port=InMemoryMergeData(
            identity_store=identity,
            siwe_store=siwe,
            oidc_store=oidc,
            provider_keys_store=provider_keys,
            me_store=me,
        ),
        runtime_port=runtime_port,
    )
    return {
        "service": service,
        "identity": identity,
        "siwe": siwe,
        "oidc": oidc,
        "provider_keys": provider_keys,
        "me": me,
        "sessions": sessions,
        "runtime": runtime_port,
    }


def _merge(ctx: dict[str, Any]) -> Any:
    return ctx["service"].merge_for_conflict(
        survivor_org_id="org_surv",
        survivor_user_id="usr_surv",
        absorbed_org_id="org_abs",
        absorbed_user_id="usr_abs",
        proof_ref=f"siwe:{_ADDR}",
    )


class TestMergeSaga:
    def test_full_saga_rekeys_everything_and_spares_the_decoy(self) -> None:
        ctx = _build()
        # Absorbed account's data: a wallet, an OIDC identity, a session.
        ctx["siwe"].create_wallet_identity(
            WalletIdentityRecord(
                address=_ADDR, org_id="org_abs", user_id="usr_abs", chain_id=8453
            )
        )
        ctx["oidc"].create_identity(
            OidcIdentityRecord(
                org_id="org_abs",
                user_id="usr_abs",
                provider_id="google",
                subject="sub-abs",
            )
        )
        ctx["sessions"].create(org_id="org_abs", user_id="usr_abs")
        ctx["sessions"].create(org_id="org_abs", user_id="usr_abs")
        survivor_session = ctx["sessions"].create(org_id="org_surv", user_id="usr_surv")
        # Decoy (NFR-2): a third account that must be untouched.
        decoy_wallet = WalletIdentityRecord(
            address="0xfb6916095ca1df60bb79ce92ce3ea74c37c5d359",
            org_id="org_decoy",
            user_id="usr_decoy",
            chain_id=1,
        )
        ctx["siwe"].create_wallet_identity(decoy_wallet)

        record = _merge(ctx)

        assert record.state == AccountMergeState.COMPLETED
        assert record.completed_at is not None
        # Wallet + OIDC identity now belong to the survivor.
        wallet = ctx["siwe"].get_wallet_identity(address=_ADDR)
        assert (wallet.org_id, wallet.user_id) == ("org_surv", "usr_surv")
        ident = ctx["oidc"].get_identity_by_subject(
            provider_id="google", subject="sub-abs"
        )
        assert (ident.org_id, ident.user_id) == ("org_surv", "usr_surv")
        # Decoy untouched (NFR-2).
        decoy = ctx["siwe"].get_wallet_identity(address=decoy_wallet.address)
        assert (decoy.org_id, decoy.user_id) == ("org_decoy", "usr_decoy")
        # Absorbed sessions revoked; survivor's stays alive (FR-M5).
        assert ctx["sessions"].list_active(org_id="org_abs", user_id="usr_abs") == ()
        alive = ctx["sessions"].list_active(org_id="org_surv", user_id="usr_surv")
        assert [s.session_id for s in alive] == [survivor_session.session_id]
        # Absorbed user soft-disabled with lineage (FR-M7 / NFR-6).
        absorbed = ctx["identity"].users["usr_abs"]
        assert absorbed.status == UserStatus.DISABLED
        assert absorbed.deleted_at is not None
        assert absorbed.absorbed_into_user_id == "usr_surv"
        assert absorbed.merged_at is not None
        # Immutable audit on BOTH orgs' trails (FR-M7 / NFR-5).
        merged_events = [
            e
            for e in ctx["identity"].identity_audit_events
            if e.action == "account.merged"
        ]
        assert {e.org_id for e in merged_events} == {"org_surv", "org_abs"}
        assert all(e.metadata["proof_ref"] == f"siwe:{_ADDR}" for e in merged_events)
        # Observability (NFR-10): per-store counts recorded.
        assert record.counts["backend"]["wallet_identities"] == 1
        assert record.counts["sessions_revoked"] == 2

    def test_provider_key_collision_survivor_wins(self) -> None:
        from backend_app.provider_keys.store import ProviderName

        ctx = _build()
        for org, user, hint in (
            ("org_surv", "usr_surv", "…surv"),
            ("org_abs", "usr_abs", "…abs"),
        ):
            ctx["provider_keys"].upsert(
                ProviderApiKeyRecord(
                    org_id=org,
                    user_id=user,
                    provider=ProviderName.OPENAI,
                    encrypted_key=f"enc-{hint}",
                    key_hint=hint,
                )
            )
        _merge(ctx)
        # FR-M8: the survivor's key wins; the absorbed duplicate is dropped.
        survivor_key = ctx["provider_keys"].get(
            org_id="org_surv", user_id="usr_surv", provider=ProviderName.OPENAI
        )
        assert survivor_key is not None and survivor_key.key_hint == "…surv"
        assert (
            ctx["provider_keys"].get(
                org_id="org_abs", user_id="usr_abs", provider=ProviderName.OPENAI
            )
            is None
        )

    def test_idempotent_re_merge_returns_completed_record(self) -> None:
        ctx = _build()
        first = _merge(ctx)
        second = _merge(ctx)
        assert second.merge_id == first.merge_id
        assert second.state == AccountMergeState.COMPLETED
        # The runtime leg ran exactly once (NFR-8).
        assert len(ctx["runtime"].calls) == 1

    def test_absorbed_already_merged_elsewhere_refused(self) -> None:
        ctx = _build()
        _merge(ctx)
        with pytest.raises(MergeNotAllowed):
            ctx["service"].merge_for_conflict(
                survivor_org_id="org_decoy",
                survivor_user_id="usr_decoy",
                absorbed_org_id="org_abs",
                absorbed_user_id="usr_abs",
                proof_ref="siwe:0xother",
            )

    def test_same_account_refused(self) -> None:
        ctx = _build()
        with pytest.raises(MergeNotAllowed):
            ctx["service"].merge_for_conflict(
                survivor_org_id="org_surv",
                survivor_user_id="usr_surv",
                absorbed_org_id="org_surv",
                absorbed_user_id="usr_surv",
                proof_ref="siwe:self",
            )

    def test_shared_org_refused(self) -> None:
        ctx = _build()
        # Absorbed org gains a second active member → not a personal org
        # (PRD non-goal: other members' data is not the caller's to move).
        ctx["identity"].add_member(
            OrganizationMemberRecord(
                org_id="org_abs",
                user_id="usr_decoy",
                source=OrganizationMemberSource.INVITE,
            )
        )
        with pytest.raises(MergeNotAllowed):
            _merge(ctx)

    def test_runtime_failure_checkpoints_then_resumes(self) -> None:
        runtime = _FlakyRuntime()
        ctx = _build(runtime=runtime)
        ctx["siwe"].create_wallet_identity(
            WalletIdentityRecord(
                address=_ADDR, org_id="org_abs", user_id="usr_abs", chain_id=8453
            )
        )

        # First attempt: backend re-key lands, runtime leg fails → the saga
        # stops AT its checkpoint with the error recorded (NFR-3/10).
        with pytest.raises(MergeRuntimeFailed):
            _merge(ctx)
        merge_store = ctx["service"]._merges  # noqa: SLF001 - test introspection
        (record,) = merge_store.find_by_absorbed(
            absorbed_org_id="org_abs", absorbed_user_id="usr_abs"
        )
        assert record.state == AccountMergeState.BACKEND_DONE
        assert record.error is not None
        # Nothing destructive happened yet: user still active, but the
        # wallet already re-keyed (idempotent step, safe either way).
        assert ctx["identity"].users["usr_abs"].status == UserStatus.ACTIVE

        # Retry (same confirm) RESUMES at the runtime leg — the backend
        # re-key is not re-run destructively, and the saga completes.
        resumed = _merge(ctx)
        assert resumed.merge_id == record.merge_id
        assert resumed.state == AccountMergeState.COMPLETED
        assert resumed.error is None
        assert runtime.calls == [record.merge_id, record.merge_id]
        assert ctx["identity"].users["usr_abs"].status == UserStatus.DISABLED
