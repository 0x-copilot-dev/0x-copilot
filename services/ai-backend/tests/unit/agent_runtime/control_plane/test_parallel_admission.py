"""SMELL-01 — the run gate is serial by default and widenable only by grant.

Every concurrency assertion here is an assertion about an **observed maximum**
recorded by the bodies themselves. Nothing waits on wall-clock time: the bodies
yield to the loop a fixed number of times, which is enough for any coroutine the
gate would have admitted to have been admitted, and no amount of scheduling luck
can push the counter past a bound the gate enforces.
"""

from __future__ import annotations

import asyncio

import pytest

from agent_runtime.control_plane.context import RunSerialAdmission
from agent_runtime.control_plane.parallel_admission import (
    ParallelAdmissionBounds,
    ParallelAdmissionGrant,
    ParallelAdmissionPort,
    ParallelAdmissionResolver,
    ToolAdmissionRequest,
)

# Enough loop turns for every coroutine the gate admits to observe every other
# one it admitted. A gate that serializes still finishes; it just never counts
# above one.
_OVERLAP_TURNS = 8


def _request(call_id: str, *, name: str = "observed_tool") -> ToolAdmissionRequest:
    return ToolAdmissionRequest(
        tool_call_id=call_id,
        tool_name=name,
        execution_scope="supervisor",
    )


class _Meter:
    """Record the greatest number of bodies that were ever inside at once.

    ``cohabitants`` additionally records *which* labels were inside together, so
    a test can assert that a particular call never shared the gate with anything
    — a stronger claim than a maximum, which a coincidence could satisfy.
    """

    def __init__(self) -> None:
        self.active = 0
        self.maximum = 0
        self.entered: list[str] = []
        self.live: set[str] = set()
        self.cohabitants: set[frozenset[str]] = set()

    async def run(self, admission: RunSerialAdmission, request, *, label: str) -> None:
        async with admission.async_permit(request):
            self.active += 1
            self.maximum = max(self.maximum, self.active)
            self.entered.append(label)
            self.live.add(label)
            self.cohabitants.add(frozenset(self.live))
            for _ in range(_OVERLAP_TURNS):
                await asyncio.sleep(0)
                self.cohabitants.add(frozenset(self.live))
            self.live.discard(label)
            self.active -= 1

    async def run_sync(self, admission: RunSerialAdmission, *, label: str) -> None:
        with admission.sync_permit():
            self.active += 1
            self.maximum = max(self.maximum, self.active)
            self.entered.append(label)
            self.active -= 1

    def ever_shared_with(self, label: str) -> set[str]:
        """Return every other label ``label`` was ever inside the gate with."""

        shared: set[str] = set()
        for group in self.cohabitants:
            if label in group:
                shared |= set(group) - {label}
        return shared


class _GrantingPort:
    """A port that admits a named set of calls into one cohort."""

    def __init__(
        self,
        *,
        call_ids: set[str],
        cohort_id: str = "batch-1:segment-0",
        max_parallelism: int = 2,
    ) -> None:
        self._call_ids = call_ids
        self._cohort_id = cohort_id
        self._max_parallelism = max_parallelism
        self.asked: list[str] = []

    def grant_for(self, request: ToolAdmissionRequest) -> ParallelAdmissionGrant | None:
        self.asked.append(request.tool_call_id)
        if request.tool_call_id not in self._call_ids:
            return None
        return ParallelAdmissionGrant(
            tool_call_id=request.tool_call_id,
            cohort_id=self._cohort_id,
            max_parallelism=self._max_parallelism,
        )


