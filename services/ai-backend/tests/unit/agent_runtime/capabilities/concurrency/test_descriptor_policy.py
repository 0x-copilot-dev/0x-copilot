from __future__ import annotations

from collections.abc import Mapping, Sequence

import pytest

from agent_runtime.capabilities.concurrency import (
    BatchOperation,
    BatchPlanner,
    BatchSegmentMode,
    BatchSegmentReason,
    CapabilityConcurrencyDeclaration,
    ConcurrencyDeclarationRejected,
    ConcurrencyDescriptorParser,
    ConcurrencyMode,
    ConcurrencyPolicy,
    ConcurrencyPolicyField,
    ConcurrencyPolicyResolution,
    ConcurrencyPolicyResolver,
    ConcurrencyPolicyWideningRejected,
    ConcurrencyRejectionReason,
    IdempotencyKind,
    NarrowableEnum,
    OperationBatch,
    OrderingRequirement,
    PolicySource,
    ProviderSessionConstraint,
    RateLimitScope,
    ResourceKeyDimension,
    ResourceKeyRenderRejected,
    ResourceKeyTemplate,
    ResourceKeyTemplateRejected,
    SideEffectKind,
)


class ConcurrencyVocabularyMixin:
    """Closed vocabularies whose conservative floor is the structural default."""

    NARROWABLE_VOCABULARIES: tuple[type[NarrowableEnum], ...] = (
        ConcurrencyMode,
        SideEffectKind,
        IdempotencyKind,
        RateLimitScope,
        OrderingRequirement,
        ProviderSessionConstraint,
        PolicySource,
    )


class DescriptorFixtureMixin:
    """Payload builders and resolver helpers shared by the concrete tests."""

    CAPABILITY_REF = "cap_0123456789abcdef0123456789abcdef"
    OTHER_CAPABILITY_REF = "cap_fedcba9876543210fedcba9876543210"
    SECRET = b"unit-test-resource-key-secret-32b"

    PARALLEL_READ_PAYLOAD: Mapping[str, object] = {
        ConcurrencyPolicyField.MODE.value: ConcurrencyMode.PARALLEL_SAFE.value,
        ConcurrencyPolicyField.SIDE_EFFECT.value: SideEffectKind.READ.value,
        ConcurrencyPolicyField.IDEMPOTENCY.value: IdempotencyKind.NATURAL.value,
        ConcurrencyPolicyField.RESOURCE_KEY_TEMPLATE.value: "{connector}/{object}",
        ConcurrencyPolicyField.MAX_PARALLELISM.value: 4,
        ConcurrencyPolicyField.RATE_LIMIT_SCOPE.value: RateLimitScope.CONNECTOR.value,
        ConcurrencyPolicyField.ORDERING_REQUIREMENT.value: (
            OrderingRequirement.NONE.value
        ),
        ConcurrencyPolicyField.PROVIDER_SESSION_CONSTRAINT.value: (
            ProviderSessionConstraint.SESSION_PARALLEL_SAFE.value
        ),
    }

    def parse(
        self,
        source: PolicySource,
        payload: object = None,
        *,
        capability_ref: str | None = None,
    ) -> CapabilityConcurrencyDeclaration:
        return ConcurrencyDescriptorParser().parse(
            capability_ref=capability_ref or self.CAPABILITY_REF,
            source=source,
            payload=payload,
        )

    def catalog(self, **overrides: object) -> CapabilityConcurrencyDeclaration:
        return self.parse(
            PolicySource.PRODUCT_CATALOG,
            {**self.PARALLEL_READ_PAYLOAD, **overrides},
        )

    def declare(
        self,
        source: PolicySource,
        **fields: object,
    ) -> CapabilityConcurrencyDeclaration:
        return CapabilityConcurrencyDeclaration(
            capability_ref=self.CAPABILITY_REF,
            source=source,
            **fields,
        )

    def resolve(
        self,
        *declarations: CapabilityConcurrencyDeclaration,
    ) -> ConcurrencyPolicyResolution:
        return ConcurrencyPolicyResolver().resolve(
            capability_ref=self.CAPABILITY_REF,
            declarations=declarations,
        )

    @staticmethod
    def rejected_fields(
        resolution: ConcurrencyPolicyResolution,
    ) -> set[ConcurrencyPolicyField]:
        return {rejection.policy_field for rejection in resolution.rejections}

    @staticmethod
    def plan_modes(
        policy: ConcurrencyPolicy,
        operation_count: int = 3,
    ) -> tuple[Sequence[BatchSegmentMode], Sequence[BatchSegmentReason]]:
        operations = tuple(
            BatchOperation(
                operation_id=f"op_{index}",
                authorization_epoch="auth_1",
                dependency_ids=(),
                resource_fingerprints=(),
            )
            for index in range(operation_count)
        )
        plan = BatchPlanner().plan(
            OperationBatch(
                batch_id="batch_1",
                operations=operations,
                max_parallelism=4,
            ),
            {operation.operation_id: policy for operation in operations},
        )
        return (
            [segment.mode for segment in plan.segments],
            [segment.reason for segment in plan.segments],
        )

    def template(self, template: str = "{connector}/{object}") -> ResourceKeyTemplate:
        return ResourceKeyTemplate.from_template(template)

    def render(
        self,
        template: ResourceKeyTemplate,
        values: Mapping[ResourceKeyDimension, str],
        *,
        secret: bytes | None = None,
    ) -> str:
        return template.render(secret=secret or self.SECRET, values=values)


