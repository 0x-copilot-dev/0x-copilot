"""Provider-independent conformance suite for ``SandboxProviderPort``.

This suite is written against a provider fixture and asserts the substitution
contract every adapter must satisfy: idempotent create, execute semantics
through the Deep Agents ``SandboxBackendProtocol``, native upload/download
(binary + Unicode paths), status, idempotent terminate, owner enumeration, and
fail-closed behavior when an egress/secret policy cannot be represented.

AC7 runs it against the in-repo fake. A future AgentCore/Daytona/Modal/Runloop/
Vercel/E2B adapter is added to ``PROVIDER_FACTORIES`` unchanged; live-provider
runs happen in a controlled staging account, never in normal PR CI.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from agent_runtime.capabilities.sandbox.contracts import (
    SandboxError,
    SandboxErrorCode,
)
from agent_runtime.capabilities.sandbox.ports import SandboxProviderPort
from tests.unit.agent_runtime.capabilities.sandbox.fakes import (
    FakeSandboxProvider,
    make_request,
)

#: Registry of provider factories the conformance suite runs against. Add a new
#: adapter's factory here (guarded by an availability check) to enforce the same
#: contract on it — no suite changes required.
PROVIDER_FACTORIES: dict[str, Callable[[], SandboxProviderPort]] = {
    "fake": FakeSandboxProvider,
}


@pytest.fixture(params=sorted(PROVIDER_FACTORIES), ids=sorted(PROVIDER_FACTORIES))
def provider(request: pytest.FixtureRequest) -> SandboxProviderPort:
    return PROVIDER_FACTORIES[request.param]()


class TestProviderConformance:
    async def test_implements_port(self, provider: SandboxProviderPort) -> None:
        assert isinstance(provider, SandboxProviderPort)

    async def test_create_is_idempotent(self, provider: SandboxProviderPort) -> None:
        req = make_request(idempotency_key="same-key")
        h1 = await provider.create(req)
        h2 = await provider.create(req)
        assert h1.session.provider_session_ref == h2.session.provider_session_ref

    async def test_execute_success_and_nonzero_exit(
        self, provider: SandboxProviderPort
    ) -> None:
        handle = await provider.create(make_request())
        ok = handle.backend.execute("echo:hi")
        assert ok.output == "hi"
        assert ok.exit_code == 0
        bad = handle.backend.execute("exit:2")
        assert bad.exit_code == 2

    async def test_execute_timeout_surfaces(
        self, provider: SandboxProviderPort
    ) -> None:
        handle = await provider.create(make_request())
        with pytest.raises(TimeoutError):
            handle.backend.execute("timeout")

    async def test_upload_download_binary_and_unicode_path(
        self, provider: SandboxProviderPort
    ) -> None:
        handle = await provider.create(make_request())
        path = "/workspace/data/ünïcödé.bin"
        payload = bytes(range(256))
        up = handle.backend.upload_files([(path, payload)])
        assert up[0].error is None
        down = handle.backend.download_files([path])
        assert down[0].content == payload

    async def test_download_missing_reports_error(
        self, provider: SandboxProviderPort
    ) -> None:
        handle = await provider.create(make_request())
        result = handle.backend.download_files(["/workspace/missing"])
        assert result[0].content is None
        assert result[0].error is not None

    async def test_terminate_is_idempotent(self, provider: SandboxProviderPort) -> None:
        handle = await provider.create(make_request())
        ref = handle.session.provider_session_ref
        await provider.terminate(ref)
        await provider.terminate(ref)  # no raise on second delete

    async def test_enumerate_by_owner_tag(self, provider: SandboxProviderPort) -> None:
        await provider.create(make_request(idempotency_key="a", owner_tag="owner-x"))
        await provider.create(
            make_request(run_id="run-2", idempotency_key="b", owner_tag="owner-y")
        )
        owned = await provider.list_owned_sessions("owner-x")
        assert {s.owner_tag for s in owned} == {"owner-x"}

    async def test_fail_closed_on_unsupported_egress(
        self, provider: SandboxProviderPort
    ) -> None:
        with pytest.raises(SandboxError) as excinfo:
            await provider.create(make_request(egress_mode="allowlist"))
        assert excinfo.value.code is SandboxErrorCode.SANDBOX_POLICY_UNSUPPORTED
