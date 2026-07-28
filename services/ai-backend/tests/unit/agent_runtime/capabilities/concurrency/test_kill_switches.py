"""F6.7 contract tests: serial kill switches may only ever narrow."""

from __future__ import annotations

from itertools import permutations

import pytest
from pydantic import ValidationError

from agent_runtime.capabilities.concurrency.kill_switches import (
    MAX_PARALLELISM,
    SERIAL_PARALLELISM,
    ConcurrencyAllowance,
    ConcurrencyKillSwitchDecision,
    ConcurrencyKillSwitchDirectives,
    ConcurrencyKillSwitchGate,
    ConcurrencyKillSwitchReason,
    ConcurrencyKillSwitchResolver,
    ConcurrencyKillSwitchScope,
    ConcurrencyKillSwitchSourcePort,
    ConcurrencyKillSwitchSourceStatus,
    ConcurrencyKillSwitchTarget,
    ConcurrencyKillSwitchTargetError,
)
from agent_runtime.control_plane.feature_modes import (
    AgentQualityFeature,
    FeatureFallback,
    FeatureMode,
    feature_mode_policy,
)


class MutableKillSwitchSource:
    """Trusted adapter stand-in whose value an operator can flip mid-run."""

    def __init__(self, raw: object = None) -> None:
        self._raw = raw
        self.reads = 0

    def flip_to(self, raw: object) -> None:
        self._raw = raw

    def current_kill_switch_directives(self) -> object:
        self.reads += 1
        return self._raw


class UnreadableKillSwitchSource:
    """Switch source that cannot answer, e.g. an unreachable control store."""

    def current_kill_switch_directives(self) -> object:
        raise RuntimeError("kill-switch store unreachable: host=control-1 db=ops")


class KillSwitchFixturesMixin:
    """Shared builders for snapshot allowances, targets, and directive sets."""

    CONNECTOR_ID = "acme-mail"
    CAPABILITY_ID = "mail.search"
    OTHER_CONNECTOR_ID = "acme-drive"
    OTHER_CAPABILITY_ID = "drive.search"
    SECRET_CONNECTOR_ID = "tenant-9f3c-secretbearing"

    @staticmethod
    def parallel_snapshot(width: int = 4) -> ConcurrencyAllowance:
        return ConcurrencyAllowance(
            mode=FeatureMode.ENFORCE,
            max_parallelism=width,
        )

    @staticmethod
    def serial_snapshot() -> ConcurrencyAllowance:
        return ConcurrencyAllowance.serial()

    @staticmethod
    def resolver() -> ConcurrencyKillSwitchResolver:
        return ConcurrencyKillSwitchResolver()

    @classmethod
    def directives(cls, *raw_elements: object) -> ConcurrencyKillSwitchDirectives:
        return ConcurrencyKillSwitchDirectives.parse(list(raw_elements))

    @classmethod
    def decide(
        cls,
        *raw_elements: object,
        snapshot: ConcurrencyAllowance | None = None,
        connector_id: object = None,
        capability_id: object = None,
    ) -> ConcurrencyKillSwitchDecision:
        return cls.resolver().resolve(
            snapshot_allowance=snapshot or cls.parallel_snapshot(),
            directives=cls.directives(*raw_elements),
            connector_id=connector_id,
            capability_id=capability_id,
        )

    @classmethod
    def scoped_decide(
        cls,
        *raw_elements: object,
        snapshot: ConcurrencyAllowance | None = None,
    ) -> ConcurrencyKillSwitchDecision:
        return cls.decide(
            *raw_elements,
            snapshot=snapshot,
            connector_id=cls.CONNECTOR_ID,
            capability_id=cls.CAPABILITY_ID,
        )


