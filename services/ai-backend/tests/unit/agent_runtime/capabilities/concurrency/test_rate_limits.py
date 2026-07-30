"""SMELL-09 (a) — a declared rate-limit scope must govern admission.

``ConcurrencyPolicy.rate_limit_scope`` was resolved through the full precedence
chain, narrowed, journalled, and read by nobody: every operation acquired the
same fixed ladder. These tests pin the join, and — because a control that only
ever narrows is worthless if it can also widen — they pin the narrowing
direction on real ``RunPermitManager`` admission rather than on the shape of a
request.
"""

from __future__ import annotations

import pytest

from agent_runtime.capabilities.concurrency import (
    ConcurrencyBounds,
    ConcurrencyPolicy,
    ConcurrencyScope,
    DeclaredRateLimitPolicies,
    PermitAcquisitionRequest,
    PermitCapacityPolicy,
    PermitScope,
    PermitWaitMode,
    RateLimitedPermitScopeFactory,
    RateLimitPoolReason,
    RateLimitScopeIdentity,
    RateLimitScopeResolver,
    RunPermitManager,
)

PROFILE = "single_user_desktop"
SUBJECT = "a" * 64
OTHER_SUBJECT = "b" * 64
CONNECTOR = "linear"
INSTALLATION = "inst-1"
CAPABILITY_REF = "cap_" + "0" * 32
OTHER_REF = "cap_" + "1" * 32


def identity(
    capability_name: str = "list_files",
    *,
    connector_id: str | None = CONNECTOR,
    installation_id: str | None = None,
    subject_fingerprint: str = SUBJECT,
) -> RateLimitScopeIdentity:
    return RateLimitScopeIdentity(
        profile_id=PROFILE,
        subject_fingerprint=subject_fingerprint,
        capability_name=capability_name,
        connector_id=connector_id,
        installation_id=installation_id,
    )


def declaring(scope: ConcurrencyScope, **fields: object) -> ConcurrencyPolicy:
    return ConcurrencyPolicy(rate_limit_scope=scope, **fields)


def kinds(scopes: tuple[PermitScope, ...]) -> set[ConcurrencyScope]:
    return {scope.kind for scope in scopes}


class TestTheDeclarationChoosesThePool:
    """The closing evidence: identity held constant, only the declaration moves."""

    def test_a_declared_connector_scope_adds_the_connector_pool(self) -> None:
        """THE (a) TEST — the same operation acquires a different pool set.

        Both calls carry the identical identity, connector id included. The only
        difference is what the capability declared, and that alone decides
        whether the connector pool is acquired.
        """

        resolver = RateLimitScopeResolver()

        undeclared = resolver.decide(ConcurrencyPolicy(), identity())
        declared = resolver.decide(declaring(ConcurrencyScope.CONNECTOR), identity())

        assert ConcurrencyScope.CONNECTOR not in kinds(undeclared.scopes)
        assert ConcurrencyScope.CONNECTOR in kinds(declared.scopes)
        assert declared.reason is RateLimitPoolReason.DECLARED_POOL_ADDED
        assert undeclared.reason is RateLimitPoolReason.ALREADY_LADDERED

    def test_a_declared_installation_scope_adds_the_installation_pool(self) -> None:
        resolver = RateLimitScopeResolver()

        decision = resolver.decide(
            declaring(ConcurrencyScope.INSTALLATION),
            identity(installation_id=INSTALLATION),
        )

        assert ConcurrencyScope.INSTALLATION in kinds(decision.scopes)
        assert decision.pool is ConcurrencyScope.INSTALLATION

    def test_two_capabilities_declaring_one_connector_now_contend(self) -> None:
        """The behavioural consequence, observed on the real permit table.

        Two *different* capabilities on one connector share no pool under the
        fixed ladder — each has its own capability pool and the broad pools are
        configured wide. Declaring a connector rate limit is what makes them
        contend, which is what a rate limit at connector scope means.
        """

        resolver = RateLimitScopeResolver()
        capacities = PermitCapacityPolicy.from_limits(
            {
                ConcurrencyScope.GLOBAL: 8,
                ConcurrencyScope.PROFILE: 8,
                ConcurrencyScope.USER: 8,
                ConcurrencyScope.CAPABILITY: 8,
                ConcurrencyScope.CONNECTOR: 1,
            }
        )
        undeclared = tuple(
            resolver.request_for(ConcurrencyPolicy(), identity(name))
            for name in ("list_files", "read_file")
        )
        declared = tuple(
            resolver.request_for(declaring(ConcurrencyScope.CONNECTOR), identity(name))
            for name in ("list_files", "read_file")
        )

        assert self._admitted(capacities, undeclared) == 2
        assert self._admitted(capacities, declared) == 1

    @staticmethod
    def _admitted(
        capacities: PermitCapacityPolicy,
        requests: tuple[PermitAcquisitionRequest, ...],
    ) -> int:
        import asyncio

        async def run() -> int:
            manager = RunPermitManager(policy=capacities)
            leases = [await manager.acquire_lease(request) for request in requests]
            return sum(1 for lease in leases if lease.admitted)

        return asyncio.run(run())


