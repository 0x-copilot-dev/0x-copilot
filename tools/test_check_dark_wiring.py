"""Unit tests for the dark-wiring gate.

Three halves rather than two, because this gate has an unusual failure mode.

:class:`TestDetectors` pins each detector against synthetic trees. Most of these
cases are regressions: the first working version of this gate produced 1,607
findings, and the five tests marked "regression" below each correspond to one
class of false positive that made up the bulk of them. They are the difference
between a burn-down list and a wall of noise nobody reads, so re-widening any of
those rules fails here.

:class:`TestRatchet` pins the ratchet semantics — new fails, stale fails,
baselined passes, waived disappears.

:class:`TestAgainstTheRealTree` is the load-bearing half. It asserts that the six
symbols a nine-dimension audit found dark by hand are still reported by the
scanner. A static gate is only worth its runtime if it catches the cases that
motivated it, and those cases are the specification.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent

sys.path.insert(0, str(HERE))

import check_dark_wiring as gate  # noqa: E402
from check_dark_wiring import (  # noqa: E402
    BASELINE_PATH,
    BASELINE_SEPARATOR,
    REPO_ROOT,
    Baseline,
    Finding,
    collect_findings,
    main,
)

# ---------------------------------------------------------------------------
# The six symbols the audit found dark by hand. This list IS the specification:
# every one was confirmed at the file:line below by reading the code, not by
# running this scanner, so it is independent evidence rather than a snapshot of
# the tool's own output.
# ---------------------------------------------------------------------------
AUDITED_DARK = (
    pytest.param(
        "runtime_adapters.file._catalog_index:CatalogIndex.search_conversations",
        "backend-only",
        id="fts5-conversation-search",
    ),
    pytest.param(
        "runtime_adapters.file.runtime_api_store:FileRuntimeApiStore.export_conversation",
        "backend-only",
        id="export-archive",
    ),
    pytest.param(
        "runtime_adapters.file.runtime_api_store:FileRuntimeApiStore.import_conversation",
        "backend-only",
        id="import-archive",
    ),
    # `runtime_worker.stream_events:_Fields.grant_options` used to sit here.
    # It has been WIRED — see `test_grant_options_is_wired_now`, which replaces
    # this case with its mirror image so the evidence is not simply deleted.
    pytest.param(
        "runtime_api.schemas.approvals:ApprovalRequestRecord.expires_at",
        "test-only",
        id="approvals-expires-at",
    ),
    # `SkillManifest.allowed_tools` was here, and is deliberately gone: it was
    # confirmed prompt-only by the nine-dimension audit and has since been
    # enforced at the tool surface, so the gate correctly stops reporting it.
    # A case asserting a symbol is STILL dark has to be retired the moment the
    # symbol is wired — leaving it would fail the build for the fix landing.
)


def _write(root: Path, relative: str, body: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


@pytest.fixture
def tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A synthetic repo whose roots the module-level constants point at.

    The scanner derives packages and backends from directory layout, so the
    constants must be redirected together or ``_package_of`` silently reports
    absolute paths and every attribution test passes for the wrong reason.
    """

    src = tmp_path / "src"
    tests = tmp_path / "tests"
    app = tmp_path / "app"
    for path in (src, tests, app):
        path.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(gate, "SRC_ROOT", src)
    monkeypatch.setattr(gate, "TEST_ROOT", tests)
    monkeypatch.setattr(gate, "APP_ROOTS", (app,))
    monkeypatch.setattr(gate, "ADAPTER_ROOT", src / "runtime_adapters")
    monkeypatch.setattr(gate, "TS_DECLARATION_ROOT", tmp_path / "api-types")

    class Tree:
        def src(self, relative: str, body: str) -> Path:
            return _write(src, relative, body)

        def test(self, relative: str, body: str) -> Path:
            return _write(tests, relative, body)

        def app(self, relative: str, body: str) -> Path:
            return _write(app, relative, body)

        def findings(self) -> list[Finding]:
            return collect_findings(
                src_roots=(src,), test_roots=(tests,), app_roots=(app,)
            )

        def keys(self) -> set[str]:
            return {finding.key for finding in self.findings()}

    return Tree()


