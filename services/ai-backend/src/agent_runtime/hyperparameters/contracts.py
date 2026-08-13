"""Frozen contracts for ``services/ai-backend/hyperparameters.json``.

One checked-in JSON document holds every agent-behaviour tunable — retries,
parallelism, budgets, timeouts, previews, ratios — so a tuning change is a
reviewable diff rather than an environment change. ``RuntimeSettings`` keeps the
deployment concerns (store backend, DSNs, provider keys, rollout switches); the
dividing line is in docs/plan/mcp-tooling-program/HYPERPARAMETERS-PLAN.md §3.

Two properties of these models carry the acceptance criteria:

* ``extra="forbid"`` — an unknown key fails at boot, not silently at first use.
  A renamed field therefore cannot quietly fall back to its default.
* every field carries ``ge=``/``le=``. Where an existing **invariant** already
  bounds the value it is imported (see :data:`SubagentLimits`) so the JSON can
  never widen a contract; where no invariant exists the ceiling lives once in
  :class:`HyperparameterBounds` with the site it mirrors.

Nothing here reads the environment or the filesystem — that is
:class:`~agent_runtime.hyperparameters.loader.HyperparameterLoader`'s job, and
keeping it there is what makes "no consumer ever reads an env var" checkable.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agent_runtime.delegation.subagents.constants import Limits as SubagentLimits


class DeferLoadingPolicy(StrEnum):
    """Whether tool definitions may be emitted with Anthropic's ``defer_loading``.

    A ``StrEnum`` rather than a ``bool`` because three states are already needed
    and a boolean would have to be widened later. The knob expresses *intent*
    only: the builder still decides whether the resolved provider can honour it,
    because ``defer_loading`` sent to an OpenAI-compatible ``/chat/completions``
    is at best ignored and at worst rejected. A JSON knob must never be able to
    put a malformed request on the wire.
    """

    #: Never emit ``defer_loading``. The only safe value until the MCP catalog
    #: work it depends on is live-verified.
    OFF = "off"
    #: Defer MCP-sourced definitions; built-ins and the search tool stay eager.
    MCP_ONLY = "mcp_only"
    #: Defer every deferrable definition.
    ALL = "all"


#: The tokenizer-free ratio `SearchContentBudget` converts with. Mirrored
#: rather than imported to keep this module free of capability imports.
_CHARS_PER_TOKEN: Final[int] = 4


class HyperparameterBounds:
    """Ceilings this document introduces, each naming the site it mirrors.

    A bound that already exists as an invariant is imported instead of restated
    (``SubagentLimits.TIMEOUT_MAX_SECONDS`` is the worked example). These are the
    remainder: values for which no named invariant exists today. They live in one
    class so the same ceiling is not re-typed in five sections, which is how a
    "bound" quietly becomes five different bounds.
    """

    #: ``ModelConfig.timeout_seconds`` (execution/contracts.py) and
    #: ``RuntimeSettings.default_timeout_seconds`` (settings.py) both cap a
    #: single call at 600s. Every timeout in this document shares that envelope.
    TIMEOUT_SECONDS_MAX: Final[float] = 600.0
    #: ``ModelConfig.max_input_tokens`` / ``max_output_tokens`` are ``le=2_000_000``.
    MODEL_TOKENS_MAX: Final[int] = 2_000_000
    #: ``ModelConfig.temperature`` is ``ge=0, le=2``.
    TEMPERATURE_MAX: Final[float] = 2.0
    #: ``RuntimeExecutionSettings`` bounds every parallelism knob at 100 today.
    #: Deliberately NOT narrowed to ``ConcurrencyBounds.MAX_PARALLELISM`` (16):
    #: that pair defines the semantics of F6 ``ConcurrencyMode``, not this
    #: envelope, and narrowing here would reject a deployment that boots today.
    PARALLELISM_MAX: Final[int] = 100
    #: ``RuntimeExecutionSettings.max_retries`` is ``le=10``; the retry policy
    #: shares it so "attempts" and "retries" cannot drift into different orders
    #: of magnitude.
    RETRY_ATTEMPTS_MAX: Final[int] = 10
    #: Preview and stream-field budgets are model-facing text; a megabyte is
    #: already far past any context window and exists only to stop a typo
    #: (a stray trailing zero) from inlining a whole blob.
    PREVIEW_CHARS_MAX: Final[int] = 1_000_000
    #: Same reasoning, counted in lines rather than characters.
    LINE_LIMIT_MAX: Final[int] = 1_000_000
    #: No invariant in ``capabilities/mcp/constants.py`` bounds a descriptor
    #: *count* — the ``Limits`` there bound descriptor *text*. The largest real
    #: connector observed is Linear at 52 tools, so this is ~20x headroom while
    #: still refusing a registry that would turn admission into a stall.
    DESCRIPTOR_COUNT_MAX: Final[int] = 1_000
    #: The always-loaded catalog tier is exactly what a 70,465-byte Linear
    #: descriptor blob blew up. A ceiling below that size keeps a mis-tuned
    #: budget from re-creating the bug the catalog exists to remove.
    ALWAYS_LOADED_BYTES_MAX: Final[int] = 65_536
    #: Byte-sized previews of a persisted tool result.
    RESULT_PREVIEW_BYTES_MAX: Final[int] = 1_048_576
    #: Per-result citation fan-out; a connector page rarely exceeds a few dozen.
    CITATIONS_PER_RESULT_MAX: Final[int] = 1_000
    #: `SearchContentBudget` in capabilities/search/contracts.py. A ceiling
    #: rather than a target: past a few thousand tokens per search the tool
    #: is no longer summarising the web, it is pasting it.
    SEARCH_CONTENT_TOKENS_MAX: Final[int] = 8_000
    SEARCH_PASSAGE_CHARS_MAX: Final[int] = 20_000
    #: LangGraph super-steps per graph invocation. At the measured cost of this
    #: repo's Deep Agents graph (see ``ExecutionHyperparameters.recursion_limit``)
    #: this ceiling is roughly 500 model/tool rounds in a single turn — past the
    #: point where "the model is working" and "the model is looping" stop being
    #: distinguishable from the outside. Not ``TIMEOUT_SECONDS_MAX``-shaped,
    #: because a super-step is a unit of work, not of time: the wall clock is
    #: bounded separately by ``RUN_DEADLINE_SECONDS_MAX``.
    RECURSION_LIMIT_MAX: Final[int] = 2_000
    #: The run-level wall clock, deliberately larger than
    #: ``TIMEOUT_SECONDS_MAX`` (600s): that bound caps a SINGLE invocation, and
    #: a run legitimately makes many. Four hours is far past any interactive
    #: session and exists only so a mis-typed value cannot mean "never".
    RUN_DEADLINE_SECONDS_MAX: Final[float] = 14_400.0


class _FrozenContract(BaseModel):
    """Frozen, closed Pydantic base shared by the document and its overrides."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class HyperparameterSection(_FrozenContract):
    """One subsystem's block of the document.

    Sections are grouped by the subsystem that owns each number rather than
    flattened, so ownership of a tunable is legible from its path alone and an
    override pointer (``/execution/max_parallel_subagents``) reads as an address.
    """


