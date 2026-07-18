"""On-disk layout + safe-key derivation for the file-native runtime store.

Untrusted logical identifiers (org_id, conversation_id, task_id) are never
used as raw path segments. Each becomes a lowercase hex SHA-256 digest, so a
crafted id can never traverse outside the store root. The canonical records
carry the real identifiers, so the one-way mapping loses nothing — the store
is always rebuilt by scanning the JSONL, never by reversing a path key.

Layout under ``RUNTIME_FILE_STORE_ROOT``::

    workspaces/<ws-key>/sessions/<conv-key>/
        conversation.json        # metadata (one JSON object, rewritten in place)
        events.jsonl             # canonical: one RuntimeEventEnvelope per line
        messages.jsonl           # canonical: one MessageRecord per line
        runs.jsonl               # canonical: one RunRecord per line
        subagents/<task-key>.jsonl
    state/<table>.jsonl          # append-with-fold back-office ledgers
    objects/sha256/<hh>/<hash>   # content-addressed blobs
    index/catalog.sqlite3        # DISPOSABLE index (rebuildable from JSONL)

Directories are created ``0o700`` and files ``0o600`` — the OS user boundary is
the tenant boundary for the ``single_user_desktop`` profile.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

_DIR_MODE = 0o700
_FILE_MODE = 0o600


class FileStoreLayout:
    """Resolve on-disk paths for one store root and derive safe path keys."""

    CONVERSATION_META = "conversation.json"
    EVENTS_FILE = "events.jsonl"
    MESSAGES_FILE = "messages.jsonl"
    RUNS_FILE = "runs.jsonl"
    SUBAGENTS_DIR = "subagents"

    def __init__(self, root: Path) -> None:
        self._root = root.expanduser().resolve()

    @property
    def root(self) -> Path:
        """Absolute, resolved store root directory."""

        return self._root

    # ----- key derivation ------------------------------------------------

    @staticmethod
    def safe_key(logical_id: str) -> str:
        """Return a filesystem-safe path segment for an untrusted identifier.

        Lowercase hex SHA-256 — collision-resistant and free of separators,
        ``.``/``..`` traversal, or reserved characters.
        """

        return hashlib.sha256(logical_id.encode("utf-8")).hexdigest()

    # ----- directories ---------------------------------------------------

    @property
    def workspaces_dir(self) -> Path:
        return self._root / "workspaces"

    def workspace_dir(self, org_id: str) -> Path:
        return self.workspaces_dir / self.safe_key(org_id)

    def sessions_dir(self, org_id: str) -> Path:
        return self.workspace_dir(org_id) / "sessions"

    def conversation_dir(self, org_id: str, conversation_id: str) -> Path:
        return self.sessions_dir(org_id) / self.safe_key(conversation_id)

    def subagents_dir(self, org_id: str, conversation_id: str) -> Path:
        return self.conversation_dir(org_id, conversation_id) / self.SUBAGENTS_DIR

    @property
    def state_dir(self) -> Path:
        return self._root / "state"

    @property
    def objects_dir(self) -> Path:
        return self._root / "objects" / "sha256"

    @property
    def index_dir(self) -> Path:
        return self._root / "index"

    @property
    def index_db_path(self) -> Path:
        return self.index_dir / "catalog.sqlite3"

    # ----- files ---------------------------------------------------------

    def conversation_meta_path(self, org_id: str, conversation_id: str) -> Path:
        return self.conversation_dir(org_id, conversation_id) / self.CONVERSATION_META

    def events_path(self, org_id: str, conversation_id: str) -> Path:
        return self.conversation_dir(org_id, conversation_id) / self.EVENTS_FILE

    def messages_path(self, org_id: str, conversation_id: str) -> Path:
        return self.conversation_dir(org_id, conversation_id) / self.MESSAGES_FILE

    def runs_path(self, org_id: str, conversation_id: str) -> Path:
        return self.conversation_dir(org_id, conversation_id) / self.RUNS_FILE

    def subagent_path(self, org_id: str, conversation_id: str, task_id: str) -> Path:
        return self.subagents_dir(org_id, conversation_id) / (
            self.safe_key(task_id) + ".jsonl"
        )

    def state_path(self, table: str) -> Path:
        return self.state_dir / f"{table}.jsonl"

    def object_path(self, sha256_hex: str) -> Path:
        return self.objects_dir / sha256_hex[:2] / sha256_hex

    # ----- fs helpers ----------------------------------------------------

    @staticmethod
    def ensure_dir(path: Path) -> Path:
        """Create ``path`` (and parents) ``0o700`` if absent; return it."""

        path.mkdir(mode=_DIR_MODE, parents=True, exist_ok=True)
        return path

    @staticmethod
    def restrict_file(path: Path) -> None:
        """Best-effort ``chmod 0o600`` on a freshly created file."""

        try:
            path.chmod(_FILE_MODE)
        except OSError:
            # Some filesystems (Windows, network mounts) reject chmod; the
            # store still functions, so this is best-effort only.
            pass

    def ensure_scaffold(self) -> None:
        """Create the top-level directory scaffold under the root."""

        for directory in (
            self._root,
            self.workspaces_dir,
            self.state_dir,
            self.objects_dir,
            self.index_dir,
        ):
            self.ensure_dir(directory)


__all__ = ("FileStoreLayout",)