class TestTestOnly:
    """Detector 1: reachable from a test and from nowhere else."""

    def test_callable_named_only_by_a_test_is_flagged(self, tree) -> None:
        tree.src(
            "pkg/ledger.py",
            "class Ledger:\n    def mark_rejected(self):\n        return 1\n",
        )
        tree.test("test_ledger.py", "def test_it():\n    Ledger().mark_rejected()\n")

        assert "pkg.ledger:Ledger.mark_rejected" in tree.keys()

    def test_callable_with_a_src_caller_is_silent(self, tree) -> None:
        tree.src(
            "pkg/ledger.py",
            "class Ledger:\n    def mark_rejected(self):\n        return 1\n",
        )
        tree.src(
            "pkg/worker.py", "def drive(ledger):\n    return ledger.mark_rejected()\n"
        )
        tree.test("test_ledger.py", "def test_it():\n    Ledger().mark_rejected()\n")

        assert "pkg.ledger:Ledger.mark_rejected" not in tree.keys()

    def test_callable_used_inside_its_own_module_is_silent(self, tree) -> None:
        """Regression: cross-file-only counting reported ~3,800 helpers dark."""

        tree.src(
            "pkg/ledger.py",
            "class Ledger:\n"
            "    def mark_rejected(self):\n"
            "        return 1\n"
            "    def sweep(self):\n"
            "        return self.mark_rejected()\n",
        )
        tree.test("test_ledger.py", "def test_it():\n    Ledger().mark_rejected()\n")

        assert "pkg.ledger:Ledger.mark_rejected" not in tree.keys()

    # -- fields -------------------------------------------------------------

    #: A contract whose other fields are computed, so the record is genuinely
    #: produced and the None-default field is the anomaly. The ``expires_at``
    #: shape exactly.
    RECORD = (
        "class ApprovalRecord(Base):\n"
        "    approval_id: str = ''\n"
        "    status: str = 'pending'\n"
        "    label: str = ''\n"
        "    expires_at: object | None = None\n"
    )

    def test_none_default_field_only_ever_copied_is_flagged(self, tree) -> None:
        tree.src("pkg/records.py", self.RECORD)
        tree.src(
            "pkg/coordinator.py",
            "def build(previous):\n"
            "    return ApprovalRecord(\n"
            "        approval_id=fresh_id(),\n"
            "        status='pending',\n"
            "        label=title(),\n"
            "        expires_at=previous.expires_at,\n"
            "    )\n",
        )

        assert "pkg.records:ApprovalRecord.expires_at" in tree.keys()

    def test_field_written_from_a_local_is_silent(self, tree) -> None:
        """Regression: treating ``x=x`` as a pass-through made ``run_id``,
        ``org_id`` and ``user_id`` — the most-written fields in the service —
        report as populated by nothing."""

        tree.src("pkg/records.py", self.RECORD)
        tree.src(
            "pkg/coordinator.py",
            "def build(expires_at, previous):\n"
            "    return ApprovalRecord(\n"
            "        approval_id=fresh_id(),\n"
            "        status='pending',\n"
            "        expires_at=expires_at,\n"
            "    )\n",
        )

        assert "pkg.records:ApprovalRecord.expires_at" not in tree.keys()

    def test_field_with_a_working_default_is_silent(self, tree) -> None:
        """Only ``= None`` qualifies: a usable default that nobody overrides is
        a shrug, not a defect."""

        tree.src(
            "pkg/records.py",
            "class ApprovalRecord(Base):\n"
            "    approval_id: str = ''\n"
            "    status: str = 'pending'\n"
            "    label: str = ''\n"
            "    retries: int = 3\n",
        )
        tree.src(
            "pkg/coordinator.py",
            "def build(previous):\n"
            "    return ApprovalRecord(approval_id=fresh_id(), retries=previous.retries)\n",
        )

        assert "pkg.records:ApprovalRecord.retries" not in tree.keys()

    def test_projection_contract_is_silent(self, tree) -> None:
        """Regression: a struct built purely by copying an existing record is a
        projection working correctly, not a contract with dead fields."""

        tree.src(
            "pkg/signals.py",
            "class Signals(Base):\n"
            "    run_id: str | None = None\n"
            "    org_id: str | None = None\n"
            "    user_id: str | None = None\n"
            "    limit: int = 0\n",
        )
        tree.src(
            "pkg/control.py",
            "def build(run):\n"
            "    return Signals(\n"
            "        run_id=run.run_id,\n"
            "        org_id=run.org_id,\n"
            "        user_id=run.user_id,\n"
            "        limit=compute(run),\n"
            "    )\n",
        )

        assert not {key for key in tree.keys() if key.startswith("pkg.signals:")}

    # -- wire keys ----------------------------------------------------------

    #: A payload key: declared as a constant AND used in key position, which is
    #: what proves it reaches the wire.
    EMITTER = (
        "class _Fields:\n"
        "    GRANT_OPTIONS = 'grant_options'\n"
        "\n"
        "def emit(options):\n"
        "    return {_Fields.GRANT_OPTIONS: options}\n"
    )

    def test_key_the_client_only_strips_is_flagged(self, tree) -> None:
        tree.src("pkg/stream.py", self.EMITTER)
        tree.app(
            "payloadHelpers.ts",
            "const STRIP = ['grant_options', 'other_key'];\n"
            "export const clean = (p) => omit(p, STRIP);\n",
        )

        assert "pkg.stream:_Fields.grant_options" in tree.keys()

    def test_key_read_off_the_payload_is_silent(self, tree) -> None:
        tree.src("pkg/stream.py", self.EMITTER)
        tree.app("useGrants.ts", "export const g = (p) => p.grant_options ?? [];\n")

        assert "pkg.stream:_Fields.grant_options" not in tree.keys()

    def test_enum_value_constant_is_not_a_wire_key(self, tree) -> None:
        """Regression: enum values are compared (``s === 'final_response'``),
        never property-read, so scanning for a property read reported every one
        of them dark — 228 of the original 272 wire-key findings."""

        tree.src(
            "pkg/events.py",
            "class EventType:\n"
            "    FINAL_RESPONSE = 'final_response'\n"
            "\n"
            "def emit():\n"
            "    return {'type': EventType.FINAL_RESPONSE}\n",
        )
        tree.app("reducer.ts", "if (e.type === 'final_response') { done(); }\n")

        assert "pkg.events:EventType.final_response" not in tree.keys()


