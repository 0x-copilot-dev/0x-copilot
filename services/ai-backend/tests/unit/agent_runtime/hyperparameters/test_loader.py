"""Loader tests: loud failure at load, and one uniform override surface.

The loader is the only place allowed to touch the filesystem or the environment
for a tunable, so these tests carry two acceptance criteria on their own: an
invalid document fails *at load* with a JSON pointer rather than lazily at first
use, and an environment override is re-validated through the same frozen model
so it is bound-checked identically to a value written into the document.
"""

from __future__ import annotations

import json
import shutil

import agent_runtime
import logging
from pathlib import Path

import pytest
from pydantic import ValidationError

from agent_runtime.hyperparameters.contracts import (
    DeferLoadingPolicy,
    Hyperparameters,
)
from agent_runtime.hyperparameters.loader import (
    HyperparameterError,
    HyperparameterLoader,
)


class DocumentWriterMixin:
    """Writes candidate documents to a tmp path."""

    LOGGER_NAME = "agent_runtime.hyperparameters"

    @staticmethod
    def write(tmp_path: Path, payload: object) -> Path:
        path = tmp_path / "hyperparameters.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    @staticmethod
    def minimal() -> dict[str, object]:
        """A document that states only the version; sections take their defaults."""

        return {"schema_version": 1}


class TestFromPath(DocumentWriterMixin):
    def test_reads_and_validates_a_document(self, tmp_path: Path) -> None:
        path = self.write(
            tmp_path, {"schema_version": 1, "execution": {"max_retries": 5}}
        )
        document = HyperparameterLoader.from_path(path)
        assert isinstance(document, Hyperparameters)
        assert document.execution.max_retries == 5

    def test_accepts_a_string_path(self, tmp_path: Path) -> None:
        path = self.write(tmp_path, self.minimal())
        assert HyperparameterLoader.from_path(str(path)).schema_version == 1

    def test_a_missing_file_fails_typed(self, tmp_path: Path) -> None:
        with pytest.raises(HyperparameterError) as caught:
            HyperparameterLoader.from_path(tmp_path / "absent.json")
        assert caught.value.pointer == "/"
        assert "absent.json" in str(caught.value)

    def test_malformed_json_fails_typed(self, tmp_path: Path) -> None:
        path = tmp_path / "hyperparameters.json"
        path.write_text("{not json", encoding="utf-8")
        with pytest.raises(HyperparameterError) as caught:
            HyperparameterLoader.from_path(path)
        assert caught.value.pointer == "/"

    def test_a_non_object_document_fails_typed(self, tmp_path: Path) -> None:
        path = self.write(tmp_path, [1, 2, 3])
        with pytest.raises(HyperparameterError) as caught:
            HyperparameterLoader.from_path(path)
        assert "top level" in str(caught.value)

    def test_an_unknown_key_names_its_pointer(self, tmp_path: Path) -> None:
        """AC1: an unknown key fails at boot, and the message says where."""

        path = self.write(
            tmp_path, {"schema_version": 1, "execution": {"max_retreis": 2}}
        )
        with pytest.raises(HyperparameterError) as caught:
            HyperparameterLoader.from_path(path)
        assert caught.value.pointer == "/execution/max_retreis"
        assert str(path) in caught.value.source

    def test_an_out_of_bounds_value_names_its_pointer(self, tmp_path: Path) -> None:
        path = self.write(
            tmp_path, {"schema_version": 1, "subagents": {"timeout_seconds": 999_999}}
        )
        with pytest.raises(HyperparameterError) as caught:
            HyperparameterLoader.from_path(path)
        assert caught.value.pointer == "/subagents/timeout_seconds"

    def test_a_cross_field_failure_is_typed_too(self, tmp_path: Path) -> None:
        path = self.write(
            tmp_path,
            {
                "schema_version": 1,
                "reads": {"default_line_limit": 2000, "offloaded_result_line_limit": 1},
            },
        )
        with pytest.raises(HyperparameterError) as caught:
            HyperparameterLoader.from_path(path)
        assert caught.value.pointer.startswith("/reads")

    def test_the_error_is_a_value_error(self) -> None:
        assert issubclass(HyperparameterError, ValueError)