class TestTargetIdentity(KillSwitchFixturesMixin):
    def test_scopes_are_closed_and_only_global_omits_an_identifier(self) -> None:
        assert {scope.value for scope in ConcurrencyKillSwitchScope} == {
            "global",
            "connector",
            "capability",
        }
        assert not ConcurrencyKillSwitchScope.GLOBAL.requires_identifier
        assert ConcurrencyKillSwitchScope.CONNECTOR.requires_identifier
        assert ConcurrencyKillSwitchScope.CAPABILITY.requires_identifier
        assert ConcurrencyKillSwitchTarget.global_().identifier is None

    def test_identifiers_are_normalized_before_becoming_a_key(self) -> None:
        target = ConcurrencyKillSwitchTarget.for_connector("  ACME-Mail  ")

        assert target.identifier == self.CONNECTOR_ID
        assert target.lookup_key == f"connector:{self.CONNECTOR_ID}"
        assert target == ConcurrencyKillSwitchTarget.for_connector(self.CONNECTOR_ID)
        assert ConcurrencyKillSwitchTarget.parse(target.lookup_key) == target

    @pytest.mark.parametrize(
        "unusable",
        [
            "",
            "   ",
            "https://mail.example.com/api?token=abc",
            "acme mail",
            "-leading-dash",
            "a" * (129),
            None,
            17,
            ("acme-mail",),
        ],
    )
    def test_unusable_identifiers_never_become_targets(self, unusable: object) -> None:
        assert (
            ConcurrencyKillSwitchTarget.for_scope(
                ConcurrencyKillSwitchScope.CONNECTOR,
                unusable,
            )
            is None
        )
        with pytest.raises(ConcurrencyKillSwitchTargetError) as excinfo:
            ConcurrencyKillSwitchTarget.for_connector(unusable)

        assert excinfo.value.scope is ConcurrencyKillSwitchScope.CONNECTOR
        assert str(excinfo.value) == "invalid connector kill-switch target identity"

    def test_directive_documents_use_a_closed_key_vocabulary(self) -> None:
        parsed = ConcurrencyKillSwitchTarget.parse(
            {"scope": "capability", "identifier": self.CAPABILITY_ID}
        )

        assert parsed == ConcurrencyKillSwitchTarget.for_capability(self.CAPABILITY_ID)
        assert ConcurrencyKillSwitchTarget.parse({"scope": "global"}) == (
            ConcurrencyKillSwitchTarget.global_()
        )
        assert (
            ConcurrencyKillSwitchTarget.parse(
                {"scope": "connector", "identifier": self.CONNECTOR_ID, "extra": 1}
            )
            is None
        )
        assert ConcurrencyKillSwitchTarget.parse({"identifier": "x"}) is None
        assert ConcurrencyKillSwitchTarget.parse({"scope": "region"}) is None
        assert (
            ConcurrencyKillSwitchTarget.parse(
                {"scope": "global", "identifier": self.CONNECTOR_ID}
            )
            is None
        )

    def test_bare_and_unknown_scope_tokens_fail_closed(self) -> None:
        assert ConcurrencyKillSwitchTarget.parse(self.CONNECTOR_ID) is None
        assert ConcurrencyKillSwitchTarget.parse("connector") is None
        assert ConcurrencyKillSwitchTarget.parse("region:eu") is None
        assert ConcurrencyKillSwitchTarget.parse("global:anything") is None
        assert ConcurrencyKillSwitchTarget.parse(b"global") is None
        assert ConcurrencyKillSwitchTarget.parse("GLOBAL") == (
            ConcurrencyKillSwitchTarget.global_()
        )

    def test_targets_are_hashable_closed_values(self) -> None:
        targets = {
            ConcurrencyKillSwitchTarget.global_(),
            ConcurrencyKillSwitchTarget.global_(),
            ConcurrencyKillSwitchTarget.for_connector(self.CONNECTOR_ID),
        }

        assert len(targets) == 2
        with pytest.raises(ValidationError):
            ConcurrencyKillSwitchTarget(
                scope=ConcurrencyKillSwitchScope.CONNECTOR,
                identifier=None,
            )


