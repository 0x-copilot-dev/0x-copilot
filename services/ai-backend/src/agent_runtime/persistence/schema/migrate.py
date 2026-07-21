"""Versioned schema migration runner for the agent runtime.

Wraps yoyo-migrations and pins the migration source to
``services/ai-backend/migrations``. Each migration file is named
``NNNN_<topic>.sql`` with a sibling ``NNNN_<topic>.rollback.sql`` and is
checksummed in ``MANIFEST.lock``.

Production runs migrations as a separate deploy step
(``RUNTIME_MIGRATIONS_AUTO_APPLY=false``); dev/test runs them automatically
via the adapter's ``migrate()`` method.
"""

from __future__ import annotations

import hashlib
import logging
import os
from collections.abc import Sequence
from pathlib import Path

import yoyo


_LOGGER = logging.getLogger(__name__)

# services/ai-backend/src/agent_runtime/persistence/schema/migrate.py
#   -> services/ai-backend/migrations
MIGRATIONS_DIR = Path(__file__).resolve().parents[4] / "migrations"
MANIFEST_FILE = MIGRATIONS_DIR / "MANIFEST.lock"


class MigrationManifestError(RuntimeError):
    """Raised when ``MANIFEST.lock`` is missing or contradicts the migrations directory."""


class MigrationRunner:
    """Apply, rollback, and report status for ai-backend yoyo migrations."""

    @classmethod
    def migrations_dir(cls) -> Path:
        """Return the absolute path of the migrations directory."""
        return MIGRATIONS_DIR

    @classmethod
    def apply(cls, database_url: str) -> list[str]:
        """Apply all pending migrations and return the list of applied IDs."""
        backend = cls._backend(database_url)
        migrations = yoyo.read_migrations(str(MIGRATIONS_DIR))
        with backend.lock():
            to_apply = backend.to_apply(migrations)
            applied_ids = [migration.id for migration in to_apply]
            backend.apply_migrations(to_apply)
        for migration_id in applied_ids:
            _LOGGER.info("migration_applied id=%s service=ai-backend", migration_id)
        return applied_ids

    @classmethod
    def rollback(cls, database_url: str, *, to: str | None = None) -> list[str]:
        """Roll back migrations in reverse order, optionally stopping at migration ``to``.

        ``to`` is exclusive: all migrations with an ID strictly greater than
        ``to`` are rolled back, and ``to`` itself is left applied.
        """
        backend = cls._backend(database_url)
        migrations = yoyo.read_migrations(str(MIGRATIONS_DIR))
        applied = backend.to_rollback(migrations)
        if to is not None:
            applied = applied.__class__(
                [migration for migration in applied if migration.id > to]
            )
        with backend.lock():
            rolled_back_ids = [migration.id for migration in applied]
            backend.rollback_migrations(applied)
        for migration_id in rolled_back_ids:
            _LOGGER.info("migration_rolled_back id=%s service=ai-backend", migration_id)
        return rolled_back_ids

    @classmethod
    def status(cls, database_url: str) -> tuple[list[str], list[str]]:
        """Return ``(applied_ids, pending_ids)`` for the current database state."""
        backend = cls._backend(database_url)
        migrations = yoyo.read_migrations(str(MIGRATIONS_DIR))
        applied = [migration.id for migration in backend.to_rollback(migrations)]
        pending = [migration.id for migration in backend.to_apply(migrations)]
        return applied, pending

    @classmethod
    def auto_apply_enabled(cls, env: dict[str, str] | None = None) -> bool:
        """Return ``True`` when ``RUNTIME_MIGRATIONS_AUTO_APPLY`` is not set to ``false``."""
        env = env if env is not None else dict(os.environ)
        return (
            env.get("RUNTIME_MIGRATIONS_AUTO_APPLY", "true").strip().lower() == "true"
        )

    @classmethod
    def expected_manifest(cls) -> dict[str, str]:
        """Parse and return the ``MANIFEST.lock`` checksum map, raising on absence."""
        if not MANIFEST_FILE.exists():
            raise MigrationManifestError(
                f"Missing manifest: {MANIFEST_FILE}. Run "
                "tools/check_migration_manifest.py to regenerate."
            )
        return cls._parse_manifest(MANIFEST_FILE.read_text())

    @classmethod
    def actual_manifest(cls) -> dict[str, str]:
        """Compute the SHA-256 checksum map for all current migration files on disk."""
        result: dict[str, str] = {}
        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            if path.name.endswith(".rollback.sql"):
                continue
            migration_id = path.stem
            rollback_path = MIGRATIONS_DIR / f"{migration_id}.rollback.sql"
            digest = hashlib.sha256()
            digest.update(path.read_bytes())
            if rollback_path.exists():
                # Mix a NUL separator before the rollback bytes so a forward-only
                # migration and one with rollback content cannot hash-collide.
                digest.update(b"\x00")
                digest.update(rollback_path.read_bytes())
            result[migration_id] = digest.hexdigest()
        return result

    @staticmethod
    def _yoyo_url(database_url: str) -> str:
        """Force yoyo onto the psycopg3 driver.

        yoyo selects its DB driver from the URL scheme: a bare
        ``postgresql://`` (or ``postgres://``) makes it import ``psycopg2``,
        which this repo deliberately does NOT install (psycopg3 only). Pinning
        the scheme to ``postgresql+psycopg://`` selects psycopg3. No-op when a
        driver is already specified.
        """
        for prefix in ("postgresql://", "postgres://"):
            if database_url.startswith(prefix):
                return "postgresql+psycopg://" + database_url[len(prefix) :]
        return database_url

    @classmethod
    def _backend(cls, database_url: str) -> yoyo.backends.DatabaseBackend:
        """Return a yoyo database backend for the given URL."""
        return yoyo.get_backend(cls._yoyo_url(database_url))

    @staticmethod
    def _parse_manifest(text: str) -> dict[str, str]:
        """Parse ``MANIFEST.lock`` text into a ``{migration_id: sha256}`` mapping."""
        result: dict[str, str] = {}
        for raw in text.splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            try:
                migration_id, marker = line.split(" sha256=", maxsplit=1)
            except ValueError as exc:
                raise MigrationManifestError(
                    f"Malformed manifest line: {raw!r}"
                ) from exc
            result[migration_id.strip()] = marker.strip()
        return result


def render_manifest(entries: Sequence[tuple[str, str]]) -> str:
    """Render manifest text from ``(migration_id, sha256)`` pairs."""

    header = (
        "# Auto-generated by tools/check_migration_manifest.py.\n"
        "# Do not hand-edit; CI will refuse if checksums drift from the\n"
        "# migrations/ directory.\n"
    )
    body = "\n".join(
        f"{migration_id} sha256={digest}" for migration_id, digest in entries
    )
    return header + body + "\n"