class TestDefaultDocumentLocation:
    def test_the_service_root_document_is_searched_first(self) -> None:
        """Exactly one location, and it is inside the package."""

        candidates = HyperparameterLoader.candidate_paths()
        assert len(candidates) == 1
        assert candidates[0].name == HyperparameterLoader.Document.FILE_NAME
        assert candidates[0].parent.name == "hyperparameters"

    def test_default_path_resolves_to_an_existing_file(self) -> None:
        assert HyperparameterLoader.default_path().is_file()

    def test_the_document_survives_a_src_only_copy(self, tmp_path: Path) -> None:
        """The check that a dev checkout cannot make pass by accident.

        `RuntimeSettings.load` hard-fails when the document is missing, and every
        packaging path copies DIRECTORIES out of `services/ai-backend` — the
        desktop stager takes src/migrations/scripts/config/skills, the Dockerfile
        the same shape. The document was first written to the SERVICE ROOT, which
        neither carries, so every packaged boot would have raised while every
        test above stayed green. `default_path().is_file()` cannot catch that: it
        resolves against the developer's own tree.

        This copies `src` exactly as the stager does and asserts the document
        came with it.
        """

        package = Path(agent_runtime.__file__).resolve().parent
        staged_src = tmp_path / "src"
        shutil.copytree(package.parent, staged_src, dirs_exist_ok=False)

        staged_document = (
            staged_src
            / "agent_runtime"
            / "hyperparameters"
            / HyperparameterLoader.Document.FILE_NAME
        )

        assert staged_document.is_file(), (
            "the document is not inside src/, so no packaged build carries it"
        )
        assert json.loads(staged_document.read_text(encoding="utf-8")) == json.loads(
            HyperparameterLoader.default_path().read_text(encoding="utf-8")
        )

    def test_default_loads_the_checked_in_document(self) -> None:
        document = HyperparameterLoader.default()
        raw = json.loads(
            HyperparameterLoader.default_path().read_text(encoding="utf-8")
        )
        assert document.model_dump(mode="json") == raw


