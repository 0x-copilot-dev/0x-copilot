"""Golden fixture for the third-party (``deepagents``) context-origin adapter.

§4.3 of the Context Occupancy Ledger design allows exactly one module to track a
dependency's context contributions centrally, on the condition that a failing
test is attached to it. This is that test.

The pinned inventory below is the condition. A ``deepagents`` upgrade that adds,
removes, or rewords a module-level prompt or tool description moves one of these
numbers, and the assertion fails naming the constant — which is the moment a
reviewer decides whether the product just took on (or shed) resident context
cost. Updating the literal is the correct fix; it is supposed to be a conscious
act, not an automatic one.

The rest of the file guards the properties that make the fixture trustworthy:
discovery is deterministic, a layout change degrades to empty instead of raising,
and the harness reads resolve through the live profile registry rather than
restating what this runtime believes it registered.
"""

from __future__ import annotations

import importlib
from collections.abc import Iterator
from typing import Final

import pytest

from agent_runtime.execution.deep_agent_builder import WEB_SUBAGENT_CHECKPOINT_SUFFIX
from agent_runtime.execution.tool_surface import (
    DEEP_AGENT_PROFILE_EXCLUDED_TOOL_NAMES,
)
from agent_runtime.observability.context_origin import (
    ContextLifecycle,
    ContextOrigin,
    ContextSegmentClass,
)
from agent_runtime.observability.context_third_party import (
    ThirdPartyContextOriginRegistry,
    ThirdPartyContextOrigins,
    ThirdPartyPromptConstant,
)
from agent_runtime.prompts.assembly import PromptCacheEligibility


