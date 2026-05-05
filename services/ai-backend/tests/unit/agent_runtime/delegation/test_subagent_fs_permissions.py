"""Unit tests for ``SubagentDefinition.fs_permissions`` (PR 1.3.5).

Covers the Pydantic-side spec validation and the factory-side translation
into deepagents' ``FilesystemPermission`` rules.
"""

from __future__ import annotations

import pytest

from agent_runtime.delegation.subagents.contracts import (
    FilesystemPermissionSpec,
    SubagentDefinition,
    SubagentTransport,
)
from agent_runtime.execution.factory import _subagents_with_fs_permissions


def _definition(**overrides: object) -> SubagentDefinition:
    base: dict[str, object] = {
        "name": "competitive_research",
        "description": "Researches competitive positioning across owned material.",
        "graph_id": "graph_competitive",
        "transport": SubagentTransport.ASGI,
    }
    base.update(overrides)
    return SubagentDefinition.model_validate(base)


class TestFilesystemPermissionSpec:
    def test_default_empty(self) -> None:
        definition = _definition()
        assert definition.fs_permissions == ()

    def test_grants_drafts_write(self) -> None:
        spec = FilesystemPermissionSpec(
            operations=("read", "write"), paths=("/drafts/",), mode="allow"
        )
        definition = _definition(fs_permissions=(spec,))
        assert definition.fs_permissions[0].mode == "allow"
        assert definition.fs_permissions[0].paths == ("/drafts/",)
        assert "write" in definition.fs_permissions[0].operations

    def test_paths_must_start_with_slash(self) -> None:
        with pytest.raises(ValueError):
            FilesystemPermissionSpec(
                operations=("write",), paths=("drafts/",), mode="allow"
            )

    def test_paths_reject_dotdot(self) -> None:
        with pytest.raises(ValueError):
            FilesystemPermissionSpec(
                operations=("write",), paths=("/drafts/../etc/",), mode="allow"
            )

    def test_paths_reject_tilde(self) -> None:
        with pytest.raises(ValueError):
            FilesystemPermissionSpec(
                operations=("write",), paths=("/~/secret/",), mode="allow"
            )


class TestFactoryTranslation:
    def test_passthrough_when_no_permissions(self) -> None:
        definition = _definition()
        result = _subagents_with_fs_permissions((definition,))
        assert result[0] is definition
        assert getattr(result[0], "permissions", None) in (None, [])

    def test_attaches_permissions_when_specs_present(self) -> None:
        spec = FilesystemPermissionSpec(
            operations=("read", "write"), paths=("/drafts/",), mode="allow"
        )
        definition = _definition(fs_permissions=(spec,))
        result = _subagents_with_fs_permissions((definition,))
        permissions = getattr(result[0], "permissions", None)
        assert permissions is not None
        assert len(permissions) == 1
        # Translated to deepagents' FilesystemPermission dataclass.
        translated = permissions[0]
        assert translated.mode == "allow"
        assert translated.paths == ["/drafts/"]
        assert "write" in translated.operations

    def test_multiple_specs_translate_in_order(self) -> None:
        deny = FilesystemPermissionSpec(
            operations=("write",), paths=("/secret/",), mode="deny"
        )
        allow = FilesystemPermissionSpec(
            operations=("read", "write"), paths=("/drafts/",), mode="allow"
        )
        definition = _definition(fs_permissions=(deny, allow))
        result = _subagents_with_fs_permissions((definition,))
        permissions = getattr(result[0], "permissions", None)
        assert permissions is not None
        assert [p.mode for p in permissions] == ["deny", "allow"]

    def test_empty_input_passthrough(self) -> None:
        assert _subagents_with_fs_permissions(()) == ()
