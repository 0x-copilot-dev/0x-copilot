"""The rollover rule and the untrusted-input boundary of ``TodoListProjector``.

:class:`TestTheFourthWriteThatWasBlamed` is a standing acquittal rather than a
guard against a bug that ever existed here — see its own docstring.
"""

from __future__ import annotations

from agent_runtime.capabilities.todo_list import (
    AgentTodoStatus,
    TodoListProjector,
)


class _WriteMixin:
    """Build ``write_todos`` argument mappings the way the model sends them."""

    RUN = "run_1"

    @staticmethod
    def args(*todos: tuple[str, str]) -> dict[str, object]:
        """Compose a ``{"todos": [...]}`` argument mapping from (content, status) pairs."""
        return {
            "todos": [
                {"content": content, "status": status} for content, status in todos
            ]
        }

    def write(
        self,
        projector: TodoListProjector,
        *todos: tuple[str, str],
        run_id: str | None = None,
        subagent_id: str | None = None,
    ) -> object:
        """Project one write and return the resulting snapshot (or ``None``)."""
        return projector.project(
            run_id=run_id or self.RUN,
            subagent_id=subagent_id,
            arguments=self.args(*todos),
        )


class TestTodoListGenerations(_WriteMixin):
    """One list stays one list until every row of it is done."""

    def test_first_write_opens_generation_one(self) -> None:
        snapshot = self.write(
            TodoListProjector(),
            ("pull the export", "in_progress"),
            ("reconcile", "pending"),
        )

        assert snapshot is not None
        assert snapshot.generation == 1
        assert snapshot.list_id == "run_1:todos:1"
        assert [todo.status for todo in snapshot.todos] == [
            AgentTodoStatus.IN_PROGRESS,
            AgentTodoStatus.PENDING,
        ]

    def test_completing_a_row_revises_the_same_list(self) -> None:
        projector = TodoListProjector()
        first = self.write(
            projector, ("pull the export", "in_progress"), ("reconcile", "pending")
        )
        second = self.write(
            projector, ("pull the export", "completed"), ("reconcile", "in_progress")
        )

        assert first is not None and second is not None
        assert second.generation == 1
        assert second.list_id == first.list_id

    def test_appending_a_row_revises_the_same_list(self) -> None:
        # Appending is how the agent adds work it only discovered mid-run. The
        # tool replaces the array, so an append is indistinguishable from a new
        # plan by shape alone — only the unfinished predecessor separates them.
        projector = TodoListProjector()
        first = self.write(
            projector, ("pull the export", "completed"), ("reconcile", "in_progress")
        )
        second = self.write(
            projector,
            ("pull the export", "completed"),
            ("reconcile", "in_progress"),
            ("resolve 14 orphan ids", "pending"),
        )

        assert first is not None and second is not None
        assert second.generation == 1
        assert second.list_id == first.list_id
        assert len(second.todos) == 3

    def test_write_after_a_finished_list_opens_the_next_generation(self) -> None:
        projector = TodoListProjector()
        self.write(projector, ("pull the export", "completed"))
        rolled = self.write(projector, ("draft the exec note", "in_progress"))

        assert rolled is not None
        assert rolled.generation == 2
        assert rolled.list_id == "run_1:todos:2"

    def test_resending_a_finished_list_verbatim_emits_nothing(self) -> None:
        # Without this guard a re-send of an already-complete list reads as
        # "the agent started a fresh plan" and bumps the generation for a call
        # that changed nothing.
        projector = TodoListProjector()
        self.write(projector, ("pull the export", "completed"))

        assert self.write(projector, ("pull the export", "completed")) is None

    def test_partially_complete_list_never_rolls_over(self) -> None:
        projector = TodoListProjector()
        self.write(projector, ("a", "completed"), ("b", "pending"))
        revised = self.write(projector, ("c", "in_progress"), ("d", "pending"))

        assert revised is not None
        assert revised.generation == 1

    def test_ids_are_replay_stable_not_generated(self) -> None:
        # Re-projecting the same run must yield byte-identical snapshots, so a
        # client folding replayed events lands where the live stream left it.
        first = TodoListProjector()
        second = TodoListProjector()
        a = self.write(first, ("step", "completed"))
        self.write(first, ("next", "pending"))
        b = self.write(second, ("step", "completed"))
        self.write(second, ("next", "pending"))

        assert a is not None and b is not None
        assert a.list_id == b.list_id


class TestTodoListLanes(_WriteMixin):
    """Deep Agents gives every subagent its own ``TodoListMiddleware``."""

    def test_subagent_list_does_not_roll_over_the_parent(self) -> None:
        projector = TodoListProjector()
        self.write(projector, ("parent step", "completed"))
        child = self.write(
            projector, ("child step", "in_progress"), subagent_id="task_1"
        )

        assert child is not None
        assert child.generation == 1
        assert child.list_id == "run_1:task_1:todos:1"

        # The parent's own next write still sees a finished parent list.
        parent = self.write(projector, ("parent step two", "in_progress"))
        assert parent is not None
        assert parent.generation == 2

    def test_discard_run_frees_every_lane_of_that_run(self) -> None:
        projector = TodoListProjector()
        self.write(projector, ("step", "completed"))
        self.write(projector, ("child", "completed"), subagent_id="task_1")
        self.write(projector, ("other run", "completed"), run_id="run_2")
        projector.discard_run(self.RUN)

        # Both lanes of run_1 restart at generation 1; run_2 is untouched.
        assert (reopened := self.write(projector, ("fresh", "pending"))) is not None
        assert reopened.generation == 1
        assert (
            child := self.write(projector, ("fresh", "pending"), subagent_id="task_1")
        ) is not None
        assert child.generation == 1
        assert (
            other := self.write(projector, ("next", "pending"), run_id="run_2")
        ) is not None
        assert other.generation == 2