class TestSerialByDefault:
    """With nothing installed the gate is the Step-2 permit, unchanged."""

    async def test_concurrent_calls_serialize_with_no_port_installed(self) -> None:
        admission = RunSerialAdmission()
        meter = _Meter()

        await asyncio.gather(
            *(
                meter.run(admission, _request(f"call-{index}"), label=str(index))
                for index in range(4)
            )
        )

        assert meter.maximum == 1
        assert len(meter.entered) == 4

    async def test_a_permit_taken_without_a_request_is_exclusive(self) -> None:
        admission = RunSerialAdmission(
            parallel_admission=_GrantingPort(call_ids={"call-0", "call-1"}),
        )
        meter = _Meter()

        await asyncio.gather(
            *(meter.run(admission, None, label=str(index)) for index in range(4))
        )

        assert meter.maximum == 1


class TestGrantedOverlap:
    """An F6-admitted cohort overlaps up to its allowance and no further."""

    async def test_granted_siblings_overlap_up_to_the_allowance(self) -> None:
        call_ids = {f"call-{index}" for index in range(5)}
        admission = RunSerialAdmission(
            parallel_admission=_GrantingPort(call_ids=call_ids, max_parallelism=2),
        )
        meter = _Meter()

        await asyncio.gather(
            *(
                meter.run(admission, _request(call_id), label=call_id)
                for call_id in sorted(call_ids)
            )
        )

        assert meter.maximum == 2
        assert len(meter.entered) == 5

    async def test_a_wider_allowance_is_observed_wider(self) -> None:
        call_ids = {f"call-{index}" for index in range(6)}
        admission = RunSerialAdmission(
            parallel_admission=_GrantingPort(call_ids=call_ids, max_parallelism=4),
        )
        meter = _Meter()

        await asyncio.gather(
            *(
                meter.run(admission, _request(call_id), label=call_id)
                for call_id in sorted(call_ids)
            )
        )

        assert meter.maximum == 4

    async def test_a_granted_group_and_a_serial_call_never_overlap(self) -> None:
        """A serial sibling waits behind the whole group, and vice versa."""

        granted = {"call-0", "call-1", "call-2"}
        admission = RunSerialAdmission(
            parallel_admission=_GrantingPort(call_ids=granted, max_parallelism=3),
        )
        meter = _Meter()

        async def granted_body(call_id: str) -> None:
            await meter.run(admission, _request(call_id), label=call_id)

        async def serial_body() -> None:
            await meter.run(admission, _request("ungranted"), label="ungranted")

        await asyncio.gather(
            *(granted_body(call_id) for call_id in sorted(granted)),
            serial_body(),
        )

        # Three may overlap; the ungranted call is exclusive, so the ceiling is
        # the granted width and never the width plus the serial call.
        assert meter.maximum == 3
        assert len(meter.entered) == 4
        # The decisive claim: the serial call was alone every moment it ran.
        assert meter.ever_shared_with("ungranted") == set()