class McpLoadingHyperparameters(HyperparameterSection):
    """Budgets applied while connecting to and admitting an MCP server."""

    max_tool_descriptors: int = Field(
        default=100, ge=1, le=HyperparameterBounds.DESCRIPTOR_COUNT_MAX
    )
    max_resource_descriptors: int = Field(
        default=100, ge=1, le=HyperparameterBounds.DESCRIPTOR_COUNT_MAX
    )
    timeout_seconds: float = Field(
        default=30.0, gt=0, le=HyperparameterBounds.TIMEOUT_SECONDS_MAX
    )
    # Pagination of the catalog index. Pagination must never hide a tool — the
    # union of pages lists every tool at any page size — so this carries no
    # cross-field constraint against the descriptor cap; the two are independent.
    catalog_page_size: int = Field(
        default=40, ge=1, le=HyperparameterBounds.DESCRIPTOR_COUNT_MAX
    )
    defer_loading_policy: DeferLoadingPolicy = DeferLoadingPolicy.OFF


class McpCatalogHyperparameters(HyperparameterSection):
    """Byte budgets for the always-loaded ``/mcp/<server>/SERVER.md`` tier."""

    server_markdown_max_bytes: int = Field(
        default=4_096, ge=1, le=HyperparameterBounds.ALWAYS_LOADED_BYTES_MAX
    )
    header_reserve_bytes: int = Field(
        default=900, ge=0, le=HyperparameterBounds.ALWAYS_LOADED_BYTES_MAX
    )
    index_summary_max_bytes: int = Field(
        default=96, ge=1, le=HyperparameterBounds.ALWAYS_LOADED_BYTES_MAX
    )
    index_summary_min_bytes: int = Field(
        default=24, ge=1, le=HyperparameterBounds.ALWAYS_LOADED_BYTES_MAX
    )

    @property
    def index_body_budget_bytes(self) -> int:
        """Bytes left for index rows after the fixed preamble is reserved.

        Mirrors the subtraction the catalog already performs when it sizes the
        summary column, so a section-level read and the writer cannot disagree
        about how much room the rows actually have.
        """

        return self.server_markdown_max_bytes - self.header_reserve_bytes

    @model_validator(mode="after")
    def _budgets_leave_room_for_rows(self) -> Self:
        """Refuse a preamble reserve that consumes the whole file budget.

        The catalog sizes the summary column as ``server_markdown_max_bytes -
        header_reserve_bytes - overhead``. A reserve at or above the file budget
        makes that expression non-positive, and the summary column silently
        vanishes from every index instead of failing — the defect this rejects.
        """

        if self.header_reserve_bytes >= self.server_markdown_max_bytes:
            raise ValueError(
                "header_reserve_bytes must leave room under "
                "server_markdown_max_bytes for at least one index row"
            )
        if self.index_summary_min_bytes > self.index_summary_max_bytes:
            raise ValueError(
                "index_summary_min_bytes cannot exceed index_summary_max_bytes; "
                "the minimum gate would never be satisfiable"
            )
        return self


