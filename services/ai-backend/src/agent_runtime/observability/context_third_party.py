"""The one place central context tracking survives: the ``deepagents`` adapter.

§3.2 of the Context Occupancy Ledger design says contributors declare what they
put in front of the model, and §4.2 makes that real with a CI gate. Both assume
the contributor is ours. ``deepagents`` is not ours and will never implement our
protocol, so §4.3 grants exactly one exception: **one module declares on the
library's behalf, and a golden fixture is attached to it.** The fixture is what
separates a shim from a liability — a dependency bump that adds prompt text
fails CI with the constant named, instead of quietly adding a thousand resident
tokens to every model call in the product.

Three things this module deliberately does *not* do.

**It does not trust its own inventory as the measurement.** §3.1 measures the
materialized ``ModelRequest`` because that is the only place library and
middleware contributions have all landed. What this module produces is the
*declaration* half — labels and lifecycles for text we did not write — so a
measured byte range can be attributed to ``deepagents.middleware.subagents``
rather than falling into ``undeclared_tokens``. An entry declared here that
never reaches the wire simply contributes no segment.

**It does not assume which constants are installed.** The same conversation has
materially different occupancy on web versus desktop because
``DEEP_AGENT_PROFILE_EXCLUDED_TOOL_NAMES`` removes ``edit_file`` / ``execute`` /
``write_file`` from the web profile, and because the per-model harness suffix
(audit item D) differs per provider. :meth:`ThirdPartyContextOrigins.excluded_tool_names`
and :meth:`ThirdPartyContextOrigins.active_harness_suffix` therefore resolve
through the **live** ``HarnessProfile`` registry rather than restating what we
believe we registered. That distinction has already earned its keep: on a
``provider:model`` spec that ``deepagents`` ships a built-in profile for (e.g.
``anthropic:claude-opus-4-7``), the library's model-level ``system_prompt_suffix``
*replaces* our provider-level ``WEB_SUBAGENT_CHECKPOINT_SUFFIX`` rather than
layering on top of it. An adapter that assumed our suffix was installed would
have reported the wrong 800 tokens on exactly the models we run most.

**It never raises.** Occupancy is best-effort observability on the model-call
path (§6.4). Every reflective read here is defensive: a package that will not
import, a private resolver that moved, a constant that vanished — all degrade to
an empty or partial result. A missing constant is a fixture diff, which is a
loud, reviewable, human-scale failure. A raised exception on the model-call path
is an outage caused by an observability feature, which is the thing this design
forbids most explicitly.

Attribution is deliberately coarse: an attribute ending in ``_TOOL_DESCRIPTION``
is tool-block text, everything else is treated as system text. Refining that
would mean modelling a dependency's internals, which is the brittleness §2.1
rejects. The adapter's job is to be a pinned change-detector with honest labels,
not a semantic classifier of somebody else's package.
"""

from __future__ import annotations

import importlib
import logging
import pkgutil
from collections.abc import Callable, Iterator, Mapping
from types import MappingProxyType, ModuleType
from typing import Annotated, ClassVar, Final, cast

from pydantic import Field, NonNegativeInt, PositiveInt

from agent_runtime.execution.contracts import RuntimeContract
from agent_runtime.execution.tool_surface import (
    DEEP_AGENT_PROFILE_EXCLUDED_TOOL_NAMES,
)
from agent_runtime.observability.context_origin import (
    ContextLifecycle,
    ContextOrigin,
    ContextSegmentClass,
)
from agent_runtime.prompts.assembly import PromptCacheEligibility


_LOGGER = logging.getLogger(__name__)


