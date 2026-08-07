"""Ratchets for code that is built but never reached — at two scales.

**Module scale.** An orphan is a module in ``src`` that nothing in ``src``
imports: built, usually tested, and never reached. Ten such modules sat
invisible for up to 2.5 months because "landed, not yet wired" is a state this
repo had no ledger for (docs/plan/ai-backend-consolidation/HANDOFF.md §4-5).
``orphan_ratchet_baseline.txt`` freezes the known set: wiring an orphan up (or
deleting it) is welcome and never fails here; a NEW unwired module fails until
it is either wired or consciously recorded — the review moment that keeps the
debt visible.

**Event scale.** The same defect at function scope is a *read model with no
producer*, and it is far harder to see. ``gate.resolved.v2`` had five readers —
``PendingWorkV2``, ``CanvasLifecycle``, the run receipt, the v2 receipt export,
the effect-ledger projection — and nothing in the tree ever wrote one, so a
workspace gate could be recorded opening and never recorded closing. Nothing
failed: the folds were all tested, and every one of the 34 ``LedgerEventType``
members is *referenced*, because reading is a reference too. Counting references
is what made an earlier pass of this analysis useless.
``ledger_event_producers`` therefore looks for the two things only a writer
does — constructing the payload model, or handing an event type and a payload
body to the same call — and fails when a member has neither.

Both halves share one rule: the list is meant to shrink, and an entry on it is
debt with a name on it, not approval.

The module scanner is a repo-root CLI (stdlib only), so this drives its real
entrypoint. If the tool tree is absent (a service-only checkout) that ratchet
cannot run and says so loudly rather than passing — a skip is a distinct
outcome, not a green. ``make verify-ai-backend`` runs from the full checkout,
which is where it enforces. The event-scale scan has no such dependency: it
reads ``src`` directly and always runs.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tests.unit.ledger_event_producers import (
    EMISSION_SIGNAL,
    PAYLOAD_MODEL_SIGNAL,
    READER_ONLY_EVENTS,
    LedgerEventProducerScan,
)

_AI_BACKEND_ROOT = Path(__file__).resolve().parents[2]
_SOURCE_ROOT = _AI_BACKEND_ROOT / "src"
_REPO_ROOT = Path(__file__).resolve().parents[4]
_SCANNER = _REPO_ROOT / "tools" / "ai-backend-smells" / "orphans.py"
_BASELINE = Path(__file__).resolve().parent / "orphan_ratchet_baseline.txt"


@pytest.fixture(scope="module")
def scanner_report() -> dict:
    """Run the real scanner CLI once and return its parsed report.

    Skips loudly when the repo tool tree is absent; enforces from a full
    checkout. A ``returncode`` other than zero fails rather than passes — a
    ratchet that cannot run must not read as green.
    """

    if not _SCANNER.is_file():
        pytest.skip(
            f"orphan scanner absent at {_SCANNER}; the ratchet runs from a full "
            "checkout (make verify-ai-backend), not a service-only tree"
        )
    proc = subprocess.run(
        [sys.executable, str(_SCANNER), str(_AI_BACKEND_ROOT)],
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert proc.returncode == 0, (
        f"orphan scanner exited {proc.returncode}; a ratchet that cannot run "
        f"must fail, not pass.\nstderr:\n{proc.stderr}"
    )
    return json.loads(proc.stdout)


@pytest.fixture(scope="module")
def producer_scan() -> LedgerEventProducerScan:
    """Scan ``src`` once for who WRITES each ledger event type.

    Deliberately ``src`` and never ``tests``: a payload that only a test ever
    constructs is the defect, not the refutation. 9,094 passing tests were green
    over this subsystem's dead half.
    """

    return LedgerEventProducerScan(_SOURCE_ROOT)


def _orphan_modules(report: dict) -> set[str]:
    return {
        entry["module"]
        for bucket in ("orphan_tested", "orphan_untested")
        for entry in report.get(bucket, ())
    }


def _baseline_modules() -> set[str]:
    lines = _BASELINE.read_text(encoding="utf-8").splitlines()
    return {
        line.strip()
        for line in lines
        if line.strip() and not line.lstrip().startswith("#")
    }


def _plant(root: Path, relative: str, source: str) -> Path:
    """Write one syntactically real module into a planted source tree."""

    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    return path


def _scan_planted(root: Path) -> set[str]:
    """Run the real scanner CLI over a planted tree and return its orphan set.

    The module-scale half is a subprocess CLI, not an importable class, so its
    planted tests drive the same entrypoint CI does rather than a reimplementation
    of its rules — the reason the fixture above shells out too.
    """

    proc = subprocess.run(
        [sys.executable, str(_SCANNER), str(root)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, f"scanner exited {proc.returncode}\n{proc.stderr}"
    return _orphan_modules(json.loads(proc.stdout))


# ---------------------------------------------------------------------------
# Module scale
# ---------------------------------------------------------------------------


def test_scanner_actually_inspected_the_source_tree(scanner_report: dict) -> None:
    """Prove the scan looked, so a broken scanner cannot pass the ratchet.

    A scan that silently walked nothing would satisfy "no new orphans" while
    inspecting zero modules — the exact failure the context-origin gate warns of
    ("a gate that returns () is indistinguishable from one that is not looking").
    Pin that it walked a substantial tree and still finds orphans.
    """

    assert scanner_report["totals"]["src_modules"] > 500
    assert _orphan_modules(scanner_report), (
        "scanner found no orphans at all — either every module is now wired (a "
        "milestone: empty the baseline and update this test) or the scan broke"
    )


def test_no_new_orphans_beyond_baseline(scanner_report: dict) -> None:
    """No module outside the baseline may be an orphan."""

    new = _orphan_modules(scanner_report) - _baseline_modules()
    assert not new, (
        f"{len(new)} module(s) became orphaned without being recorded: "
        f"{sorted(new)}.\n"
        "An orphan is built but nothing in src imports it. Either wire it in "
        "(preferred), or — if it is deliberately landing ahead of its wiring — "
        f"add it to {_BASELINE.name} AND record what it waits on in "
        "docs/audit/ai-backend-smells/PENDING-WIRINGS.md (T1.1). Adding it to "
        "the baseline with no ledger entry just re-hides the debt."
    )


def test_baseline_has_no_entry_the_scanner_cannot_reproduce(
    scanner_report: dict,
) -> None:
    """A line that stopped being an orphan must fail, not linger.

    The module half had the mirror-image of the gap its event half already
    guards (``test_reader_only_declarations_stay_current``): the ratchet failed
    only on debt *appearing*, never on debt *clearing*, so a module that got
    wired — or deleted outright — kept its line and the list quietly stopped
    shrinking. PENDING-WIRINGS.md §"Wired up" records two entries that rode
    along that way, and calls the asymmetry "the ledger's one blind spot".
    ``runtime_worker.jobs.encrypt_existing_columns`` was a third: the Postgres
    removal deleted the module and left the line.
    """

    stale = _baseline_modules() - _orphan_modules(scanner_report)
    assert not stale, (
        f"{len(stale)} baseline entr(y/ies) no longer scan as orphans: "
        f"{sorted(stale)}.\n"
        "Either the module was wired up or it was deleted — both are good news "
        f"and both mean the line comes out of {_BASELINE.name} in the SAME "
        "change, along with its PENDING-WIRINGS.md row. The list is meant to "
        "shrink; an entry nothing can reproduce is where debt hides."
    )


def test_a_package_facade_does_not_vouch_for_its_own_submodule(tmp_path: Path) -> None:
    """The blind spot that hid a whole broken package for months.

    ``agent_runtime.persistence.schema`` lost its last outside importer with the
    Postgres backend and went import-time BROKEN — it read a migrations file the
    same commit deleted — yet this scan stayed green, because ``schema/__init__``
    still imported ``schema.postgres`` and ``__init__`` is itself never a
    candidate (it is in ``ENTRY_HINTS``). The facade vouched for its submodule
    and nothing vouched for the facade.
    """

    _plant(tmp_path, "src/pkg/__init__.py", "from pkg.dead import Thing\n")
    _plant(tmp_path, "src/pkg/dead.py", "class Thing:\n    pass\n")

    assert "pkg.dead" in _scan_planted(tmp_path)


def test_an_outside_import_through_the_facade_credits_the_real_module(
    tmp_path: Path,
) -> None:
    """``from pkg import Thing`` is a use of the module that defines Thing.

    The fix must not simply stop trusting ``__init__`` — that would report every
    re-exported module in the tree as dead. The re-export is *resolved*: it
    records what ``pkg.Thing`` means, and an import from outside the package
    spends that credit on ``pkg.live``.
    """

    _plant(tmp_path, "src/pkg/__init__.py", "from pkg.live import Thing\n")
    _plant(tmp_path, "src/pkg/live.py", "class Thing:\n    pass\n")
    _plant(tmp_path, "src/consumer.py", "from pkg import Thing\n\nuse = Thing\n")

    assert "pkg.live" not in _scan_planted(tmp_path)


def test_facade_resolution_follows_a_nested_re_export(tmp_path: Path) -> None:
    """Facades nest, so one hop of resolution is not enough.

    ``from agent_runtime import RuntimeContract`` reaches ``agent_runtime.execution``
    which reaches ``agent_runtime.execution.contracts``. A single-hop resolver
    would credit the middle package and report the leaf — the module that
    actually defines the symbol — as an orphan.
    """

    _plant(tmp_path, "src/outer/__init__.py", "from outer.inner import Thing\n")
    _plant(
        tmp_path, "src/outer/inner/__init__.py", "from outer.inner.leaf import Thing\n"
    )
    _plant(tmp_path, "src/outer/inner/leaf.py", "class Thing:\n    pass\n")
    _plant(tmp_path, "src/consumer.py", "from outer import Thing\n\nuse = Thing\n")

    assert "outer.inner.leaf" not in _scan_planted(tmp_path)


def test_a_logger_name_is_not_lazy_wiring(tmp_path: Path) -> None:
    """A dotted string naming a module is not always an import of it.

    ``_LOGGER_NAME = "runtime_worker.jobs.routine_scheduler"`` sits in
    ``routine_pre_fire_gate.py`` and quietly cleared a genuinely unwired 907-line
    job out of this report. That is worse than missing a new orphan: it removed
    debt the ledger had already recorded, so the baseline entry read as stale.
    """

    _plant(
        tmp_path,
        "src/gate.py",
        "from typing import ClassVar\n\n\nclass Gate:\n"
        '    _LOGGER_NAME: ClassVar[str] = "jobs.scheduler"\n',
    )
    _plant(tmp_path, "src/jobs/__init__.py", "")
    _plant(tmp_path, "src/jobs/scheduler.py", "def run() -> None:\n    return None\n")

    assert "jobs.scheduler" in _scan_planted(tmp_path)


def test_a_provider_module_literal_is_still_lazy_wiring(tmp_path: Path) -> None:
    """The importlib registry pattern must keep counting as a real import.

    ``runtime_adapters/registry.py`` names its two providers with
    ``provider_module="runtime_adapters.providers.file_provider"`` and imports
    them through ``importlib``. Tightening the literal rule to exclude logger
    names must not re-flag those: they are wired, just late.
    """

    _plant(
        tmp_path,
        "src/registry.py",
        "def register() -> object:\n"
        '    return Descriptor(provider_module="providers.file_provider")\n',
    )
    _plant(tmp_path, "src/providers/__init__.py", "")
    _plant(
        tmp_path,
        "src/providers/file_provider.py",
        "def build() -> None:\n    return None\n",
    )

    assert "providers.file_provider" not in _scan_planted(tmp_path)


# ---------------------------------------------------------------------------
# Event scale
# ---------------------------------------------------------------------------


def test_every_ledger_event_type_has_a_producer(
    producer_scan: LedgerEventProducerScan,
) -> None:
    """No member of the vocabulary may be readable but unwritable."""

    orphaned = producer_scan.undeclared_producerless_members()
    assert not orphaned, (
        f"{len(orphaned)} ledger event type(s) have readers and no writer: "
        f"{list(orphaned)}.\n"
        "Nothing in src constructs their payload model or hands their event "
        "type to a call with a body, so every fold, projection and receipt that "
        "reads them is folding over an event that can never arrive — which is "
        "exactly how gate.resolved.v2 shipped with five readers and no "
        "producer. Either write the producer (preferred), or declare the member "
        "in READER_ONLY_EVENTS in tests/unit/ledger_event_producers.py WITH the "
        "reason it cannot have one."
    )


def test_reader_only_declarations_stay_current(
    producer_scan: LedgerEventProducerScan,
) -> None:
    """A declaration that stopped being true must fail, not linger.

    The failure mode a baseline invites is one-way growth: entries go in and
    never come out, and the file becomes the place the debt hides. This fails
    when an entry names a member the enum no longer has, when a declared
    reader-only member has since acquired a producer (prune it in the same
    change that wired it), and when an entry carries no reason.
    """

    assert producer_scan.stale_reader_only_declarations() == ()


def test_producer_scan_actually_inspected_the_source_tree(
    producer_scan: LedgerEventProducerScan,
) -> None:
    """Prove the scan looked — an empty scan would pass the check above.

    Same reasoning as the module-scale canary: a scan that walked nothing finds
    no producerless members, because it finds no members at all. Pin that the
    vocabulary is non-trivial and that real producers were located across
    several modules, not one.
    """

    assert len(producer_scan.members) >= 34
    assert len(producer_scan.sites) > len(producer_scan.members)
    assert len({site.path for site in producer_scan.sites}) > 5


def test_producer_scan_ignores_reader_only_references(tmp_path: Path) -> None:
    """A fold that reads an event type is not a producer of it.

    This is the whole reason the gate exists. ``gate.resolved.v2`` appeared in
    ``PendingWorkV2``'s ``frozenset`` of interesting types and in a fold's
    comparison; a reference-counting check would have cleared it on that
    evidence alone and left the bug in place for another release.
    """

    _plant(
        tmp_path,
        "agent_runtime/planted/fold.py",
        "from agent_runtime.surfaces_v2.ledger_models import LedgerEventType\n"
        "\n"
        "INTERESTING = frozenset({LedgerEventType.GATE_RESOLVED_V2})\n"
        "\n"
        "def fold(event_type, gates, gate_id):\n"
        "    if event_type == LedgerEventType.GATE_RESOLVED_V2.value:\n"
        "        gates.pop(gate_id, None)\n"
        "    return sorted(INTERESTING)\n",
    )

    scan = LedgerEventProducerScan(tmp_path)

    assert scan.sites_for("GATE_RESOLVED_V2") == ()
    assert "GATE_RESOLVED_V2" in scan.members_without_a_producer()


def test_producer_scan_does_not_mistake_validation_for_production(
    tmp_path: Path,
) -> None:
    """Parsing an untrusted dict through the payload model is a read.

    ``WorkLedgerVocabulary.validate_payload`` reaches every payload model this
    way. If the gate counted it, the vocabulary module itself would vouch for
    all 34 members and the check would be worthless.
    """

    _plant(
        tmp_path,
        "agent_runtime/planted/validate.py",
        "from agent_runtime.surfaces_v2.ledger_models import GateResolvedV2Payload\n"
        "\n"
        "def parse(raw):\n"
        "    return GateResolvedV2Payload.model_validate(raw)\n",
    )

    scan = LedgerEventProducerScan(tmp_path)

    assert scan.sites_for("GATE_RESOLVED_V2") == ()


def test_producer_scan_sees_a_payload_model_construction(tmp_path: Path) -> None:
    """Building the payload model field-by-field is a writer's move."""

    _plant(
        tmp_path,
        "agent_runtime/planted/build.py",
        "from agent_runtime.surfaces_v2.ledger_models import GateResolvedV2Payload\n"
        "\n"
        "def build(gate_id):\n"
        "    return GateResolvedV2Payload(\n"
        "        v=1, gate_id=gate_id, decision='denied', actor='policy'\n"
        "    )\n",
    )

    scan = LedgerEventProducerScan(tmp_path)

    sites = scan.sites_for("GATE_RESOLVED_V2")
    assert [site.signal for site in sites] == [PAYLOAD_MODEL_SIGNAL]
    assert sites[0].path == "agent_runtime/planted/build.py"


