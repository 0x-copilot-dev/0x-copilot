from __future__ import annotations

import asyncio
from collections.abc import Mapping

import pytest
from pydantic import ValidationError

from agent_runtime.capabilities.concurrency import (
    ConcurrencyScope,
    PermitAcquisitionRequest,
    PermitCapacityPolicy,
    PermitDoubleReleaseError,
    PermitEventLoopMismatchError,
    PermitLease,
    PermitNotAdmittedError,
    PermitOutcome,
    PermitScope,
    PermitWaitMode,
    RunPermitManager,
)

SUBJECT_A = "a" * 64
SUBJECT_B = "b" * 64
PROFILE = "single_user_desktop"


class PermitFixtureMixin:
    """Deterministic builders shared by every permit test."""

    def policy(self, **limits: int) -> PermitCapacityPolicy:
        mapping: dict[ConcurrencyScope, int] = {
            ConcurrencyScope(kind): value for kind, value in limits.items()
        }
        return PermitCapacityPolicy.from_limits(mapping)

    def manager(self, **limits: int) -> RunPermitManager:
        return RunPermitManager(policy=self.policy(**limits))

    def request(
        self,
        *scopes: PermitScope,
        wait_mode: PermitWaitMode = PermitWaitMode.REFUSE_IF_SATURATED,
        timeout_seconds: float | None = None,
        max_parallelism: int | None = None,
    ) -> PermitAcquisitionRequest:
        return PermitAcquisitionRequest(
            scopes=scopes,
            wait_mode=wait_mode,
            timeout_seconds=timeout_seconds,
            max_parallelism=max_parallelism,
        )

    def queued(
        self,
        *scopes: PermitScope,
        timeout_seconds: float = 5.0,
        max_parallelism: int | None = None,
    ) -> PermitAcquisitionRequest:
        return self.request(
            *scopes,
            wait_mode=PermitWaitMode.QUEUE,
            timeout_seconds=timeout_seconds,
            max_parallelism=max_parallelism,
        )

    def capability(self, name: str, *, subject: str = SUBJECT_A) -> PermitScope:
        return PermitScope.for_capability(
            profile_id=PROFILE,
            subject_fingerprint=subject,
            capability_name=name,
        )

    def connector(self, name: str, *, subject: str = SUBJECT_A) -> PermitScope:
        return PermitScope.for_connector(
            profile_id=PROFILE,
            subject_fingerprint=subject,
            connector_id=name,
        )

    async def drain(self, turns: int = 6) -> None:
        """Yield control without consuming wall-clock time."""

        for _ in range(turns):
            await asyncio.sleep(0)


class ConcurrencyProbeMixin:
    """Observe real overlap instead of inferring it from timing."""

    class Probe:
        def __init__(self) -> None:
            self.current = 0
            self.observed_max = 0
            self.completed = 0

        async def occupy(self, turns: int = 6) -> None:
            self.current += 1
            self.observed_max = max(self.observed_max, self.current)
            for _ in range(turns):
                await asyncio.sleep(0)
            self.current -= 1
            self.completed += 1

    async def run_workers(
        self,
        manager: RunPermitManager,
        requests: tuple[PermitAcquisitionRequest, ...],
    ) -> Probe:
        probe = self.Probe()

        async def worker(request: PermitAcquisitionRequest) -> None:
            async with manager.acquire(request) as lease:
                assert lease.admitted, lease.outcome
                await probe.occupy()

        await asyncio.gather(*(worker(request) for request in requests))
        return probe