class TestNarrowingOnly:
    """A declaration may make an operation more bounded and never less."""

    @pytest.mark.parametrize("scope", list(ConcurrencyScope))
    def test_no_declaration_can_remove_a_ladder_rung(
        self, scope: ConcurrencyScope
    ) -> None:
        resolver = RateLimitScopeResolver()
        base = kinds(resolver.decide(ConcurrencyPolicy(), identity()).scopes)

        declared = kinds(resolver.decide(declaring(scope), identity()).scopes)

        assert base <= declared

    @pytest.mark.parametrize("scope", list(ConcurrencyScope))
    def test_no_declaration_can_raise_the_callers_width(
        self, scope: ConcurrencyScope
    ) -> None:
        resolver = RateLimitScopeResolver()

        request = resolver.request_for(
            declaring(scope, max_parallelism=ConcurrencyBounds.MAX_PARALLELISM),
            identity(),
            max_parallelism=2,
        )

        assert request.max_parallelism is not None
        assert request.max_parallelism <= 2

    @pytest.mark.parametrize("scope", list(ConcurrencyScope))
    def test_no_declaration_can_raise_the_effective_capacity(
        self, scope: ConcurrencyScope
    ) -> None:
        """Measured on the permit table, not inferred from the request shape."""

        resolver = RateLimitScopeResolver()
        capacities = PermitCapacityPolicy.from_limits(
            {kind: 4 for kind in ConcurrencyScope.permit_pool_kinds()}
        )
        manager = RunPermitManager(policy=capacities)
        base = manager.effective_capacity(
            resolver.request_for(ConcurrencyPolicy(), identity())
        )

        declared = manager.effective_capacity(
            resolver.request_for(declaring(scope), identity())
        )

        assert declared <= base

    def test_a_declared_bound_narrows_the_requested_width(self) -> None:
        resolver = RateLimitScopeResolver()

        request = resolver.request_for(
            declaring(ConcurrencyScope.CONNECTOR, max_parallelism=2),
            identity(),
            max_parallelism=8,
        )

        assert request.max_parallelism == 2


class TestUnidentifiablePoolsAreSerial:
    """Unknown means serial, structurally, on the new path too."""

    def test_a_connector_declaration_without_a_connector_is_serial(self) -> None:
        resolver = RateLimitScopeResolver()

        decision = resolver.decide(
            declaring(ConcurrencyScope.CONNECTOR),
            identity(connector_id=None),
        )

        assert decision.pool is None
        assert decision.reason is RateLimitPoolReason.POOL_UNIDENTIFIABLE
        assert decision.max_parallelism == ConcurrencyBounds.SERIAL_PARALLELISM

    def test_an_installation_declaration_without_an_installation_is_serial(
        self,
    ) -> None:
        resolver = RateLimitScopeResolver()

        request = resolver.request_for(
            declaring(ConcurrencyScope.INSTALLATION),
            identity(installation_id=None),
        )

        assert request.max_parallelism == ConcurrencyBounds.SERIAL_PARALLELISM

    def test_the_serial_bound_survives_a_wider_caller_ceiling(self) -> None:
        resolver = RateLimitScopeResolver()

        request = resolver.request_for(
            declaring(ConcurrencyScope.CONNECTOR, max_parallelism=8),
            identity(connector_id=None),
            max_parallelism=ConcurrencyBounds.MAX_PARALLELISM,
        )

        assert request.max_parallelism == ConcurrencyBounds.SERIAL_PARALLELISM

    async def test_a_serial_bound_admits_exactly_one(self) -> None:
        resolver = RateLimitScopeResolver()
        manager = RunPermitManager(
            policy=PermitCapacityPolicy.from_limits(
                {kind: 8 for kind in ConcurrencyScope.permit_pool_kinds()}
            )
        )
        request = resolver.request_for(
            declaring(ConcurrencyScope.CONNECTOR),
            identity(connector_id=None),
        )

        first = await manager.acquire_lease(request)
        second = await manager.acquire_lease(request)

        assert first.admitted
        assert not second.admitted


