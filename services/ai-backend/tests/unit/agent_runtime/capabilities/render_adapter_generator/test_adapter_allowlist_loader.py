"""Lock-in canary for the shared adapter-allowlist JSON spec.

A mirror canary lives at
``packages/api-types/src/adapterAllowlist.test.ts``. Editing the JSON
should give a visible signal on **both** sides.
"""

from __future__ import annotations

from enterprise_service_contracts.adapter_allowlist import load_adapter_allowlist

from agent_runtime.capabilities.render_adapter_generator.capability import (
    _ForbiddenPattern,
    _ImportAllowlist,
)


class TestAdapterAllowlistLoaderSnapshot:
    def test_schema_version_pinned(self) -> None:
        assert load_adapter_allowlist()["schema_version"] == 1

    def test_allowed_modules_match_expected_set(self) -> None:
        data = load_adapter_allowlist()
        assert set(data["allowed_imports"].keys()) == {
            "react",
            "react-dom",
            "@enterprise-search/design-system",
        }

    def test_react_named_exports_narrow(self) -> None:
        data = load_adapter_allowlist()
        react = data["allowed_imports"]["react"]
        assert "createElement" in react
        assert "Fragment" in react
        assert "useState" in react
        assert "useEffect" not in react
        assert "useRef" not in react
        assert "useLayoutEffect" not in react

    def test_well_known_globals_forbidden(self) -> None:
        data = load_adapter_allowlist()
        forbidden = set(data["forbidden_globals"])
        for name in (
            "window",
            "document",
            "fetch",
            "XMLHttpRequest",
            "WebSocket",
            "EventSource",
            "localStorage",
            "sessionStorage",
            "navigator",
            "process",
            "globalThis",
            "require",
        ):
            assert name in forbidden, name

    def test_syntax_set_matches_documented_three(self) -> None:
        data = load_adapter_allowlist()
        assert set(data["forbidden_syntax"]) == {"eval", "Function", "__proto__"}

    def test_forbidden_globals_length_floor(self) -> None:
        # Soft length floor: detects accidental wholesale deletion without
        # pinning every entry (which would be brittle as the union grows).
        assert len(load_adapter_allowlist()["forbidden_globals"]) >= 20

    def test_budget_ms_positive(self) -> None:
        assert load_adapter_allowlist()["budget_ms"] > 0


class TestAuditorWiring:
    def test_forbidden_pattern_tokens_include_spec_globals(self) -> None:
        data = load_adapter_allowlist()
        tokens = set(_ForbiddenPattern.TOKENS)
        for name in data["forbidden_globals"]:
            assert name in tokens, f"globals leak: {name!r} missing from auditor"
        for name in data["forbidden_syntax"]:
            assert name in tokens, f"syntax leak: {name!r} missing from auditor"

    def test_import_allowlist_drops_tombstone_modules(self) -> None:
        # Modules with an empty named-export list are tombstones (the
        # desktop scanner allows the name but rejects every specifier);
        # codegen must never emit imports from them, so the auditor's
        # ALLOWED set excludes them.
        data = load_adapter_allowlist()
        tombstones = {
            module for module, names in data["allowed_imports"].items() if not names
        }
        for module in tombstones:
            assert module not in _ImportAllowlist.ALLOWED, module
        for module, names in data["allowed_imports"].items():
            if names:
                assert module in _ImportAllowlist.ALLOWED, module