class TestUnknownMeansSerial:
    """Anything not positively identified as F6-admitted takes the exclusive lane."""

    async def test_a_grant_for_a_different_call_does_not_widen_this_one(self) -> None:
        """The load-bearing default: a grant must name the call it admits."""

        class _MisKeyedPort:
            def grant_for(
                self,
                request: ToolAdmissionRequest,
            ) -> ParallelAdmissionGrant:
                del request
                return ParallelAdmissionGrant(
                    tool_call_id="some-other-call",
                    cohort_id="batch-1:segment-0",
                    max_parallelism=4,
                )

        admission = RunSerialAdmission(parallel_admission=_MisKeyedPort())
        meter = _Meter()

        await asyncio.gather(
            *(
                meter.run(admission, _request(f"call-{index}"), label=str(index))
                for index in range(4)
            )
        )

        assert meter.maximum == 1

    async def test_a_raising_port_is_a_serial_port(self) -> None:
        class _RaisingPort:
            def grant_for(self, request: ToolAdmissionRequest) -> object:
                del request
                raise RuntimeError("the batch coordinator is unavailable")

        admission = RunSerialAdmission(parallel_admission=_RaisingPort())
        meter = _Meter()

        await asyncio.gather(
            *(
                meter.run(admission, _request(f"call-{index}"), label=str(index))
                for index in range(4)
            )
        )

        assert meter.maximum == 1

    async def test_a_port_returning_a_foreign_type_is_serial(self) -> None:
        class _ForeignPort:
            def grant_for(self, request: ToolAdmissionRequest) -> object:
                del request
                return {"max_parallelism": 8}

        admission = RunSerialAdmission(parallel_admission=_ForeignPort())
        meter = _Meter()

        await asyncio.gather(
            *(
                meter.run(admission, _request(f"call-{index}"), label=str(index))
                for index in range(4)
            )
        )

        assert meter.maximum == 1

    async def test_an_unidentified_call_is_serial_beside_granted_siblings(
        self,
    ) -> None:
        """A blank id is what a malformed framework call produces."""

        admission = RunSerialAdmission(
            parallel_admission=_GrantingPort(
                call_ids={"call-0", "call-1"},
                max_parallelism=2,
            ),
        )
        meter = _Meter()

        await asyncio.gather(
            meter.run(admission, _request(""), label="blank-a"),
            meter.run(admission, _request(""), label="blank-b"),
        )

        assert meter.maximum == 1

    async def test_a_grant_of_width_one_is_held_to_a_width_of_one(self) -> None:
        """A width-one grant buys no overlap — it only changes what it waits on.

        The grant is honoured rather than discarded, because a grantor that orders
        its calls behind its own barrier needs them all to reach it, and the
        exclusive lock admits one at a time. Honouring it must not cost the width:
        a cohort semaphore of one is exactly as narrow as the lock it replaces,
        which is what this asserts over a cohort of four.
        """

        call_ids = {f"call-{index}" for index in range(4)}
        admission = RunSerialAdmission(
            parallel_admission=_GrantingPort(call_ids=call_ids, max_parallelism=1),
        )
        meter = _Meter()

        await asyncio.gather(
            *(
                meter.run(admission, _request(call_id), label=call_id)
                for call_id in sorted(call_ids)
            )
        )

        assert meter.maximum == 1

    async def test_a_cohort_whose_width_disagrees_mid_flight_is_serial(self) -> None:
        """A live cohort's width is fixed; a disagreeing grant is not honoured."""

        class _DriftingPort:
            def __init__(self) -> None:
                self.calls = 0

            def grant_for(
                self,
                request: ToolAdmissionRequest,
            ) -> ParallelAdmissionGrant:
                self.calls += 1
                return ParallelAdmissionGrant(
                    tool_call_id=request.tool_call_id,
                    cohort_id="batch-1:segment-0",
                    max_parallelism=2 if self.calls == 1 else 4,
                )

        admission = RunSerialAdmission(parallel_admission=_DriftingPort())
        meter = _Meter()

        await asyncio.gather(
            *(
                meter.run(admission, _request(f"call-{index}"), label=str(index))
                for index in range(4)
            )
        )

        # The first grant binds the cohort at two. Every later grant disagrees,
        # so it is refused rather than reshaping a gate coroutines are already
        # queued on — and a refused grant is an exclusive call, which cannot
        # overlap the cohort member already inside. Narrowing, never widening.
        assert meter.maximum == 1
        assert len(meter.entered) == 4