class TestUndeclaredParity:
    """With no declaration the request is the one the fixed ladder produced."""

    def test_the_conservative_policy_reproduces_the_fixed_ladder(self) -> None:
        resolver = RateLimitScopeResolver()
        ladder = PermitAcquisitionRequest.for_operation(
            profile_id=PROFILE,
            subject_fingerprint=SUBJECT,
            capability_name="list_files",
        )

        request = resolver.request_for(ConcurrencyPolicy(), identity())

        assert request.scope_keys() == ladder.scope_keys()
        assert request.max_parallelism is ladder.max_parallelism

    def test_an_unknown_scope_resolves_to_a_rung_the_ladder_already_has(self) -> None:
        resolver = RateLimitScopeResolver()

        decision = resolver.decide(ConcurrencyPolicy(), identity())

        assert decision.declared is ConcurrencyScope.UNKNOWN
        assert decision.pool is ConcurrencyScope.GLOBAL
        assert not decision.bounded_by_declaration

    @pytest.mark.parametrize(
        "scope",
        [
            ConcurrencyScope.GLOBAL,
            ConcurrencyScope.PROFILE,
            ConcurrencyScope.USER,
            ConcurrencyScope.CAPABILITY,
        ],
    )
    def test_pools_the_ladder_already_carries_change_nothing(
        self, scope: ConcurrencyScope
    ) -> None:
        resolver = RateLimitScopeResolver()
        base = resolver.request_for(ConcurrencyPolicy(), identity())

        request = resolver.request_for(declaring(scope), identity())

        assert request.scope_keys() == base.scope_keys()


class TestPoolsStaySubjectQualified:
    """A declared shared pool is still one subject's, never everyone's."""

    def test_two_subjects_declaring_one_connector_do_not_share_it(self) -> None:
        resolver = RateLimitScopeResolver()
        policy = declaring(ConcurrencyScope.CONNECTOR)

        mine = resolver.decide(policy, identity()).scopes
        theirs = resolver.decide(
            policy, identity(subject_fingerprint=OTHER_SUBJECT)
        ).scopes

        connectors = {
            tuple(
                scope.key().digest
                for scope in scopes
                if scope.kind is ConcurrencyScope.CONNECTOR
            )
            for scopes in (mine, theirs)
        }
        assert len(connectors) == 2


class TestTheScopeFactoryIsADropInReplacement:
    """The composition root's identity-only closure keeps working."""

    def factory(
        self, policies: dict[str, ConcurrencyPolicy] | None = None
    ) -> RateLimitedPermitScopeFactory:
        return RateLimitedPermitScopeFactory(
            profile_id=PROFILE,
            subject_fingerprint=SUBJECT,
            policies=DeclaredRateLimitPolicies(policies or {}),
        )

    def test_a_declared_child_acquires_the_declared_pool(self) -> None:
        factory = self.factory({CAPABILITY_REF: declaring(ConcurrencyScope.CONNECTOR)})

        scopes = factory.scopes_for(
            capability_ref=CAPABILITY_REF,
            capability_name="list_files",
            connector_id=CONNECTOR,
        )

        assert ConcurrencyScope.CONNECTOR in kinds(scopes)

    def test_an_unplanned_child_gets_the_fixed_ladder(self) -> None:
        factory = self.factory({CAPABILITY_REF: declaring(ConcurrencyScope.CONNECTOR)})

        scopes = factory.scopes_for(
            capability_ref=None,
            capability_name="list_files",
            connector_id=CONNECTOR,
        )

        assert kinds(scopes) == {
            ConcurrencyScope.GLOBAL,
            ConcurrencyScope.PROFILE,
            ConcurrencyScope.USER,
            ConcurrencyScope.CAPABILITY,
        }

    def test_an_unknown_capability_gets_the_fixed_ladder(self) -> None:
        factory = self.factory({CAPABILITY_REF: declaring(ConcurrencyScope.CONNECTOR)})

        scopes = factory.scopes_for(
            capability_ref=OTHER_REF,
            capability_name="list_files",
            connector_id=CONNECTOR,
        )

        assert ConcurrencyScope.CONNECTOR not in kinds(scopes)

    def test_a_lookup_that_raises_gets_the_fixed_ladder(self) -> None:
        class Exploding:
            def policy_for(self, capability_ref: str) -> ConcurrencyPolicy | None:
                raise RuntimeError("policy store is unreachable")

        factory = RateLimitedPermitScopeFactory(
            profile_id=PROFILE,
            subject_fingerprint=SUBJECT,
            policies=Exploding(),
        )

        scopes = factory.scopes_for(
            capability_ref=CAPABILITY_REF,
            capability_name="list_files",
            connector_id=CONNECTOR,
        )

        assert ConcurrencyScope.CONNECTOR not in kinds(scopes)

    def test_a_lookup_returning_junk_gets_the_fixed_ladder(self) -> None:
        class Junk:
            def policy_for(self, capability_ref: str) -> ConcurrencyPolicy | None:
                return "parallel_safe"  # type: ignore[return-value]

        factory = RateLimitedPermitScopeFactory(
            profile_id=PROFILE,
            subject_fingerprint=SUBJECT,
            policies=Junk(),
        )

        scopes = factory.scopes_for(
            capability_ref=CAPABILITY_REF,
            capability_name="list_files",
        )

        assert ConcurrencyScope.CONNECTOR not in kinds(scopes)


