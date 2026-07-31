"""Tests for the branch-protection reconciler.

The bug these exist to prevent: the previous version of this logic lived in a
YAML heredoc, carried a module-level ``return``, and raised
``SyntaxError: 'return' outside function`` on every dispatch. Nothing caught it
because nothing could see it. The first test below is therefore the important
one -- it compiles the module the way CPython does, which is stricter than
``ast.parse``. The rest pin the reconcile decisions against a fake CLI so no
test touches the network or mutates a repository.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from apply_branch_protection import (
    CONFIG_PATH,
    GitHubCli,
    RulesetConfigError,
    RulesetPlanner,
    RulesetReconciler,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def _embedded_python_blocks() -> list[tuple[str, str]]:
    """Return ``(workflow_name, source)`` for every heredoc Python block in CI."""
    import re

    blocks: list[tuple[str, str]] = []
    for workflow in sorted((REPO_ROOT / ".github/workflows").glob("*.yml")):
        text = workflow.read_text()
        for match in re.finditer(
            r"python3?\s*<<\s*'(\w+)'\n(.*?)\n\s*\1\n", text, re.S
        ):
            blocks.append((workflow.name, match.group(2)))
    return blocks


def test_every_embedded_workflow_script_compiles() -> None:
    """No workflow may carry Python that CPython would refuse to compile.

    ``ast.parse`` accepts a module-level ``return``; ``compile`` does not, and
    ``compile`` is what actually runs. Checking with the weaker one is how the
    original defect survived long enough to leave ``main`` unprotected.

    ``${{ ... }}`` is replaced with a bare identifier first: the file on disk is
    a template, and what must compile is the substituted form.
    """

    import re
    import textwrap

    offenders: list[str] = []
    for name, raw in _embedded_python_blocks():
        source = textwrap.dedent(re.sub(r"\$\{\{.*?\}\}", "_EXPR_", raw, flags=re.S))
        try:
            compile(source, name, "exec")
        except SyntaxError as error:
            offenders.append(f"{name}: {error.msg} (line {error.lineno})")
    assert not offenders, "embedded workflow scripts must compile: " + "; ".join(
        offenders
    )


def test_no_embedded_script_interpolates_a_workflow_expression() -> None:
    """Embedded Python must read values from the environment, not from templating.

    Two distinct defects live in this pattern, and the repo has had both:

    * Type corruption. A ``type: boolean`` input substitutes as lowercase
      ``true``/``false``. ``"force_deploy": ${{ inputs.force_deploy }},`` became
      ``"force_deploy": true,`` -- valid syntax, ``NameError`` at runtime -- so
      the deploy manifest step failed on every run.
    * Script injection. Any value pasted into the program text can end the
      string literal it landed in. ``env:`` keeps data out of the source.
    """

    import re

    offenders: list[str] = []
    for name, raw in _embedded_python_blocks():
        for expression in re.findall(r"\$\{\{.*?\}\}", raw, flags=re.S):
            offenders.append(f"{name}: {' '.join(expression.split())}")
    assert not offenders, (
        "pass these through `env:` and read them with os.environ instead: "
        + "; ".join(offenders)
    )


class _FakeCli(GitHubCli):
    """Records calls instead of performing them."""

    def __init__(self, existing: list[dict[str, Any]], full: dict[int, dict[str, Any]]):
        super().__init__("owner/repo")
        self._existing = existing
        self._full = full
        self.created: list[dict[str, Any]] = []
        self.updated: list[tuple[int, dict[str, Any]]] = []

    def list_rulesets(self) -> list[dict[str, Any]]:
        return self._existing

    def get_ruleset(self, ruleset_id: int) -> dict[str, Any]:
        return self._full[ruleset_id]

    def create_ruleset(self, desired: dict[str, Any]) -> None:
        self.created.append(desired)

    def update_ruleset(self, ruleset_id: int, desired: dict[str, Any]) -> None:
        self.updated.append((ruleset_id, desired))


def _desired(name: str = "main-branch-protection") -> dict[str, Any]:
    return {
        "name": name,
        "target": "branch",
        "enforcement": "active",
        "conditions": {"ref_name": {"include": ["refs/heads/main"], "exclude": []}},
        "bypass_actors": [],
        "rules": [{"type": "deletion"}],
    }


def test_the_repository_config_loads_and_declares_main_and_dev() -> None:
    rulesets = RulesetPlanner.load_desired(CONFIG_PATH)
    names = {ruleset["name"] for ruleset in rulesets}
    assert names == {"main-branch-protection", "dev-branch-protection"}
    # Documentation keys must never reach the API.
    assert not any(key.startswith("$") for r in rulesets for key in r)


def test_config_rejects_duplicate_names(tmp_path: Path) -> None:
    path = tmp_path / "bp.json"
    path.write_text(json.dumps({"rulesets": [_desired(), _desired()]}))
    with pytest.raises(RulesetConfigError, match="duplicate"):
        RulesetPlanner.load_desired(path)


def test_config_rejects_a_bare_ruleset_object(tmp_path: Path) -> None:
    """The old single-object shape must fail loudly, not be applied as one ruleset."""
    path = tmp_path / "bp.json"
    path.write_text(json.dumps(_desired()))
    with pytest.raises(RulesetConfigError, match="rulesets"):
        RulesetPlanner.load_desired(path)


def test_a_missing_ruleset_is_created_only_when_applying() -> None:
    desired = [_desired()]
    dry = _FakeCli([], {})
    assert RulesetReconciler(dry, dry_run=True).run(desired) == 1
    assert dry.created == []

    live = _FakeCli([], {})
    assert RulesetReconciler(live, dry_run=False).run(desired) == 1
    assert live.created == desired


def test_an_identical_ruleset_is_a_no_op() -> None:
    desired = _desired()
    existing = [{"name": desired["name"], "id": 7}]
    # Server echoes back the same content plus metadata the config never sets.
    full = {7: {**desired, "id": 7, "created_at": "2026-01-01", "_links": {}}}
    cli = _FakeCli(existing, full)
    assert RulesetReconciler(cli, dry_run=False).run([desired]) == 0
    assert cli.updated == []


def test_drift_is_updated_and_the_diff_is_non_empty() -> None:
    desired = _desired()
    drifted = {**desired, "enforcement": "disabled"}
    cli = _FakeCli([{"name": desired["name"], "id": 7}], {7: {**drifted, "id": 7}})
    assert RulesetPlanner.diff(drifted, desired) != ""
    assert RulesetReconciler(cli, dry_run=False).run([desired]) == 1
    assert cli.updated == [(7, desired)]


def test_server_metadata_alone_never_counts_as_drift() -> None:
    desired = _desired()
    noisy = {**desired, "id": 7, "node_id": "x", "updated_at": "now", "source": "repo"}
    assert RulesetPlanner.diff(noisy, desired) == ""