class TestConservativeDefaults(ConcurrencyVocabularyMixin, DescriptorFixtureMixin):
    def test_every_vocabulary_declares_its_conservative_member_first(self) -> None:
        for vocabulary in self.NARROWABLE_VOCABULARIES:
            assert vocabulary.conservative().rank == 0
            assert vocabulary.conservative() is next(iter(vocabulary))

    def test_narrowest_never_returns_a_wider_member(self) -> None:
        for vocabulary in self.NARROWABLE_VOCABULARIES:
            members = tuple(vocabulary)
            for left in members:
                for right in members:
                    narrowed = vocabulary.narrowest(left, right)
                    assert narrowed.rank <= left.rank
                    assert narrowed.rank <= right.rank
                    assert narrowed is vocabulary.narrowest(right, left)

    def test_default_policy_encodes_no_knowledge(self) -> None:
        policy = ConcurrencyPolicy()

        assert policy.mode is ConcurrencyMode.SERIAL
        assert policy.side_effect is SideEffectKind.UNKNOWN
        assert policy.idempotency is IdempotencyKind.NONE
        assert policy.resource_key_template is None
        assert policy.max_parallelism is None
        assert policy.rate_limit_scope is RateLimitScope.UNKNOWN
        assert policy.ordering_requirement is OrderingRequirement.INPUT_ORDER
        assert policy.provider_session_constraint is ProviderSessionConstraint.UNKNOWN
        assert policy.policy_source is PolicySource.CONSERVATIVE_DEFAULT
        for policy_field in ConcurrencyPolicyField:
            declared = ConcurrencyPolicy.model_fields[policy_field.value].default
            assert policy.value_for(policy_field) == declared