class TestPermitScopeKeys(PermitFixtureMixin):
    def test_same_logical_scope_produces_the_same_key(self) -> None:
        first = self.capability("search_library")
        second = PermitScope.for_capability(
            profile_id=PROFILE,
            subject_fingerprint=SUBJECT_A,
            capability_name="search_library",
        )

        assert first.key() == second.key()
        assert first.key().token == second.key().token

    def test_keys_are_content_free_digests(self) -> None:
        key = self.connector("google-drive").key()

        assert key.kind is ConcurrencyScope.CONNECTOR
        assert len(key.digest) == 64
        assert key.digest == key.digest.lower()
        assert "google-drive" not in key.token
        assert SUBJECT_A not in key.digest
        assert PROFILE not in key.token

    def test_different_kinds_never_collide(self) -> None:
        connector_key = self.connector("acme").key()
        capability_key = self.capability("acme").key()

        assert connector_key.kind is not capability_key.kind
        assert connector_key.digest != capability_key.digest

    def test_different_subjects_and_profiles_never_share_a_key(self) -> None:
        subject_a = self.connector("acme", subject=SUBJECT_A).key()
        subject_b = self.connector("acme", subject=SUBJECT_B).key()
        other_profile = PermitScope.for_connector(
            profile_id="self_host",
            subject_fingerprint=SUBJECT_A,
            connector_id="acme",
        ).key()

        assert len({subject_a.digest, subject_b.digest, other_profile.digest}) == 3

    def test_every_narrow_scope_is_subject_and_profile_qualified(self) -> None:
        for kind in ConcurrencyScope.permit_pool_kinds():
            required = PermitScope._REQUIRED_COMPONENTS[kind]
            if kind is ConcurrencyScope.GLOBAL:
                assert required == ()
                continue
            assert PermitScope.Keys.PROFILE_ID in required
            if kind is not ConcurrencyScope.PROFILE:
                assert PermitScope.Keys.SUBJECT_FINGERPRINT in required

    def test_scope_rejects_urls_paths_and_free_text(self) -> None:
        for hostile in (
            "https://drive.example.com/v3/files",
            "/Users/someone/Documents/secret.txt",
            "acme connector",
            "user@example.com",
        ):
            with pytest.raises(ValidationError):
                self.connector(hostile)

    def test_scope_rejects_raw_subject_identity(self) -> None:
        with pytest.raises(ValidationError):
            PermitScope.for_user(
                profile_id=PROFILE,
                subject_fingerprint="sarah_acme",
            )

    def test_scope_requires_exactly_its_kind_components(self) -> None:
        with pytest.raises(ValidationError):
            PermitScope(kind=ConcurrencyScope.CONNECTOR, profile_id=PROFILE)
        with pytest.raises(ValidationError):
            PermitScope(kind=ConcurrencyScope.GLOBAL, profile_id=PROFILE)


class TestPermitCapacityPolicy(PermitFixtureMixin):
    def test_absent_or_unknown_capacity_is_serial(self) -> None:
        empty = PermitCapacityPolicy.serial()

        for kind in ConcurrencyScope:
            assert empty.capacity_for(kind) == 1

        partial = self.policy(**{ConcurrencyScope.GLOBAL.value: 8})
        assert partial.capacity_for(ConcurrencyScope.GLOBAL) == 8
        assert partial.capacity_for(ConcurrencyScope.CAPABILITY) == 1

    def test_duplicate_kinds_are_rejected(self) -> None:
        with pytest.raises(ValidationError):
            PermitCapacityPolicy(
                capacities=(
                    {"kind": ConcurrencyScope.GLOBAL, "max_concurrency": 2},
                    {"kind": ConcurrencyScope.GLOBAL, "max_concurrency": 4},
                )
            )

    def test_capacity_is_bounded(self) -> None:
        with pytest.raises(ValidationError):
            PermitCapacityPolicy.from_limits({ConcurrencyScope.GLOBAL: 0})
        with pytest.raises(ValidationError):
            PermitCapacityPolicy.from_limits({ConcurrencyScope.GLOBAL: 64})