class TestBackendOnly:
    """Detector 2: a capability only one store backend can serve."""

    SEARCH = (
        "class FileStore:\n"
        "    def search_conversations(self, query):\n"
        "        return []\n"
    )

    def test_capability_on_one_backend_with_no_outside_caller_is_flagged(
        self, tree
    ) -> None:
        tree.src("runtime_adapters/file/store.py", self.SEARCH)
        tree.src(
            "runtime_adapters/in_memory/store.py",
            "class MemStore:\n    def get(self):\n        return None\n",
        )

        assert (
            "runtime_adapters.file.store:FileStore.search_conversations" in tree.keys()
        )

    def test_capability_present_on_both_backends_is_silent(self, tree) -> None:
        tree.src("runtime_adapters/file/store.py", self.SEARCH)
        tree.src(
            "runtime_adapters/in_memory/store.py",
            "class MemStore:\n    def search_conversations(self, query):\n        return []\n",
        )

        assert (
            "runtime_adapters.file.store:FileStore.search_conversations"
            not in tree.keys()
        )

    def test_capability_with_a_caller_outside_the_backend_is_silent(self, tree) -> None:
        """Regression: "implemented on one backend" alone is unremarkable — a
        file-native store owns dozens of such methods and every one is called."""

        tree.src("runtime_adapters/file/store.py", self.SEARCH)
        tree.src(
            "runtime_adapters/in_memory/store.py",
            "class MemStore:\n    def get(self):\n        return None\n",
        )
        tree.src(
            "runtime_api/routes.py",
            "def search(store, q):\n    return store.search_conversations(q)\n",
        )

        assert (
            "runtime_adapters.file.store:FileStore.search_conversations"
            not in tree.keys()
        )

    def test_capability_declared_on_a_port_is_silent(self, tree) -> None:
        tree.src("runtime_adapters/file/store.py", self.SEARCH)
        tree.src(
            "runtime_adapters/in_memory/store.py",
            "class MemStore:\n    def get(self):\n        return None\n",
        )
        tree.src(
            "persistence/ports.py",
            "class ConversationStore(Protocol):\n    def search_conversations(self, query): ...\n",
        )

        assert (
            "runtime_adapters.file.store:FileStore.search_conversations"
            not in tree.keys()
        )