class ReadHyperparameters(HyperparameterSection):
    """Line budgets applied when the runtime serves a read.

    ``default_line_limit`` is the shared value of the ``limit`` default that is
    restated at ~20 backend implementations; it matches the upstream deepagents
    ``BackendProtocol`` signature and must keep matching it, so it is carried
    here as the runtime's applied budget, not as licence to change the signature.
    """

    default_line_limit: int = Field(
        default=2_000, ge=1, le=HyperparameterBounds.LINE_LIMIT_MAX
    )
    # An already-offloaded blob is a content-addressed dump the model asked for
    # by reference; 2000 lines was chosen for a source file. With a descriptor
    # blob that contains no newlines at all, re-reading at an offset returns the
    # same first 2000 characters forever and the run ends in empty success.
    offloaded_result_line_limit: int = Field(
        default=20_000, ge=1, le=HyperparameterBounds.LINE_LIMIT_MAX
    )

    @model_validator(mode="after")
    def _offloaded_budget_is_the_larger_one(self) -> Self:
        """Refuse an offloaded budget below the generic one.

        The offloaded budget exists solely to make a legitimately large result
        reachable. Set below ``default_line_limit`` the knob is inverted: an
        explicitly-referenced blob would be truncated harder than an ordinary
        workspace file, which is the exact failure it was added to fix.
        """

        if self.offloaded_result_line_limit < self.default_line_limit:
            raise ValueError(
                "offloaded_result_line_limit must be at least default_line_limit"
            )
        return self


class RetryHyperparameters(HyperparameterSection):
    """Transient-failure retry policy shared by every wrapped tool."""

    max_attempts: int = Field(
        default=3, ge=1, le=HyperparameterBounds.RETRY_ATTEMPTS_MAX
    )
    initial_backoff_seconds: float = Field(
        default=0.5, gt=0, le=HyperparameterBounds.TIMEOUT_SECONDS_MAX
    )
    max_backoff_seconds: float = Field(
        default=4.0, gt=0, le=HyperparameterBounds.TIMEOUT_SECONDS_MAX
    )

    @model_validator(mode="after")
    def _initial_backoff_is_reachable(self) -> Self:
        """Refuse an initial backoff above the ceiling that clamps it.

        Full-jitter backoff waits ``uniform(0, min(initial * 2 ** (n - 1), max))``.
        With ``initial`` above ``max`` every wait collapses to ``uniform(0, max)``
        and the configured initial value is silently ignored — a setting that
        appears to apply and does not.
        """

        if self.initial_backoff_seconds > self.max_backoff_seconds:
            raise ValueError(
                "initial_backoff_seconds cannot exceed max_backoff_seconds; the "
                "backoff schedule would clamp it away on the first attempt"
            )
        return self