class TestEnvironmentOverrides(DocumentWriterMixin):
    def test_no_overrides_returns_the_same_document(self) -> None:
        base = Hyperparameters()
        assert HyperparameterLoader.with_overrides(base, {}) is base

    def test_unprefixed_variables_are_ignored(self) -> None:
        base = Hyperparameters()
        env = {"RUNTIME_MAX_PARALLEL_SUBAGENTS": "1", "PATH": "/usr/bin"}
        assert HyperparameterLoader.with_overrides(base, env) is base

    def test_an_integer_override_applies(self) -> None:
        result = HyperparameterLoader.with_overrides(
            Hyperparameters(), {"COPILOT_HP__EXECUTION__MAX_PARALLEL_SUBAGENTS": "1"}
        )
        assert result.execution.max_parallel_subagents == 1

    def test_a_float_override_applies(self) -> None:
        result = HyperparameterLoader.with_overrides(
            Hyperparameters(), {"COPILOT_HP__RETRY__INITIAL_BACKOFF_SECONDS": "1.5"}
        )
        assert result.retry.initial_backoff_seconds == 1.5

    def test_a_bare_enum_member_override_applies(self) -> None:
        """``mcp_only`` is not valid JSON; it must survive as a string."""

        result = HyperparameterLoader.with_overrides(
            Hyperparameters(),
            {"COPILOT_HP__MCP_LOADING__DEFER_LOADING_POLICY": "mcp_only"},
        )
        assert result.mcp_loading.defer_loading_policy is DeferLoadingPolicy.MCP_ONLY

    def test_an_override_leaves_its_siblings_untouched(self) -> None:
        base = Hyperparameters()
        result = HyperparameterLoader.with_overrides(
            base, {"COPILOT_HP__EXECUTION__MAX_RETRIES": "7"}
        )
        assert result.execution.max_retries == 7
        assert result.execution.max_parallel_runs == base.execution.max_parallel_runs
        assert result.subagents == base.subagents

    def test_an_override_is_bound_checked_like_the_document(self) -> None:
        """The break-glass path cannot widen an invariant."""

        with pytest.raises(HyperparameterError) as caught:
            HyperparameterLoader.with_overrides(
                Hyperparameters(), {"COPILOT_HP__SUBAGENTS__TIMEOUT_SECONDS": "999999"}
            )
        assert caught.value.pointer == "/subagents/timeout_seconds"

    def test_an_override_cannot_smuggle_an_unknown_field(self) -> None:
        with pytest.raises(HyperparameterError) as caught:
            HyperparameterLoader.with_overrides(
                Hyperparameters(), {"COPILOT_HP__EXECUTION__MAX_RETREIS": "2"}
            )
        assert caught.value.pointer == "/execution/max_retreis"

    def test_an_override_cannot_invent_a_section(self) -> None:
        with pytest.raises(HyperparameterError) as caught:
            HyperparameterLoader.with_overrides(
                Hyperparameters(), {"COPILOT_HP__BILLING__RATE": "2"}
            )
        assert caught.value.pointer == "/billing"

    def test_an_override_naming_no_field_fails_typed(self) -> None:
        with pytest.raises(HyperparameterError) as caught:
            HyperparameterLoader.with_overrides(
                Hyperparameters(), {"COPILOT_HP__": "1"}
            )
        assert "COPILOT_HP__" in str(caught.value)

    def test_a_trailing_separator_fails_typed(self) -> None:
        with pytest.raises(HyperparameterError):
            HyperparameterLoader.with_overrides(
                Hyperparameters(), {"COPILOT_HP__EXECUTION__": "1"}
            )

    def test_the_process_environment_is_the_default_source(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Only the loader reads env — but it does read it, with no argument."""

        monkeypatch.setenv("COPILOT_HP__CITATIONS__PER_RESULT_MAX", "3")
        result = HyperparameterLoader.with_overrides(Hyperparameters())
        assert result.citations.per_result_max == 3

    def test_every_applied_override_is_logged_with_old_and_new(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """An incident knob must never be invisible six months later."""

        with caplog.at_level(logging.INFO, logger=self.LOGGER_NAME):
            HyperparameterLoader.with_overrides(
                Hyperparameters(),
                {"COPILOT_HP__EXECUTION__MAX_PARALLEL_SUBAGENTS": "1"},
            )
        messages = [record.getMessage() for record in caplog.records]
        assert any(
            "hyperparameters.override_applied" in message
            and "pointer=/execution/max_parallel_subagents" in message
            and "old=4" in message
            and "new=1" in message
            for message in messages
        ), messages

    def test_a_failing_override_is_logged_before_it_is_rejected(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The line naming the knob must sit next to the error naming it."""

        with caplog.at_level(logging.INFO, logger=self.LOGGER_NAME):
            with pytest.raises(HyperparameterError):
                HyperparameterLoader.with_overrides(
                    Hyperparameters(),
                    {"COPILOT_HP__SUBAGENTS__CONCURRENCY_LIMIT": "10000"},
                )
        assert any(
            "pointer=/subagents/concurrency_limit" in record.getMessage()
            for record in caplog.records
        )


class TestAppliedOverrideRecords:
    def test_records_carry_pointer_old_and_new(self) -> None:
        records = HyperparameterLoader.applied_overrides(
            Hyperparameters(), {"COPILOT_HP__EXECUTION__MAX_PARALLEL_RUNS": "2"}
        )
        assert len(records) == 1
        assert records[0].pointer == "/execution/max_parallel_runs"
        assert records[0].previous == "4"
        assert records[0].applied == "2"

    def test_an_unknown_pointer_records_no_previous_value(self) -> None:
        records = HyperparameterLoader.applied_overrides(
            Hyperparameters(), {"COPILOT_HP__EXECUTION__MAX_RETREIS": "2"}
        )
        assert records[0].previous is None

    def test_records_are_ordered_by_variable_name(self) -> None:
        records = HyperparameterLoader.applied_overrides(
            Hyperparameters(),
            {
                "COPILOT_HP__EXECUTION__MAX_RETRIES": "1",
                "COPILOT_HP__CITATIONS__PER_RESULT_MAX": "9",
            },
        )
        assert [record.pointer for record in records] == [
            "/citations/per_result_max",
            "/execution/max_retries",
        ]

    def test_collecting_records_does_not_bound_check(self) -> None:
        """A rejected value must still be recorded, or the log cannot explain it."""

        records = HyperparameterLoader.applied_overrides(
            Hyperparameters(), {"COPILOT_HP__SUBAGENTS__TIMEOUT_SECONDS": "999999"}
        )
        assert records[0].applied == "999999"

    def test_a_malformed_variable_name_still_raises(self) -> None:
        """There is no pointer to record for a name that addresses no field."""

        with pytest.raises(HyperparameterError):
            HyperparameterLoader.applied_overrides(
                Hyperparameters(), {"COPILOT_HP__EXECUTION__": "1"}
            )

    def test_records_are_frozen(self) -> None:
        record = HyperparameterLoader.applied_overrides(
            Hyperparameters(), {"COPILOT_HP__CITATIONS__PER_RESULT_MAX": "9"}
        )[0]
        with pytest.raises(ValidationError):
            record.pointer = "/elsewhere"


class TestLoadActuallyReadsTheDocument:
    """The one path where editing the JSON changes runtime behaviour.

    `RuntimeSettings.load()` is the ONLY wired consumer: `settings.execution.*`
    reads the document, and a `COPILOT_HP__` override reaches it. An adversarial
    review mutated this seam — replacing the load with bare `Hyperparameters()`
    defaults — and NOTHING failed, because every other test either constructs a
    document explicitly or asserts the JSON equals the model defaults (which it
    does, so a mirror and a source are indistinguishable to them).

    These tests distinguish them: they write a document whose values DIFFER from
    the defaults and assert the difference survives into settings.
    """

    def _document_with(self, tmp_path: Path, **execution: object) -> Path:
        raw = json.loads(
            HyperparameterLoader.default_path().read_text(encoding="utf-8")
        )
        raw["execution"].update(execution)
        path = tmp_path / "hyperparameters.json"
        path.write_text(json.dumps(raw), encoding="utf-8")
        return path

    def test_a_non_default_document_value_reaches_settings(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from agent_runtime.settings import RuntimeSettings

        default = RuntimeSettings.load(environ={}).execution.max_parallel_runs
        changed = default + 3
        path = self._document_with(tmp_path, max_parallel_runs=changed)
        monkeypatch.setattr(
            HyperparameterLoader, "candidate_paths", classmethod(lambda cls: (path,))
        )

        settings = RuntimeSettings.load(environ={})

        assert settings.execution.max_parallel_runs == changed
        assert settings.hyperparameters.execution.max_parallel_runs == changed

    def test_an_env_override_beats_the_document_through_load(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from agent_runtime.settings import RuntimeSettings

        path = self._document_with(tmp_path, max_parallel_runs=7)
        monkeypatch.setattr(
            HyperparameterLoader, "candidate_paths", classmethod(lambda cls: (path,))
        )

        settings = RuntimeSettings.load(
            environ={"COPILOT_HP__EXECUTION__MAX_PARALLEL_RUNS": "11"}
        )

        assert settings.execution.max_parallel_runs == 11

    def test_an_unknown_document_key_fails_the_boot(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from agent_runtime.settings import RuntimeSettings

        raw = json.loads(
            HyperparameterLoader.default_path().read_text(encoding="utf-8")
        )
        raw["execution"]["not_a_real_knob"] = 1
        path = tmp_path / "hyperparameters.json"
        path.write_text(json.dumps(raw), encoding="utf-8")
        monkeypatch.setattr(
            HyperparameterLoader, "candidate_paths", classmethod(lambda cls: (path,))
        )

        with pytest.raises(HyperparameterError):
            RuntimeSettings.load(environ={})