class TestPermitRequestValidation(PermitFixtureMixin):
    def test_queueing_requires_a_deadline(self) -> None:
        with pytest.raises(ValidationError):
            self.request(
                self.capability("read"),
                wait_mode=PermitWaitMode.QUEUE,
            )

    def test_refusing_must_not_carry_a_deadline(self) -> None:
        with pytest.raises(ValidationError):
            self.request(self.capability("read"), timeout_seconds=1.0)

    def test_duplicate_scopes_are_rejected(self) -> None:
        with pytest.raises(ValidationError):
            self.request(self.capability("read"), self.capability("read"))

    def test_operation_ladder_is_broad_to_narrow_and_bounded(self) -> None:
        request = PermitAcquisitionRequest.for_operation(
            profile_id=PROFILE,
            subject_fingerprint=SUBJECT_A,
            capability_name="list_files",
            connector_id="acme",
            installation_id="inst1",
        )

        kinds = {scope.kind for scope in request.scopes}
        assert kinds == set(ConcurrencyScope.permit_pool_kinds())
        assert len(request.scope_keys()) == len(ConcurrencyScope.permit_pool_kinds())

    def test_operation_ladder_omits_absent_sources(self) -> None:
        request = PermitAcquisitionRequest.for_operation(
            profile_id=PROFILE,
            subject_fingerprint=SUBJECT_A,
            capability_name="write_todos",
        )

        assert {scope.kind for scope in request.scopes} == {
            ConcurrencyScope.GLOBAL,
            ConcurrencyScope.PROFILE,
            ConcurrencyScope.USER,
            ConcurrencyScope.CAPABILITY,
        }


class TestPermitAdmission(PermitFixtureMixin, ConcurrencyProbeMixin):
    async def test_minimum_across_scopes_governs(self) -> None:
        manager = self.manager(**{"global": 8, "capability": 2})
        request = self.queued(PermitScope.for_global(), self.capability("read"))

        assert manager.effective_capacity(request) == 2

        probe = await self.run_workers(manager, (request,) * 6)
        assert probe.observed_max == 2
        assert probe.completed == 6
        assert manager.active_leases == 0
        assert manager.tracked_scopes == 0

    async def test_each_scope_is_enforced_independently(self) -> None:
        manager = self.manager(**{"global": 4, "capability": 1})
        first = self.queued(PermitScope.for_global(), self.capability("alpha"))
        second = self.queued(PermitScope.for_global(), self.capability("beta"))

        probe = await self.run_workers(manager, (first, second, first, second))

        # Each capability stays serial, but they do not block one another.
        assert probe.observed_max == 2
        assert probe.completed == 4

    async def test_unknown_capacity_is_serial(self) -> None:
        manager = RunPermitManager()
        request = self.queued(PermitScope.for_global(), self.capability("unknown"))

        assert manager.effective_capacity(request) == 1

        probe = await self.run_workers(manager, (request,) * 4)
        assert probe.observed_max == 1
        assert probe.completed == 4

    async def test_requested_parallelism_can_only_narrow(self) -> None:
        manager = self.manager(**{"global": 8, "capability": 8})
        scopes = (PermitScope.for_global(), self.capability("read"))
        narrowed = self.queued(*scopes, max_parallelism=2)
        widened = self.queued(*scopes, max_parallelism=16)

        assert manager.effective_capacity(narrowed) == 2
        assert manager.effective_capacity(widened) == 8

        probe = await self.run_workers(manager, (narrowed,) * 5)
        assert probe.observed_max == 2
        assert probe.completed == 5

    async def test_requested_parallelism_cannot_widen_a_serial_policy(self) -> None:
        manager = RunPermitManager()
        request = self.queued(
            PermitScope.for_global(),
            self.capability("read"),
            max_parallelism=8,
        )

        assert manager.effective_capacity(request) == 1

        probe = await self.run_workers(manager, (request,) * 4)
        assert probe.observed_max == 1
        assert probe.completed == 4

    async def test_saturation_returns_a_typed_outcome_not_an_exception(self) -> None:
        manager = RunPermitManager()
        request = self.request(self.capability("read"))

        held = await manager.acquire_lease(request)
        refused = await manager.acquire_lease(request)

        assert held.outcome is PermitOutcome.ADMITTED
        assert refused.outcome is PermitOutcome.REFUSED_SATURATED
        assert refused.admitted is False
        assert refused.lease_id is None
        assert refused.effective_capacity == 1

        manager.release(held)

    async def test_disjoint_subjects_do_not_share_capacity(self) -> None:
        manager = self.manager(**{"global": 4, "connector": 1})
        first = self.request(self.connector("acme", subject=SUBJECT_A))
        second = self.request(self.connector("acme", subject=SUBJECT_B))

        lease_a = await manager.acquire_lease(first)
        lease_b = await manager.acquire_lease(second)
        blocked = await manager.acquire_lease(first)

        assert lease_a.admitted and lease_b.admitted
        assert blocked.outcome is PermitOutcome.REFUSED_SATURATED

        manager.release(lease_a)
        manager.release(lease_b)