class ModelRetryHyperparameters(HyperparameterSection):
    """Pacing for re-dispatching ONE model call, owned by us not the SDK.

    Distinct from :class:`RetryHyperparameters` above, which paces a *tool*
    call, and from ``execution.max_retries``, which paces a whole *run claim*.
    Three scopes, three sections: a 429 twenty tool calls into a turn should
    cost a few seconds of backoff on the model call, not a re-run of the turn.

    ``max_attempts`` governs only the deployment default path. When the F10
    model-invocation journal is installed, ``ModelInvocationBudget`` remains the
    attempt authority and this section contributes pacing alone — one ceiling,
    not two disagreeing ones.
    """

    #: Total attempts for one model call, counting the first. ``3`` matches
    #: ``ModelInvocationBudget.max_attempts``' own ``le=3`` ceiling so the
    #: default path can never out-retry the journaled path.
    max_attempts: int = Field(default=3, ge=1, le=3)
    initial_backoff_seconds: float = Field(
        default=2.0, gt=0, le=HyperparameterBounds.TIMEOUT_SECONDS_MAX
    )
    #: Multiplier per attempt. ``1.0`` is a legal (constant-delay) schedule.
    backoff_factor: float = Field(default=2.0, ge=1.0, le=10.0)
    #: Fraction of the base delay added as upper jitter, spreading concurrent
    #: callers instead of synchronising them onto the same peak.
    jitter_factor: float = Field(default=0.25, ge=0.0, le=1.0)
    #: Ceiling when the provider sent no ``retry-after``.
    max_backoff_seconds: float = Field(
        default=30.0, gt=0, le=HyperparameterBounds.TIMEOUT_SECONDS_MAX
    )
    #: Ceiling applied to a provider-stated wait. Bounded for a concrete
    #: reason: ``execution.worker_lock_seconds`` is 60, so honouring a literal
    #: ``retry-after: 3600`` would let a second worker claim a run this one is
    #: still executing.
    provider_hint_max_seconds: float = Field(
        default=30.0, gt=0, le=HyperparameterBounds.TIMEOUT_SECONDS_MAX
    )

    @model_validator(mode="after")
    def _initial_backoff_is_reachable(self) -> Self:
        """Refuse an initial backoff the ceiling would clamp away on attempt 1.

        Same failure mode as :class:`RetryHyperparameters`: a configured value
        that appears to apply and never does.
        """

        if self.initial_backoff_seconds > self.max_backoff_seconds:
            raise ValueError(
                "initial_backoff_seconds cannot exceed max_backoff_seconds; the "
                "backoff schedule would clamp it away on the first attempt"
            )
        return self


