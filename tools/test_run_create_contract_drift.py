"""The gate: a run-create field the facade forgets is a field the user loses.

See `run_create_contract_drift.py` for the four times this has happened and why
the check parses rather than imports.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from run_create_contract_drift import (
    FACADE_MODEL,
    INTENTIONALLY_NOT_RELAYED,
    MUST_NEVER_BE_RELAYED,
    NOT_RELAYED_UNREVIEWED,
    RUNTIME_MODEL,
    ModelFieldReader,
    RunCreateContractDrift,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def drift() -> RunCreateContractDrift:
    return RunCreateContractDrift(REPO_ROOT)


class TestTheContractsAreReadable:
    """If the check cannot find either model it must FAIL, never pass quietly.

    A renamed class or moved file would otherwise turn this gate into a no-op
    that keeps reporting green — the same failure mode as the bug it guards.
    """

    @pytest.mark.parametrize(
        "spec", [RUNTIME_MODEL, FACADE_MODEL], ids=["runtime", "facade"]
    )
    def test_the_model_is_found_and_has_fields(self, spec: tuple[str, str]) -> None:
        path = REPO_ROOT / spec[0]

        assert path.is_file(), f"{spec[1]} moved: {spec[0]} no longer exists"
        assert ModelFieldReader.fields(path, spec[1]), f"{spec[1]} declares no fields"

    def test_a_missing_class_raises_rather_than_returning_empty(self) -> None:
        """Pins the failure mode, so "found nothing" can never read as "nothing to do"."""

        with pytest.raises(LookupError):
            ModelFieldReader.fields(REPO_ROOT / RUNTIME_MODEL[0], "NotAClassHere")


class TestNoRunCreateFieldIsSilentlyDropped:
    def test_every_runtime_field_is_relayed_or_deliberately_withheld(
        self, drift: RunCreateContractDrift
    ) -> None:
        """The one assertion that would have caught all four regressions."""

        dropped = drift.dropped()

        assert dropped == frozenset(), (
            "these run-create fields reach the facade and are silently dropped by "
            f'Pydantic\'s extra="ignore" before ai-backend ever sees them: '
            f"{sorted(dropped)}.\n"
            "Declare each on `FacadeRunRequest`, or add it to "
            "INTENTIONALLY_NOT_RELAYED with the reason it must not be relayed."
        )

    def test_the_withheld_list_names_only_fields_that_still_exist(
        self, drift: RunCreateContractDrift
    ) -> None:
        stale = drift.stale_exemptions()

        assert stale == frozenset(), (
            f"exempted run-create fields no longer exist upstream: {sorted(stale)}. "
            "Remove them — a dead exemption silently covers whatever reuses the name."
        )

    def test_the_field_that_motivated_this_gate_is_relayed(
        self, drift: RunCreateContractDrift
    ) -> None:
        """`filesystem_bypass`, named explicitly.

        The generic assertion above already covers it. Naming it too means a
        future edit that drops the field fails with a message about the bypass
        pill rather than about a set difference.
        """

        assert "filesystem_bypass" in drift.facade_fields()


class TestTheExemptionsAreTheOnesWeMeant:
    def test_a_client_can_never_post_its_own_runtime_context(
        self, drift: RunCreateContractDrift
    ) -> None:
        """The one exemption that must never become a declaration.

        The run's sealed `filesystem_bypass` decision lives on
        `runtime_context`. If the facade relayed a client-supplied one, a caller
        could seal `mode: "bypass"` directly — skipping the master switch, the
        resolver and the whole three-tier design, and turning "the server
        decides" into "the client asserts".

        Declaring it would be an authorization hole, not a feature, so this is
        asserted on the FACADE model rather than on the exemption list: the list
        can be edited, the contract is what ships.
        """

        assert "runtime_context" in MUST_NEVER_BE_RELAYED
        assert "runtime_context" not in drift.facade_fields(), (
            "`runtime_context` is declared on FacadeRunRequest — a client could "
            "post its own sealed bypass decision"
        )

    def test_the_two_exemption_kinds_do_not_overlap(self) -> None:
        """ "Must never" and "nobody looked yet" are different claims.

        Collapsing them is how a security decision quietly becomes a backlog
        item, or a backlog item acquires the authority of a security decision.
        """

        assert MUST_NEVER_BE_RELAYED & NOT_RELAYED_UNREVIEWED == frozenset()
        assert INTENTIONALLY_NOT_RELAYED == (
            MUST_NEVER_BE_RELAYED | NOT_RELAYED_UNREVIEWED
        )