class TestAbsentAndUnknownMetadataIsSerial(DescriptorFixtureMixin):
    def test_no_declaration_resolves_to_the_conservative_default(self) -> None:
        resolution = self.resolve()

        assert resolution.policy == ConcurrencyPolicy()
        assert resolution.policy.policy_source is PolicySource.CONSERVATIVE_DEFAULT
        assert resolution.rejections == ()

    @pytest.mark.parametrize(
        "payload",
        [None, {}, "not-a-mapping", 17, {"unrecognized_key": "parallel_safe"}],
    )
    def test_absent_or_unusable_catalog_metadata_plans_serial(
        self,
        payload: object,
    ) -> None:
        resolution = self.resolve(self.parse(PolicySource.PRODUCT_CATALOG, payload))
        modes, reasons = self.plan_modes(resolution.policy)

        assert resolution.policy.mode is ConcurrencyMode.SERIAL
        assert set(modes) == {BatchSegmentMode.SERIAL}
        assert set(reasons) == {BatchSegmentReason.POLICY_REQUIRES_SERIAL}

    def test_unparseable_mode_falls_to_serial_and_is_recorded(self) -> None:
        resolution = self.resolve(
            self.catalog(**{ConcurrencyPolicyField.MODE.value: "yolo"})
        )
        modes, reasons = self.plan_modes(resolution.policy)

        assert resolution.policy.mode is ConcurrencyMode.SERIAL
        assert set(modes) == {BatchSegmentMode.SERIAL}
        assert set(reasons) == {BatchSegmentReason.POLICY_REQUIRES_SERIAL}
        assert [
            (rejection.policy_field, rejection.reason)
            for rejection in resolution.rejections
        ] == [
            (
                ConcurrencyPolicyField.MODE,
                ConcurrencyRejectionReason.UNPARSEABLE_DEFAULTED_SAFE,
            )
        ]

    @pytest.mark.parametrize(
        ("policy_field", "expected"),
        [
            (ConcurrencyPolicyField.SIDE_EFFECT, SideEffectKind.UNKNOWN),
            (ConcurrencyPolicyField.IDEMPOTENCY, IdempotencyKind.NONE),
            (ConcurrencyPolicyField.RATE_LIMIT_SCOPE, RateLimitScope.UNKNOWN),
            (
                ConcurrencyPolicyField.ORDERING_REQUIREMENT,
                OrderingRequirement.INPUT_ORDER,
            ),
            (
                ConcurrencyPolicyField.PROVIDER_SESSION_CONSTRAINT,
                ProviderSessionConstraint.UNKNOWN,
            ),
        ],
    )
    def test_every_unparseable_vocabulary_falls_to_its_conservative_floor(
        self,
        policy_field: ConcurrencyPolicyField,
        expected: NarrowableEnum,
    ) -> None:
        resolution = self.resolve(self.catalog(**{policy_field.value: object()}))

        assert resolution.policy.value_for(policy_field) is expected
        assert self.rejected_fields(resolution) == {policy_field}

    def test_unparseable_side_effect_reaches_the_planner_as_serial(self) -> None:
        resolution = self.resolve(
            self.catalog(**{ConcurrencyPolicyField.SIDE_EFFECT.value: "mostly-safe"})
        )
        modes, reasons = self.plan_modes(resolution.policy)

        assert resolution.policy.side_effect is SideEffectKind.UNKNOWN
        assert set(modes) == {BatchSegmentMode.SERIAL}
        assert set(reasons) == {BatchSegmentReason.UNKNOWN_SIDE_EFFECT}

    @pytest.mark.parametrize("raw", [0, 17, -1, True, "4", 2.5, [4]])
    def test_unparseable_scheduling_bound_never_widens(self, raw: object) -> None:
        resolution = self.resolve(
            self.catalog(**{ConcurrencyPolicyField.MAX_PARALLELISM.value: raw})
        )

        assert resolution.policy.max_parallelism == 1
        assert self.rejected_fields(resolution) == {
            ConcurrencyPolicyField.MAX_PARALLELISM
        }

    def test_an_explicit_null_declares_nothing(self) -> None:
        resolution = self.resolve(
            self.catalog(
                **{policy_field.value: None for policy_field in ConcurrencyPolicyField}
            )
        )

        assert resolution.policy == ConcurrencyPolicy(
            policy_source=PolicySource.PRODUCT_CATALOG
        )
        assert resolution.rejections == ()

    def test_curated_catalog_metadata_admits_a_parallel_segment(self) -> None:
        resolution = self.resolve(self.catalog())
        modes, reasons = self.plan_modes(resolution.policy)

        assert modes == [BatchSegmentMode.PARALLEL]
        assert reasons == [BatchSegmentReason.INDEPENDENT_READS]


