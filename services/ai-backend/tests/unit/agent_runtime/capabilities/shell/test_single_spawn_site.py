"""§16.6 guardrail: ``agent_runtime`` has exactly one place that spawns a process.

The claim the rest of this package rests on is that every command goes through
:mod:`agent_runtime.capabilities.shell.executor` — the allowlist environment, the
process-group teardown and the output bound are all properties of *that* call
site, and a second one anywhere in the service would have none of them.

The scan is an AST walk rather than a substring grep, deliberately: the
executor's own docstring contains the words ``shell=True`` and
``create_subprocess_shell`` while saying it does not use them, and a grep cannot
tell a sentence from a call. ``compile``-level parsing also means a file that is
syntactically broken fails this test loudly instead of being skipped — the
failure mode a heredoc-shaped gate in this repository has already paid for once.
"""

from __future__ import annotations

import ast
from pathlib import Path

import agent_runtime
from agent_runtime.capabilities.shell import executor as executor_module


class SourceTreeMixin:
    """Locates the package under test through the imported module, not a path.

    Resolving from ``agent_runtime.__file__`` means the gate always scans the
    tree that was actually imported — in a worktree that is the worktree's copy,
    not the main checkout's.
    """

    #: Spawning calls that must not appear anywhere in the service. Matched on
    #: the unparsed dotted call target, so ``subprocess.run`` is caught while an
    #: unrelated ``self.run`` is not.
    FORBIDDEN_CALLS = (
        "create_subprocess_shell",
        "subprocess.Popen",
        "subprocess.run",
        "subprocess.call",
        "subprocess.check_call",
        "subprocess.check_output",
        "os.system",
        "os.popen",
        "os.execv",
        "os.execvp",
        "os.spawnv",
        "os.posix_spawn",
    )

    #: The single permitted spawn primitive, and the only file allowed to call it.
    SPAWN_CALL = "create_subprocess_exec"

    def package_root(self) -> Path:
        return Path(agent_runtime.__file__).resolve().parent

    def executor_path(self) -> Path:
        return Path(executor_module.__file__).resolve()

    def python_files(self) -> list[Path]:
        return sorted(self.package_root().rglob("*.py"))

    def calls_in(self, path: Path) -> list[tuple[str, int]]:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        found: list[tuple[str, int]] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                found.append((ast.unparse(node.func), node.lineno))
        return found


class TestSingleSpawnSite(SourceTreeMixin):
    def test_the_scan_actually_reads_the_worktree_under_test(self) -> None:
        """A gate that scans the wrong tree reports a green over nothing."""

        files = self.python_files()

        assert len(files) > 100
        assert self.executor_path() in files

    def test_no_module_spawns_a_shell_or_a_bare_subprocess(self) -> None:
        offenders = [
            f"{path.relative_to(self.package_root())}:{line} -> {target}"
            for path in self.python_files()
            for target, line in self.calls_in(path)
            if target.endswith(self.FORBIDDEN_CALLS)
        ]

        assert offenders == []

    def test_only_the_executor_calls_the_spawn_primitive(self) -> None:
        """One call site, so one place holds the environment and the teardown."""

        spawners = {
            path
            for path in self.python_files()
            for target, _ in self.calls_in(path)
            if target.endswith(self.SPAWN_CALL)
        }

        assert spawners == {self.executor_path()}

    def test_nothing_passes_shell_true(self) -> None:
        """The keyword, as an argument — not as the word in a docstring."""

        offenders: list[str] = []
        for path in self.python_files():
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                for keyword in node.keywords:
                    if (
                        keyword.arg == "shell"
                        and isinstance(keyword.value, ast.Constant)
                        and keyword.value.value is True
                    ):
                        offenders.append(
                            f"{path.relative_to(self.package_root())}:{node.lineno}"
                        )

        assert offenders == []

    def test_the_executor_starts_a_new_session(self) -> None:
        """``start_new_session`` is what makes the group kill reach the children.

        Asserted structurally because losing it would not fail any behavioural
        test on a command that has no children — it would only fail the day a
        command spawns one, in production.
        """

        source = self.executor_path().read_text(encoding="utf-8")
        tree = ast.parse(source)
        spawns = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and ast.unparse(node.func).endswith(self.SPAWN_CALL)
        ]

        assert len(spawns) == 1
        keywords = {keyword.arg for keyword in spawns[0].keywords}
        assert {"start_new_session", "cwd", "env", "stdin"} <= keywords
