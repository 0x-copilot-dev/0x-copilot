"""The per-PR desktop-journey gate: its phase pin, and the seam CI depends on.

`tools/desktop-journeys/` drives the real packaged app and has repeatedly caught
regressions 13k unit tests did not. Nothing ran it on a PR until
`.github/workflows/ci-desktop.yml` grew the `desktop-journey-first-run` job, and
that job rests on two things this file keeps true:

1. ``JOURNEY_PHASES`` really does narrow a run to ONE phase. Without it the job
   would run all seven phases off one shared boot and fail in a later phase with
   a symptom-shaped message that reads like a product bug.
2. The phase id the workflow pins still EXISTS in the journey. A pinned id that
   matches nothing must be loud, because the alternative — a CI gate that runs
   zero phases and reports success — is the exact "landed but not wired"
   pathology the journey harness exists to catch.

These are checked here, in a Python test the `repo-gates` job already runs on
every PR, because the workflow itself only runs on a macOS runner when the
desktop paths change. A guard that runs less often than the thing it guards is
not a guard.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
JOURNEYS_DIR = REPO_ROOT / "tools" / "desktop-journeys"
CI_DESKTOP = REPO_ROOT / ".github" / "workflows" / "ci-desktop.yml"
JOURNEY_JOB = "desktop-journey-first-run"


def _load(module_name: str) -> Any:
    """Import a `tools/desktop-journeys/*.py` module by path.

    The directory has a dash in its name, so it is not a package and cannot be
    imported by name. It is put on `sys.path` because `first_run` does a bare
    `from _lib import ...`.
    """

    if str(JOURNEYS_DIR) not in sys.path:
        sys.path.insert(0, str(JOURNEYS_DIR))
    path = JOURNEYS_DIR / f"{module_name}.py"
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None, f"cannot load {path}"
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


lib = _load("_lib")
first_run = _load("first_run")


class _StubSession:
    """Enough of a DriverSession for the phase runner: a context manager with a
    settable `phase_prefix`. Nothing here launches an app."""

    def __init__(self) -> None:
        self.phase_prefix = ""

    def __enter__(self) -> "_StubSession":
        return self

    def __exit__(self, *exc: object) -> None:
        return None


#: Spelled out rather than read off `_lib`, so a harness without the mechanism
#: fails these tests on BEHAVIOUR ("it ran all three phases") instead of on a
#: missing attribute. The name is also what `ci-desktop.yml` sets, and the two
#: agreeing is the point.
SELECTOR_ENV = "JOURNEY_PHASES"


def _plan_with(monkeypatch: pytest.MonkeyPatch, selector: str | None) -> Any:
    if selector is None:
        monkeypatch.delenv(SELECTOR_ENV, raising=False)
    else:
        monkeypatch.setenv(SELECTOR_ENV, selector)
    return lib.JourneyPlan("stub-journey")


def _phases(ran: list[str]) -> list[tuple[str, str, Any]]:
    return [
        ("FR-0", "first", lambda _s: ran.append("FR-0")),
        ("FR-1", "second", lambda _s: ran.append("FR-1")),
        ("FR-2", "third", lambda _s: ran.append("FR-2")),
    ]


class TestPhasePinning:
    def test_unpinned_runs_every_phase(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The default is unchanged: no selector means the whole file runs."""

        ran: list[str] = []
        plan = _plan_with(monkeypatch, None)
        plan.boot("stub", _StubSession, phases=_phases(ran))
        assert ran == ["FR-0", "FR-1", "FR-2"]
        assert plan.exit_code == 0

    def test_pin_runs_only_that_phase_and_exits_zero(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The whole point: one phase runs, the rest are DROPPED, exit is 0.

        Dropped and not recorded-as-skipped is the load-bearing half. A skipped
        phase is deliberately non-zero, so recording the two the caller did not
        ask for would turn every pinned CI run red.
        """

        ran: list[str] = []
        plan = _plan_with(monkeypatch, "FR-0")
        plan.boot("stub", _StubSession, phases=_phases(ran))
        assert ran == ["FR-0"]
        assert [result.phase_id for result in plan.results] == ["FR-0"]
        assert plan.counts()["skipped"] == 0
        assert plan.exit_code == 0

    def test_pin_accepts_several_ids_case_insensitively(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ran: list[str] = []
        plan = _plan_with(monkeypatch, "fr-2, FR-0")
        plan.boot("stub", _StubSession, phases=_phases(ran))
        assert ran == ["FR-0", "FR-2"]
        assert plan.exit_code == 0

    def test_a_group_with_no_selected_phase_is_never_booted(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A supervised boot costs initdb + migrations + three uvicorns. A group
        that would run nothing must not pay for it."""

        booted: list[str] = []

        def factory() -> _StubSession:
            booted.append("boot")
            return _StubSession()

        ran: list[str] = []
        plan = _plan_with(monkeypatch, "FR-0")
        plan.boot("group-a", factory, phases=_phases(ran))
        plan.boot("group-b", factory, phases=[("ZZ-9", "other", lambda _s: None)])
        assert booted == ["boot"], "the unselected group booted an app anyway"
        assert plan.exit_code == 0

    def test_an_unknown_pinned_id_fails_loudly(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A pin that matches nothing must FAIL, never pass over an empty run.

        This is the failure mode that makes a CI gate worthless: rename a phase,
        the workflow keeps pinning the old id, the journey runs zero phases and
        the job goes green forever.
        """

        ran: list[str] = []
        plan = _plan_with(monkeypatch, "FR-0 FR-404")
        plan.boot("stub", _StubSession, phases=_phases(ran))
        assert ran == ["FR-0"]
        assert plan.unmatched_selection == ("fr-404",)
        assert plan.exit_code == 1

    def test_the_selector_env_name_is_the_one_ci_sets(self) -> None:
        assert lib.PHASE_SELECTOR_ENV == SELECTOR_ENV

    def test_setup_still_gates_the_selected_phase(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Pinning does not smuggle a phase past a failed setup."""

        ran: list[str] = []

        def boom(_s: object) -> None:
            raise RuntimeError("setup exploded")

        plan = _plan_with(monkeypatch, "FR-0")
        plan.boot("stub", _StubSession, phases=_phases(ran), setup=boom)
        assert ran == []
        assert plan.exit_code == 1


@pytest.fixture(scope="module")
def workflow() -> dict[str, Any]:
    return yaml.safe_load(CI_DESKTOP.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def job(workflow: dict[str, Any]) -> dict[str, Any]:
    """The journey job, or an empty mapping — its ABSENCE is a test, not an
    error, so a missing job reports as one clear failure rather than five."""

    return dict(workflow["jobs"].get(JOURNEY_JOB) or {})


class TestTheWorkflowIsWiredToTheJourney:
    """The seam between `ci-desktop.yml` and `first_run.py`, checked both ways."""

    def test_the_journey_job_exists(self, job: dict[str, Any]) -> None:
        assert job, (
            f"{CI_DESKTOP.name} does not define the {JOURNEY_JOB} job — nothing "
            "runs a desktop journey on a PR, which is the state this gate ended"
        )

    def test_the_job_runs_on_pull_requests(self, workflow: dict[str, Any]) -> None:
        # PyYAML parses the bare `on:` key as the boolean True.
        triggers = workflow.get("on", workflow.get(True))
        assert isinstance(triggers, dict) and "pull_request" in triggers

    def test_the_job_pins_exactly_one_phase_that_first_run_declares(
        self, job: dict[str, Any]
    ) -> None:
        pinned = str(job.get("env", {}).get(SELECTOR_ENV, "")).replace(",", " ").split()
        assert len(pinned) == 1, (
            "the per-PR job must run ONE phase: since e8622a1d the phases in a "
            f"journey file share one boot, and {pinned} would inherit each "
            "other's route/rail/env/run-history state"
        )
        declared = {phase_id for phase_id, _title, _fn in first_run.PHASES}
        assert pinned[0] in declared, (
            f"ci-desktop.yml pins {pinned[0]!r}, which first_run.py does not "
            f"declare (has {sorted(declared)}) — the gate would prove nothing"
        )

    def test_the_pinned_phase_needs_no_provider_key(self, job: dict[str, Any]) -> None:
        """PR CI must not require a secret or a live third-party service.

        Every phase but the pinned one calls `_needs_key`, which skips without a
        BYOK key from `.env` — and a skip is non-zero, so pinning one of those
        would be a permanently red gate that also wanted a secret.
        """

        pinned = str(job.get("env", {}).get(SELECTOR_ENV, "")).strip()
        fn = next(
            (f for phase_id, _t, f in first_run.PHASES if phase_id == pinned), None
        )
        assert fn is not None, f"first_run.py declares no phase {pinned!r}"
        assert "_needs_key" not in fn.__code__.co_names, (
            f"{pinned} depends on a BYOK key; PR CI has none"
        )

    def test_the_job_stages_fresh_before_running_the_journey(
        self, job: dict[str, Any]
    ) -> None:
        """The staged runtime is a SNAPSHOT of services/*, so a cached or absent
        stage would test code this PR does not contain."""

        steps = list(job.get("steps", []))
        runs = [str(step.get("run", "")) for step in steps]

        def index_of(fragment: str) -> int:
            hits = [i for i, cmd in enumerate(runs) if fragment in cmd]
            assert hits, f"no step runs {fragment!r}"
            return hits[0]

        stage = index_of("desktop-runtime/stage.mjs")
        build = index_of("@0x-copilot/desktop")
        journey = index_of("first_run.py")
        assert stage < journey and build < journey, (
            "the journey must run AFTER a fresh stage and a desktop build"
        )
        cached = [
            step
            for step in steps
            if "cache" in str(step.get("uses", ""))
            and "apps/desktop/resources" in str(step.get("with", {}).get("path", ""))
        ]
        assert not cached, "the staged runtime tree must never be cached"

    def test_the_job_needs_no_secret(self, job: dict[str, Any]) -> None:
        assert "secrets." not in yaml.safe_dump(job), (
            "PR CI must not require production secrets or a live third-party "
            "service; the pinned phase drives no model"
        )

    def test_the_job_does_not_pretend_to_set_the_fake_model(
        self, job: dict[str, Any]
    ) -> None:
        """RUNTIME_FAKE_MODEL cannot reach the supervised ai-backend from here.

        `buildServiceEnv` starts from {} and copies only
        ENV_PASSTHROUGH_ALLOWLIST (apps/desktop/main/services/service-env.ts),
        which deliberately omits it — the fail-closed guarantee stated in
        agent_runtime/execution/fake_model.py. Setting it in this job would be a
        no-op that reads like coverage, which is worse than not setting it.
        """

        assert "RUNTIME_FAKE_MODEL" not in yaml.safe_dump(job)

    def test_the_journey_is_not_a_required_check_yet(self) -> None:
        """A new e2e gate earns required status by being observed stable first."""

        protection = REPO_ROOT / "deploy" / "branch-protection.json"
        if not protection.is_file():
            pytest.skip("no branch-protection config in this checkout")
        assert JOURNEY_JOB not in protection.read_text(encoding="utf-8")