class TestGrantConstruction:
    """A widening or anonymous grant is not a value that exists."""

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"tool_call_id": "", "cohort_id": "c", "max_parallelism": 2},
            {"tool_call_id": "   ", "cohort_id": "c", "max_parallelism": 2},
            {"tool_call_id": "call-1", "cohort_id": "", "max_parallelism": 2},
            {"tool_call_id": "call-1", "cohort_id": "c", "max_parallelism": 0},
            {"tool_call_id": "call-1", "cohort_id": "c", "max_parallelism": -1},
            {
                "tool_call_id": "call-1",
                "cohort_id": "c",
                "max_parallelism": ParallelAdmissionBounds.MAX_PARALLELISM + 1,
            },
            {
                "tool_call_id": "x"
                * (ParallelAdmissionBounds.MAX_IDENTIFIER_LENGTH + 1),
                "cohort_id": "c",
                "max_parallelism": 2,
            },
        ],
    )
    def test_an_invalid_grant_cannot_be_constructed(self, kwargs: dict) -> None:
        with pytest.raises(ValueError):
            ParallelAdmissionGrant(**kwargs)

    def test_a_grant_authorizes_only_the_call_it_names(self) -> None:
        grant = ParallelAdmissionGrant(
            tool_call_id="call-1",
            cohort_id="batch-1:segment-0",
            max_parallelism=2,
        )

        assert grant.authorizes(_request("call-1"))
        assert not grant.authorizes(_request("call-2"))
        assert not grant.authorizes(_request(""))

    def test_the_port_protocol_is_structural(self) -> None:
        assert isinstance(_GrantingPort(call_ids=set()), ParallelAdmissionPort)


class TestResolver:
    """The single fail-closed reading, asserted directly."""

    def test_no_port_and_no_request_resolve_to_serial(self) -> None:
        port = _GrantingPort(call_ids={"call-1"})

        assert ParallelAdmissionResolver.resolve(None, _request("call-1")) is None
        assert ParallelAdmissionResolver.resolve(port, None) is None

    def test_a_matching_grant_resolves(self) -> None:
        port = _GrantingPort(call_ids={"call-1"}, max_parallelism=3)

        grant = ParallelAdmissionResolver.resolve(port, _request("call-1"))

        assert grant is not None
        assert grant.max_parallelism == 3
        assert grant.tool_call_id == "call-1"


class TestSyncPathStaysSerial:
    """The synchronous lane is never widened, even with a granting port."""

    async def test_sync_permit_is_exclusive_with_a_granting_port_installed(
        self,
    ) -> None:
        admission = RunSerialAdmission(
            parallel_admission=_GrantingPort(
                call_ids={"call-0", "call-1", "call-2"},
                max_parallelism=3,
            ),
        )
        meter = _Meter()

        await asyncio.gather(
            *(meter.run_sync(admission, label=str(index)) for index in range(3))
        )

        assert meter.maximum == 1
        assert len(meter.entered) == 3

    def test_sync_permit_takes_no_admission_request(self) -> None:
        """The lane has no widened form, so it advertises no way to select one."""

        admission = RunSerialAdmission()

        with pytest.raises(TypeError):
            admission.sync_permit(_request("call-1"))  # type: ignore[call-arg]


class TestInstallation:
    """Installing the source is install-once, like the F2/F10 runtime slots."""

    def test_installing_the_same_port_twice_is_idempotent(self) -> None:
        port = _GrantingPort(call_ids={"call-1"})
        admission = RunSerialAdmission()

        admission.install_parallel_admission(port)
        admission.install_parallel_admission(port)

    def test_installing_a_second_different_port_is_refused(self) -> None:
        admission = RunSerialAdmission()
        admission.install_parallel_admission(_GrantingPort(call_ids={"call-1"}))

        with pytest.raises(RuntimeError, match="already installed"):
            admission.install_parallel_admission(_GrantingPort(call_ids={"call-2"}))

    async def test_an_installed_port_widens_a_previously_serial_admission(
        self,
    ) -> None:
        admission = RunSerialAdmission()
        call_ids = {f"call-{index}" for index in range(4)}
        meter = _Meter()

        admission.install_parallel_admission(
            _GrantingPort(call_ids=call_ids, max_parallelism=2)
        )
        await asyncio.gather(
            *(
                meter.run(admission, _request(call_id), label=call_id)
                for call_id in sorted(call_ids)
            )
        )

        assert meter.maximum == 2