class TestTodoListUntrustedInput(_WriteMixin):
    """``todos`` is model output arriving over a tool boundary."""

    def test_missing_or_wrongly_typed_todos_emits_nothing(self) -> None:
        projector = TodoListProjector()

        assert (
            projector.project(run_id=self.RUN, subagent_id=None, arguments={}) is None
        )
        assert (
            projector.project(
                run_id=self.RUN,
                subagent_id=None,
                arguments={"todos": "check the thing"},
            )
            is None
        )
        assert (
            projector.project(
                run_id=self.RUN, subagent_id=None, arguments={"todos": ["bare string"]}
            )
            is None
        )

    def test_unknown_status_drops_the_whole_list(self) -> None:
        # Half a checklist read as the agent's plan is worse than no panel: a
        # row the client cannot place would render as pending and read as work
        # still to come.
        projector = TodoListProjector()

        assert self.write(projector, ("a", "pending"), ("b", "blocked")) is None

    def test_oversized_list_is_rejected_whole(self) -> None:
        projector = TodoListProjector()
        overflow = tuple(("step", "pending") for _ in range(65))

        assert self.write(projector, *overflow) is None

    def test_oversized_content_is_rejected_whole(self) -> None:
        projector = TodoListProjector()

        assert self.write(projector, ("x" * 501, "pending")) is None

    def test_empty_list_is_a_legitimate_clear(self) -> None:
        projector = TodoListProjector()
        self.write(projector, ("step", "in_progress"))
        cleared = projector.project(
            run_id=self.RUN, subagent_id=None, arguments={"todos": []}
        )

        assert cleared is not None
        assert cleared.todos == ()
        assert cleared.generation == 1


class TestTheFourthWriteThatWasBlamed(_WriteMixin):
    """The four writes a live benchmark reported as a ``write_todos`` crash.

    Benchmark run ``2d30ca71ad2b4d8da58ee88deb9d4036`` failed a checklist task
    and its ledger closed the fourth ``write_todos`` call with
    ``safe_error_code=tool_exception``; the published finding read that as the
    tool throwing on its fourth call. Replayed here verbatim — the four
    argument lists are the ones the model actually sent, recovered from that
    run's ``tool_invocations.jsonl`` and its ``tool_call_delta`` buffers — the
    sequence projects four clean snapshots and raises nothing.

    That matters because the rollover rule is the only stateful thing in this
    class, so "the Nth call behaves differently from the first" is exactly the
    shape of bug it could have had. It did not: no list was ever completed, so
    nothing rolled over. The real cause was run-level (see
    ``tests/unit/runtime_worker/test_inflight_tool_call_run_failure.py``), and
    this class exists so a future reader does not re-suspect the projector.
    """

    #: The plan, in the order the model wrote it.
    STEPS = (
        "List three European capitals",
        "Identify the country of each capital",
        "Name one river in each country",
        "Produce final table of all three rows",
    )

    #: One row of statuses per call: plan, then one step at a time.
    SEQUENCE = (
        ("pending", "pending", "pending", "pending"),
        ("completed", "in_progress", "pending", "pending"),
        ("completed", "completed", "in_progress", "pending"),
        ("completed", "completed", "completed", "in_progress"),
    )

    def _replay(
        self, projector: TodoListProjector, statuses: tuple[str, ...]
    ) -> object:
        """Project one call of the recorded sequence."""
        return self.write(projector, *zip(self.STEPS, statuses, strict=True))

    def test_all_four_recorded_writes_project_cleanly(self) -> None:
        projector = TodoListProjector()

        snapshots = [self._replay(projector, statuses) for statuses in self.SEQUENCE]

        assert all(snapshot is not None for snapshot in snapshots)
        assert [snapshot.generation for snapshot in snapshots] == [1, 1, 1, 1]
        assert {snapshot.list_id for snapshot in snapshots} == {"run_1:todos:1"}

    def test_the_fourth_write_carries_the_progress_it_reported(self) -> None:
        # The row-level detail is the point of the panel, and the fourth call
        # is the one that supposedly failed. It resolves to three done and one
        # underway — a list still in progress, which is why it did not roll.
        projector = TodoListProjector()
        for statuses in self.SEQUENCE:
            fourth = self._replay(projector, statuses)

        assert fourth is not None
        assert [todo.status for todo in fourth.todos] == [
            AgentTodoStatus.COMPLETED,
            AgentTodoStatus.COMPLETED,
            AgentTodoStatus.COMPLETED,
            AgentTodoStatus.IN_PROGRESS,
        ]
        assert fourth.is_complete is False

    def test_a_fourth_write_onto_a_finished_list_opens_the_next_one(self) -> None:
        # The transition the crash was first attributed to, driven at the same
        # four-call depth. It is a rollover, not a fault: generation two, a new
        # list id, and the previous list left intact behind it.
        projector = TodoListProjector()
        for statuses in self.SEQUENCE[:2]:
            self._replay(projector, statuses)
        finished = self._replay(projector, ("completed",) * 4)
        rolled = self.write(projector, ("Draft the follow-up note", "in_progress"))

        assert finished is not None and finished.is_complete
        assert finished.generation == 1
        assert rolled is not None
        assert rolled.generation == 2
        assert rolled.list_id == "run_1:todos:2"