class TestAllowanceNarrowing(KillSwitchFixturesMixin):
    def test_only_enforce_with_width_permits_parallel(self) -> None:
        assert self.parallel_snapshot().permits_parallel
        assert not ConcurrencyAllowance(
            mode=FeatureMode.SHADOW,
            max_parallelism=8,
        ).permits_parallel
        assert not ConcurrencyAllowance(
            mode=FeatureMode.ENFORCE,
            max_parallelism=SERIAL_PARALLELISM,
        ).permits_parallel
        assert ConcurrencyAllowance.serial().is_serial
        assert (
            ConcurrencyAllowance(
                mode=FeatureMode.SHADOW,
                max_parallelism=8,
            ).effective_max_parallelism
            == SERIAL_PARALLELISM
        )

    def test_composition_never_widens_and_is_order_independent(self) -> None:
        allowances = [
            ConcurrencyAllowance(mode=mode, max_parallelism=width)
            for mode in FeatureMode
            for width in (SERIAL_PARALLELISM, 2, 5, MAX_PARALLELISM)
        ]

        for left in allowances:
            for right in allowances:
                combined = left.narrowed_by(right)
                assert combined.mode.rank <= min(left.mode.rank, right.mode.rank)
                assert combined.max_parallelism <= min(
                    left.max_parallelism,
                    right.max_parallelism,
                )
                assert combined == right.narrowed_by(left)
                assert combined == combined.narrowed_by(combined)
                assert combined.effective_max_parallelism <= (
                    left.effective_max_parallelism
                )

    def test_forcing_serial_matches_the_f6_safe_fallback(self) -> None:
        forced = self.parallel_snapshot().narrowed_to_serial()

        assert forced == ConcurrencyAllowance.serial()
        assert forced.mode is FeatureMode.OFF
        assert forced.effective_max_parallelism == SERIAL_PARALLELISM
        assert (
            feature_mode_policy(
                AgentQualityFeature.F6_CAPABILITY_CONCURRENCY
            ).safe_fallback
            is FeatureFallback.SERIAL
        )

    def test_allowance_ceiling_is_bounded_like_the_planner_contracts(self) -> None:
        with pytest.raises(ValidationError):
            ConcurrencyAllowance(mode=FeatureMode.ENFORCE, max_parallelism=0)
        with pytest.raises(ValidationError):
            ConcurrencyAllowance(
                mode=FeatureMode.ENFORCE,
                max_parallelism=MAX_PARALLELISM + 1,
            )


class TestDirectiveParsing(KillSwitchFixturesMixin):
    def test_absent_configuration_leaves_the_snapshot_alone(self) -> None:
        for raw in (None, "", "   "):
            parsed = ConcurrencyKillSwitchDirectives.parse(raw)
            assert parsed.status is ConcurrencyKillSwitchSourceStatus.ABSENT
            assert not parsed.forces_serial_everywhere
            assert not parsed.asserts(ConcurrencyKillSwitchTarget.global_())

    def test_json_and_iterable_forms_produce_the_same_validated_set(self) -> None:
        expected = {
            ConcurrencyKillSwitchTarget.global_(),
            ConcurrencyKillSwitchTarget.for_connector(self.CONNECTOR_ID),
        }
        from_json = ConcurrencyKillSwitchDirectives.parse(
            '["global", {"scope": "connector", "identifier": "acme-mail"}]'
        )
        from_iterable = ConcurrencyKillSwitchDirectives.parse(
            (
                ConcurrencyKillSwitchTarget.global_(),
                f"connector:{self.CONNECTOR_ID}",
            )
        )

        assert from_json.status is ConcurrencyKillSwitchSourceStatus.AVAILABLE
        assert set(from_json.targets) == expected
        assert set(from_iterable.targets) == expected

    def test_an_empty_configured_list_kills_nothing(self) -> None:
        parsed = ConcurrencyKillSwitchDirectives.parse([])

        assert parsed.status is ConcurrencyKillSwitchSourceStatus.AVAILABLE
        assert not parsed.targets
        assert not parsed.forces_serial_everywhere

    @pytest.mark.parametrize(
        "unparseable",
        [
            "{not json",
            '{"scope": "global"}',
            '"global"',
            b"[]",
            {"scope": "global"},
            17,
            object(),
        ],
    )
    def test_unparseable_configuration_fails_closed(self, unparseable: object) -> None:
        parsed = ConcurrencyKillSwitchDirectives.parse(unparseable)

        assert parsed.status is ConcurrencyKillSwitchSourceStatus.UNPARSEABLE
        assert parsed.forces_serial_everywhere
        assert not parsed.targets

    def test_one_bad_element_invalidates_the_whole_set(self) -> None:
        parsed = ConcurrencyKillSwitchDirectives.parse(
            ["global", "connector:", f"capability:{self.CAPABILITY_ID}"]
        )

        assert parsed.status is ConcurrencyKillSwitchSourceStatus.UNPARSEABLE
        assert not parsed.targets

    def test_oversized_directive_sets_fail_closed(self) -> None:
        parsed = ConcurrencyKillSwitchDirectives.parse(
            [f"connector:c{index}" for index in range(257)]
        )

        assert parsed.status is ConcurrencyKillSwitchSourceStatus.UNPARSEABLE

    def test_only_an_available_set_may_carry_targets(self) -> None:
        with pytest.raises(ValidationError):
            ConcurrencyKillSwitchDirectives(
                status=ConcurrencyKillSwitchSourceStatus.UNPARSEABLE,
                targets=frozenset({ConcurrencyKillSwitchTarget.global_()}),
            )