class ThirdPartyPromptConstant(RuntimeContract):
    """One module-level string constant discovered in a third-party package.

    This is the fixture's unit. ``module`` and ``attribute`` together name the
    thing precisely enough that a failing golden assertion tells a reviewer
    which constant moved without them having to go read the dependency, which is
    the entire point of pinning the inventory (§4.3).

    ``byte_count`` is UTF-8 bytes rather than ``len(str)``: the prompts carry
    non-ASCII punctuation, and the wire cost follows the encoded form.
    ``estimated_tokens`` is the repository's char/4 heuristic applied to that
    byte count, **not** a tokenizer result. That is a deliberate choice for a
    golden fixture — a tokenizer count would move when ``litellm`` bumps or when
    the routed model changes, and a fixture that fails for reasons unrelated to
    the thing it guards is a fixture people delete. Real occupancy numbers come
    from measuring the materialized request through
    :class:`~agent_runtime.observability.context_token_counter.ContextTokenCounter`
    (§3.1); this number exists so the inventory is comparable across bumps.
    """

    # Dotted module path. Constrained loosely rather than to a strict identifier
    # pattern so a dependency reorganisation cannot make *discovery* raise; the
    # strict shape is enforced where it matters, at ``ContextOrigin`` owner
    # validation in :meth:`to_origin`, which is guarded by its caller.
    module: Annotated[str, Field(min_length=1, max_length=300)]
    attribute: Annotated[str, Field(min_length=1, max_length=200, pattern=r"^[^\s:]+$")]
    byte_count: NonNegativeInt
    estimated_tokens: NonNegativeInt

    # ``ClassVar`` so Pydantic treats this as a constant on the contract rather
    # than as a field with a default that every construction site could override.
    TOOL_DESCRIPTION_SUFFIX: ClassVar[str] = "_TOOL_DESCRIPTION"

    @property
    def qualified_name(self) -> str:
        """``module:attribute`` — the key the golden fixture is pinned on.

        Shares the ``owner:name`` shape of :attr:`ContextOrigin.label` on
        purpose: a fixture row and the label that ends up on a persisted segment
        differ only in the attribute's case, so a reader who sees
        ``deepagents.middleware.subagents:task_tool_description`` in an
        occupancy report can find the pinned row by eye.
        """

        return f"{self.module}:{self.attribute}"

    @property
    def segment_class(self) -> ContextSegmentClass:
        """Which half of the provider request these bytes land in.

        ``deepagents`` names every tool-block constant ``*_TOOL_DESCRIPTION``
        and everything else is prompt text that joins the system block. The rule
        is one string comparison because a richer inference would have to model
        the library's internals — precisely the coupling §2.1 rules out. A
        constant misfiled by this rule still measures correctly; it is only
        grouped under the wrong ``segment_class`` in the report, and the fixture
        makes any such row visible at review time.
        """

        if self.attribute.endswith(self.TOOL_DESCRIPTION_SUFFIX):
            return ContextSegmentClass.TOOLS
        return ContextSegmentClass.SYSTEM

    def to_origin(self) -> ContextOrigin:
        """Declare this constant as a context origin owned by its defining module.

        ``owner`` is the dotted ``deepagents`` module so ownership stays
        intrinsic to the label and a reader can tell at a glance that the bytes
        are not fixable by editing this repository — they are fixed by a profile
        exclusion or a dependency change, which is exactly what ``third_party``
        exists to communicate (§4.1).

        Lifecycle is ``RESIDENT`` because these are module-level literals baked
        into the agent's prompt and tool surface at build time: they are re-sent
        on every model call until the surface itself changes. Cache eligibility
        is ``STABLE_PREFIX`` for the same reason — the bytes are byte-identical
        across every call for a pinned library version and profile, so a report
        that omitted it would recommend trimming the one part of the window that
        is already billed at a tenth (§6.6).

        Raises:
            pydantic.ValidationError: If ``module`` is not a dotted identifier.
                Callers on the observability path must catch this; see
                :meth:`ThirdPartyContextOrigins.registry`.
        """

        return ContextOrigin(
            owner=self.module,
            name=self.attribute.lower(),
            segment_class=self.segment_class,
            lifecycle=ContextLifecycle.RESIDENT,
            cache_eligibility=PromptCacheEligibility.STABLE_PREFIX,
            third_party=True,
        )