class TestGateIntegrity:
    """Overlap is granted by the gate; it is never a way around it."""

    async def test_a_failing_granted_body_does_not_strand_the_run_lock(self) -> None:
        call_ids = {"call-0", "call-1"}
        admission = RunSerialAdmission(
            parallel_admission=_GrantingPort(call_ids=call_ids, max_parallelism=2),
        )

        async def failing(call_id: str) -> None:
            async with admission.async_permit(_request(call_id)):
                raise RuntimeError("tool exploded")

        results = await asyncio.gather(
            *(failing(call_id) for call_id in sorted(call_ids)),
            return_exceptions=True,
        )
        assert all(isinstance(item, RuntimeError) for item in results)

        # The gate must still admit ordinary serial work afterwards.
        meter = _Meter()
        await meter.run(admission, _request("later"), label="later")
        assert meter.entered == ["later"]

    async def test_a_cancelled_granted_body_does_not_strand_the_run_lock(self) -> None:
        admission = RunSerialAdmission(
            parallel_admission=_GrantingPort(
                call_ids={"call-0", "call-1"},
                max_parallelism=2,
            ),
        )
        started = asyncio.Event()

        async def parked(call_id: str) -> None:
            async with admission.async_permit(_request(call_id)):
                started.set()
                await asyncio.Event().wait()

        task = asyncio.create_task(parked("call-0"))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        meter = _Meter()
        await meter.run(admission, _request("later"), label="later")
        assert meter.entered == ["later"]

    async def test_cohorts_are_evicted_so_a_long_run_cannot_leak_them(self) -> None:
        admission = RunSerialAdmission()
        meter = _Meter()

        for index in range(ParallelAdmissionBounds.MAX_TRACKED_COHORTS * 2):
            port = _GrantingPort(
                call_ids={"call-a", "call-b"},
                cohort_id=f"batch-{index}:segment-0",
                max_parallelism=2,
            )
            scoped = RunSerialAdmission(parallel_admission=port)
            await asyncio.gather(
                meter.run(scoped, _request("call-a"), label="a"),
                meter.run(scoped, _request("call-b"), label="b"),
            )

        del admission
        assert meter.maximum == 2

    async def test_the_cohort_table_bound_is_the_run_wide_ceiling(self) -> None:
        """This gate bounds width *per cohort*; the table bound is the rest.

        Pinned deliberately, because it is the one place the composed ceiling is
        not simply "the allowance F6 computed": concurrent cohorts each get
        their own width, so the gate's own run-wide ceiling is the cohort table
        bound. Saturation past it narrows to serial rather than raising or
        growing. Whoever wires F6 owns keeping live cohorts to one segment at a
        time, which is what makes the per-cohort width the whole story.
        """

        class _UniqueCohortPort:
            def __init__(self) -> None:
                self.calls = 0

            def grant_for(
                self,
                request: ToolAdmissionRequest,
            ) -> ParallelAdmissionGrant:
                self.calls += 1
                return ParallelAdmissionGrant(
                    tool_call_id=request.tool_call_id,
                    cohort_id=f"cohort-{self.calls}",
                    max_parallelism=2,
                )

        admission = RunSerialAdmission(parallel_admission=_UniqueCohortPort())
        meter = _Meter()
        bound = ParallelAdmissionBounds.MAX_TRACKED_COHORTS
        count = bound * 2

        await asyncio.gather(
            *(
                meter.run(admission, _request(f"call-{index}"), label=str(index))
                for index in range(count)
            )
        )

        assert len(meter.entered) == count
        assert meter.maximum == bound