class TestEachScopeForcesSerial(KillSwitchFixturesMixin):
    def test_global_switch_forces_serial(self) -> None:
        decision = self.scoped_decide("global")

        assert not decision.permits_parallel
        assert decision.max_parallelism == SERIAL_PARALLELISM
        assert decision.serial_forced
        assert decision.reason is ConcurrencyKillSwitchReason.GLOBAL_KILL_SWITCH
        assert decision.narrowed_by_scope is ConcurrencyKillSwitchScope.GLOBAL

    def test_connector_switch_forces_serial_only_for_that_connector(self) -> None:
        killed = self.scoped_decide(f"connector:{self.CONNECTOR_ID}")
        untouched = self.decide(
            f"connector:{self.CONNECTOR_ID}",
            connector_id=self.OTHER_CONNECTOR_ID,
            capability_id=self.CAPABILITY_ID,
        )

        assert not killed.permits_parallel
        assert killed.reason is ConcurrencyKillSwitchReason.CONNECTOR_KILL_SWITCH
        assert killed.narrowed_by_scope is ConcurrencyKillSwitchScope.CONNECTOR
        assert untouched.permits_parallel
        assert untouched.reason is ConcurrencyKillSwitchReason.SNAPSHOT_GOVERNS
        assert untouched.narrowed_by_scope is None

    def test_capability_switch_forces_serial_only_for_that_capability(self) -> None:
        killed = self.scoped_decide(f"capability:{self.CAPABILITY_ID}")
        untouched = self.decide(
            f"capability:{self.CAPABILITY_ID}",
            connector_id=self.CONNECTOR_ID,
            capability_id=self.OTHER_CAPABILITY_ID,
        )

        assert not killed.permits_parallel
        assert killed.reason is ConcurrencyKillSwitchReason.CAPABILITY_KILL_SWITCH
        assert killed.narrowed_by_scope is ConcurrencyKillSwitchScope.CAPABILITY
        assert untouched.permits_parallel

    def test_a_dimension_the_decision_lacks_cannot_be_narrowed_by_accident(
        self,
    ) -> None:
        decision = self.decide(
            f"connector:{self.CONNECTOR_ID}",
            capability_id=self.CAPABILITY_ID,
        )

        assert decision.permits_parallel
        assert decision.reason is ConcurrencyKillSwitchReason.SNAPSHOT_GOVERNS