def test_producer_scan_sees_an_emission_through_an_unnamed_seam(
    tmp_path: Path,
) -> None:
    """A body riding with the event type is the signal — not the callee's name.

    ``record_it`` is in no allow-list, and deliberately so: the append seams in
    this service are spelled ``append_api_event``, ``ledger.emit``,
    ``append_stage_event``, ``_sign`` and ``_append``, and the next one has not
    been written yet. A gate that enumerated seam names would silently stop
    seeing producers the day somebody added a sixth.
    """

    _plant(
        tmp_path,
        "agent_runtime/planted/emit.py",
        "from agent_runtime.surfaces_v2.ledger_models import LedgerEventType\n"
        "\n"
        "async def close(sink, gate_id):\n"
        "    await sink.record_it(\n"
        "        LedgerEventType.GATE_RESOLVED_V2,\n"
        "        {'v': 1, 'gate_id': gate_id, 'decision': 'denied'},\n"
        "    )\n",
    )

    scan = LedgerEventProducerScan(tmp_path)

    assert [site.signal for site in scan.sites_for("GATE_RESOLVED_V2")] == [
        EMISSION_SIGNAL
    ]


def test_producer_scan_resolves_an_event_type_named_by_a_module_constant(
    tmp_path: Path,
) -> None:
    """``_EVENT_* = LedgerEventType.X.value`` is how the effect modules spell it.

    ``effects/staging.py`` and ``effects/coordinator.py`` bind every event type
    to a module constant and never mention the enum at the emit site, so a scan
    that only matched ``LedgerEventType.X`` would report six live producers as
    missing and force six false baseline entries.
    """

    _plant(
        tmp_path,
        "agent_runtime/planted/constant.py",
        "from agent_runtime.surfaces_v2.ledger_models import LedgerEventType\n"
        "\n"
        "_EVENT_RESOLVED = LedgerEventType.GATE_RESOLVED_V2.value\n"
        "\n"
        "async def close(ledger, payload):\n"
        "    await ledger.append_stage_event(\n"
        "        event_type=_EVENT_RESOLVED, payload=payload.model_dump()\n"
        "    )\n",
    )

    scan = LedgerEventProducerScan(tmp_path)

    assert [site.signal for site in scan.sites_for("GATE_RESOLVED_V2")] == [
        EMISSION_SIGNAL
    ]


def test_reader_only_declarations_carry_a_reason() -> None:
    """The declaration format cannot express an unexplained entry.

    An empty tuple is the correct state and this test is trivially true today;
    it exists so the requirement survives the first entry, when the temptation
    to paste in a member name and move on is highest.
    """

    assert all(entry.reason.strip() for entry in READER_ONLY_EVENTS)
