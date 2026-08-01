"""The facade's run-create contract must not be narrower than the runtime's.

THE BUG THIS EXISTS TO END. `FacadeRunRequest` is a typed Pydantic model, the
route forwards `payload.model_dump(exclude_none=True)`, and Pydantic's default
is `extra="ignore"`. A field the model does not DECLARE is therefore not
rejected — it is accepted, silently dropped, and forwarded as though the client
never sent it. The client sees `200 OK`. Apps may call ONLY the facade, so that
model is the entire contract.

It has bitten four times, each occurrence earning its own one-field test:

    conversation_idempotency_key   new-chat sends 422'd before proxying
    reasoning_depth               Fast / Balanced / Deep never reached the runtime
    web_search_enabled            the Tools toggle did nothing
    filesystem_bypass             the execution-mode pill was inoperable on BOTH
                                  hosts — the desktop sent it, the run sealed
                                  `source: "master"` ("no selection arrived"),
                                  and every write paused with the pill on Bypass

Four instances of one bug is a missing invariant, not four mistakes. The
per-field tests each prove one field survives; none of them notices the FIFTH.

WHY IT LIVES IN tools/ AND PARSES RATHER THAN IMPORTS. Comparing the two
contracts means reading both services, and no deployable component may import
another's `src` — a check that violated the boundary to enforce a boundary would
be its own bad trade. `ast` reads the class body without importing either
service, so this needs no shared venv, no PYTHONPATH, and cannot execute code
from either side.

Run by `tools/test_run_create_contract_drift.py` under the repo-wide gate, which
is unconditional (see CLAUDE.md on required checks and `paths:` filters).
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Final

#: The runtime's inbound contract — every field a client MAY send.
RUNTIME_MODEL: Final = (
    "services/ai-backend/src/runtime_api/schemas/runs.py",
    "CreateRunRequest",
)

#: The facade's relay contract — every field that actually survives the proxy.
FACADE_MODEL: Final = (
    "services/backend-facade/src/backend_facade/app.py",
    "FacadeRunRequest",
)

#: Runtime fields the facade does NOT relay, and must not start relaying by
#: accident. An entry here is a decision; the reason is the entry's whole value.
#:
#: NOT listed: `org_id` / `user_id`. Those ARE declared on `FacadeRunRequest`
#: and are then overwritten by `identity.scoped_payload` from the verified
#: bearer, so they never reach ai-backend as the client wrote them. Exempting
#: them would have been wrong twice over — they are relayed, and the property
#: that protects them is overwriting, not omission.
MUST_NEVER_BE_RELAYED: Final[frozenset[str]] = frozenset(
    {
        # SERVER-DERIVED, and security-relevant for this very feature: the
        # sealed `filesystem_bypass` decision lives on the run's
        # `runtime_context`. A client that could post one would seal its own
        # bypass, skipping the master switch and the resolver entirely — the
        # exact opt-in-by-assertion the three-tier design exists to prevent.
        "runtime_context",
    }
)

#: Runtime fields the facade does not relay today, WITHOUT that having been a
#: decision. Recorded so the gate is honest about the difference between "we
#: chose not to" and "nobody has looked".
#:
#: Each is a candidate bug of exactly the shape this file guards: a client that
#: sends one gets `200 OK` and no effect. None is currently reachable from a
#: composer, which is the only reason they are not defects yet.
NOT_RELAYED_UNREVIEWED: Final[frozenset[str]] = frozenset(
    {
        "content_format",
        "conversation_title",
        "request_options",
    }
)

#: Everything the gate tolerates missing from the facade contract.
INTENTIONALLY_NOT_RELAYED: Final[frozenset[str]] = (
    MUST_NEVER_BE_RELAYED | NOT_RELAYED_UNREVIEWED
)


class ModelFieldReader:
    """Field names declared on one Pydantic model, read without importing it."""

    @staticmethod
    def _class_node(source: str, class_name: str) -> ast.ClassDef:
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.ClassDef) and node.name == class_name:
                return node
        raise LookupError(f"class {class_name} not found")

    @classmethod
    def fields(cls, path: Path, class_name: str) -> frozenset[str]:
        """Annotated assignments in the class body — i.e. pydantic fields.

        Only `name: type` forms count. A bare `name = value` is not a field, and
        a nested class or method is not one either, so walking the class body
        directly (rather than `ast.walk`) keeps inner classes out.
        """

        node = cls._class_node(path.read_text(encoding="utf-8"), class_name)
        return frozenset(
            statement.target.id
            for statement in node.body
            if isinstance(statement, ast.AnnAssign)
            and isinstance(statement.target, ast.Name)
            and not statement.target.id.startswith("_")
        )


class RunCreateContractDrift:
    """Compares the two contracts and reports what the proxy would swallow."""

    def __init__(self, repo_root: Path) -> None:
        self._root = repo_root

    def _fields(self, spec: tuple[str, str]) -> frozenset[str]:
        relative, class_name = spec
        return ModelFieldReader.fields(self._root / relative, class_name)

    def runtime_fields(self) -> frozenset[str]:
        return self._fields(RUNTIME_MODEL)

    def facade_fields(self) -> frozenset[str]:
        return self._fields(FACADE_MODEL)

    def dropped(self) -> frozenset[str]:
        """Runtime fields a client can send that the facade silently discards."""

        return self.runtime_fields() - self.facade_fields() - INTENTIONALLY_NOT_RELAYED

    def stale_exemptions(self) -> frozenset[str]:
        """Exempted names that no longer exist upstream.

        A dead exemption exempts nothing today and silently exempts whatever
        reuses the name tomorrow, which is how this kind of guard rots.
        """

        return INTENTIONALLY_NOT_RELAYED - self.runtime_fields()