class TestPrecedenceAndOrderIndependence(KillSwitchFixturesMixin):
    def test_narrowest_wins_when_scopes_disagree(self) -> None:
        decision = self.scoped_decide(
            "global",
            f"connector:{self.CONNECTOR_ID}",
            f"capability:{self.CAPABILITY_ID}",
        )

        assert not decision.permits_parallel
        assert decision.max_parallelism == SERIAL_PARALLELISM
        assert decision.reason is ConcurrencyKillSwitchReason.GLOBAL_KILL_SWITCH

    def test_outcome_and_reason_are_independent_of_directive_order(self) -> None:
        elements = (
            f"capability:{self.CAPABILITY_ID}",
            "global",
            f"connector:{self.CONNECTOR_ID}",
        )
        decisions = {
            self.scoped_decide(*ordering) for ordering in permutations(elements)
        }

        assert len(decisions) == 1
        assert decisions.pop().reason is (
            ConcurrencyKillSwitchReason.GLOBAL_KILL_SWITCH
        )

    def test_attribution_falls_to_the_broadest_asserted_scope(self) -> None:
        connector_and_capability = self.scoped_decide(
            f"capability:{self.CAPABILITY_ID}",
            f"connector:{self.CONNECTOR_ID}",
        )

        assert connector_and_capability.reason is (
            ConcurrencyKillSwitchReason.CONNECTOR_KILL_SWITCH
        )
        assert ConcurrencyKillSwitchScope.GLOBAL.precedence < (
            ConcurrencyKillSwitchScope.CONNECTOR.precedence
        )
        assert ConcurrencyKillSwitchScope.CONNECTOR.precedence < (
            ConcurrencyKillSwitchScope.CAPABILITY.precedence
        )


class TestNarrowingOnly(KillSwitchFixturesMixin):
    def test_no_switch_combination_can_widen_a_serial_snapshot(self) -> None:
        elements = (
            "global",
            f"connector:{self.CONNECTOR_ID}",
            f"capability:{self.CAPABILITY_ID}",
        )
        raw_sources: list[object] = [None, "", [], "not-json", 17]
        raw_sources.extend(list(ordering) for ordering in permutations(elements))
        raw_sources.extend([element] for element in elements)

        for raw in raw_sources:
            decision = self.resolver().resolve(
                snapshot_allowance=self.serial_snapshot(),
                directives=ConcurrencyKillSwitchDirectives.parse(raw),
                connector_id=self.CONNECTOR_ID,
                capability_id=self.CAPABILITY_ID,
            )
            assert not decision.permits_parallel
            assert decision.max_parallelism == SERIAL_PARALLELISM
            assert decision.effective_allowance.mode.rank <= (
                decision.snapshot_allowance.mode.rank
            )
            assert not decision.serial_forced

    def test_a_switch_never_raises_the_snapshot_ceiling(self) -> None:
        narrow_snapshot = self.parallel_snapshot(width=2)

        decision = self.resolver().resolve(
            snapshot_allowance=narrow_snapshot,
            directives=ConcurrencyKillSwitchDirectives.parse([]),
            connector_id=self.CONNECTOR_ID,
        )

        assert decision.max_parallelism == 2
        assert decision.effective_allowance == narrow_snapshot

    def test_the_decision_contract_refuses_to_broaden_the_snapshot(self) -> None:
        with pytest.raises(ValidationError):
            ConcurrencyKillSwitchDecision(
                snapshot_allowance=ConcurrencyAllowance.serial(),
                effective_allowance=self.parallel_snapshot(),
                reason=ConcurrencyKillSwitchReason.SNAPSHOT_GOVERNS,
            )
        with pytest.raises(ValidationError):
            ConcurrencyKillSwitchDecision(
                snapshot_allowance=self.parallel_snapshot(width=2),
                effective_allowance=self.parallel_snapshot(width=8),
                reason=ConcurrencyKillSwitchReason.SNAPSHOT_GOVERNS,
            )
        with pytest.raises(ValidationError):
            ConcurrencyKillSwitchDecision(
                snapshot_allowance=self.parallel_snapshot(),
                effective_allowance=ConcurrencyAllowance.serial(),
                reason=ConcurrencyKillSwitchReason.GLOBAL_KILL_SWITCH,
                narrowed_by_scope=ConcurrencyKillSwitchScope.CONNECTOR,
            )