class TestTheFailClosedRuleIsInTheType:
    """A decision that names no pool cannot claim a width above serial."""

    def test_an_unidentifiable_pool_may_not_be_wide(self) -> None:
        from pydantic import ValidationError

        from agent_runtime.capabilities.concurrency.rate_limits import (
            RateLimitPoolDecision,
        )

        with pytest.raises(ValidationError):
            RateLimitPoolDecision(
                declared=ConcurrencyScope.CONNECTOR,
                pool=None,
                reason=RateLimitPoolReason.POOL_UNIDENTIFIABLE,
                scopes=(PermitScope.for_global(),),
                max_parallelism=4,
            )

    def test_unknown_can_never_be_recorded_as_a_pool(self) -> None:
        from pydantic import ValidationError

        from agent_runtime.capabilities.concurrency.rate_limits import (
            RateLimitPoolDecision,
        )

        with pytest.raises(ValidationError):
            RateLimitPoolDecision(
                declared=ConcurrencyScope.UNKNOWN,
                pool=ConcurrencyScope.UNKNOWN,
                reason=RateLimitPoolReason.ALREADY_LADDERED,
                scopes=(PermitScope.for_global(),),
            )


class TestTheRequestBuilderHonoursTheDeclaration:
    """``for_operation`` is the contract-level half of the same join."""

    def test_an_undeclared_request_is_unchanged(self) -> None:
        without = PermitAcquisitionRequest.for_operation(
            profile_id=PROFILE,
            subject_fingerprint=SUBJECT,
            capability_name="list_files",
            connector_id=CONNECTOR,
        )

        declared = PermitAcquisitionRequest.for_operation(
            profile_id=PROFILE,
            subject_fingerprint=SUBJECT,
            capability_name="list_files",
            connector_id=CONNECTOR,
            rate_limit_scope=ConcurrencyScope.UNKNOWN,
        )

        assert declared.scope_keys() == without.scope_keys()
        assert declared.max_parallelism is without.max_parallelism

    def test_a_declared_pool_the_ladder_lacks_is_serial(self) -> None:
        request = PermitAcquisitionRequest.for_operation(
            profile_id=PROFILE,
            subject_fingerprint=SUBJECT,
            capability_name="list_files",
            rate_limit_scope=ConcurrencyScope.CONNECTOR,
            max_parallelism=ConcurrencyBounds.MAX_PARALLELISM,
        )

        assert request.max_parallelism == ConcurrencyBounds.SERIAL_PARALLELISM

    def test_a_declared_pool_the_ladder_has_is_acquired_once(self) -> None:
        request = PermitAcquisitionRequest.for_operation(
            profile_id=PROFILE,
            subject_fingerprint=SUBJECT,
            capability_name="list_files",
            connector_id=CONNECTOR,
            rate_limit_scope=ConcurrencyScope.CONNECTOR,
            wait_mode=PermitWaitMode.REFUSE_IF_SATURATED,
        )

        connectors = [
            scope
            for scope in request.scopes
            if scope.kind is ConcurrencyScope.CONNECTOR
        ]
        assert len(connectors) == 1


class TestForPoolIsTheOneComponentDrivenConstructor:
    """The required-component table stays the single source of truth."""

    @pytest.mark.parametrize("kind", list(ConcurrencyScope.permit_pool_kinds()))
    def test_every_pool_kind_is_buildable_with_its_components(
        self, kind: ConcurrencyScope
    ) -> None:
        scope = PermitScope.for_pool(
            kind,
            profile_id=PROFILE,
            subject_fingerprint=SUBJECT,
            installation_id=INSTALLATION,
            connector_id=CONNECTOR,
            capability_name="list_files",
        )

        assert scope is not None
        assert scope.kind is kind

    def test_unknown_names_no_pool(self) -> None:
        assert PermitScope.required_components(ConcurrencyScope.UNKNOWN) is None
        assert (
            PermitScope.for_pool(
                ConcurrencyScope.UNKNOWN,
                profile_id=PROFILE,
                subject_fingerprint=SUBJECT,
            )
            is None
        )

    def test_a_missing_component_answers_none_rather_than_raising(self) -> None:
        assert (
            PermitScope.for_pool(
                ConcurrencyScope.CONNECTOR,
                profile_id=PROFILE,
                subject_fingerprint=SUBJECT,
            )
            is None
        )