class TestPermitFairness(PermitFixtureMixin):
    async def test_waiters_are_admitted_in_arrival_order(self) -> None:
        manager = RunPermitManager()
        scope = self.capability("read")
        holder = await manager.acquire_lease(self.request(scope))
        admitted: list[str] = []

        async def waiter(name: str) -> None:
            async with manager.acquire(self.queued(scope)) as lease:
                assert lease.outcome is PermitOutcome.QUEUED_ADMITTED
                admitted.append(name)

        tasks = []
        for name in ("w1", "w2", "w3"):
            tasks.append(asyncio.create_task(waiter(name)))
            await self.drain()

        assert manager.pending_waiters == 3
        manager.release(holder)
        await asyncio.gather(*tasks)

        assert admitted == ["w1", "w2", "w3"]
        assert manager.pending_waiters == 0
        assert manager.tracked_scopes == 0

    async def test_a_later_arrival_cannot_overtake_a_queued_waiter(self) -> None:
        manager = RunPermitManager()
        capability_scope = self.capability("read")
        connector_scope = self.connector("acme")

        # The connector is saturated, so the two-scope waiter must queue even
        # though its capability scope is completely free.
        holder = await manager.acquire_lease(self.request(connector_scope))
        waiting = asyncio.create_task(
            manager.acquire_lease(self.queued(capability_scope, connector_scope))
        )
        await self.drain()
        assert manager.pending_waiters == 1
        assert manager.in_flight(capability_scope.key()) == 0

        newcomer = await manager.acquire_lease(self.request(capability_scope))
        assert newcomer.outcome is PermitOutcome.REFUSED_SATURATED

        manager.release(holder)
        lease = await waiting
        assert lease.outcome is PermitOutcome.QUEUED_ADMITTED
        manager.release(lease)

    async def test_disjoint_scopes_are_not_blocked_by_an_unrelated_waiter(
        self,
    ) -> None:
        manager = RunPermitManager()
        blocked_scope = self.connector("acme")
        free_scope = self.connector("other")

        holder = await manager.acquire_lease(self.request(blocked_scope))
        waiting = asyncio.create_task(manager.acquire_lease(self.queued(blocked_scope)))
        await self.drain()

        unrelated = await manager.acquire_lease(self.request(free_scope))
        assert unrelated.outcome is PermitOutcome.ADMITTED

        manager.release(holder)
        manager.release(unrelated)
        manager.release(await waiting)

    async def test_a_freed_slot_goes_to_the_waiter_not_a_new_caller(self) -> None:
        manager = RunPermitManager()
        scope = self.capability("read")
        holder = await manager.acquire_lease(self.request(scope))
        waiting = asyncio.create_task(manager.acquire_lease(self.queued(scope)))
        await self.drain()

        manager.release(holder)
        newcomer = await manager.acquire_lease(self.request(scope))

        assert newcomer.outcome is PermitOutcome.REFUSED_SATURATED
        lease = await waiting
        assert lease.outcome is PermitOutcome.QUEUED_ADMITTED
        manager.release(lease)