class ThirdPartyContextOriginRegistry:
    """Deterministic, duplicate-rejecting collection of third-party declarations.

    Mirrors :class:`~agent_runtime.prompts.sources.PromptFragmentProviderRegistry`
    in the two properties that make that pattern trustworthy: ordering is a pure
    function of the origins themselves rather than of registration order, and a
    duplicate label is a construction-time error rather than a silent overwrite.

    It diverges on exactly one point, and the divergence is load-bearing. The
    prompt registry rejects an empty collection because an agent with no system
    prompt is a bug. An empty *third-party* registry is not a bug — it is the
    §4.3 fail-open outcome when the dependency's layout has moved out from under
    the sweep. Rejecting empty here would convert a fixture diff into an
    exception on the model-call path, which §6.4 forbids. The emptiness is still
    caught loudly, just by the golden fixture instead of by a constructor.
    """

    def __init__(self, origins: tuple[ContextOrigin, ...]) -> None:
        ordered = tuple(sorted(origins, key=lambda origin: (origin.owner, origin.name)))
        labels = tuple(origin.label for origin in ordered)
        if len(labels) != len(set(labels)):
            msg = "third-party context origin labels must be unique"
            raise ValueError(msg)
        self._origins = ordered
        self._by_label: Mapping[str, ContextOrigin] = MappingProxyType(
            {origin.label: origin for origin in ordered}
        )

    @property
    def origins(self) -> tuple[ContextOrigin, ...]:
        """Declarations in deterministic ``(owner, name)`` order."""

        return self._origins

    @property
    def labels(self) -> tuple[str, ...]:
        """Every declared ``owner:name`` label, in the same deterministic order."""

        return tuple(self._by_label)

    def get(self, label: str) -> ContextOrigin | None:
        """Return the declaration for ``label``, or ``None`` when undeclared.

        Returns ``None`` rather than raising because the caller is the
        reconciliation step (§4.4), whose correct response to an unrecognised
        label is to count it into ``undeclared_tokens`` — not to fail.
        """

        return self._by_label.get(label)

    def __len__(self) -> int:
        return len(self._origins)

    def __iter__(self) -> Iterator[ContextOrigin]:
        return iter(self._origins)


