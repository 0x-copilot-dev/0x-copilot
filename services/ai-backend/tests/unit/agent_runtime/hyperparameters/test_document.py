"""The checked-in ``hyperparameters.json`` itself.

Two acceptance criteria live here.

**AC3 — the document is fully loggable.** A boot-time line that dumps the whole
document is a feature; the secret-shaped-key scan is what keeps it safe. The scan
is deliberately word-based rather than substring-based, because ``max_input_tokens``
is a token *count* and a substring rule would either flag it or be relaxed into
uselessness. The two legitimate ``tokens`` keys are allowlisted by exact path, so
adding a third is a conscious act in review.

**AC4 — defaults are byte-identical to today's.** The document is asserted
field-by-field against the values the migration will move, and round-tripped
through the model so a field that is *only* a model default (silently absent from
the JSON) fails here rather than drifting unnoticed.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

from agent_runtime.hyperparameters.contracts import Hyperparameters
from agent_runtime.hyperparameters.loader import HyperparameterLoader
from agent_runtime.persistence.records.tool_budgets import DefaultToolBudget


class CheckedInDocumentMixin:
    """The document on disk, plus the values the plan pins it to."""

    #: Every value here is today's runtime value, so landing the document
    #: changes no behaviour. Two entries are new knobs that no consumer reads
    #: yet: ``mcp_loading.catalog_page_size`` and
    #: ``reads.offloaded_result_line_limit`` (plus ``defer_loading_policy``,
    #: which ships ``off`` precisely so it is inert).
    EXPECTED: dict[str, object] = {
        "schema_version": 1,
        "mcp_loading": {
            "max_tool_descriptors": 100,
            "max_resource_descriptors": 100,
            "timeout_seconds": 30.0,
            "catalog_page_size": 40,
            "defer_loading_policy": "off",
        },
        "mcp_catalog": {
            "server_markdown_max_bytes": 4096,
            "header_reserve_bytes": 900,
            "index_summary_max_bytes": 96,
            "index_summary_min_bytes": 24,
        },
        # ``always`` is today's behaviour, so landing the knob changes nothing.
        # The saving is realised by an operator writing ``off`` here — a
        # reviewable one-line diff, which is the whole point of the document.
        "tool_surface": {
            "artifact_family": "always",
        },
        "reads": {
            "default_line_limit": 2000,
            "offloaded_result_line_limit": 20000,
        },
        "retry": {
            "max_attempts": 3,
            "initial_backoff_seconds": 0.5,
            "max_backoff_seconds": 4.0,
        },
        "execution": {
            "max_retries": 2,
            "max_parallel_runs": 4,
            "max_parallel_tasks": 4,
            "max_parallel_subagents": 4,
            # The plan's illustrative snippet says 40; the ENFORCED seed row
            # (DefaultToolBudget.MAX_CALLS_PER_RUN) says 10, and AC4 requires
            # today's value. 40 here would fork a number that
            # tests/unit/architecture/test_named_default_ssot.py already pins
            # across four sites.
            "tool_call_budget": 10,
            "default_timeout_seconds": 180.0,
            "delta_coalesce_window_ms": 0,
            "delta_coalesce_max_chunks": 64,
            "worker_poll_interval_seconds": 1.0,
            "worker_lock_seconds": 60,
        },
        "subagents": {"timeout_seconds": 120, "concurrency_limit": 2},
        "context": {
            "max_input_tokens": 128000,
            "recent_context_ratio": 0.25,
            "summary_threshold_ratio": 0.85,
            "preview_line_limit": 10,
            "preview_char_limit": 2000,
            "offload_preview_chars": 200,
            "model_result_preview_bytes": 8192,
        },
        "model_mapper": {"max_output_tokens": 1024, "temperature": 0.0},
        "observability": {"max_stream_field_length": 2000},
        "citations": {"per_result_max": 25},
        "search": {
            "content_token_budget": 1_200,
            "window_chars": 1_200,
            "lead_chars": 900,
            "min_window_chars": 200,
        },
    }

    @staticmethod
    def raw_document() -> dict[str, object]:
        path = HyperparameterLoader.default_path()
        return json.loads(path.read_text(encoding="utf-8"))


class SecretKeyScanMixin:
    """Word-based scan for secret-shaped key paths."""

    SECRET_WORDS = frozenset(
        {"key", "secret", "token", "password", "credential", "dsn", "url"}
    )
    #: Exact paths whose ``tokens`` word is a COUNT of context tokens, not a
    #: credential. Kept as full paths, not as a relaxed rule, so a genuinely
    #: secret-shaped key can never be admitted by a pattern loophole.
    ALLOWED_PATHS = frozenset(
        {
            "/context/max_input_tokens",
            "/model_mapper/max_output_tokens",
            # A count of context tokens `web_search` may spend, not a bearer of
            # anything. Listed as a full path for the reason above: the leaf
            # genuinely contains the word, and admitting it by loosening the
            # rule would admit a real credential named the same way.
            "/search/content_token_budget",
        }
    )

    @classmethod
    def key_paths(cls, payload: object, prefix: str = "") -> Iterator[str]:
        """Yield every key path in ``payload``, sections included."""

        if not isinstance(payload, dict):
            return
        for key, value in payload.items():
            path = f"{prefix}/{key}"
            yield path
            yield from cls.key_paths(value, path)

    @classmethod
    def is_secret_shaped(cls, path: str) -> bool:
        """Return whether any ``_``-separated word in the leaf is a secret word.

        A trailing ``s`` is stripped before the comparison so ``tokens`` is
        judged as ``token``: a plural is not a disguise.
        """

        leaf = path.rsplit("/", 1)[-1]
        return any(
            word.removesuffix("s") in cls.SECRET_WORDS or word in cls.SECRET_WORDS
            for word in leaf.split("_")
        )

    @classmethod
    def offenders(cls, payload: object) -> tuple[str, ...]:
        return tuple(
            path
            for path in cls.key_paths(payload)
            if cls.is_secret_shaped(path) and path not in cls.ALLOWED_PATHS
        )


class TestCheckedInDocument(CheckedInDocumentMixin):
    def test_the_document_validates(self) -> None:
        assert isinstance(HyperparameterLoader.default(), Hyperparameters)

    def test_values_match_the_plan(self) -> None:
        assert self.raw_document() == self.EXPECTED

    def test_the_document_states_every_field_explicitly(self) -> None:
        """A field present only as a model default would drift unnoticed."""

        document = HyperparameterLoader.default()
        assert document.model_dump(mode="json") == self.raw_document()

    def test_model_defaults_equal_the_document(self) -> None:
        """AC4 both ways: constructing with no document reproduces it exactly."""

        assert Hyperparameters().model_dump(mode="json") == self.raw_document()

    def test_tool_call_budget_equals_the_enforced_seed_cap(self) -> None:
        """A fifth declaration site must not fork the number the middleware admits.

        ``DefaultToolBudget.MAX_CALLS_PER_RUN`` is the value
        ``ToolBudgetMiddleware`` actually enforces; a document copy that
        disagrees tells the model a budget it does not have.
        """

        execution = self.EXPECTED["execution"]
        assert isinstance(execution, dict)
        assert execution["tool_call_budget"] == DefaultToolBudget.MAX_CALLS_PER_RUN

    def test_the_document_lives_inside_the_package(self) -> None:
        """Not at the service root — that location ships in nothing.

        Every packaging path copies DIRECTORIES out of `services/ai-backend`, so
        a loose file at the service root reaches neither the desktop stage nor
        the Docker image. Since a missing document is a hard boot failure, the
        root location meant every packaged boot raised while every test passed.
        """

        import agent_runtime

        package = Path(agent_runtime.__file__).resolve().parent
        assert HyperparameterLoader.default_path() == (
            package / "hyperparameters" / "hyperparameters.json"
        )


class TestSecretHygiene(CheckedInDocumentMixin, SecretKeyScanMixin):
    def test_no_key_path_in_the_document_is_secret_shaped(self) -> None:
        assert self.offenders(self.raw_document()) == ()

    def test_no_field_in_the_schema_is_secret_shaped(self) -> None:
        """Scan the model too: a field added but not yet written is still caught."""

        assert self.offenders(Hyperparameters().model_dump(mode="json")) == ()

    def test_the_scan_actually_walked_the_document(self) -> None:
        """A scanner that walked nothing is indistinguishable from a clean one."""

        paths = tuple(self.key_paths(self.raw_document()))
        assert len(paths) > 30
        assert "/execution/max_parallel_subagents" in paths

    def test_the_scan_flags_a_planted_secret_key(self) -> None:
        """Negative control: the rule must actually reject the shapes it names."""

        planted = {
            "mcp_loading": {
                "api_key": "x",
                "client_secret": "x",
                "refresh_token": "x",
                "admin_password": "x",
                "credential_ref": "x",
                "database_dsn": "x",
                "registry_url": "x",
            }
        }
        assert set(self.offenders(planted)) == {
            "/mcp_loading/api_key",
            "/mcp_loading/client_secret",
            "/mcp_loading/refresh_token",
            "/mcp_loading/admin_password",
            "/mcp_loading/credential_ref",
            "/mcp_loading/database_dsn",
            "/mcp_loading/registry_url",
        }

    def test_a_plural_secret_word_is_not_a_disguise(self) -> None:
        assert self.offenders({"auth": {"api_keys": "x"}}) == ("/auth/api_keys",)

    def test_every_allowlisted_path_still_exists(self) -> None:
        """The allowlist cannot rot into cover for a key that was since renamed."""

        paths = set(self.key_paths(self.raw_document()))
        assert self.ALLOWED_PATHS <= paths