class TestPermitDeadlines(PermitFixtureMixin):
    async def test_expired_deadline_refuses_without_suspending(self) -> None:
        manager = RunPermitManager()
        scope = self.capability("read")
        holder = await manager.acquire_lease(self.request(scope))

        refused = await manager.acquire_lease(self.queued(scope, timeout_seconds=0.0))

        assert refused.outcome is PermitOutcome.REFUSED_DEADLINE
        assert manager.pending_waiters == 0
        manager.release(holder)

    async def test_deadline_expiry_leaves_no_waiter_or_permit_behind(self) -> None:
        manager = RunPermitManager()
        scope = self.capability("read")
        holder = await manager.acquire_lease(self.request(scope))

        refused = await manager.acquire_lease(self.queued(scope, timeout_seconds=0.02))

        assert refused.outcome is PermitOutcome.REFUSED_DEADLINE
        assert manager.pending_waiters == 0
        assert manager.in_flight(scope.key()) == 1

        manager.release(holder)
        assert manager.tracked_scopes == 0

    async def test_queue_capacity_is_bounded(self) -> None:
        manager = RunPermitManager(max_waiters=1)
        scope = self.capability("read")
        holder = await manager.acquire_lease(self.request(scope))
        waiting = asyncio.create_task(manager.acquire_lease(self.queued(scope)))
        await self.drain()

        refused = await manager.acquire_lease(self.queued(scope))
        assert refused.outcome is PermitOutcome.REFUSED_QUEUE_FULL

        manager.release(holder)
        manager.release(await waiting)


class TestPermitReleaseSafety(PermitFixtureMixin):
    async def test_exception_inside_the_block_releases_the_permit(self) -> None:
        manager = RunPermitManager()
        scope = self.capability("read")
        request = self.request(scope)

        class Boom(RuntimeError):
            pass

        with pytest.raises(Boom):
            async with manager.acquire(request) as lease:
                assert lease.admitted
                raise Boom()

        assert manager.in_flight(scope.key()) == 0
        assert manager.active_leases == 0
        assert (await manager.acquire_lease(request)).admitted

    async def test_cancelling_a_holder_releases_the_permit(self) -> None:
        manager = RunPermitManager()
        scope = self.capability("read")
        request = self.request(scope)
        entered = asyncio.Event()

        async def holder() -> None:
            async with manager.acquire(request) as lease:
                assert lease.admitted
                entered.set()
                await asyncio.Event().wait()

        task = asyncio.create_task(holder())
        await entered.wait()
        assert manager.in_flight(scope.key()) == 1

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert manager.in_flight(scope.key()) == 0
        assert manager.active_leases == 0

    async def test_cancelling_a_waiter_leaves_no_permit_behind(self) -> None:
        manager = RunPermitManager()
        scope = self.capability("read")
        holder = await manager.acquire_lease(self.request(scope))
        task = asyncio.create_task(manager.acquire_lease(self.queued(scope)))
        await self.drain()
        assert manager.pending_waiters == 1

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert manager.pending_waiters == 0
        assert manager.in_flight(scope.key()) == 1

        manager.release(holder)
        assert manager.tracked_scopes == 0
        assert manager.active_leases == 0

    async def test_refusal_never_holds_capacity(self) -> None:
        manager = RunPermitManager()
        scope = self.capability("read")
        request = self.request(scope)
        holder = await manager.acquire_lease(request)

        async with manager.acquire(request) as refused:
            assert refused.admitted is False

        assert manager.in_flight(scope.key()) == 1
        manager.release(holder)
        assert manager.tracked_scopes == 0

    async def test_double_release_is_a_typed_error(self) -> None:
        manager = RunPermitManager()
        lease = await manager.acquire_lease(self.request(self.capability("read")))

        manager.release(lease)
        with pytest.raises(PermitDoubleReleaseError) as caught:
            manager.release(lease)

        assert caught.value.safe_message == "Concurrency permit was already released."

    async def test_releasing_a_refusal_is_a_typed_error(self) -> None:
        manager = RunPermitManager()
        request = self.request(self.capability("read"))
        holder = await manager.acquire_lease(request)
        refused = await manager.acquire_lease(request)

        with pytest.raises(PermitNotAdmittedError) as caught:
            manager.release(refused)

        assert "never admitted" in caught.value.safe_message
        manager.release(holder)

    async def test_early_release_inside_the_block_is_not_double_released(self) -> None:
        manager = RunPermitManager()
        scope = self.capability("read")
        request = self.request(scope)

        async with manager.acquire(request) as lease:
            assert lease.admitted
            manager.release(lease)
            assert manager.in_flight(scope.key()) == 0

        assert manager.in_flight(scope.key()) == 0
        assert manager.active_leases == 0

    def test_a_refused_lease_cannot_claim_a_lease_id(self) -> None:
        key = self.capability("read").key()

        with pytest.raises(ValidationError):
            PermitLease(
                outcome=PermitOutcome.REFUSED_SATURATED,
                scope_keys=(key,),
                effective_capacity=1,
                lease_id="permit_lease_1",
            )
        with pytest.raises(ValidationError):
            PermitLease(
                outcome=PermitOutcome.ADMITTED,
                scope_keys=(key,),
                effective_capacity=1,
            )


