"""Durable LangGraph savers, registered per storage backend.

This is the sibling half of ``runtime_adapters.registry``: that one composes a
backend's *stores*, this one composes its *checkpointer*. They are keyed by the
same ``RUNTIME_STORE_BACKEND`` names but live apart because savers are composed
inside the domain, which must never import ``runtime_adapters`` (adapters depend
on the domain, never the reverse).

A backend registers a builder that returns its saver, or ``None`` when its env
signals are absent — in which case the caller falls back to the process-local
``InMemorySaver``. A backend that registers nothing at all has no durable saver
by design; absence is the signal, not an error.

Every builder imports its LangGraph package **inside** the function and only
after its env gate passes, so selecting one backend never imports another's
driver.

**Adding a backend** — for example putting a SQL store back — is one
:meth:`CheckpointerRegistry.register` call next to a
``runtime_adapters.registry`` registration under the same name.
"""

from __future__ import annotations

import os
from collections.abc import Callable

CheckpointerBuilder = Callable[[], object | None]

ENV_STORE_BACKEND = "RUNTIME_STORE_BACKEND"
ENV_FILE_STORE_ROOT = "RUNTIME_FILE_STORE_ROOT"
ENV_DATABASE_URL = "DATABASE_URL"


class CheckpointerRegistry:
    """Map a storage-backend name to the builder for its durable saver."""

    def __init__(self) -> None:
        self._builders: dict[str, CheckpointerBuilder] = {}

    def register(self, name: str, builder: CheckpointerBuilder) -> None:
        """Register (or replace) the saver builder for backend *name*."""

        self._builders[name] = builder

    def names(self) -> tuple[str, ...]:
        """Backends that have a durable saver registered, sorted."""

        return tuple(sorted(self._builders))

    def build(self, name: str) -> object | None:
        """Build the durable saver for *name*, or ``None``.

        ``None`` covers both "no saver registered for this backend" and
        "registered, but its env preconditions are not met" — the caller treats
        them identically and falls back to the in-memory saver.
        """

        builder = self._builders.get(name)
        if builder is None:
            return None
        return builder()


def selected_backend() -> str:
    """Return the normalized ``RUNTIME_STORE_BACKEND`` value ("" when unset)."""

    return os.environ.get(ENV_STORE_BACKEND, "").strip().lower()


def build_in_memory_checkpointer() -> object:
    """Return a fresh process-local ``InMemorySaver`` (the last-resort default)."""

    try:
        from langgraph.checkpoint.memory import InMemorySaver
    except ImportError:  # pragma: no cover — older langgraph alias
        from langgraph.checkpoint.memory import MemorySaver as InMemorySaver

    return InMemorySaver()


def build_file_store_checkpointer() -> object | None:
    """Build a durable SQLite checkpointer for the desktop file store, or ``None``.

    Returns ``None`` unless the file store is active: ``RUNTIME_STORE_BACKEND=file``
    **and** ``RUNTIME_FILE_STORE_ROOT`` set. The checkpoint database lives next to
    the disposable catalog index at ``<root>/index/checkpoints.sqlite3`` — it is
    NOT the disposable index itself, so wiping ``index/catalog.sqlite3`` never
    drops in-flight graph state.

    The async graph is driven via ``ainvoke``/``astream``; the synchronous
    ``SqliteSaver`` rejects async calls, so we use ``AsyncSqliteSaver`` over a
    lazily-connected ``aiosqlite`` connection (it binds to the worker event loop
    on first use and auto-creates its tables). ``check_same_thread=False`` lets
    aiosqlite service the connection from its own worker thread.
    """

    root = os.environ.get(ENV_FILE_STORE_ROOT, "").strip()
    if selected_backend() != "file" or not root:
        return None

    from pathlib import Path

    import aiosqlite
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    # ``index/checkpoints.sqlite3`` mirrors ``FileStoreLayout.index_dir``; keep
    # the two in sync if the on-disk layout ever moves. Referenced by string
    # here rather than pulling ``runtime_adapters`` into ``agent_runtime``.
    db_dir = Path(root).expanduser().resolve() / "index"
    db_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    db_path = db_dir / "checkpoints.sqlite3"
    connection = aiosqlite.connect(str(db_path), check_same_thread=False)
    return AsyncSqliteSaver(connection)


def build_postgres_checkpointer() -> object | None:
    """Build a durable ``AsyncPostgresSaver`` for a server deployment, or ``None``.

    Returns ``None`` unless the server Postgres path is active:
    ``RUNTIME_STORE_BACKEND=postgres`` **and** ``DATABASE_URL`` set. This is what
    stops a multi-process server from losing in-flight graph state (and paused
    approvals) to a process-local ``InMemorySaver`` on every worker restart.

    The pool is constructed with ``open=False`` so selecting/importing the saver
    never blocks on a live database — ``setup_runtime_checkpointer()`` opens it
    and creates the checkpoint tables once at startup. ``autocommit=True`` +
    ``row_factory=dict_row`` + ``prepare_threshold=0`` are the connection
    settings ``AsyncPostgresSaver`` documents for pooled usage.

    ``ImportError`` is deliberately NOT swallowed: a server that asked for the
    Postgres backend but is missing the driver must fail loudly, not silently
    degrade to a non-durable saver.
    """

    database_url = os.environ.get(ENV_DATABASE_URL, "").strip()
    if selected_backend() != "postgres" or not database_url:
        return None

    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from psycopg.rows import dict_row
    from psycopg_pool import AsyncConnectionPool

    pool = AsyncConnectionPool(
        conninfo=database_url,
        open=False,
        kwargs={
            "autocommit": True,
            "row_factory": dict_row,
            "prepare_threshold": 0,
        },
    )
    return AsyncPostgresSaver(pool)


CHECKPOINTERS = CheckpointerRegistry()
"""The process-wide saver registry ``runtime_checkpointer()`` reads."""

CHECKPOINTERS.register("file", build_file_store_checkpointer)
CHECKPOINTERS.register("postgres", build_postgres_checkpointer)
# ``in_memory`` / ``in_memory_async`` register nothing: a process-local store has
# no durable saver to offer, and the in-memory fallback is already what they want.


__all__ = (
    "CHECKPOINTERS",
    "CheckpointerBuilder",
    "CheckpointerRegistry",
    "build_file_store_checkpointer",
    "build_in_memory_checkpointer",
    "build_postgres_checkpointer",
    "selected_backend",
)
