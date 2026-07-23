"""EffectiveActionPolicyResolver + override parsing (PRD-C1, SDR §10 inv 1/3)."""

from __future__ import annotations

from agent_runtime.capabilities.actions.contracts import (
    ActionClass,
    CatalogActionKind,
    ClassificationBasis,
    ClassifiedAction,
    ConnectorWritePolicy,
)
from agent_runtime.capabilities.actions.policy import (
    ConnectorWritePolicyOverrides,
    EffectiveActionPolicyResolver,
)
from agent_runtime.capabilities.tools.permissions import (
    ToolUsePolicyKind,
    ToolUsePolicyMode,
    ToolUsePolicySnapshot,
)


def _classified(
    *,
    action_class: ActionClass,
    basis: ClassificationBasis,
    catalog_kind: CatalogActionKind | None = None,
    connector: str = "linear",
    op: str = "op",
) -> ClassifiedAction:
    return ClassifiedAction(
        connector=connector,
        op=op,
        action_class=action_class,
        basis=basis,
        catalog_kind=catalog_kind,
    )


def _resolver(
    *,
    modes: dict[str, str] | None = None,
    overrides: dict[str, str] | None = None,
) -> EffectiveActionPolicyResolver:
    snapshot = ToolUsePolicySnapshot.from_response(workspace=modes or {})
    ov = ConnectorWritePolicyOverrides.from_user_policies(
        {"tool_use": {"connector_write_policy": overrides}} if overrides else None
    )
    return EffectiveActionPolicyResolver(snapshot=snapshot, overrides=ov)


class TestResolutionAxis:
    def test_annotation_read_resolves_write_axis_held(self) -> None:
        # DoD adversarial: annotation-only read routes to the WRITE axis (ask by
        # default) -> held. A catalog read routes to the READ axis (auto) -> not
        # held. Same default deployment policy.
        resolver = _resolver()

        ann_read = resolver.resolve(
            _classified(
                action_class=ActionClass.READ, basis=ClassificationBasis.ANNOTATION
            )
        )
        assert ann_read.policy_kind is ToolUsePolicyKind.WRITE
        assert ann_read.mode is ToolUsePolicyMode.ASK
        assert ann_read.hold is True

        cat_read = resolver.resolve(
            _classified(
                action_class=ActionClass.READ,
                basis=ClassificationBasis.CATALOG,
                catalog_kind=CatalogActionKind.READ,
            )
        )
        assert cat_read.policy_kind is ToolUsePolicyKind.READ
        assert cat_read.mode is ToolUsePolicyMode.AUTO
        assert cat_read.hold is False

    def test_catalog_write_uses_write_axis(self) -> None:
        resolver = _resolver()
        result = resolver.resolve(
            _classified(
                action_class=ActionClass.WRITE,
                basis=ClassificationBasis.CATALOG,
                catalog_kind=CatalogActionKind.WRITE,
            )
        )
        assert result.policy_kind is ToolUsePolicyKind.WRITE
        assert result.hold is True  # write default = ask

    def test_catalog_destructive_uses_destructive_axis(self) -> None:
        resolver = _resolver()
        result = resolver.resolve(
            _classified(
                action_class=ActionClass.WRITE,
                basis=ClassificationBasis.CATALOG,
                catalog_kind=CatalogActionKind.DESTRUCTIVE,
            )
        )
        assert result.policy_kind is ToolUsePolicyKind.DESTRUCTIVE
        assert result.mode is ToolUsePolicyMode.REQUIRE  # destructive default
        assert result.hold is True

    def test_annotation_destructive_hint_uses_destructive_axis(self) -> None:
        resolver = _resolver()
        result = resolver.resolve(
            _classified(
                action_class=ActionClass.WRITE, basis=ClassificationBasis.ANNOTATION
            )
        )
        assert result.policy_kind is ToolUsePolicyKind.DESTRUCTIVE

    def test_default_write_uses_write_axis_held(self) -> None:
        resolver = _resolver()
        result = resolver.resolve(
            _classified(
                action_class=ActionClass.WRITE, basis=ClassificationBasis.DEFAULT
            )
        )
        assert result.policy_kind is ToolUsePolicyKind.WRITE
        assert result.hold is True