class TestPrecedenceCannotWiden(DescriptorFixtureMixin):
    def test_provider_cannot_widen_the_product_catalog(self) -> None:
        catalog = self.catalog(
            **{
                ConcurrencyPolicyField.MODE.value: (
                    ConcurrencyMode.SAME_SUBJECT_SERIAL.value
                ),
                ConcurrencyPolicyField.MAX_PARALLELISM.value: 2,
                ConcurrencyPolicyField.RATE_LIMIT_SCOPE.value: (
                    RateLimitScope.USER.value
                ),
            }
        )
        provider = self.declare(
            PolicySource.TRUSTED_PROVIDER,
            mode=ConcurrencyMode.PARALLEL_SAFE,
            side_effect=SideEffectKind.NONE,
            idempotency=IdempotencyKind.NATURAL,
            max_parallelism=16,
            rate_limit_scope=RateLimitScope.CAPABILITY,
            provider_session_constraint=(
                ProviderSessionConstraint.SESSION_PARALLEL_SAFE
            ),
        )

        resolution = self.resolve(catalog, provider)

        assert resolution.policy.mode is ConcurrencyMode.SAME_SUBJECT_SERIAL
        assert resolution.policy.side_effect is SideEffectKind.READ
        assert resolution.policy.idempotency is IdempotencyKind.NATURAL
        assert resolution.policy.max_parallelism == 2
        assert resolution.policy.rate_limit_scope is RateLimitScope.USER
        assert self.rejected_fields(resolution) == {
            ConcurrencyPolicyField.MODE,
            ConcurrencyPolicyField.SIDE_EFFECT,
            ConcurrencyPolicyField.MAX_PARALLELISM,
            ConcurrencyPolicyField.RATE_LIMIT_SCOPE,
        }
        assert all(
            rejection.reason is ConcurrencyRejectionReason.WIDER_THAN_ESTABLISHED
            for rejection in resolution.widening_rejections
        )

    def test_provider_without_a_catalog_entry_stays_serial(self) -> None:
        provider = self.declare(
            PolicySource.TRUSTED_PROVIDER,
            mode=ConcurrencyMode.PARALLEL_SAFE,
            side_effect=SideEffectKind.READ,
            max_parallelism=8,
            resource_key_template=self.template(),
        )

        resolution = self.resolve(provider)
        modes, reasons = self.plan_modes(resolution.policy)

        assert resolution.policy.mode is ConcurrencyMode.SERIAL
        assert resolution.policy.side_effect is SideEffectKind.UNKNOWN
        assert resolution.policy.resource_key_template is None
        assert resolution.policy.policy_source is PolicySource.CONSERVATIVE_DEFAULT
        assert set(modes) == {BatchSegmentMode.SERIAL}
        assert set(reasons) == {BatchSegmentReason.CONSERVATIVE_POLICY_DEFAULT}
        assert self.rejected_fields(resolution) == {
            ConcurrencyPolicyField.MODE,
            ConcurrencyPolicyField.SIDE_EFFECT,
            ConcurrencyPolicyField.RESOURCE_KEY_TEMPLATE,
        }

    def test_bounding_an_unbounded_policy_is_a_narrowing_not_a_widening(self) -> None:
        provider = self.declare(PolicySource.TRUSTED_PROVIDER, max_parallelism=8)

        resolution = self.resolve(provider)

        assert resolution.policy.max_parallelism == 8
        assert resolution.policy.mode is ConcurrencyMode.SERIAL
        assert resolution.policy.policy_source is PolicySource.CONSERVATIVE_DEFAULT
        assert resolution.rejections == ()

    def test_user_tightening_applies_and_is_credited(self) -> None:
        user = self.declare(
            PolicySource.USER_APPROVED_OVERRIDE,
            mode=ConcurrencyMode.SAME_SUBJECT_SERIAL,
            max_parallelism=2,
        )

        resolution = self.resolve(self.catalog(), user)

        assert resolution.policy.mode is ConcurrencyMode.SAME_SUBJECT_SERIAL
        assert resolution.policy.max_parallelism == 2
        assert resolution.policy.side_effect is SideEffectKind.READ
        assert resolution.policy.policy_source is PolicySource.USER_APPROVED_OVERRIDE
        assert resolution.contributing_sources == (
            PolicySource.PRODUCT_CATALOG,
            PolicySource.USER_APPROVED_OVERRIDE,
        )
        assert resolution.rejections == ()

    def test_provider_cannot_widen_a_user_tightening(self) -> None:
        user = self.declare(
            PolicySource.USER_APPROVED_OVERRIDE,
            mode=ConcurrencyMode.SERIAL,
        )
        provider = self.declare(
            PolicySource.TRUSTED_PROVIDER,
            mode=ConcurrencyMode.PARALLEL_SAFE,
        )

        resolution = self.resolve(self.catalog(), user, provider)

        assert resolution.policy.mode is ConcurrencyMode.SERIAL
        assert self.rejected_fields(resolution) == {ConcurrencyPolicyField.MODE}
        assert resolution.rejections[0].source is PolicySource.TRUSTED_PROVIDER
        assert resolution.contributing_sources == (
            PolicySource.PRODUCT_CATALOG,
            PolicySource.USER_APPROVED_OVERRIDE,
        )
        assert resolution.policy.policy_source is PolicySource.USER_APPROVED_OVERRIDE

    def test_a_source_that_changes_nothing_is_not_credited(self) -> None:
        provider = self.declare(
            PolicySource.TRUSTED_PROVIDER,
            mode=ConcurrencyMode.PARALLEL_SAFE,
        )

        resolution = self.resolve(self.catalog(), provider)

        assert resolution.contributing_sources == (PolicySource.PRODUCT_CATALOG,)
        assert resolution.considered_sources == (
            PolicySource.PRODUCT_CATALOG,
            PolicySource.TRUSTED_PROVIDER,
        )
        assert resolution.policy.policy_source is PolicySource.PRODUCT_CATALOG

    def test_narrowing_is_order_independent(self) -> None:
        catalog = self.catalog()
        user = self.declare(
            PolicySource.USER_APPROVED_OVERRIDE,
            max_parallelism=3,
            idempotency=IdempotencyKind.KEYED,
        )
        provider = self.declare(
            PolicySource.TRUSTED_PROVIDER,
            mode=ConcurrencyMode.SAME_SUBJECT_SERIAL,
            max_parallelism=8,
            ordering_requirement=OrderingRequirement.COMPLETION_ORDER,
        )

        forward = self.resolve(catalog, user, provider)
        backward = self.resolve(provider, user, catalog)
        interleaved = self.resolve(user, catalog, provider)

        assert forward == backward == interleaved
        assert forward.policy.mode is ConcurrencyMode.SAME_SUBJECT_SERIAL
        assert forward.policy.max_parallelism == 3
        assert forward.policy.idempotency is IdempotencyKind.KEYED
        assert forward.policy.ordering_requirement is (
            OrderingRequirement.COMPLETION_ORDER
        )

    def test_the_narrowing_fold_itself_commutes(self) -> None:
        established = self.catalog().establish()
        user = self.declare(
            PolicySource.USER_APPROVED_OVERRIDE,
            max_parallelism=3,
        )
        provider = self.declare(
            PolicySource.TRUSTED_PROVIDER,
            mode=ConcurrencyMode.SERIAL,
            max_parallelism=2,
        )

        user_first = provider.narrow(user.narrow(established).policy).policy
        provider_first = user.narrow(provider.narrow(established).policy).policy

        assert user_first == provider_first
        assert user_first.mode is ConcurrencyMode.SERIAL
        assert user_first.max_parallelism == 2

    def test_policy_digest_is_stable_and_binds_the_resolved_policy(self) -> None:
        first = self.resolve(self.catalog())
        second = self.resolve(self.catalog())
        tightened = self.resolve(
            self.catalog(),
            self.declare(PolicySource.TRUSTED_PROVIDER, max_parallelism=2),
        )

        assert first.policy_digest == second.policy_digest
        assert first.policy_digest != tightened.policy_digest
        assert first.policy_digest == ConcurrencyPolicyResolution.digest_of(
            first.policy
        )