class TestMidRunEffectiveness(KillSwitchFixturesMixin):
    def test_a_switch_flipped_mid_run_binds_the_next_decision_only(self) -> None:
        source = MutableKillSwitchSource()
        gate = ConcurrencyKillSwitchGate(
            snapshot_allowance=self.parallel_snapshot(),
            source=source,
        )

        admitted = gate.admit(
            connector_id=self.CONNECTOR_ID,
            capability_id=self.CAPABILITY_ID,
        )
        source.flip_to(["global"])
        after_flip = gate.admit(
            connector_id=self.CONNECTOR_ID,
            capability_id=self.CAPABILITY_ID,
        )

        assert admitted.permits_parallel
        assert admitted.max_parallelism == 4
        assert admitted.reason is ConcurrencyKillSwitchReason.SNAPSHOT_GOVERNS
        assert not after_flip.permits_parallel
        assert after_flip.reason is ConcurrencyKillSwitchReason.GLOBAL_KILL_SWITCH
        assert source.reads == 2

    def test_work_already_admitted_is_never_retroactively_invalidated(self) -> None:
        source = MutableKillSwitchSource()
        gate = ConcurrencyKillSwitchGate(
            snapshot_allowance=self.parallel_snapshot(),
            source=source,
        )

        completed = gate.admit(capability_id=self.CAPABILITY_ID)
        source.flip_to([f"capability:{self.CAPABILITY_ID}"])
        gate.admit(capability_id=self.CAPABILITY_ID)

        assert completed.permits_parallel
        assert completed.max_parallelism == 4
        assert completed.reason is ConcurrencyKillSwitchReason.SNAPSHOT_GOVERNS
        with pytest.raises(ValidationError):
            completed.effective_allowance.max_parallelism = SERIAL_PARALLELISM

    def test_clearing_a_switch_restores_at_most_the_frozen_snapshot(self) -> None:
        source = MutableKillSwitchSource(["global"])
        gate = ConcurrencyKillSwitchGate(
            snapshot_allowance=self.parallel_snapshot(width=2),
            source=source,
        )

        killed = gate.admit(connector_id=self.CONNECTOR_ID)
        source.flip_to([])
        restored = gate.admit(connector_id=self.CONNECTOR_ID)

        assert not killed.permits_parallel
        assert restored.permits_parallel
        assert restored.max_parallelism == 2
        assert restored.effective_allowance == gate.snapshot_allowance

    def test_a_serial_run_snapshot_stays_serial_when_a_switch_clears(self) -> None:
        source = MutableKillSwitchSource(["global"])
        gate = ConcurrencyKillSwitchGate(
            snapshot_allowance=self.serial_snapshot(),
            source=source,
        )

        source.flip_to([])
        restored = gate.admit(connector_id=self.CONNECTOR_ID)

        assert not restored.permits_parallel
        assert restored.reason is (ConcurrencyKillSwitchReason.SNAPSHOT_ALREADY_SERIAL)