class TestAGrantorThatEnforcesItsOwnWidth:
    """The one case where a slotless cohort is admitted instead of serialized.

    The cohort table is finite, and a grant it cannot represent has to be
    answered somehow. Answering with the exclusive lock is fail-closed on
    *width* and open on *liveness*: a grantor that orders its calls behind its
    own barrier has members that wait for each other, and one of them holding
    the run's only lock is a stall, not a narrowing.

    So the grant carries who enforces the width. Absent that claim nothing
    changes — the exclusive lock, as before. With it, the call joins the shared
    lane without a slot, where the lightswitch still keeps granted work from
    overlapping ungranted work and the grantor's own gate still decides how many
    of its members run at once.
    """

    class _SaturatingPort:
        """One fresh cohort per call, so the table saturates by construction."""

        def __init__(self, *, width_enforced_by_grantor: bool) -> None:
            self._self_enforcing = width_enforced_by_grantor
            self.calls = 0

        def grant_for(self, request: ToolAdmissionRequest) -> ParallelAdmissionGrant:
            self.calls += 1
            return ParallelAdmissionGrant(
                tool_call_id=request.tool_call_id,
                cohort_id=f"cohort-{self.calls}",
                max_parallelism=2,
                width_enforced_by_grantor=self._self_enforcing,
            )

    def test_a_grant_makes_no_such_claim_by_default(self) -> None:
        grant = ParallelAdmissionGrant(
            tool_call_id="call-1",
            cohort_id="cohort-1",
            max_parallelism=2,
        )

        assert grant.width_enforced_by_grantor is False

    async def test_saturating_the_table_serializes_a_grantor_that_does_not_claim_it(
        self,
    ) -> None:
        """The unchanged answer, restated beside its opposite so both are pinned."""

        admission = RunSerialAdmission(
            parallel_admission=self._SaturatingPort(width_enforced_by_grantor=False),
        )
        meter = _Meter()
        bound = ParallelAdmissionBounds.MAX_TRACKED_COHORTS

        await asyncio.gather(
            *(
                meter.run(admission, _request(f"call-{index}"), label=str(index))
                for index in range(bound * 2)
            )
        )

        assert len(meter.entered) == bound * 2
        assert meter.maximum == bound

    async def test_saturating_the_table_still_admits_a_self_enforcing_grantor(
        self,
    ) -> None:
        """Past the table bound it is admitted, not pushed onto the run's lock."""

        admission = RunSerialAdmission(
            parallel_admission=self._SaturatingPort(width_enforced_by_grantor=True),
        )
        meter = _Meter()
        bound = ParallelAdmissionBounds.MAX_TRACKED_COHORTS
        count = bound * 2

        await asyncio.gather(
            *(
                meter.run(admission, _request(f"call-{index}"), label=str(index))
                for index in range(count)
            )
        )

        assert len(meter.entered) == count
        assert meter.maximum > bound, (
            "a self-enforcing grantor was serialized past the table bound, which "
            "is the stall the claim exists to avoid"
        )

    async def test_a_slotless_grant_still_never_overlaps_an_ungranted_call(
        self,
    ) -> None:
        """The lightswitch, not the slot, is what excludes ungranted work.

        This is the claim that makes admitting a slotless cohort safe rather than
        merely convenient: the shared lane takes the same exclusive lock a serial
        call takes, on the whole group's behalf.
        """

        class _SelfEnforcingForKnownCalls:
            def __init__(self) -> None:
                self.calls = 0

            def grant_for(
                self,
                request: ToolAdmissionRequest,
            ) -> ParallelAdmissionGrant | None:
                if not request.tool_call_id.startswith("granted-"):
                    return None
                self.calls += 1
                return ParallelAdmissionGrant(
                    tool_call_id=request.tool_call_id,
                    cohort_id=f"cohort-{self.calls}",
                    max_parallelism=2,
                    width_enforced_by_grantor=True,
                )

        admission = RunSerialAdmission(
            parallel_admission=_SelfEnforcingForKnownCalls(),
        )
        meter = _Meter()
        bound = ParallelAdmissionBounds.MAX_TRACKED_COHORTS

        await asyncio.gather(
            *(
                meter.run(admission, _request(f"granted-{index}"), label=f"g{index}")
                for index in range(bound * 2)
            ),
            meter.run(admission, _request("plain"), label="serial"),
        )

        assert meter.ever_shared_with("serial") == set(), (
            "an ungranted serial call shared the gate with a granted cohort"
        )