class PinnedDeepAgentsInventoryMixin:
    """The pinned ``deepagents`` inventory and the seams used to perturb it."""

    # Pinned against ``deepagents==0.6.12`` (see services/ai-backend/pyproject.toml).
    # Keys are ``module:ATTRIBUTE``; values are the char/4 ceiling estimate over
    # UTF-8 bytes, matching the reference measurements in §11 of the design doc.
    #
    # To update after a dependency bump: run the adapter's ``inventory()`` and
    # paste the result here, then state in the PR description what the net
    # change in resident third-party tokens is. A bump that moves the total by
    # hundreds of tokens is a product decision, not a lockfile detail.
    PINNED_INVENTORY: Final[dict[str, int]] = {
        "deepagents.backends.sandbox:_EDIT_COMMAND_TEMPLATE": 541,
        "deepagents.backends.sandbox:_EDIT_TMPFILE_TEMPLATE": 536,
        "deepagents.backends.sandbox:_GLOB_COMMAND_TEMPLATE": 205,
        "deepagents.backends.sandbox:_READ_COMMAND_TEMPLATE": 893,
        "deepagents.backends.sandbox:_WRITE_CHECK_TEMPLATE": 63,
        "deepagents.graph:BASE_AGENT_PROMPT": 569,
        "deepagents.middleware:DEEPAGENTS_DEFAULT_SUMMARY_PROMPT": 841,
        "deepagents.middleware:GRADER_SYSTEM_PROMPT": 277,
        "deepagents.middleware.async_subagents:ASYNC_TASK_SYSTEM_PROMPT": 626,
        "deepagents.middleware.async_subagents:ASYNC_TASK_TOOL_DESCRIPTION": 169,
        "deepagents.middleware.filesystem:EDIT_FILE_TOOL_DESCRIPTION": 109,
        "deepagents.middleware.filesystem:EXECUTE_TOOL_DESCRIPTION": 693,
        "deepagents.middleware.filesystem:EXECUTION_SYSTEM_PROMPT": 70,
        "deepagents.middleware.filesystem:FILESYSTEM_SYSTEM_PROMPT": 292,
        "deepagents.middleware.filesystem:GLOB_TOOL_DESCRIPTION": 93,
        "deepagents.middleware.filesystem:GREP_TOOL_DESCRIPTION": 130,
        "deepagents.middleware.filesystem:LIST_FILES_TOOL_DESCRIPTION": 52,
        "deepagents.middleware.filesystem:READ_FILE_TOOL_DESCRIPTION": 468,
        "deepagents.middleware.filesystem:READ_FILE_TRUNCATION_MSG": 82,
        "deepagents.middleware.filesystem:TOO_LARGE_HUMAN_MSG": 64,
        "deepagents.middleware.filesystem:TOO_LARGE_TOOL_MSG": 154,
        "deepagents.middleware.filesystem:_FILESYSTEM_SYSTEM_PROMPT_TEMPLATE": 296,
        "deepagents.middleware.memory:MEMORY_SYSTEM_PROMPT": 1281,
        "deepagents.middleware.skills:SKILLS_SYSTEM_PROMPT": 465,
        "deepagents.middleware.subagents:DEFAULT_GENERAL_PURPOSE_DESCRIPTION": 87,
        "deepagents.middleware.subagents:DEFAULT_SUBAGENT_PROMPT": 72,
        "deepagents.middleware.subagents:TASK_SYSTEM_PROMPT": 539,
        "deepagents.middleware.subagents:TASK_TOOL_DESCRIPTION": 1644,
        "deepagents.middleware.summarization:DEFAULT_SUMMARY_PROMPT": 681,
        "deepagents.middleware.summarization:SUMMARIZATION_SYSTEM_PROMPT": 104,
        "deepagents.middleware.summarization:_MEDIA_REFERENCE_SUMMARY_PROMPT": 159,
        "deepagents.profiles.harness._anthropic_haiku_4_5:_SYSTEM_PROMPT_SUFFIX": 364,
        "deepagents.profiles.harness._anthropic_opus_4_7:_SYSTEM_PROMPT_SUFFIX": 537,
        "deepagents.profiles.harness._anthropic_sonnet_4_6:_SYSTEM_PROMPT_SUFFIX": 364,
        "deepagents.profiles.harness._openai_codex:_SYSTEM_PROMPT_SUFFIX": 292,
    }

    PINNED_TOTAL_ESTIMATED_TOKENS: Final[int] = 13812

    # Paths that do not exist, standing in for "the dependency reorganised".
    # Pointing the adapter at them is a real constructor seam rather than a
    # monkeypatch of library internals, so the test exercises the production
    # degrade path instead of a stub of it.
    MOVED_PACKAGE: Final[str] = "deepagents.__moved_by_an_upgrade__"
    MOVED_HARNESS_MODULE: Final[str] = (
        "deepagents.profiles.harness.__moved_by_an_upgrade__"
    )

    # Registered under this runtime's provider-wide web profile.
    PROVIDER_PROFILE_KEY: Final[str] = "anthropic"
    # Shipped by the library as a per-model profile, and merged on top of the
    # provider registration above.
    MODEL_PROFILE_KEY: Final[str] = "anthropic:claude-opus-4-7"
    LIBRARY_OPUS_SUFFIX_MODULE: Final[str] = (
        "deepagents.profiles.harness._anthropic_opus_4_7"
    )
    UNREGISTERED_PROFILE_KEY: Final[str] = "no_such_provider"
    HARNESS_PROFILES_MODULE: Final[str] = "deepagents.profiles.harness.harness_profiles"
    DEEP_AGENT_BUILDER_MODULE: Final[str] = "agent_runtime.execution.deep_agent_builder"

    # Carries non-ASCII punctuation, so its UTF-8 length exceeds its character
    # length. Pins that ``byte_count`` means bytes rather than ``len(str)``.
    NON_ASCII_CONSTANT: Final[str] = (
        "deepagents.middleware.filesystem:FILESYSTEM_SYSTEM_PROMPT"
    )
    LARGEST_CONSTANT: Final[str] = (
        "deepagents.middleware.subagents:TASK_TOOL_DESCRIPTION"
    )
    # Present in the package and therefore in the inventory, but removed from
    # the web profile's tool surface.
    EXCLUDED_TOOL_CONSTANT: Final[str] = (
        "deepagents.middleware.filesystem:EXECUTE_TOOL_DESCRIPTION"
    )

    @pytest.fixture
    def registered_web_harness_profiles(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> Iterator[None]:
        """Register this runtime's web harness profiles, then restore the registry.

        The adapter deliberately never registers — registration is build-time
        state, and an observability read that fired it would perturb the exact
        topology it claims to observe — so a test that wants to see production
        topology has to arrange it explicitly.

        Restoring afterwards is not tidiness. ``register_harness_profile`` merges
        additively, so a leaked registration makes the *next* real registration
        in the session merge onto this one, which collapses the per-child
        ``extra_middleware`` factory into a fixed instance sequence and breaks
        an unrelated builder test three files away. The built-ins are loaded
        before the snapshot is taken so the restore can never drop them.
        """

        library = importlib.import_module(self.HARNESS_PROFILES_MODULE)
        builder = importlib.import_module(self.DEEP_AGENT_BUILDER_MODULE)
        library._ensure_harness_profiles_loaded()  # noqa: SLF001 — library internals
        profiles = library._HARNESS_PROFILES  # noqa: SLF001 — library internals
        snapshot = dict(profiles)

        monkeypatch.setattr(builder, "_web_harness_profiles_registered", False)
        builder._ensure_web_harness_profiles_registered()  # noqa: SLF001 — module guard
        yield

        for key in tuple(profiles):
            if key not in snapshot:
                del profiles[key]
        profiles.update(snapshot)

    def adapter(self, **overrides: object) -> ThirdPartyContextOrigins:
        """Build an adapter, defaulting to the real installed package."""

        return ThirdPartyContextOrigins(**overrides)  # type: ignore[arg-type]

    def live_harness_profiles(self) -> dict[str, object]:
        """Snapshot the library's harness registry for mutation assertions."""

        library = importlib.import_module(self.HARNESS_PROFILES_MODULE)
        library._ensure_harness_profiles_loaded()  # noqa: SLF001 — library internals
        return dict(library._HARNESS_PROFILES)  # noqa: SLF001 — library internals

    def constant_by_name(
        self,
        adapter: ThirdPartyContextOrigins,
        qualified_name: str,
    ) -> ThirdPartyPromptConstant:
        """Return the discovered constant with ``qualified_name``."""

        for constant in adapter.discover():
            if constant.qualified_name == qualified_name:
                return constant
        pytest.fail(f"{qualified_name} is not in the discovered inventory")

    def origin(
        self,
        owner: str,
        name: str,
        segment_class: ContextSegmentClass = ContextSegmentClass.SYSTEM,
    ) -> ContextOrigin:
        """Build a declaration for registry-level assertions."""

        return ContextOrigin(
            owner=owner,
            name=name,
            segment_class=segment_class,
            lifecycle=ContextLifecycle.RESIDENT,
            third_party=True,
        )


class TestPinnedDeepAgentsInventory(PinnedDeepAgentsInventoryMixin):
    """The §4.3 golden fixture: a dependency bump must fail here, by name."""

    def test_discovered_inventory_matches_the_pinned_fixture(self) -> None:
        assert dict(self.adapter().inventory()) == self.PINNED_INVENTORY

    def test_estimated_total_matches_the_pinned_fixture(self) -> None:
        assert self.adapter().estimated_total_tokens() == (
            self.PINNED_TOTAL_ESTIMATED_TOKENS
        )

    def test_largest_constant_is_the_task_tool_description(self) -> None:
        inventory = self.adapter().inventory()
        largest = max(inventory, key=lambda name: inventory[name])

        assert largest == self.LARGEST_CONSTANT

    def test_estimated_tokens_is_the_ceiling_of_bytes_over_four(self) -> None:
        for constant in self.adapter().discover():
            assert constant.estimated_tokens == -(-constant.byte_count // 4)

    def test_byte_count_is_utf8_length_not_character_length(self) -> None:
        adapter = self.adapter()
        constant = self.constant_by_name(adapter, self.NON_ASCII_CONSTANT)
        module = importlib.import_module(constant.module)
        value = getattr(module, constant.attribute)

        assert constant.byte_count == len(value.encode("utf-8"))
        assert constant.byte_count > len(value)

    def test_module_metadata_is_never_mistaken_for_a_constant(self) -> None:
        attributes = {constant.attribute for constant in self.adapter().discover()}

        assert not any(
            attribute.startswith("__") and attribute.endswith("__")
            for attribute in attributes
        )

    def test_reexported_constant_is_declared_once_under_its_public_module(
        self,
    ) -> None:
        # ``deepagents.middleware`` re-exports the summarization prompt. It is
        # one string object in the window, so it must be one inventory row —
        # under the shorter, public path, which survives a private-module
        # reorganisation.
        inventory = self.adapter().inventory()

        assert "deepagents.middleware:DEEPAGENTS_DEFAULT_SUMMARY_PROMPT" in inventory
        assert (
            "deepagents.middleware.summarization:DEEPAGENTS_DEFAULT_SUMMARY_PROMPT"
            not in inventory
        )

    def test_discovery_is_deterministic_across_independent_instances(self) -> None:
        # Two fresh adapters rather than two calls on one: the instance memoizes,
        # so a single instance could not detect real nondeterminism in the sweep.
        assert self.adapter().discover() == self.adapter().discover()

    def test_repeated_discovery_on_one_instance_is_stable(self) -> None:
        adapter = self.adapter()

        assert adapter.discover() == adapter.discover()

    def test_raising_the_byte_threshold_narrows_the_inventory(self) -> None:
        threshold = 5000
        narrowed = self.adapter(min_constant_bytes=threshold).discover()
        default = self.adapter().discover()

        assert narrowed
        assert set(narrowed) < set(default)
        assert all(constant.byte_count >= threshold for constant in narrowed)


class TestThirdPartyRegistryProjection(PinnedDeepAgentsInventoryMixin):
    """Every discovered constant becomes an owner-namespaced declaration."""

    def test_registry_declares_every_discovered_constant(self) -> None:
        adapter = self.adapter()

        assert len(adapter.registry()) == len(adapter.discover())

    def test_every_declaration_is_third_party_and_resident(self) -> None:
        for declared in self.adapter().registry():
            assert declared.third_party is True
            assert declared.lifecycle is ContextLifecycle.RESIDENT
            assert declared.cache_eligibility is PromptCacheEligibility.STABLE_PREFIX

    def test_labels_are_the_lowercased_owner_namespaced_constant(self) -> None:
        adapter = self.adapter()
        registry = adapter.registry()

        for constant in adapter.discover():
            label = f"{constant.module}:{constant.attribute.lower()}"
            declared = registry.get(label)
            assert declared is not None
            assert declared.owner == constant.module
            assert declared.name == constant.attribute.lower()

    def test_tool_description_constants_land_in_the_tools_segment(self) -> None:
        adapter = self.adapter()
        registry = adapter.registry()

        for constant in adapter.discover():
            declared = registry.get(f"{constant.module}:{constant.attribute.lower()}")
            assert declared is not None
            expected = (
                ContextSegmentClass.TOOLS
                if constant.attribute.endswith("_TOOL_DESCRIPTION")
                else ContextSegmentClass.SYSTEM
            )
            assert declared.segment_class is expected

    def test_task_tool_description_is_declared_as_a_tool_segment(self) -> None:
        declared = (
            self.adapter()
            .registry()
            .get("deepagents.middleware.subagents:task_tool_description")
        )

        assert declared is not None
        assert declared.segment_class is ContextSegmentClass.TOOLS
        assert declared.third_party is True

    def test_unknown_label_reads_as_undeclared_rather_than_raising(self) -> None:
        # Reconciliation's correct response to an unknown label is to count it
        # into ``undeclared_tokens`` (§4.4), so lookup must not raise.
        assert self.adapter().registry().get("deepagents.nowhere:missing") is None


class TestThirdPartyContextOriginRegistryContract(PinnedDeepAgentsInventoryMixin):
    """Ordering, uniqueness, and the deliberate divergence on emptiness."""

    def test_ordering_is_independent_of_construction_order(self) -> None:
        first = self.origin("deepagents.middleware.subagents", "task_system_prompt")
        second = self.origin("deepagents.middleware.memory", "memory_system_prompt")

        forward = ThirdPartyContextOriginRegistry((first, second))
        reverse = ThirdPartyContextOriginRegistry((second, first))

        assert forward.origins == reverse.origins
        assert forward.labels == (
            "deepagents.middleware.memory:memory_system_prompt",
            "deepagents.middleware.subagents:task_system_prompt",
        )

    def test_duplicate_labels_are_rejected_at_construction(self) -> None:
        declared = self.origin("deepagents.middleware.memory", "memory_system_prompt")

        with pytest.raises(ValueError, match="unique"):
            ThirdPartyContextOriginRegistry((declared, declared))

    def test_empty_registry_is_allowed_because_fail_open_needs_it(self) -> None:
        # The prompt-fragment registry this mirrors rejects empty. Here empty is
        # the §4.3 degrade outcome, and raising would move a fixture diff onto
        # the model-call path.
        registry = ThirdPartyContextOriginRegistry(())

        assert len(registry) == 0
        assert registry.labels == ()
        assert list(registry) == []


class TestThirdPartyDegradesInsteadOfRaising(PinnedDeepAgentsInventoryMixin):
    """A layout change is a fixture diff, never an exception on the model path."""

    def test_missing_package_discovers_nothing(self) -> None:
        assert self.adapter(root_package=self.MOVED_PACKAGE).discover() == ()

    def test_missing_package_yields_an_empty_registry(self) -> None:
        registry = self.adapter(root_package=self.MOVED_PACKAGE).registry()

        assert len(registry) == 0

    def test_missing_package_yields_an_empty_inventory(self) -> None:
        adapter = self.adapter(root_package=self.MOVED_PACKAGE)

        assert dict(adapter.inventory()) == {}
        assert adapter.estimated_total_tokens() == 0

    def test_moved_harness_module_yields_no_suffix(self) -> None:
        adapter = self.adapter(harness_profiles_module=self.MOVED_HARNESS_MODULE)

        assert adapter.active_harness_suffix(self.PROVIDER_PROFILE_KEY) is None

    def test_moved_harness_module_falls_back_to_declared_exclusions(self) -> None:
        # Less-wrong failure: our registration is what creates the exclusion, so
        # assuming it held beats assuming the excluded tools came back and
        # over-reporting the tool block by the ``execute`` description.
        adapter = self.adapter(harness_profiles_module=self.MOVED_HARNESS_MODULE)

        assert adapter.excluded_tool_names() == DEEP_AGENT_PROFILE_EXCLUDED_TOOL_NAMES


class TestLiveHarnessProfileResolution(PinnedDeepAgentsInventoryMixin):
    """Suffix and exclusions are resolved, not assumed (§4.3)."""

    @pytest.mark.usefixtures("registered_web_harness_profiles")
    def test_provider_key_resolves_this_runtimes_web_suffix(self) -> None:
        adapter = self.adapter()

        assert adapter.active_harness_suffix(self.PROVIDER_PROFILE_KEY) == (
            WEB_SUBAGENT_CHECKPOINT_SUFFIX
        )

    @pytest.mark.usefixtures("registered_web_harness_profiles")
    def test_model_spec_prefers_the_librarys_own_suffix(self) -> None:
        # The merge semantics let a model-level suffix REPLACE the provider-level
        # one, so on a spec deepagents ships a profile for, our checkpoint suffix
        # is not what occupies the window. An adapter that assumed our constant
        # would mis-report ~800 tokens on the busiest models in the fleet.
        library = importlib.import_module(self.LIBRARY_OPUS_SUFFIX_MODULE)
        expected = library._SYSTEM_PROMPT_SUFFIX  # noqa: SLF001 — pinning library text

        resolved = self.adapter().active_harness_suffix(self.MODEL_PROFILE_KEY)

        assert resolved == expected
        assert resolved != WEB_SUBAGENT_CHECKPOINT_SUFFIX

    @pytest.mark.usefixtures("registered_web_harness_profiles")
    def test_unregistered_provider_has_no_attributable_suffix(self) -> None:
        assert (
            self.adapter().active_harness_suffix(self.UNREGISTERED_PROFILE_KEY) is None
        )

    @pytest.mark.usefixtures("registered_web_harness_profiles")
    def test_malformed_profile_key_has_no_attributable_suffix(self) -> None:
        # ``"anthropic:"`` must not fall through to the provider-wide profile,
        # or a malformed spec would silently report the wrong suffix.
        assert self.adapter().active_harness_suffix("anthropic:") is None

    @pytest.mark.usefixtures("registered_web_harness_profiles")
    def test_excluded_tool_names_resolve_through_the_live_profile(self) -> None:
        assert (
            self.adapter().excluded_tool_names()
            == DEEP_AGENT_PROFILE_EXCLUDED_TOOL_NAMES
        )

    @pytest.mark.usefixtures("registered_web_harness_profiles")
    def test_excluded_tool_descriptions_stay_in_the_inventory(self) -> None:
        # The package still ships ``EXECUTE_TOOL_DESCRIPTION``; the web profile
        # simply never puts it on the wire. Keeping it discovered while reporting
        # the exclusion separately is what lets a consumer produce a
        # topology-correct number for web and desktop from one inventory (§2.1).
        adapter = self.adapter()

        assert self.EXCLUDED_TOOL_CONSTANT in adapter.inventory()
        assert "execute" in adapter.excluded_tool_names()

    def test_reads_never_mutate_the_libraries_profile_registry(self) -> None:
        # The regression guard for a hazard this module hit during development:
        # an adapter that forced harness registration changed global state that a
        # later real registration then merged onto itself, collapsing the
        # per-child middleware factory and failing an unrelated builder test.
        before = self.live_harness_profiles()
        adapter = self.adapter()

        adapter.active_harness_suffix(self.PROVIDER_PROFILE_KEY)
        adapter.active_harness_suffix(self.MODEL_PROFILE_KEY)
        adapter.excluded_tool_names()
        adapter.registry()

        assert self.live_harness_profiles() == before