class TestPromptOnly:
    """Detector 3: validated, then spent on an f-string."""

    MANIFEST = (
        "class SkillManifest(Base):\n"
        "    name: str = Field(default='')\n"
        "    allowed_tools: frozenset = Field(default_factory=frozenset)\n"
    )

    def test_field_read_only_into_a_prompt_is_flagged(self, tree) -> None:
        tree.src("skills/manifest.py", self.MANIFEST)
        tree.src(
            "execution/factory.py",
            "from skills.manifest import SkillManifest\n"
            "\n"
            "def card(skill):\n"
            "    return f'- {skill.name} (allowed={skill.allowed_tools})'\n",
        )

        assert "skills:SkillManifest.allowed_tools" in tree.keys()

    def test_field_with_a_real_read_is_silent(self, tree) -> None:
        tree.src("skills/manifest.py", self.MANIFEST)
        tree.src(
            "execution/factory.py",
            "from skills.manifest import SkillManifest\n"
            "\n"
            "def card(skill):\n"
            "    return f'- {skill.name} (allowed={skill.allowed_tools})'\n",
        )
        tree.src(
            "enforcement/guard.py",
            "from skills.manifest import SkillManifest\n"
            "\n"
            "def check(skill, tool):\n"
            "    if tool not in skill.allowed_tools:\n"
            "        raise PermissionError(tool)\n",
        )

        assert "skills:SkillManifest.allowed_tools" not in tree.keys()

    def test_a_read_from_a_package_that_cannot_see_the_field_is_not_attributed(
        self, tree
    ) -> None:
        """Regression, and the reason the whole import-graph channel exists.

        Six classes across three packages declare ``allowed_tools``. Keyed on the
        name alone, the subagent handoff's genuine enforcement made the *name*
        look enforced and hid the skills manifest field entirely — so the gate
        flagged the one field that IS enforced and stayed silent on the one that
        is not. ``handoff.py`` never imports ``capabilities.skills``, so its read
        cannot be of the skills field.
        """

        tree.src("skills/manifest.py", self.MANIFEST)
        tree.src(
            "execution/factory.py",
            "from skills.manifest import SkillManifest\n"
            "\n"
            "def card(skill):\n"
            "    return f'- {skill.name} (allowed={skill.allowed_tools})'\n",
        )
        # A different contract, same field name, enforced inside its own package
        # and importing nothing from ``skills``.
        tree.src(
            "subagents/contracts.py",
            "class SubagentTask(Base):\n"
            "    name: str = Field(default='')\n"
            "    allowed_tools: frozenset = Field(default_factory=frozenset)\n",
        )
        tree.src(
            "subagents/handoff.py",
            "from subagents.contracts import SubagentTask\n"
            "\n"
            "def narrow(task, tools):\n"
            "    return [t for t in tools if t in task.allowed_tools]\n",
        )

        keys = tree.keys()
        assert "skills:SkillManifest.allowed_tools" in keys
        assert "subagents:SubagentTask.allowed_tools" not in keys


class TestWaiver:
    def test_waived_declaration_disappears(self, tree) -> None:
        tree.src(
            "pkg/ledger.py",
            "class Ledger:\n"
            f"    def mark_rejected(self):  {gate.WAIVER_MARKER} kept for the CLI\n"
            "        return 1\n",
        )
        tree.test("test_ledger.py", "def test_it():\n    Ledger().mark_rejected()\n")

        assert "pkg.ledger:Ledger.mark_rejected" not in tree.keys()


class TestRatchet:
    """New fails, stale fails, baselined passes."""

    @staticmethod
    def _finding(key: str) -> Finding:
        return Finding(
            key=key,
            detector="test-only",
            file=Path("services/ai-backend/src/pkg/thing.py"),
            lineno=1,
            reason="synthetic",
        )

    @pytest.fixture
    def baseline_file(self, tmp_path: Path) -> Path:
        path = tmp_path / "baseline.txt"
        path.write_text(
            "# a comment\n\npkg.thing:Thing.known :: known dark, kept deliberately\n",
            encoding="utf-8",
        )
        return path

    def _run(self, monkeypatch, baseline_file: Path, keys: list[str]) -> int:
        monkeypatch.setattr(
            gate, "collect_findings", lambda **_: [self._finding(k) for k in keys]
        )
        return main(["--baseline", str(baseline_file)])

    def test_exactly_the_baseline_passes(self, monkeypatch, baseline_file) -> None:
        assert self._run(monkeypatch, baseline_file, ["pkg.thing:Thing.known"]) == 0

    def test_a_new_dark_symbol_fails(self, monkeypatch, baseline_file) -> None:
        code = self._run(
            monkeypatch,
            baseline_file,
            ["pkg.thing:Thing.known", "pkg.thing:Thing.fresh"],
        )
        assert code == 1

    def test_a_stale_baseline_line_fails(self, monkeypatch, baseline_file) -> None:
        """The property that makes the file shrink-only: wiring a baselined
        symbol to a real consumer must force its line to be deleted."""

        assert self._run(monkeypatch, baseline_file, []) == 1

    def test_list_mode_never_fails(self, monkeypatch, baseline_file) -> None:
        monkeypatch.setattr(
            gate, "collect_findings", lambda **_: [self._finding("pkg.thing:Thing.new")]
        )
        assert main(["--baseline", str(baseline_file), "--list"]) == 0


