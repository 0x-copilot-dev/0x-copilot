"""What the model is TOLD about folders the user attached.

Permission without instruction is not a capability, and this block is the proof:
the rules and the floor both allowed a write inside a writable grant while the
model still refused, verbatim — "I can't write to /Users/…/seed.csv from here
because I only have read access to that filesystem path". It never called
`write_file`. The prompt, not the policy, was the blocker.

So the property under test is narrower than "the wording is nice". It is:

    the prompt must never claim a capability the rule set does not grant,
    and never withhold one it does.

Both directions have now bitten. The refusal above was the withholding
direction. The claiming direction arrived with bypass: this block promised "no
staging, no separate approval" unconditionally, which stopped being true the
moment a write under Manual began parking on a consent card — a model told its
write is unconditional, whose write then pauses, has been handed a reason to
narrate a problem instead of making the call.

These assertions are on MEANING, not phrasing: a substring that would change the
model's belief about what it may do, rather than the sentence that carries it.
"""

from __future__ import annotations

from agent_runtime.capabilities.desktop.host_filesystem import GrantedRoot
from agent_runtime.execution.factory import _instructions_with_granted_folders
from agent_runtime.execution.filesystem_bypass import (
    MANUAL_FILESYSTEM_BYPASS,
    FilesystemBypassDecision,
    FilesystemBypassMode,
)

BASE = "You are a helpful assistant."
GRANTED = "/Users/ada/Projects"

MANUAL = MANUAL_FILESYSTEM_BYPASS
BYPASS = FilesystemBypassDecision(master_enabled=True, mode=FilesystemBypassMode.BYPASS)


def _prompt(
    *roots: GrantedRoot,
    bypass: FilesystemBypassDecision = MANUAL,
) -> str:
    return _instructions_with_granted_folders(
        instructions=BASE, roots=roots or None, bypass=bypass
    )


class TestNoGrantsCostsNothing:
    def test_absent_roots_leave_the_prompt_byte_identical(self) -> None:
        """A run with no grants must pay no token tax and advertise no route."""

        assert _instructions_with_granted_folders(instructions=BASE, roots=None) == BASE

    def test_empty_roots_leave_the_prompt_byte_identical(self) -> None:
        assert _instructions_with_granted_folders(instructions=BASE, roots=()) == BASE


class TestTheFoldersAreNamedByTheirRealPaths:
    def test_the_host_path_appears_verbatim(self) -> None:
        """Real paths, because they are what the user recognises.

        The model must narrate "saved to /Users/ada/Projects/notes.md", not a
        virtual mount id the user has never seen.
        """

        assert GRANTED in _prompt(GrantedRoot(path=GRANTED, writable=True))

    def test_a_read_only_grant_is_described_as_read_only(self) -> None:
        prompt = _prompt(GrantedRoot(path=GRANTED, writable=False))

        assert "read only" in prompt
        assert "read and write" not in prompt

    def test_a_read_only_grant_gets_no_write_guidance_at_all(self) -> None:
        """The rules deny that write, so the prompt must not imply otherwise.

        This is the claiming direction of the property, at its simplest: no
        `write_file` advice for a folder where `write_file` is refused.
        """

        prompt = _prompt(GrantedRoot(path=GRANTED, writable=False))

        assert "write_file" not in prompt
        assert "edit_file" not in prompt


class TestTheApprovalSentenceTracksTheRules:
    """The half that must move with the bypass pill, and the half that must not."""

    def test_manual_tells_the_model_a_pause_is_coming(self) -> None:
        """Under Manual every write parks. Saying so is what keeps the model calling.

        Asserted on the two beliefs that matter — that a pause happens, and that
        it is not a refusal — rather than on the sentence.
        """

        prompt = _prompt(GrantedRoot(path=GRANTED, writable=True), bypass=MANUAL)

        assert "confirmed by the user" in prompt
        assert "not a refusal" in prompt

    def test_manual_never_promises_writes_are_unapproved(self) -> None:
        """The false claim that motivated this test, pinned so it cannot return."""

        prompt = _prompt(GrantedRoot(path=GRANTED, writable=True), bypass=MANUAL)

        assert "no separate approval" not in prompt

    def test_bypass_says_the_confirmation_is_off(self) -> None:
        prompt = _prompt(GrantedRoot(path=GRANTED, writable=True), bypass=BYPASS)

        assert "run immediately" in prompt
        assert "not a refusal" not in prompt

    def test_the_tool_guidance_is_the_same_in_both_postures(self) -> None:
        """Bypass changes WHETHER a write pauses, never HOW a write is made.

        `write_file` refuses an existing path and `edit_file` is the tool for one
        that exists — a fact about deepagents, not about consent. If a future
        edit lets the posture change this, the model would learn a different API
        depending on a pill, which is how a capability claim drifts out of step
        with the rules.
        """

        for bypass in (MANUAL, BYPASS):
            prompt = _prompt(GrantedRoot(path=GRANTED, writable=True), bypass=bypass)
            assert "`write_file` CREATES" in prompt
            assert "use `edit_file`" in prompt

    def test_the_refusal_boundary_is_stated_in_both_postures(self) -> None:
        """Bypass must not read as "you may now write anywhere"."""

        for bypass in (MANUAL, BYPASS):
            prompt = _prompt(GrantedRoot(path=GRANTED, writable=True), bypass=bypass)
            assert "writing outside" in prompt


class TestSeveralGrants:
    def test_every_attached_folder_is_named(self) -> None:
        other = "/Users/ada/Reports"
        prompt = _prompt(
            GrantedRoot(path=GRANTED, writable=True),
            GrantedRoot(path=other, writable=False),
        )

        assert GRANTED in prompt
        assert other in prompt

    def test_one_writable_grant_is_enough_to_earn_the_write_guidance(self) -> None:
        """Mixed modes: the per-folder line carries the mode, the block explains it."""

        prompt = _prompt(
            GrantedRoot(path=GRANTED, writable=True),
            GrantedRoot(path="/Users/ada/Reports", writable=False),
        )

        assert "read and write" in prompt
        assert "read only" in prompt
        assert "`write_file` CREATES" in prompt