class ExecutionHyperparameters(HyperparameterSection):
    """Run-level parallelism, budgets, and worker cadence.

    Bounds mirror the ``Field(...)`` envelopes these values carry in
    ``RuntimeSettings`` today. Keeping the accept/reject envelope identical is
    part of "this refactor changes no behaviour": narrowing a bound here would
    reject a deployment that boots on the current code.
    """

    max_retries: int = Field(
        default=2, ge=0, le=HyperparameterBounds.RETRY_ATTEMPTS_MAX
    )
    max_parallel_runs: int = Field(
        default=4, ge=1, le=HyperparameterBounds.PARALLELISM_MAX
    )
    max_parallel_tasks: int = Field(
        default=4, ge=1, le=HyperparameterBounds.PARALLELISM_MAX
    )
    max_parallel_subagents: int = Field(
        default=4, ge=1, le=HyperparameterBounds.PARALLELISM_MAX
    )
    # Per distinct tool name, per run. This is a fifth declaration site of a
    # number whose other four are pinned against each other by
    # tests/unit/architecture/test_named_default_ssot.py; the enforced seed row
    # (DefaultToolBudget.MAX_CALLS_PER_RUN) is authoritative and this must equal
    # it. Held as a literal rather than an import to keep the document's import
    # graph to constants modules only; the document test asserts the equality.
    tool_call_budget: int = Field(
        default=10, ge=1, le=HyperparameterBounds.PARALLELISM_MAX
    )
    # 180, not the plan sketch's 60: `env_example` shipped
    # `RUNTIME_DEFAULT_TIMEOUT_SECONDS=180`, so 180 is what every deployment
    # actually ran. Removing that line from the template while the document said
    # 60 would have cut the effective timeout by a third of its value silently —
    # AC4 is "byte-identical to today's", and today's is 180.
    default_timeout_seconds: float = Field(
        default=180.0, gt=0, le=HyperparameterBounds.TIMEOUT_SECONDS_MAX
    )
    delta_coalesce_window_ms: int = Field(default=0, ge=0, le=1_000)
    delta_coalesce_max_chunks: int = Field(default=64, ge=1, le=1_024)
    worker_poll_interval_seconds: float = Field(default=1.0, gt=0, le=60.0)
    worker_lock_seconds: int = Field(default=60, gt=0, le=3_600)
    # LangGraph super-steps allowed per graph invocation, passed through
    # ``runtime_config`` into the ``RunnableConfig``.
    #
    # Measured, not guessed. Driving the REAL Deep Agents graph (a scripted chat
    # model emitting N tool rounds) and bisecting the smallest limit that still
    # completes gives a linear fit, on langgraph 1.2.9 / deepagents 0.7.1:
    #
    #     minimal graph (no middleware, no subagents):  3 + 2 * tool_rounds
    #     with RuntimeControlMiddleware + a subagent:   6 + 4 * tool_rounds
    #
    # Measured at 0/1/2/3/5/8 rounds, exact at every point. The shape that
    # matters is the SLOPE: a model->tool->model round costs a small constant
    # number of super-steps, so the limit converts to a round count by simple
    # division. The intercept and slope both grow with the middleware stack, so
    # treat 4/round as the working figure and expect it to drift upward as
    # middleware is added — which is the argument for a generous backstop rather
    # than a tight one.
    #
    # Two library defaults are in play and neither is ours. ``langchain_core``
    # still defines ``DEFAULT_RECURSION_LIMIT = 25``, which by the fit above is
    # about five tool rounds — below the per-tool-name ``tool_call_budget`` of
    # 10, so a perfectly healthy run would die on a step limit instead of on the
    # legible budget message. But that is not the number this graph actually
    # gets: ``langgraph._internal._config`` supplies its own
    # ``DEFAULT_RECURSION_LIMIT`` of 10007 — some 2500 tool rounds, i.e. no
    # meaningful bound at all, and on a BYOK key 2500 completions of a loop the
    # user is paying for. Checked against the installed library rather than the
    # docs, because the two constants disagree and only one is reachable here.
    #
    # 500 is ~125 tool rounds at 4/round: an order of magnitude clear of every
    # in-loop budget a healthy run can legitimately spend (``tool_call_budget``
    # is 10 per tool name, 20 at ``deep``), and a bounded worst case of ~125
    # completions instead of ~2500. It is a backstop, not a working budget — the
    # budgets the model is *told* about are what should end a healthy run. It is
    # deliberately NOT a cost cap in time; that is ``run_deadline_seconds``,
    # which binds a slow loop where this one binds a fast one.
    recursion_limit: int = Field(
        default=500, ge=1, le=HyperparameterBounds.RECURSION_LIMIT_MAX
    )
    # Wall-clock ceiling for one worker execution of a run, distinct from
    # ``default_timeout_seconds`` / ``ModelConfig.timeout_seconds``: those bound
    # a single invocation (and are depth-scaled per subagent), this bounds the
    # whole agent loop. 1800s = 10x the 180s invocation default, so a healthy
    # multi-step run with subagents never approaches it, while a run wedged
    # somewhere the super-step counter cannot see — a tool that never returns,
    # a provider stream that stalls without erroring — still terminates.
    run_deadline_seconds: float = Field(
        default=1800.0, gt=0, le=HyperparameterBounds.RUN_DEADLINE_SECONDS_MAX
    )