class TestResolverRejectsInadmissibleDeclarations(DescriptorFixtureMixin):
    def test_duplicate_source_is_a_typed_rejection(self) -> None:
        with pytest.raises(ConcurrencyDeclarationRejected) as excinfo:
            self.resolve(self.catalog(), self.catalog())

        assert excinfo.value.reason is ConcurrencyRejectionReason.DUPLICATE_SOURCE
        assert str(excinfo.value) == excinfo.value.safe_summary
        assert self.CAPABILITY_REF not in str(excinfo.value)

    def test_foreign_capability_declaration_is_a_typed_rejection(self) -> None:
        foreign = self.parse(
            PolicySource.TRUSTED_PROVIDER,
            self.PARALLEL_READ_PAYLOAD,
            capability_ref=self.OTHER_CAPABILITY_REF,
        )

        with pytest.raises(ConcurrencyDeclarationRejected) as excinfo:
            self.resolve(self.catalog(), foreign)

        assert excinfo.value.reason is ConcurrencyRejectionReason.CAPABILITY_MISMATCH

    def test_conservative_default_is_not_a_declarable_source(self) -> None:
        with pytest.raises(ConcurrencyDeclarationRejected) as excinfo:
            self.declare(PolicySource.CONSERVATIVE_DEFAULT)

        assert excinfo.value.reason is ConcurrencyRejectionReason.UNSUPPORTED_SOURCE

    def test_strict_resolution_raises_on_a_widening_attempt(self) -> None:
        provider = self.declare(
            PolicySource.TRUSTED_PROVIDER,
            mode=ConcurrencyMode.PARALLEL_SAFE,
        )

        with pytest.raises(ConcurrencyPolicyWideningRejected) as excinfo:
            ConcurrencyPolicyResolver().resolve_strict(
                capability_ref=self.CAPABILITY_REF,
                declarations=(
                    self.catalog(
                        **{
                            ConcurrencyPolicyField.MODE.value: (
                                ConcurrencyMode.SERIAL.value
                            )
                        }
                    ),
                    provider,
                ),
            )

        assert excinfo.value.reason is (
            ConcurrencyRejectionReason.WIDER_THAN_ESTABLISHED
        )

    def test_strict_resolution_accepts_a_clean_narrowing(self) -> None:
        resolution = ConcurrencyPolicyResolver().resolve_strict(
            capability_ref=self.CAPABILITY_REF,
            declarations=(
                self.catalog(),
                self.declare(PolicySource.TRUSTED_PROVIDER, max_parallelism=2),
            ),
        )

        assert resolution.policy.max_parallelism == 2

    def test_capability_ref_must_be_an_opaque_reference(self) -> None:
        with pytest.raises(ValueError, match="capability_ref"):
            CapabilityConcurrencyDeclaration(
                capability_ref="gmail.search_threads",
                source=PolicySource.PRODUCT_CATALOG,
            )


