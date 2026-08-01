"""The agent's working checklist, resolved from ``write_todos`` into snapshots.

LangChain's ``TodoListMiddleware`` gives the agent one tool, ``write_todos``,
which REPLACES the entire list on every call. That is a fine contract for the
model and a poor one for a client: "appended a step", "marked step 2 done" and
"abandoned that plan and started a new one" all arrive as the same undifferentiated
array. Nothing in the middleware carries list identity — see
``langchain.agents.middleware.todo``.

This module supplies the missing half. :class:`TodoListProjector` holds the last
list per (run, subagent) and resolves each incoming write into a
:class:`TodoListSnapshot`: same list, or the next generation. One rule decides
it — **a write that lands when every row of the previous list was already
completed opens a new list** — because the only honest reading of "here is a
fresh set of steps, and the last set is finished" is that the agent moved on.

Two properties this deliberately keeps:

- **Deterministic on replay.** ``list_id`` is composed from the run/subagent id
  and the generation counter, never a uuid or a clock, so re-projecting a run's
  events yields byte-identical snapshots.
- **Untrusted input.** ``todos`` is model output arriving over a tool boundary,
  so it is validated and bounded here (:class:`_Limits`) before it can reach the
  SSE channel — never splatted onto the wire as raw provider JSON.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from enum import StrEnum

from pydantic import Field, ValidationError

from agent_runtime.execution.contracts import RuntimeContract


class _Limits:
    """Bounds on untrusted model output before it reaches the SSE channel."""

    # A checklist longer than this is a runaway, not a plan. The list is
    # re-sent in full on every write, so the cap also bounds per-event size.
    MAX_TODOS = 64
    MAX_CONTENT = 500


class _Fields:
    """``write_todos`` argument keys, as LangChain's ``WriteTodosInput`` names them."""

    TODOS = "todos"
    CONTENT = "content"
    STATUS = "status"


class AgentTodoStatus(StrEnum):
    """The three states LangChain's ``Todo.status`` literal allows.

    Mirrored rather than imported: a middleware upgrade that adds a fourth state
    should fail validation loudly here (and be handled deliberately) instead of
    arriving on the wire as a string the client silently renders as pending.
    """

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


class AgentTodo(RuntimeContract):
    """One checklist row exactly as the agent wrote it.

    ``write_todos`` assigns no per-item id, so ``content`` is also the row's
    identity — which is what lets a client tell which row just flipped to
    ``completed`` between two snapshots.
    """

    content: str = Field(min_length=1, max_length=_Limits.MAX_CONTENT)
    status: AgentTodoStatus


class TodoListSnapshot(RuntimeContract):
    """The resolved checklist after one ``write_todos`` call.

    This is the whole state, not a diff: the tool replaces the list, so a client
    renders the newest snapshot and never reconstructs anything.
    """

    list_id: str = Field(min_length=1, max_length=256)
    generation: int = Field(ge=1)
    todos: tuple[AgentTodo, ...]

    @property
    def is_complete(self) -> bool:
        """Return ``True`` when the list has rows and every one of them is done."""

        return bool(self.todos) and all(
            todo.status is AgentTodoStatus.COMPLETED for todo in self.todos
        )


class TodoListProjector:
    """Resolves ``write_todos`` arguments into snapshots, one lane per agent.

    The worker owns a single instance for its lifetime and calls
    :meth:`project` at the ``write_todos`` tool-result seam. State is keyed by
    ``(run_id, subagent_id)`` because Deep Agents gives every subagent its OWN
    ``TodoListMiddleware`` — the parent's plan and a child's plan are separate
    lists and must not roll each other over.

    :meth:`discard_run` frees a run's lanes at termination; without it a
    long-lived worker keeps every run's final checklist forever.
    """

    def __init__(self) -> None:
        """Initialise the empty per-(run, subagent) snapshot state."""

        self._current: dict[tuple[str, str | None], TodoListSnapshot] = {}

    def project(
        self,
        *,
        run_id: str,
        subagent_id: str | None,
        arguments: Mapping[str, object],
    ) -> TodoListSnapshot | None:
        """Resolve one ``write_todos`` call into the snapshot to publish.

        Returns ``None`` — meaning "emit nothing" — when the arguments carry no
        usable list, or when they repeat the current one verbatim. The repeat
        case matters: a re-send of an already-finished list would otherwise
        register as "the agent started a new plan" and bump the generation for
        a call that changed nothing.
        """

        todos = self._parse(arguments)
        if todos is None:
            return None

        key = (run_id, subagent_id)
        previous = self._current.get(key)
        if previous is not None and previous.todos == todos:
            return None

        generation = 1
        if previous is not None:
            # The rollover rule. Anything short of a fully-completed previous
            # list is a revision of it — appended rows, a status flip, a dropped
            # step — and keeps the same list identity.
            generation = (
                previous.generation + 1 if previous.is_complete else previous.generation
            )

        snapshot = TodoListSnapshot(
            list_id=self._list_id(
                run_id=run_id, subagent_id=subagent_id, generation=generation
            ),
            generation=generation,
            todos=todos,
        )
        self._current[key] = snapshot
        return snapshot

    def discard_run(self, run_id: str) -> None:
        """Free every lane belonging to ``run_id`` once the run is terminal."""

        for key in [key for key in self._current if key[0] == run_id]:
            del self._current[key]

    @staticmethod
    def _list_id(*, run_id: str, subagent_id: str | None, generation: int) -> str:
        """Compose a replay-stable list id from the lane and its generation."""

        lane = run_id if subagent_id is None else f"{run_id}:{subagent_id}"
        return f"{lane}:todos:{generation}"

    @staticmethod
    def _parse(arguments: Mapping[str, object]) -> tuple[AgentTodo, ...] | None:
        """Validate the untrusted ``todos`` argument, or return ``None`` if unusable.

        A malformed list is dropped whole rather than partially rendered: half a
        checklist read as the agent's plan is worse than no panel at all. An
        empty list is legitimate (the agent clearing its plan) and validates to
        an empty tuple, which is distinct from ``None``.
        """

        raw = arguments.get(_Fields.TODOS)
        if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
            return None
        if len(raw) > _Limits.MAX_TODOS:
            return None
        todos: list[AgentTodo] = []
        for item in raw:
            if not isinstance(item, Mapping):
                return None
            try:
                todos.append(
                    AgentTodo(
                        content=str(item.get(_Fields.CONTENT, "")).strip(),
                        status=AgentTodoStatus(item.get(_Fields.STATUS)),
                    )
                )
            except (ValidationError, ValueError):
                return None
        return tuple(todos)