class SubagentHyperparameters(HyperparameterSection):
    """Delegation defaults, bounded by the delegation invariants themselves."""

    timeout_seconds: int = Field(
        default=120, ge=1, le=SubagentLimits.TIMEOUT_MAX_SECONDS
    )
    concurrency_limit: int = Field(
        default=2, ge=1, le=SubagentLimits.CONCURRENCY_LIMIT_MAX
    )
    #: Delegation hops below the supervisor. Unlike the two above — which the
    #: package cannot read back, see ``delegation.subagents.constants.Defaults``
    #: — this one IS live: ``DelegationDepthPolicy.snapshot`` loads the document
    #: at agent-build time through a function-local import, so the value here
    #: (and its ``COPILOT_HP__SUBAGENTS__MAX_DELEGATION_DEPTH`` override) is
    #: what the ``task`` tool admits against.
    max_delegation_depth: int = Field(
        default=1, ge=1, le=SubagentLimits.DELEGATION_DEPTH_MAX
    )


class ContextHyperparameters(HyperparameterSection):
    """Context-window budgets, compaction ratios, and preview sizes."""

    max_input_tokens: int = Field(
        default=128_000, ge=1, le=HyperparameterBounds.MODEL_TOKENS_MAX
    )
    recent_context_ratio: float = Field(default=0.25, gt=0, lt=1)
    summary_threshold_ratio: float = Field(default=0.85, gt=0, le=1)
    preview_line_limit: int = Field(
        default=10, ge=1, le=HyperparameterBounds.LINE_LIMIT_MAX
    )
    preview_char_limit: int = Field(
        default=2_000, ge=1, le=HyperparameterBounds.PREVIEW_CHARS_MAX
    )
    offload_preview_chars: int = Field(
        default=200, ge=1, le=HyperparameterBounds.PREVIEW_CHARS_MAX
    )
    model_result_preview_bytes: int = Field(
        default=8_192, ge=1, le=HyperparameterBounds.RESULT_PREVIEW_BYTES_MAX
    )

    @property
    def summary_threshold_tokens(self) -> int:
        """Token count at which compaction triggers.

        Mirrors the budget evaluator's expression, floor included, so a
        section-level read and the runtime's own snapshot cannot disagree about
        where the threshold is.
        """

        return max(int(self.max_input_tokens * self.summary_threshold_ratio), 1)

    @property
    def recent_context_tokens(self) -> int:
        """Token count of the recent window compaction preserves verbatim."""

        return max(int(self.max_input_tokens * self.recent_context_ratio), 1)

    @model_validator(mode="after")
    def _recent_window_fits_under_the_threshold(self) -> Self:
        """Refuse a recent window at or above the compaction trigger.

        Compaction fires at ``summary_threshold_ratio`` and keeps
        ``recent_context_ratio`` verbatim. When the kept window is at least as
        large as the trigger, compacting cannot bring the context back under the
        threshold, so the summarizer re-fires on every subsequent turn.
        """

        if self.recent_context_ratio >= self.summary_threshold_ratio:
            raise ValueError(
                "recent_context_ratio must stay below summary_threshold_ratio; "
                "otherwise compaction can never drop back under its own trigger"
            )
        return self


class ModelMapperHyperparameters(HyperparameterSection):
    """Call shape of the small deterministic mapper model.

    Distinct from the main model config, which already threads ``temperature``
    and ``max_tokens`` from ``ModelConfig``; only the mapper's hard-coded pair
    lives here.
    """

    max_output_tokens: int = Field(
        default=1_024, ge=1, le=HyperparameterBounds.MODEL_TOKENS_MAX
    )
    temperature: float = Field(
        default=0.0, ge=0, le=HyperparameterBounds.TEMPERATURE_MAX
    )