class TestFailClosed(KillSwitchFixturesMixin):
    def test_an_unreadable_source_resolves_to_serial(self) -> None:
        gate = ConcurrencyKillSwitchGate(
            snapshot_allowance=self.parallel_snapshot(),
            source=UnreadableKillSwitchSource(),
        )

        decision = gate.admit(connector_id=self.CONNECTOR_ID)

        assert not decision.permits_parallel
        assert decision.serial_forced
        assert decision.reason is (
            ConcurrencyKillSwitchReason.SWITCH_SOURCE_UNAVAILABLE
        )

    def test_unparseable_live_configuration_resolves_to_serial(self) -> None:
        gate = ConcurrencyKillSwitchGate(
            snapshot_allowance=self.parallel_snapshot(),
            source=MutableKillSwitchSource("{not-json"),
        )

        decision = gate.admit(connector_id=self.CONNECTOR_ID)

        assert not decision.permits_parallel
        assert decision.reason is (
            ConcurrencyKillSwitchReason.UNPARSEABLE_SWITCH_CONFIG
        )

    @pytest.mark.parametrize(
        "unusable",
        ["", "   ", "https://mail.example.com/inbox?token=abc", 17],
    )
    def test_an_unusable_request_target_resolves_to_serial(
        self,
        unusable: object,
    ) -> None:
        decision = self.decide(
            "global",
            connector_id=unusable,
        )

        assert not decision.permits_parallel
        assert decision.reason is ConcurrencyKillSwitchReason.UNKNOWN_TARGET
        assert decision.narrowed_by_scope is None

    def test_an_unusable_capability_target_resolves_to_serial(self) -> None:
        decision = self.decide(
            connector_id=self.CONNECTOR_ID,
            capability_id="mail search",
        )

        assert not decision.permits_parallel
        assert decision.reason is ConcurrencyKillSwitchReason.UNKNOWN_TARGET

    def test_no_configured_source_leaves_the_snapshot_authoritative(self) -> None:
        gate = ConcurrencyKillSwitchGate(snapshot_allowance=self.parallel_snapshot())

        decision = gate.admit(connector_id=self.CONNECTOR_ID)

        assert decision.permits_parallel
        assert decision.reason is ConcurrencyKillSwitchReason.SNAPSHOT_GOVERNS

    def test_the_source_protocol_is_structural(self) -> None:
        assert isinstance(MutableKillSwitchSource(), ConcurrencyKillSwitchSourcePort)
        assert not isinstance(object(), ConcurrencyKillSwitchSourcePort)


class TestContentFreeDiagnostics(KillSwitchFixturesMixin):
    def test_reason_codes_are_a_stable_closed_low_cardinality_set(self) -> None:
        assert {reason.value for reason in ConcurrencyKillSwitchReason} == {
            "snapshot_governs",
            "snapshot_already_serial",
            "global_kill_switch",
            "connector_kill_switch",
            "capability_kill_switch",
            "unknown_target",
            "unparseable_switch_config",
            "switch_source_unavailable",
        }
        assert {
            ConcurrencyKillSwitchReason.for_scope(scope)
            for scope in ConcurrencyKillSwitchScope
        } == {
            ConcurrencyKillSwitchReason.GLOBAL_KILL_SWITCH,
            ConcurrencyKillSwitchReason.CONNECTOR_KILL_SWITCH,
            ConcurrencyKillSwitchReason.CAPABILITY_KILL_SWITCH,
        }

    def test_a_decision_never_carries_the_target_identity(self) -> None:
        decision = self.decide(
            f"connector:{self.SECRET_CONNECTOR_ID}",
            connector_id=self.SECRET_CONNECTOR_ID,
            capability_id=self.CAPABILITY_ID,
        )
        serialized = decision.model_dump_json()

        assert decision.reason is ConcurrencyKillSwitchReason.CONNECTOR_KILL_SWITCH
        assert self.SECRET_CONNECTOR_ID not in serialized
        assert self.SECRET_CONNECTOR_ID not in repr(decision)
        assert self.CAPABILITY_ID not in serialized
        assert set(decision.model_dump()) == {
            "snapshot_allowance",
            "effective_allowance",
            "reason",
            "narrowed_by_scope",
        }

    def test_an_unreadable_source_never_leaks_its_failure_detail(self) -> None:
        gate = ConcurrencyKillSwitchGate(
            snapshot_allowance=self.parallel_snapshot(),
            source=UnreadableKillSwitchSource(),
        )

        decision = gate.admit(connector_id=self.CONNECTOR_ID)

        assert "control-1" not in repr(decision)
        assert "control-1" not in decision.model_dump_json()

    def test_an_unparseable_document_is_never_echoed(self) -> None:
        directives = ConcurrencyKillSwitchDirectives.parse(
            '["connector:tenant-9f3c-secretbearing", "not a target"]'
        )

        assert directives.status is ConcurrencyKillSwitchSourceStatus.UNPARSEABLE
        assert self.SECRET_CONNECTOR_ID not in repr(directives)
        assert self.SECRET_CONNECTOR_ID not in directives.model_dump_json()