class TestResourceKeyTemplate(DescriptorFixtureMixin):
    def test_rendered_keys_are_digested_stable_and_batch_compatible(self) -> None:
        template = self.template()
        values = {
            ResourceKeyDimension.CONNECTOR: "gmail",
            ResourceKeyDimension.OBJECT: "thread/42",
        }

        first = self.render(template, values)
        second = self.render(template, values)

        assert first == second
        assert ResourceKeyTemplate.Digest.PATTERN.fullmatch(first) is not None
        assert "gmail" not in first
        assert "thread" not in first
        assert BatchOperation(
            operation_id="op_1",
            authorization_epoch="auth_1",
            dependency_ids=(),
            resource_fingerprints=(first,),
        ).resource_fingerprints == (first,)

    @pytest.mark.parametrize(
        ("left", "right"),
        [
            (
                {"connector": "gmail", "object": "a"},
                {"connector": "gmail", "object": "b"},
            ),
            (
                {"connector": "gmail", "object": "a"},
                {"connector": "slack", "object": "a"},
            ),
            ({"connector": "ab", "object": "c"}, {"connector": "a", "object": "bc"}),
            (
                {"connector": "gmail", "object": ""},
                {"connector": "gmail", "object": " "},
            ),
        ],
    )
    def test_distinct_resources_never_share_a_key(
        self,
        left: Mapping[str, str],
        right: Mapping[str, str],
    ) -> None:
        template = self.template()
        rendered = [
            template.render_or_none(
                secret=self.SECRET,
                values={
                    ResourceKeyDimension(name): value for name, value in values.items()
                },
            )
            for values in (left, right)
        ]

        assert rendered[0] != rendered[1] or rendered == [None, None]

    def test_the_template_itself_is_part_of_the_key_material(self) -> None:
        values = {ResourceKeyDimension.CONNECTOR: "gmail"}
        broad = ResourceKeyTemplate.from_template("{connector}")
        reordered = ResourceKeyTemplate.from_template("{object}/{connector}")

        first = self.render(broad, values)
        second = self.render(
            reordered,
            {**values, ResourceKeyDimension.OBJECT: "thread"},
        )
        third = self.render(
            ResourceKeyTemplate.from_template("{connector}/{object}"),
            {**values, ResourceKeyDimension.OBJECT: "thread"},
        )

        assert len({first, second, third}) == 3

    def test_a_different_secret_produces_a_different_key(self) -> None:
        template = self.template()
        values = {
            ResourceKeyDimension.CONNECTOR: "gmail",
            ResourceKeyDimension.OBJECT: "thread",
        }

        assert self.render(template, values) != self.render(
            template,
            values,
            secret=b"a-different-resource-key-secret!",
        )

    @pytest.mark.parametrize(
        ("values", "secret", "reason"),
        [
            (
                {ResourceKeyDimension.CONNECTOR: "gmail"},
                None,
                ConcurrencyRejectionReason.MISSING_DIMENSION_VALUE,
            ),
            (
                {
                    ResourceKeyDimension.CONNECTOR: "gmail",
                    ResourceKeyDimension.OBJECT: "  ",
                },
                None,
                ConcurrencyRejectionReason.MISSING_DIMENSION_VALUE,
            ),
            (
                {
                    ResourceKeyDimension.CONNECTOR: "gmail",
                    ResourceKeyDimension.OBJECT: "thread",
                    ResourceKeyDimension.REGION: "eu",
                },
                None,
                ConcurrencyRejectionReason.UNEXPECTED_DIMENSION_VALUE,
            ),
            (
                {
                    ResourceKeyDimension.CONNECTOR: "gmail",
                    ResourceKeyDimension.OBJECT: "x" * 513,
                },
                None,
                ConcurrencyRejectionReason.OVERSIZED_DIMENSION_VALUE,
            ),
            (
                {
                    ResourceKeyDimension.CONNECTOR: "gmail",
                    ResourceKeyDimension.OBJECT: "thread",
                },
                b"too-short",
                ConcurrencyRejectionReason.WEAK_DIGEST_SECRET,
            ),
        ],
    )
    def test_unrenderable_key_material_is_a_typed_rejection(
        self,
        values: Mapping[ResourceKeyDimension, str],
        secret: bytes | None,
        reason: ConcurrencyRejectionReason,
    ) -> None:
        template = self.template()

        with pytest.raises(ResourceKeyRenderRejected) as excinfo:
            self.render(template, values, secret=secret)

        assert excinfo.value.reason is reason
        assert "gmail" not in str(excinfo.value)
        assert (
            template.render_or_none(
                secret=secret or self.SECRET,
                values=values,
            )
            is None
        )

    @pytest.mark.parametrize(
        "template",
        [
            "",
            "   ",
            "connector",
            "{connector}{object}",
            "{connector}/{unknown_dimension}",
            "{connector}/{connector}",
            "{Connector}",
            "{connector}/" + "{object}/" * 40,
        ],
    )
    def test_malformed_templates_are_typed_rejections(self, template: str) -> None:
        with pytest.raises(ResourceKeyTemplateRejected) as excinfo:
            ResourceKeyTemplate.from_template(template)

        assert excinfo.value.reason is ConcurrencyRejectionReason.MALFORMED_TEMPLATE
        assert ResourceKeyTemplate.parse(template) is None

    def test_canonical_template_round_trips(self) -> None:
        template = ResourceKeyTemplate.from_template(" {connector}/{account}/{object} ")

        assert template.canonical_template == "{connector}/{account}/{object}"
        assert (
            ResourceKeyTemplate.from_template(template.canonical_template) == template
        )

    def test_policy_coerces_and_validates_a_raw_template_string(self) -> None:
        policy = ConcurrencyPolicy(resource_key_template="{connector}/{object}")

        assert policy.resource_key_template == self.template()
        with pytest.raises(ResourceKeyTemplateRejected):
            ConcurrencyPolicy(resource_key_template="{connector}{object}")