class SearchHyperparameters(HyperparameterSection):
    """What ``web_search`` may spend to read the page instead of the snippet.

    This is the cost control the local-search PRD names in §5: with no ranking
    stage, "the window size IS the cost control". It is a tunable rather than a
    constant because the right answer was only knowable once the feature ran —
    measured across four live queries, extraction costs **3.6x** the snippet-only
    baseline (~735 -> ~2650 tokens over three searches) while staying inside the
    1200-token per-search budget. Whether that price is worth reading the page is
    a product judgement someone should be able to make with a diff, not a patch.

    ``content_token_budget`` bounds the whole model-visible payload for ONE
    search; the three ``*_chars`` values bound individual passages inside it.
    Lower the budget first: it is the bound that holds regardless of how the
    passages are chosen.
    """

    content_token_budget: int = Field(
        default=1_200, ge=100, le=HyperparameterBounds.SEARCH_CONTENT_TOKENS_MAX
    )
    #: Upper bound on one snippet-anchored window.
    window_chars: int = Field(
        default=1_200, ge=200, le=HyperparameterBounds.SEARCH_PASSAGE_CHARS_MAX
    )
    #: Upper bound on an article lead, used when the snippet is not locatable.
    lead_chars: int = Field(
        default=900, ge=200, le=HyperparameterBounds.SEARCH_PASSAGE_CHARS_MAX
    )
    #: Below this a window cannot reliably contain the answer, so the source
    #: keeps its engine snippet rather than spending tokens on a stub.
    min_window_chars: int = Field(
        default=200, ge=50, le=HyperparameterBounds.SEARCH_PASSAGE_CHARS_MAX
    )

    @model_validator(mode="after")
    def _window_must_fit_the_budget(self) -> "SearchHyperparameters":
        """A single window wider than the whole budget starves every other source.

        Four sources share ``content_token_budget``. A ``window_chars`` above it
        means the first source can consume the entire allowance and the rest fall
        back to snippets — technically within budget, and not what anyone setting
        a window size intends.
        """

        if self.window_chars > self.content_token_budget * _CHARS_PER_TOKEN:
            raise ValueError(
                "search.window_chars exceeds the whole content budget; one "
                "source would starve the others"
            )
        if self.min_window_chars > self.window_chars:
            raise ValueError("search.min_window_chars exceeds search.window_chars")
        return self


class ObservabilityHyperparameters(HyperparameterSection):
    """Bounds applied while projecting runtime events onto the stream."""

    max_stream_field_length: int = Field(
        default=2_000, ge=1, le=HyperparameterBounds.PREVIEW_CHARS_MAX
    )


class CitationHyperparameters(HyperparameterSection):
    """Caps that keep the citation registry bounded on high-volume connectors."""

    per_result_max: int = Field(
        default=25, ge=1, le=HyperparameterBounds.CITATIONS_PER_RESULT_MAX
    )


class Hyperparameters(_FrozenContract):
    """The whole document: every agent-behaviour tunable, loaded once.

    ``schema_version`` is a ``Literal``, not an int: a document written for a
    future shape must fail on this field with a legible pointer rather than be
    coerced into today's model and half-applied.
    """

    schema_version: Literal[1] = 1
    mcp_loading: McpLoadingHyperparameters = Field(
        default_factory=McpLoadingHyperparameters
    )
    mcp_catalog: McpCatalogHyperparameters = Field(
        default_factory=McpCatalogHyperparameters
    )
    reads: ReadHyperparameters = Field(default_factory=ReadHyperparameters)
    retry: RetryHyperparameters = Field(default_factory=RetryHyperparameters)
    model_retry: ModelRetryHyperparameters = Field(
        default_factory=ModelRetryHyperparameters
    )
    execution: ExecutionHyperparameters = Field(
        default_factory=ExecutionHyperparameters
    )
    subagents: SubagentHyperparameters = Field(default_factory=SubagentHyperparameters)
    context: ContextHyperparameters = Field(default_factory=ContextHyperparameters)
    model_mapper: ModelMapperHyperparameters = Field(
        default_factory=ModelMapperHyperparameters
    )
    observability: ObservabilityHyperparameters = Field(
        default_factory=ObservabilityHyperparameters
    )
    citations: CitationHyperparameters = Field(default_factory=CitationHyperparameters)
    search: SearchHyperparameters = Field(default_factory=SearchHyperparameters)


class HyperparameterOverride(_FrozenContract):
    """One environment override that was applied on top of the document.

    Not a section — it describes a *mutation* of the document rather than part
    of it. Values are rendered as strings because this record exists to be
    logged at boot and carried in the run's observability context: an eval run
    whose numbers were quietly overridden has to stay identifiable afterwards.
    """

    pointer: str = Field(min_length=1, max_length=200)
    previous: str | None = Field(default=None, max_length=200)
    applied: str = Field(max_length=200)