class TestOverride:
    def test_allow_always_downgrades_only_write_ask(self) -> None:
        resolver = _resolver(overrides={"linear": "allow_always"})

        # write-axis ASK -> AUTO with bypass=True.
        write = resolver.resolve(
            _classified(
                action_class=ActionClass.WRITE,
                basis=ClassificationBasis.CATALOG,
                catalog_kind=CatalogActionKind.WRITE,
                connector="linear",
            )
        )
        assert write.mode is ToolUsePolicyMode.AUTO
        assert write.bypass is True
        assert write.hold is False

        # destructive REQUIRE is never touched.
        destructive = resolver.resolve(
            _classified(
                action_class=ActionClass.WRITE,
                basis=ClassificationBasis.CATALOG,
                catalog_kind=CatalogActionKind.DESTRUCTIVE,
                connector="linear",
            )
        )
        assert destructive.mode is ToolUsePolicyMode.REQUIRE
        assert destructive.bypass is False
        assert destructive.hold is True

    def test_allow_always_never_downgrades_require_on_write_axis(self) -> None:
        # If the write axis is REQUIRE (not ASK), allow_always leaves it.
        resolver = _resolver(
            modes={"write": "require"}, overrides={"linear": "allow_always"}
        )
        result = resolver.resolve(
            _classified(
                action_class=ActionClass.WRITE,
                basis=ClassificationBasis.CATALOG,
                catalog_kind=CatalogActionKind.WRITE,
                connector="linear",
            )
        )
        assert result.mode is ToolUsePolicyMode.REQUIRE
        assert result.bypass is False
        assert result.hold is True

    def test_allow_always_never_overrides_block(self) -> None:
        resolver = _resolver(
            modes={"write": "block"}, overrides={"linear": "allow_always"}
        )
        result = resolver.resolve(
            _classified(
                action_class=ActionClass.WRITE,
                basis=ClassificationBasis.DEFAULT,
                connector="linear",
            )
        )
        assert result.mode is ToolUsePolicyMode.BLOCK
        assert result.bypass is False
        assert result.hold is True

    def test_ask_first_override_does_not_downgrade(self) -> None:
        resolver = _resolver(overrides={"linear": "ask_first"})
        result = resolver.resolve(
            _classified(
                action_class=ActionClass.WRITE,
                basis=ClassificationBasis.CATALOG,
                catalog_kind=CatalogActionKind.WRITE,
                connector="linear",
            )
        )
        assert result.mode is ToolUsePolicyMode.ASK
        assert result.bypass is False
        assert result.hold is True

    def test_override_for_other_connector_does_not_apply(self) -> None:
        resolver = _resolver(overrides={"github": "allow_always"})
        result = resolver.resolve(
            _classified(
                action_class=ActionClass.WRITE,
                basis=ClassificationBasis.CATALOG,
                catalog_kind=CatalogActionKind.WRITE,
                connector="linear",
            )
        )
        assert result.mode is ToolUsePolicyMode.ASK
        assert result.bypass is False


class TestSettingsAndParsing:
    def test_settings_mode_change_changes_resolution(self) -> None:
        # DoD: flipping the write axis in the Approval Policy changes the
        # effective hold with no other change.
        classified = _classified(
            action_class=ActionClass.WRITE,
            basis=ClassificationBasis.CATALOG,
            catalog_kind=CatalogActionKind.WRITE,
        )
        ask = _resolver(modes={"write": "ask"}).resolve(classified)
        auto = _resolver(modes={"write": "auto"}).resolve(classified)
        assert ask.hold is True
        assert auto.hold is False

    def test_missing_policy_json_uses_deployment_defaults(self) -> None:
        # None / garbage policy JSON -> empty overrides + default snapshot modes.
        for bad in (None, {}, {"tool_use": "nope"}, {"tool_use": {}}):
            ov = ConnectorWritePolicyOverrides.from_user_policies(bad)  # type: ignore[arg-type]
            assert ov.for_connector("linear") is None

    def test_overrides_parse_and_normalize_keys(self) -> None:
        ov = ConnectorWritePolicyOverrides.from_user_policies(
            {
                "tool_use": {
                    "connector_write_policy": {
                        "seed:linear": "allow_always",
                        "GitHub": "ask_first",
                        "bogus": "not_a_mode",  # dropped
                    }
                }
            }
        )
        assert ov.for_connector("linear") is ConnectorWritePolicy.ALLOW_ALWAYS
        assert ov.for_connector("github") is ConnectorWritePolicy.ASK_FIRST
        assert ov.for_connector("bogus") is None