class TestResourceKeyTemplatePrecedence(DescriptorFixtureMixin):
    def test_a_provider_cannot_introduce_a_resource_key(self) -> None:
        catalog = self.catalog(
            **{ConcurrencyPolicyField.RESOURCE_KEY_TEMPLATE.value: None}
        )
        provider = self.declare(
            PolicySource.TRUSTED_PROVIDER,
            resource_key_template=self.template(),
        )

        resolution = self.resolve(catalog, provider)

        assert resolution.policy.resource_key_template is None
        assert [rejection.reason for rejection in resolution.rejections] == [
            ConcurrencyRejectionReason.TEMPLATE_NOT_NARROWER
        ]

    def test_a_coarser_template_narrows_the_established_one(self) -> None:
        provider = self.declare(
            PolicySource.TRUSTED_PROVIDER,
            resource_key_template=ResourceKeyTemplate.from_template("{connector}"),
        )

        resolution = self.resolve(self.catalog(), provider)

        assert resolution.policy.resource_key_template == (
            ResourceKeyTemplate.from_template("{connector}")
        )
        assert resolution.rejections == ()

    def test_a_finer_template_is_refused(self) -> None:
        provider = self.declare(
            PolicySource.TRUSTED_PROVIDER,
            resource_key_template=ResourceKeyTemplate.from_template(
                "{connector}/{object}/{region}"
            ),
        )

        resolution = self.resolve(self.catalog(), provider)

        assert resolution.policy.resource_key_template == self.template()
        assert [rejection.reason for rejection in resolution.rejections] == [
            ConcurrencyRejectionReason.TEMPLATE_NOT_NARROWER
        ]

    def test_an_unorderable_template_falls_to_no_key(self) -> None:
        provider = self.declare(
            PolicySource.TRUSTED_PROVIDER,
            resource_key_template=ResourceKeyTemplate.from_template(
                "{account}/{region}"
            ),
        )

        resolution = self.resolve(self.catalog(), provider)

        assert resolution.policy.resource_key_template is None
        assert [rejection.reason for rejection in resolution.rejections] == [
            ConcurrencyRejectionReason.TEMPLATE_NOT_NARROWER
        ]

    def test_a_malformed_declared_template_never_replaces_the_catalog_one(self) -> None:
        provider = self.parse(
            PolicySource.TRUSTED_PROVIDER,
            {ConcurrencyPolicyField.RESOURCE_KEY_TEMPLATE.value: "{oops"},
        )

        resolution = self.resolve(self.catalog(), provider)

        assert resolution.policy.resource_key_template == self.template()
        assert [rejection.reason for rejection in resolution.rejections] == [
            ConcurrencyRejectionReason.MALFORMED_TEMPLATE
        ]