class TestAgainstTheRealTree:
    """The half that decides whether this gate was worth writing."""

    @staticmethod
    @pytest.fixture(scope="class")
    def findings() -> list[Finding]:
        # Class-scoped: one full scan of the service shared by every case here.
        return collect_findings()

    @pytest.mark.parametrize(("key", "detector"), AUDITED_DARK)
    def test_audited_dark_symbol_is_reported(self, findings, key, detector) -> None:
        found = {finding.key: finding.detector for finding in findings}
        assert key in found, (
            f"{key} was confirmed dark by hand in the nine-dimension audit and "
            "this gate no longer reports it. Either it has genuinely been wired "
            "(delete its baseline line and this case) or a detector was narrowed "
            "until it stopped catching the defect it exists for."
        )
        assert found[key] == detector

    @pytest.mark.parametrize(
        "key",
        [
            "runtime_worker.stream_events:_Fields.grant_options",
            "agent_runtime.surfaces_v2.gate:_PayloadKey.grant_options",
        ],
    )
    def test_grant_options_is_wired_now(self, findings, key) -> None:
        """The mirror image of an `AUDITED_DARK` case, kept as evidence.

        `grant_options` was the audit's headline example of a wire key that
        LOOKS wired and is not: emitted on every filesystem approval and every
        write gate, declared in ``packages/api-types``, and mentioned in app
        code exactly once — in a STRIP LIST, i.e. the client's instruction to
        throw it away.

        It is read now. ``packages/chat-surface`` projects
        ``payload.grant_options`` onto the approval
        (``approvalProjection.buildGrantOptions``) and decides from it whether
        the ask card may offer a run-scoped ``always``. Deleting that read — or
        softening it back to a bare string in a list — puts both keys back in
        the findings and fails here, which is the whole point of turning the
        audit's hand-confirmed list into a test rather than a note.

        Both lanes are asserted because they emit the same word for two
        different acts, and only the write-gate one reaches a control; a fix
        that wired one lane and left the other as a string would otherwise pass.
        """

        reported = {finding.key: finding.detector for finding in findings}
        assert key not in reported, (
            f"{key} is dark again — the app tree no longer READS it off the "
            "payload (a bare string in a strip list or a type member does not "
            "count). See packages/chat-surface/src/destinations/run/"
            "approvalProjection.ts."
        )

    def test_grant_options_read_is_code_and_not_a_comment(self) -> None:
        """The case above, tightened past what the scanner itself can see.

        ``collect_ts_usage`` matches ``_TS_DOT_READ`` against RAW FILE TEXT, so
        a doc comment saying ``payload.grant_options`` satisfies it exactly as a
        real expression does. That is tolerable for a ratchet — it errs toward
        silence — but it is not good enough for the one key this program was
        opened to un-darken: deleting the projection's read while leaving its
        JSDoc in place would keep the gate green over a key nothing consumes.

        So this asserts the EXPRESSION, in the statement that binds it.
        """

        projection = (
            REPO_ROOT
            / "packages"
            / "chat-surface"
            / "src"
            / "destinations"
            / "run"
            / "approvalProjection.ts"
        )
        source = projection.read_text(encoding="utf-8")
        assert "const raw = event.payload.grant_options;" in source, (
            "approvalProjection no longer binds `event.payload.grant_options`. "
            "If the read moved, move this assertion with it — do not delete it: "
            "a JSDoc mention alone keeps check_dark_wiring green over a key the "
            "client has stopped consuming."
        )

    def test_baseline_is_in_sync(self) -> None:
        assert main([]) == 0

    def test_every_baseline_line_carries_a_reason(self) -> None:
        entries = Baseline.load(BASELINE_PATH).entries
        assert entries, "the baseline should not be empty while debt remains"
        missing = sorted(key for key, reason in entries.items() if not reason.strip())
        assert not missing, (
            "every baseline line needs a hand-written reason after "
            f"{BASELINE_SEPARATOR.strip()!r}: {missing}"
        )

    def test_baseline_has_no_duplicate_keys(self) -> None:
        raw = BASELINE_PATH.read_text(encoding="utf-8").splitlines()
        keys = [
            line.split(BASELINE_SEPARATOR.strip())[0].strip()
            for line in raw
            if line.strip() and not line.strip().startswith("#")
        ]
        assert len(keys) == len(set(keys))