class ThirdPartyContextOrigins:
    """Declare ``deepagents``' resident prompt and tool text on its behalf (§4.3).

    Four reads, all defensive, all deterministic:

    - :meth:`discover` sweeps the installed package for module-level string
      constants large enough to matter and returns a sorted inventory.
    - :meth:`registry` projects that inventory into :class:`ContextOrigin`
      declarations the ledger can attribute measured bytes to.
    - :meth:`active_harness_suffix` resolves the system-prompt suffix a given
      provider key or ``provider:model`` spec actually receives.
    - :meth:`excluded_tool_names` reports which built-in tools the live profile
      removes, so the tool block is measured against the real topology.

    Every third-party symbol is reached through ``importlib`` + ``getattr`` and
    every failure degrades to an empty or partial answer. The private resolver
    this module reads (``_get_harness_profile``) has no public equivalent in
    ``deepagents`` 0.6.x; reading it reflectively means a rename produces a
    ``None`` suffix and a failing fixture rather than an ``ImportError`` at
    module load, which would take down the whole service for an observability
    concern.

    Every read here is **non-mutating**. In particular this adapter does not
    trigger harness registration: that is ``build_deep_agent``'s job, it is
    guarded by a module-level flag because the library merges registrations
    additively, and an observability read that fired it would perturb the exact
    topology it is measuring. The consequence is that the harness reads describe
    what is registered *now* — before any agent has been built, they report no
    suffix and the declared exclusion fallback.

    The constructor takes the package and module paths as parameters so tests
    can simulate a layout change by pointing the adapter at a path that does not
    exist. That is a real seam, not a testing convenience: it is the only honest
    way to assert the degrade-to-empty behaviour without monkeypatching a
    dependency's internals, which would test the monkeypatch rather than the
    adapter.
    """

    class Keys:
        """Every third-party symbol name this module resolves by string.

        Collected in one place rather than inlined at each ``getattr`` because
        together they are the complete list of assumptions this adapter makes
        about a package it does not own. A dependency bump that renames any of
        them is then a one-line change with a failing test pointing at it,
        instead of a string buried three methods deep.
        """

        ROOT_PACKAGE: Final[str] = "deepagents"
        HARNESS_PROFILES_MODULE: Final[str] = (
            "deepagents.profiles.harness.harness_profiles"
        )
        HARNESS_PROFILE_RESOLVER: Final[str] = "_get_harness_profile"
        SYSTEM_PROMPT_SUFFIX: Final[str] = "system_prompt_suffix"
        EXCLUDED_TOOLS: Final[str] = "excluded_tools"
        DEEP_AGENT_BUILDER_MODULE: Final[str] = (
            "agent_runtime.execution.deep_agent_builder"
        )
        HARNESS_PROFILE_KEYS: Final[str] = "_WEB_HARNESS_PROFILE_KEYS"

    # 200 bytes ~= 50 estimated tokens. Chosen to sit just under the smallest
    # genuine tool description in the pinned version (``LIST_FILES_TOOL_DESCRIPTION``
    # at 206 bytes) so no real contributor is filtered out, while excluding the
    # package's short operational strings, whose churn would make the fixture
    # noisy without telling anyone anything about occupancy.
    DEFAULT_MIN_CONSTANT_BYTES: Final[int] = 200

    # The repository-wide estimation heuristic (``TokenBudgetEvaluator``), used
    # here so the pinned numbers line up with the reference measurements in the
    # design document's §11 rather than forming a second, silently different
    # scale.
    CHARS_PER_TOKEN: Final[int] = 4

    def __init__(
        self,
        *,
        root_package: str = Keys.ROOT_PACKAGE,
        harness_profiles_module: str = Keys.HARNESS_PROFILES_MODULE,
        min_constant_bytes: PositiveInt = DEFAULT_MIN_CONSTANT_BYTES,
    ) -> None:
        if min_constant_bytes <= 0:
            msg = "min_constant_bytes must be positive"
            raise ValueError(msg)
        self._root_package = root_package
        self._harness_profiles_module = harness_profiles_module
        self._min_constant_bytes = int(min_constant_bytes)
        # Memoized because the sweep imports every submodule of the dependency
        # and calls ``dir()`` on each. Callers build the registry once per
        # process; nothing here belongs on the per-model-call path. A test that
        # wants to prove determinism uses two independent instances rather than
        # two calls on one, so the cache cannot make the assertion vacuous.
        self._discovered: tuple[ThirdPartyPromptConstant, ...] | None = None

    def discover(self) -> tuple[ThirdPartyPromptConstant, ...]:
        """Return the installed package's large module-level string constants.

        Sorted by ``(module, attribute)`` so the result is a stable fixture
        rather than a reflection of import order, which ``pkgutil`` does not
        guarantee across platforms.

        Both public and private (leading-underscore) names are swept. That is
        not an oversight: the per-model harness suffixes — audit item D, and the
        single largest source of cross-model occupancy variance — are named
        ``_SYSTEM_PROMPT_SUFFIX``. Excluding private names would have hidden
        exactly the constants the fixture exists to watch.

        Returns an empty tuple, never an exception, when the package cannot be
        imported or the sweep fails part-way. Partial results are deliberately
        discarded on failure: a half-swept inventory would produce a fixture
        diff that reads like "deepagents deleted twelve prompts", while an empty
        one reads unambiguously as "the sweep broke".
        """

        if self._discovered is None:
            self._discovered = self._sweep()
        return self._discovered

    def inventory(self) -> Mapping[str, int]:
        """``module:attribute -> estimated_tokens``, the shape the fixture pins.

        A mapping rather than the full constant tuple because that is what a
        reviewer reads in a diff: a bump that rewords a prompt shows up as one
        changed number next to a named constant, and a bump that adds or removes
        one shows up as an added or removed line.
        """

        return MappingProxyType(
            {
                constant.qualified_name: constant.estimated_tokens
                for constant in self.discover()
            }
        )

    def estimated_total_tokens(self) -> int:
        """Sum of the inventory — the package's total declarable resident text.

        An upper bound on third-party occupancy, not a measurement of it: the
        installed subset varies by harness profile (see
        :meth:`excluded_tool_names`) and by which middleware a build actually
        mounts. The measured number comes from the materialized request (§3.1).
        """

        return sum(constant.estimated_tokens for constant in self.discover())

    def registry(self) -> ThirdPartyContextOriginRegistry:
        """Project the discovered inventory into declared context origins.

        A constant whose module path is not a valid dotted identifier is skipped
        rather than allowed to fail the projection, and a duplicate-label
        collision collapses the whole registry to empty rather than propagating
        the ``ValueError``. Both are the §4.3 fail-open posture: the ledger
        would rather attribute less than it could than raise into a model call,
        and both conditions are caught loudly by the golden fixture.
        """

        origins: list[ContextOrigin] = []
        for constant in self.discover():
            try:
                origins.append(constant.to_origin())
            except Exception:  # noqa: BLE001 — a declaration is never load-bearing
                _LOGGER.debug(
                    "Skipping third-party context origin for %s; "
                    "it does not form a valid declaration.",
                    constant.qualified_name,
                )
        try:
            return ThirdPartyContextOriginRegistry(tuple(origins))
        except ValueError:
            _LOGGER.warning(
                "Third-party context origins collided on label; declaring none for %s.",
                self._root_package,
            )
            return ThirdPartyContextOriginRegistry(())

    def active_harness_suffix(self, profile_key: str) -> str | None:
        """Return the system-prompt suffix installed for ``profile_key``, if any.

        ``profile_key`` accepts both shapes the harness registry understands: a
        bare provider key (``"anthropic"``) for the provider-wide registration
        this runtime makes, and a ``provider:model`` spec
        (``"anthropic:claude-opus-4-7"``) for the per-model profiles the library
        ships. Both are resolved through the library's own lookup, which merges
        provider and model registrations, so the answer is what the model will
        actually be sent rather than what either side registered in isolation.

        This is the reason §4.3 insists on resolving rather than assuming. Merge
        semantics let a model-level suffix *replace* a provider-level one, so on
        a spec ``deepagents`` ships a built-in profile for, this runtime's
        ``WEB_SUBAGENT_CHECKPOINT_SUFFIX`` is not what occupies the window — the
        library's suffix is. An adapter that hard-coded our constant would
        report a number that is wrong on the busiest models in the fleet.

        This read **never registers**. Harness registration is a build-time
        concern owned by ``build_deep_agent``, and an observability read that
        mutated the library's global profile registry would be changing the
        thing it claims to be observing. That is not hypothetical: the builder's
        registration is guarded by a module-level flag precisely because
        ``register_harness_profile`` merges additively, and a second
        registration collapses the per-child ``extra_middleware`` *factory* into
        a fixed instance sequence. Measurement happens inside a graph that has
        already been built (§3.1), so there is nothing to force.

        Returns ``None`` when no profile matches the key, when the profile
        carries no suffix, when the resolver has moved, or when this is called
        before any agent has been built. All four are the same answer to the
        caller — "no suffix is attributable here" — and none of them is worth an
        exception.
        """

        resolver = self._harness_profile_resolver()
        if resolver is None:
            return None
        profile = self._resolve_profile(resolver, profile_key)
        if profile is None:
            return None
        suffix = getattr(profile, self.Keys.SYSTEM_PROMPT_SUFFIX, None)
        if not isinstance(suffix, str) or not suffix:
            return None
        return suffix

    def excluded_tool_names(self) -> frozenset[str]:
        """Return the built-in tools the live web harness profile removes.

        The tool block is the largest single third-party contributor, and
        ``DEEP_AGENT_PROFILE_EXCLUDED_TOOL_NAMES`` means web and desktop have
        materially different occupancy for the same conversation (§2.1). A
        consumer that counted every discovered ``*_TOOL_DESCRIPTION`` would
        over-report the web profile by the ``execute`` description alone — 693
        estimated tokens of text the model never sees.

        Resolved by unioning the ``excluded_tools`` of every profile key this
        runtime registers, so an exclusion added by the library (or by a future
        registration on our side) is picked up without editing this module.

        When no profile resolves — the resolver moved, or this was called before
        any agent was built — falls back to the exclusion set this runtime
        *declares*. That is the less-wrong failure: our registration is what
        creates the exclusion in the first place, so assuming it held is a
        better estimate than assuming the tools came back and over-reporting the
        tool block by the 693-token ``execute`` description. Like
        :meth:`active_harness_suffix`, this read never registers anything.
        """

        resolver = self._harness_profile_resolver()
        if resolver is None:
            return DEEP_AGENT_PROFILE_EXCLUDED_TOOL_NAMES

        resolved: set[str] = set()
        matched = False
        for profile_key in self._harness_profile_keys():
            profile = self._resolve_profile(resolver, profile_key)
            if profile is None:
                continue
            excluded = getattr(profile, self.Keys.EXCLUDED_TOOLS, None)
            if not isinstance(excluded, (frozenset, set, tuple, list)):
                continue
            matched = True
            resolved.update(str(name) for name in excluded)
        if not matched:
            return DEEP_AGENT_PROFILE_EXCLUDED_TOOL_NAMES
        return frozenset(resolved)

    def _sweep(self) -> tuple[ThirdPartyPromptConstant, ...]:
        """Walk the installed package and collect its large string constants."""

        root = self._import_module(self._root_package)
        if root is None:
            _LOGGER.warning(
                "Third-party context origins unavailable: %s did not import.",
                self._root_package,
            )
            return ()

        # Keyed by object identity so a constant re-exported from a package
        # ``__init__`` is counted once. The value holds a reference to the
        # string itself, which keeps it alive for the duration of the sweep and
        # makes the identity key safe from address reuse.
        candidates: dict[int, tuple[str, str, str]] = {}
        try:
            for module_name, module in self._iter_modules(root):
                self._collect_constants(module_name, module, candidates)
        except Exception:  # noqa: BLE001 — a broken sweep is a fixture diff
            _LOGGER.warning(
                "Third-party context origin sweep of %s failed; declaring none.",
                self._root_package,
                exc_info=True,
            )
            return ()

        constants: list[ThirdPartyPromptConstant] = []
        for module_name, attribute, value in candidates.values():
            byte_count = len(value.encode("utf-8"))
            constants.append(
                ThirdPartyPromptConstant(
                    module=module_name,
                    attribute=attribute,
                    byte_count=byte_count,
                    estimated_tokens=self._estimate_tokens(byte_count),
                )
            )
        constants.sort(key=lambda constant: (constant.module, constant.attribute))
        return tuple(constants)

    def _iter_modules(self, root: ModuleType) -> Iterator[tuple[str, ModuleType]]:
        """Yield the root package and every submodule that imports cleanly."""

        yield self._root_package, root
        search_paths = getattr(root, "__path__", None)
        if search_paths is None:
            return
        walked = pkgutil.walk_packages(
            search_paths,
            prefix=f"{self._root_package}.",
            onerror=self._ignore_walk_error,
        )
        for module_info in walked:
            module = self._import_module(module_info.name)
            if module is not None:
                yield module_info.name, module

    def _collect_constants(
        self,
        module_name: str,
        module: ModuleType,
        candidates: dict[int, tuple[str, str, str]],
    ) -> None:
        """Record ``module``'s qualifying constants into ``candidates``.

        When the same string object is reachable under several module paths —
        a package ``__init__`` re-exporting a submodule's prompt — the shortest
        path wins, tie-broken lexicographically. Shortest is the public,
        documented name, and it is the more stable of the two: a dependency that
        reorganises its private modules does not move a public re-export, so the
        fixture keeps tracking prompt *text* rather than package structure.
        """

        for attribute in dir(module):
            if not self._is_constant_name(attribute):
                continue
            try:
                value = getattr(module, attribute)
            except Exception:  # noqa: BLE001 — a property that raises is not a constant
                continue
            if not isinstance(value, str):
                continue
            if len(value.encode("utf-8")) < self._min_constant_bytes:
                continue
            candidate = (module_name, attribute, value)
            existing = candidates.get(id(value))
            if existing is None or self._sort_key(candidate) < self._sort_key(existing):
                candidates[id(value)] = candidate

    def _harness_profile_resolver(self) -> Callable[[str], object] | None:
        """Return the library's profile-lookup callable, or ``None`` if it moved."""

        module = self._import_module(self._harness_profiles_module)
        if module is None:
            return None
        resolver = getattr(module, self.Keys.HARNESS_PROFILE_RESOLVER, None)
        if not callable(resolver):
            _LOGGER.warning(
                "Harness profile resolver %s.%s is unavailable; "
                "third-party suffix attribution is degraded.",
                self._harness_profiles_module,
                self.Keys.HARNESS_PROFILE_RESOLVER,
            )
            return None
        return cast("Callable[[str], object]", resolver)

    def _harness_profile_keys(self) -> tuple[str, ...]:
        """Return the provider keys this runtime registers a web profile under."""

        builder = self._import_module(self.Keys.DEEP_AGENT_BUILDER_MODULE)
        if builder is None:
            return ()
        keys = getattr(builder, self.Keys.HARNESS_PROFILE_KEYS, ())
        if not isinstance(keys, (tuple, list)):
            return ()
        return tuple(str(key) for key in keys)

    @staticmethod
    def _resolve_profile(
        resolver: Callable[[str], object],
        profile_key: str,
    ) -> object | None:
        """Call ``resolver`` for ``profile_key``, absorbing any failure as ``None``."""

        try:
            return resolver(profile_key)
        except Exception:  # noqa: BLE001 — a lookup failure is not a run failure
            _LOGGER.debug(
                "Harness profile lookup failed for %r.", profile_key, exc_info=True
            )
            return None

    @staticmethod
    def _import_module(name: str) -> ModuleType | None:
        """Import ``name`` defensively, returning ``None`` on any failure.

        Broad by design. A dependency's submodule can fail to import for reasons
        that have nothing to do with us — an optional extra that is not
        installed, a deprecation shim that raises on touch — and none of them
        should cost the caller anything beyond a missing inventory row.
        """

        try:
            return importlib.import_module(name)
        except Exception:  # noqa: BLE001 — an unimportable module is simply absent
            return None

    @staticmethod
    def _ignore_walk_error(name: str) -> None:
        """Swallow a package-traversal import error and keep walking."""

        _LOGGER.debug("Skipping unimportable package during context sweep: %s", name)

    @staticmethod
    def _is_constant_name(attribute: str) -> bool:
        """True for ``UPPER_SNAKE`` names, with or without a private prefix.

        The leading-underscore strip is what admits ``_SYSTEM_PROMPT_SUFFIX``.
        Dunders (``__doc__``, ``__file__``) fail the ``isupper`` check on their
        trailing underscores' neighbouring lowercase, so module metadata — which
        is long enough to clear the byte threshold — never enters the inventory.
        """

        core = attribute.lstrip("_")
        return bool(core) and core.isupper()

    @staticmethod
    def _sort_key(candidate: tuple[str, str, str]) -> tuple[int, str, str]:
        """Rank a duplicate candidate: shortest module path, then lexicographic."""

        module_name, attribute, _value = candidate
        return len(module_name), module_name, attribute

    @classmethod
    def _estimate_tokens(cls, byte_count: int) -> int:
        """Ceiling char/4 estimate, matching the design document's §11 numbers."""

        return (byte_count + cls.CHARS_PER_TOKEN - 1) // cls.CHARS_PER_TOKEN


__all__ = (
    "ThirdPartyContextOriginRegistry",
    "ThirdPartyContextOrigins",
    "ThirdPartyPromptConstant",
)