class TestPermitRunLifecycle(PermitFixtureMixin):
    async def test_dispose_refuses_queued_waiters_and_clears_state(self) -> None:
        manager = RunPermitManager()
        scope = self.capability("read")
        holder = await manager.acquire_lease(self.request(scope))
        waiting = asyncio.create_task(manager.acquire_lease(self.queued(scope)))
        await self.drain()
        assert manager.pending_waiters == 1

        manager.dispose()
        lease = await waiting

        assert lease.outcome is PermitOutcome.REFUSED_DISPOSED
        assert lease.admitted is False
        assert manager.pending_waiters == 0
        assert manager.active_leases == 0
        assert manager.tracked_scopes == 0
        assert manager.holds(holder) is False

    async def test_acquiring_after_dispose_is_refused_not_raised(self) -> None:
        manager = RunPermitManager()
        manager.dispose()
        manager.dispose()

        lease = await manager.acquire_lease(self.request(self.capability("read")))
        assert lease.outcome is PermitOutcome.REFUSED_DISPOSED

    async def test_permit_state_does_not_survive_into_a_new_run(self) -> None:
        scope = self.capability("read")
        first = RunPermitManager()
        await first.acquire_lease(self.request(scope))
        first.dispose()

        second = RunPermitManager()
        assert second.in_flight(scope.key()) == 0
        assert second.tracked_scopes == 0
        assert (await second.acquire_lease(self.request(scope))).admitted

    async def test_held_counters_are_pruned_to_zero(self) -> None:
        manager = self.manager(**{"capability": 2})
        scope = self.capability("read")
        request = self.request(scope)

        first = await manager.acquire_lease(request)
        second = await manager.acquire_lease(request)
        assert manager.tracked_scopes == 1
        assert manager.in_flight(scope.key()) == 2

        manager.release(first)
        assert manager.tracked_scopes == 1
        manager.release(second)
        assert manager.tracked_scopes == 0

    async def test_active_lease_count_is_bounded(self) -> None:
        manager = RunPermitManager(
            policy=self.policy(**{"capability": 4}),
            max_active_leases=1,
        )
        held = await manager.acquire_lease(self.request(self.capability("alpha")))
        refused = await manager.acquire_lease(self.request(self.capability("beta")))

        assert held.admitted
        assert refused.outcome is PermitOutcome.REFUSED_SATURATED
        manager.release(held)

    def test_cross_event_loop_reuse_is_a_typed_error(self) -> None:
        manager = RunPermitManager()
        request = self.request(self.capability("read"))

        async def acquire() -> PermitLease:
            return await manager.acquire_lease(request)

        first = asyncio.run(acquire())
        assert first.admitted

        with pytest.raises(PermitEventLoopMismatchError) as caught:
            asyncio.run(acquire())

        assert "single run event loop" in caught.value.safe_message


class TestPermitPolicyMapping(PermitFixtureMixin):
    def test_from_limits_is_deterministic(self) -> None:
        limits: Mapping[ConcurrencyScope, int] = {
            ConcurrencyScope.CAPABILITY: 2,
            ConcurrencyScope.GLOBAL: 8,
        }

        assert PermitCapacityPolicy.from_limits(limits) == (
            PermitCapacityPolicy.from_limits(dict(reversed(list(limits.items()))))
        )
