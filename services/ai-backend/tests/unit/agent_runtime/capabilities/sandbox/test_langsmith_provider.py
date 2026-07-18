"""LangSmith provider adapter fail-closed tests.

The ``langsmith[sandbox]`` extra is intentionally not installed in this
FOUNDATION change, so the adapter must fail closed with typed errors rather than
importing a missing SDK or running without the controls it advertises.
"""

from __future__ import annotations

import importlib.util

import pytest

from agent_runtime.capabilities.sandbox.contracts import (
    SandboxError,
    SandboxErrorCode,
)
from agent_runtime.capabilities.sandbox.providers.langsmith import (
    LangSmithSandboxProvider,
)
from tests.unit.agent_runtime.capabilities.sandbox.fakes import make_request

_HAS_LANGSMITH_SANDBOX = importlib.util.find_spec("langsmith.sandbox") is not None


class TestLangSmithFailClosed:
    async def test_egress_request_rejected_before_sdk(self) -> None:
        provider = LangSmithSandboxProvider()
        with pytest.raises(SandboxError) as excinfo:
            await provider.create(make_request(egress_mode="allowlist"))
        assert excinfo.value.code is SandboxErrorCode.SANDBOX_POLICY_UNSUPPORTED

    @pytest.mark.skipif(
        _HAS_LANGSMITH_SANDBOX,
        reason="langsmith[sandbox] extra is installed; SDK path is exercised in staging.",
    )
    async def test_missing_extra_fails_closed(self) -> None:
        provider = LangSmithSandboxProvider()
        with pytest.raises(SandboxError) as excinfo:
            await provider.create(make_request())
        assert excinfo.value.code is SandboxErrorCode.SANDBOX_PROVIDER_UNCONFIGURED

    @pytest.mark.skipif(
        _HAS_LANGSMITH_SANDBOX,
        reason="langsmith[sandbox] extra is installed.",
    )
    async def test_status_requires_sdk(self) -> None:
        provider = LangSmithSandboxProvider()
        with pytest.raises(SandboxError):
            await provider.status("ref-1")
