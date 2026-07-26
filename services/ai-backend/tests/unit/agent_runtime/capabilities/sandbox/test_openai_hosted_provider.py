"""Hermetic tests for the fail-closed OpenAI hosted-container D3 adapter."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_runtime.capabilities.sandbox.config import (
    OpenAIHostedContainerConfig,
    RemoteSandboxConfig,
    SandboxLimitProfile,
)
from agent_runtime.capabilities.sandbox.contracts import (
    SandboxDeliverable,
    SandboxError,
    SandboxErrorCode,
    SandboxIsolationAttestation,
    SandboxProviderId,
    SandboxRunRequest,
)
from agent_runtime.capabilities.sandbox.cleanup_store import FileSandboxCleanupStore
from agent_runtime.capabilities.sandbox.policy_backend import (
    PolicyEnforcedSandboxBackend,
)
from agent_runtime.capabilities.sandbox.provisioning import (
    SandboxProvisioningAuthority,
)
from agent_runtime.capabilities.sandbox.provider_registry import (
    InMemorySandboxSessionStore,
    SandboxProviderRegistry,
)
from agent_runtime.capabilities.sandbox.providers.openai_hosted import (
    OpenAIHostedContainerBackend,
    OpenAIHostedContainerProvider,
)
from agent_runtime.capabilities.sandbox.readiness import (
    SandboxCapabilityReadiness,
    SandboxReadinessReason,
)
from agent_runtime.capabilities.sandbox.remote_execution_service import (
    RemoteExecutionService,
)
from runtime_adapters.file._paths import FileStoreLayout
from tests.unit.agent_runtime.capabilities.sandbox.fakes import make_request


class _ContainerFiles:
    def __init__(self, client: "_Client") -> None:
        self._client = client
        self.content = _ContainerFileContent(client)
        self.create_calls: list[dict[str, object]] = []

    async def create(self, *, container_id: str, file: tuple[str, bytes]) -> object:
        filename, content = file
        file_id = f"cfile_input_{len(self._client.files) + 1}"
        record = {
            "id": file_id,
            "container_id": container_id,
            "path": f"/mnt/data/{file_id}-{filename}",
            "source": "user",
            "bytes": len(content),
        }
        self._client.files[file_id] = (record, content)
        self.create_calls.append({"container_id": container_id, "file": file})
        return SimpleNamespace(**record)

    async def list(
        self, *, container_id: str, limit: int, after: str | None = None
    ) -> object:
        del limit, after
        data = [
            SimpleNamespace(**record)
            for record, _content in self._client.files.values()
            if record["container_id"] == container_id
        ]
        return SimpleNamespace(data=data, has_more=False, last_id=None)


class _ContainerFileContent:
    def __init__(self, client: "_Client") -> None:
        self._client = client

    async def retrieve(self, *, container_id: str, file_id: str) -> bytes:
        record, content = self._client.files[file_id]
        assert record["container_id"] == container_id
        return content


class _Containers:
    def __init__(self, client: "_Client") -> None:
        self._client = client
        self.files = _ContainerFiles(client)
        self.create_calls: list[dict[str, object]] = []
        self.delete_calls: list[str] = []
        self.delete_error: Exception | None = None
        self.list_error: Exception | None = None

    async def create(self, **kwargs: object) -> object:
        if self._client.before_container_create is not None:
            await self._client.before_container_create()
        self.create_calls.append(kwargs)
        container_id = f"cntr_{len(self._client.containers_by_id) + 1}"
        policy = self._client.created_policy or kwargs["network_policy"]
        record = {
            "id": container_id,
            "name": kwargs["name"],
            "status": "running",
            "expires_after": kwargs["expires_after"],
            "memory_limit": kwargs["memory_limit"],
            "network_policy": policy,
            "_request_id": f"req_container_{container_id}",
        }
        self._client.containers_by_id[container_id] = record
        return SimpleNamespace(**record)

    async def retrieve(self, container_id: str) -> object:
        return SimpleNamespace(**self._client.containers_by_id[container_id])

    async def delete(self, container_id: str) -> object:
        self.delete_calls.append(container_id)
        if self.delete_error is not None:
            raise self.delete_error
        if container_id in self._client.containers_by_id:
            self._client.containers_by_id[container_id]["status"] = "deleted"
        return SimpleNamespace(id=container_id, deleted=True)

    async def list(
        self, *, limit: int, name: str | None = None, after: str | None = None
    ) -> object:
        del limit, after
        if self.list_error is not None:
            raise self.list_error
        data = [
            SimpleNamespace(**record)
            for record in self._client.containers_by_id.values()
            if name is None or record["name"] == name
        ]
        return SimpleNamespace(data=data, has_more=False, last_id=None)


class _Responses:
    def __init__(self, client: "_Client") -> None:
        self._client = client
        self.create_calls: list[dict[str, object]] = []
        self.cancel_calls: list[str] = []

    async def create(self, **kwargs: object) -> object:
        self.create_calls.append(kwargs)
        response_id = f"resp_{len(self.create_calls)}"
        canonical = str(kwargs["input"])
        document = json.loads(canonical.split("\n", maxsplit=1)[1])
        for deliverable in document["deliverables"]:
            file_id = f"cfile_output_{len(self._client.files) + 1}"
            container_id = str(kwargs["tools"][0]["container"])
            filename = deliverable["container_filename"]
            record = {
                "id": file_id,
                "container_id": container_id,
                "path": f"/mnt/data/{filename}",
                "source": "assistant",
                "bytes": len(self._client.output_bytes),
            }
            self._client.files[file_id] = (record, self._client.output_bytes)
        status = "in_progress" if self._client.keep_running else "completed"
        response = SimpleNamespace(
            id=response_id,
            status=status,
            output_text=self._client.output_text,
            _request_id=f"req_response_{response_id}",
        )
        self._client.responses_by_id[response_id] = response
        return response

    async def retrieve(self, response_id: str) -> object:
        return self._client.responses_by_id[response_id]

    async def cancel(self, response_id: str) -> object:
        self.cancel_calls.append(response_id)
        response = self._client.responses_by_id[response_id]
        response.status = "cancelled"
        return response


class _Client:
    def __init__(
        self,
        *,
        output_bytes: bytes = b"report bytes",
        output_text: str = "completed",
        keep_running: bool = False,
        created_policy: dict[str, object] | None = None,
    ) -> None:
        self.files: dict[str, tuple[dict[str, object], bytes]] = {}
        self.containers_by_id: dict[str, dict[str, object]] = {}
        self.responses_by_id: dict[str, object] = {}
        self.output_bytes = output_bytes
        self.output_text = output_text
        self.keep_running = keep_running
        self.created_policy = created_policy
        self.before_container_create = None
        self.containers = _Containers(self)
        self.responses = _Responses(self)


def _config(**updates: object) -> OpenAIHostedContainerConfig:
    return OpenAIHostedContainerConfig.model_validate(updates)


def _run_request() -> SandboxRunRequest:
    return SandboxRunRequest(
        create_request=make_request(),
        command="summarize the sealed input",
        deliverables=(
            SandboxDeliverable(
                path="/workspace/reports/summary.csv",
                media_type="text/csv",
                suggested_filename="summary.csv",
                title="Summary",
            ),
        ),
    )


class _AttestedOpenAIProvider(OpenAIHostedContainerProvider):
    """Test-only subclass; production provider remains intentionally dark."""

    @property
    def isolation_ready(self) -> bool:
        return True

    async def attest(self, request) -> SandboxIsolationAttestation:  # type: ignore[override]
        return SandboxIsolationAttestation(
            provider=SandboxProviderId.OPENAI_HOSTED_CONTAINER,
            isolation="microvm",
            process_isolated=True,
            filesystem_fresh=True,
            teardown_guaranteed=True,
            host_credentials_absent=True,
            cpu_quota_enforced=True,
            memory_quota_enforced=True,
            wall_clock_quota_enforced=True,
            process_quota_enforced=True,
            file_quota_enforced=True,
            egress_mode=request.egress.mode,
            attestation_ref="attestation://test/openai-hosted",
        )


def _service_config(config: OpenAIHostedContainerConfig) -> RemoteSandboxConfig:
    return RemoteSandboxConfig(
        enabled=True,
        provider=SandboxProviderId.OPENAI_HOSTED_CONTAINER,
        limit_profile="desktop_v1",
        openai_hosted_container=config,
    )


def _service(
    *,
    provider: _AttestedOpenAIProvider,
    config: OpenAIHostedContainerConfig,
    root: Path,
) -> tuple[RemoteExecutionService, FileSandboxCleanupStore]:
    resolved = _service_config(config)
    cleanup = FileSandboxCleanupStore(layout=FileStoreLayout(root / "agent-data"))
    return (
        RemoteExecutionService(
            registry=SandboxProviderRegistry(
                provider, SandboxProviderId.OPENAI_HOSTED_CONTAINER
            ),
            config=resolved,
            session_store=InMemorySandboxSessionStore(),
            cleanup_store=cleanup,
        ),
        cleanup,
    )


async def _active(
    *,
    client: _Client,
    root: Path,
    config: OpenAIHostedContainerConfig | None = None,
    request=None,
):
    selected = config or _config()
    provider = _AttestedOpenAIProvider(config=selected, client=client)
    service, cleanup = _service(provider=provider, config=selected, root=root)
    active = await service.create(request or make_request())
    return active, provider, cleanup


class TestOpenAIHostedContainerConfig:
    def test_default_network_policy_is_disabled(self) -> None:
        config = OpenAIHostedContainerConfig.from_env({})
        assert config.network_policy.is_deny_all()

    def test_allowlist_is_explicit_typed_configuration(self) -> None:
        config = OpenAIHostedContainerConfig.from_env(
            {
                "RUNTIME_SANDBOX_OPENAI_EGRESS_MODE": "allowlist",
                "RUNTIME_SANDBOX_OPENAI_ALLOWED_DOMAINS": "api.example.test",
            }
        )
        assert config.network_policy.mode == "allowlist"
        assert config.network_policy.destinations == ("api.example.test",)

    def test_invalid_allowlist_leaves_remote_provider_disabled(self) -> None:
        config = RemoteSandboxConfig.from_env(
            {
                "RUNTIME_ENABLE_REMOTE_SANDBOX": "true",
                "RUNTIME_SANDBOX_PROVIDER": "openai_hosted_container",
                "RUNTIME_SANDBOX_OPENAI_EGRESS_MODE": "allowlist",
            }
        )
        assert not config.is_active


class TestOpenAIHostedContainerProvider:
    async def test_direct_provider_transport_and_backend_calls_fail_before_sdk_or_cleanup(
        self, tmp_path: Path
    ) -> None:
        """No direct call can turn an injected client into a provisioner."""

        client = _Client()
        provider = _AttestedOpenAIProvider(config=_config(), client=client)
        cleanup = FileSandboxCleanupStore(
            layout=FileStoreLayout(tmp_path / "agent-data")
        )

        with pytest.raises(SandboxError) as direct_create:
            await provider.create(make_request())
        with pytest.raises(SandboxError) as direct_transport:
            await provider.transport.provision(object())
        with pytest.raises(SandboxError) as direct_binding:
            provider.bind_provisioning_authority(SandboxProvisioningAuthority())

        assert direct_create.value.code is SandboxErrorCode.SANDBOX_POLICY_UNSUPPORTED
        assert (
            direct_transport.value.code is SandboxErrorCode.SANDBOX_POLICY_UNSUPPORTED
        )
        assert direct_binding.value.code is SandboxErrorCode.SANDBOX_POLICY_UNSUPPORTED
        assert client.containers.create_calls == []
        assert client.responses.create_calls == []
        assert await cleanup.list_pending() == ()

    async def test_revoked_backend_aexecute_fails_before_response_creation(
        self, tmp_path: Path
    ) -> None:
        client = _Client()
        active, provider, _cleanup = await _active(client=client, root=tmp_path)
        await provider.terminate(active.session.provider_session_ref)

        with pytest.raises(SandboxError) as excinfo:
            await active.backend.aexecute("not service-authorized", timeout=1)

        assert excinfo.value.code is SandboxErrorCode.SANDBOX_POLICY_UNSUPPORTED
        assert client.responses.create_calls == []

    def test_sdk_client_is_required_at_composition(self) -> None:
        with pytest.raises(TypeError):
            OpenAIHostedContainerProvider(config=_config())  # type: ignore[call-arg]

    async def test_service_persists_provisioning_recovery_before_sdk_create(
        self, tmp_path: Path
    ) -> None:
        client = _Client()
        provider = _AttestedOpenAIProvider(config=_config(), client=client)
        service, cleanup = _service(provider=provider, config=_config(), root=tmp_path)

        async def assert_durable_reservation() -> None:
            duty = await cleanup.get("sandbox:run-1")
            assert duty is not None
            assert duty.state == "provisioning"
            assert duty.provider_session_ref is None
            assert duty.owner_marker == provider.cleanup_owner_marker(make_request())

        client.before_container_create = assert_durable_reservation
        active = await service.create(make_request())

        duty = await cleanup.get("sandbox:run-1")
        assert active.session.provider_session_ref == "cntr_1"
        assert duty is not None
        assert duty.state == "cleanup_pending"
        assert duty.provider_session_ref == "cntr_1"

    async def test_create_uses_disabled_network_and_captures_response_evidence(
        self, tmp_path: Path
    ) -> None:
        client = _Client()
        provider = _AttestedOpenAIProvider(config=_config(), client=client)
        service, cleanup = _service(provider=provider, config=_config(), root=tmp_path)

        first = await service.create(make_request())
        second = await service.create(make_request())

        assert first.session == second.session
        assert client.containers.create_calls == [
            {
                "name": first.session.provider_evidence.owner_marker,
                "expires_after": {"anchor": "last_active_at", "minutes": 20},
                "memory_limit": "1g",
                "network_policy": {"type": "disabled"},
            }
        ]
        assert first.session.provider is SandboxProviderId.OPENAI_HOSTED_CONTAINER
        assert first.session.provider_evidence.resource_id == "cntr_1"
        assert (
            first.session.provider_evidence.provider_request_id
            == "req_container_cntr_1"
        )
        assert first.session.provider_evidence.network_policy.is_deny_all()
        assert first.session.provider_evidence.expires_after_minutes == 20
        duty = await cleanup.get("sandbox:run-1")
        assert duty is not None
        assert duty.state == "cleanup_pending"
        assert duty.provider_session_ref == "cntr_1"
        assert duty.owner_marker == first.session.provider_evidence.owner_marker
        assert list(tmp_path.iterdir()) == [tmp_path / "agent-data"]

    async def test_create_compiles_only_the_explicit_allowlist(
        self, tmp_path: Path
    ) -> None:
        client = _Client()
        config = _config(
            network_policy={
                "mode": "allowlist",
                "destinations": ("api.example.test",),
            }
        )
        await _active(
            client=client,
            root=tmp_path,
            config=config,
            request=make_request(egress_mode="allowlist"),
        )

        assert client.containers.create_calls[0]["network_policy"] == {
            "type": "allowlist",
            "allowed_domains": ["api.example.test"],
        }

    async def test_transfers_only_sealed_bytes_and_collects_declared_output(
        self, tmp_path: Path
    ) -> None:
        client = _Client()
        active, _provider, _cleanup = await _active(client=client, root=tmp_path)
        backend = active.backend
        assert isinstance(backend._delegate, OpenAIHostedContainerBackend)  # noqa: SLF001

        uploaded = await backend.a_upload_files(
            [("/workspace/input/sales.csv", b"sealed,input\n1,2\n")]
        )
        request = _run_request()
        await backend.prepare_execution(request)
        response = await backend.aexecute(request.command, timeout=1)
        outputs = await backend.a_download_files(["/workspace/reports/summary.csv"])

        assert uploaded[0].error is None
        transfer = client.containers.files.create_calls[0]
        assert transfer["file"][1] == b"sealed,input\n1,2\n"
        assert "file://" not in str(transfer)
        assert "tmp_path" not in str(transfer)
        assert response.output == "completed"
        assert outputs[0].content == b"report bytes"
        canonical = str(client.responses.create_calls[0]["input"])
        assert canonical.startswith("D3_CANONICAL_EXECUTION_V1\n")
        assert "sealed,input" not in canonical
        assert "file://" not in canonical
        evidence = backend._delegate.execution_evidence  # noqa: SLF001
        assert evidence.input_file_ids == ("cfile_input_1",)
        assert evidence.response_id == "resp_1"
        assert evidence.response_request_id == "req_response_resp_1"
        assert list(tmp_path.iterdir()) == [tmp_path / "agent-data"]

    async def test_upload_rejects_non_byte_input_without_provider_transfer(
        self, tmp_path: Path
    ) -> None:
        client = _Client()
        active, _provider, _cleanup = await _active(client=client, root=tmp_path)
        backend = active.backend

        with pytest.raises(SandboxError) as excinfo:
            await backend.a_upload_files([("/workspace/input.txt", "not bytes")])  # type: ignore[list-item]

        assert excinfo.value.code is SandboxErrorCode.SANDBOX_UPLOAD_FAILED
        assert client.containers.files.create_calls == []

    async def test_async_timeout_cancels_the_concrete_response(
        self, tmp_path: Path
    ) -> None:
        client = _Client(keep_running=True)
        active, _provider, _cleanup = await _active(client=client, root=tmp_path)
        backend = active.backend
        request = _run_request()
        await backend.prepare_execution(request)

        with pytest.raises(TimeoutError):
            await backend.aexecute(request.command, timeout=0.001)

        assert client.responses.cancel_calls == ["resp_1"]

    async def test_execution_rejects_unbound_or_mismatched_coordinator_command(
        self, tmp_path: Path
    ) -> None:
        client = _Client()
        active, _provider, _cleanup = await _active(client=client, root=tmp_path)
        backend = active.backend

        with pytest.raises(SandboxError) as excinfo:
            await backend.aexecute("not coordinator-bound", timeout=1)
        assert excinfo.value.code is SandboxErrorCode.SANDBOX_MANIFEST_MISMATCH
        assert client.responses.create_calls == []

        request = _run_request()
        await backend.prepare_execution(request)
        with pytest.raises(SandboxError) as excinfo:
            await backend.aexecute("different command", timeout=1)
        assert excinfo.value.code is SandboxErrorCode.SANDBOX_MANIFEST_MISMATCH
        assert client.responses.create_calls == []

    async def test_status_list_owned_and_reap_are_container_scoped(
        self, tmp_path: Path
    ) -> None:
        client = _Client()
        provider = _AttestedOpenAIProvider(config=_config(), client=client)
        service, _cleanup = _service(provider=provider, config=_config(), root=tmp_path)
        active = await service.create(make_request(owner_tag="owner-a"))
        await service.create(
            make_request(run_id="run-2", idempotency_key="other", owner_tag="owner-b")
        )

        status = await provider.status(active.session.provider_session_ref)
        owned = await provider.list_owned_sessions("owner-a")
        await provider.terminate(active.session.provider_session_ref)
        await provider.terminate(active.session.provider_session_ref)

        assert status.provider_session_ref == "cntr_1"
        assert {session.owner_tag for session in owned} == {"owner-a"}
        assert client.containers.delete_calls == ["cntr_1", "cntr_1"]

    async def test_termination_failure_keeps_cleanup_pending_for_the_reaper(
        self, tmp_path: Path
    ) -> None:
        client = _Client()
        active, provider, _cleanup = await _active(client=client, root=tmp_path)
        client.containers.delete_error = RuntimeError("provider unavailable")

        with pytest.raises(SandboxError) as excinfo:
            await provider.terminate(active.session.provider_session_ref)

        assert excinfo.value.code is SandboxErrorCode.SANDBOX_CLEANUP_PENDING
        assert client.containers.delete_calls == ["cntr_1"]

    async def test_mismatched_provider_network_policy_is_deleted_and_rejected(
        self, tmp_path: Path
    ) -> None:
        client = _Client(
            created_policy={
                "type": "allowlist",
                "allowed_domains": ["api.example.test"],
            }
        )
        provider = _AttestedOpenAIProvider(config=_config(), client=client)
        service, _cleanup = _service(provider=provider, config=_config(), root=tmp_path)

        with pytest.raises(SandboxError) as excinfo:
            await service.create(make_request())

        assert excinfo.value.code is SandboxErrorCode.SANDBOX_PROVISION_FAILED
        assert client.containers.delete_calls == ["cntr_1"]

    async def test_prebind_recovery_duty_reaps_after_binding_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A lost post-create bind leaves a durable, exact-owner recovery duty."""

        client = _Client()
        provider = _AttestedOpenAIProvider(config=_config(), client=client)
        service, cleanup = _service(provider=provider, config=_config(), root=tmp_path)

        async def fail_binding(*_args: object, **_kwargs: object) -> None:
            raise OSError("simulated cleanup bind loss")

        original_transition = cleanup.transition
        monkeypatch.setattr(cleanup, "transition", fail_binding)
        client.containers.list_error = OSError("simulated provider list outage")
        with pytest.raises(SandboxError) as excinfo:
            await service.create(make_request())

        assert excinfo.value.code is SandboxErrorCode.SANDBOX_EXECUTION_INDETERMINATE
        duty = await cleanup.get("sandbox:run-1")
        assert duty is not None
        assert duty.state == "provisioning"
        assert duty.provider_session_ref is None
        assert client.containers.delete_calls == []

        client.containers.list_error = None
        monkeypatch.setattr(cleanup, "transition", original_transition)
        assert await service.cleanup_provisioning_reservation(
            run_id=duty.run_id,
            owner_marker=duty.owner_marker or "",
            operation_id=duty.operation_id,
        )
        recovered = await cleanup.get("sandbox:run-1")
        assert recovered is not None and recovered.state == "cleaned"
        assert client.containers.delete_calls == ["cntr_1"]

    async def test_output_is_bounded_by_the_existing_policy_wrapper(
        self, tmp_path: Path
    ) -> None:
        client = _Client(output_text="x" * 128)
        active, _provider, _cleanup = await _active(client=client, root=tmp_path)
        policy = PolicyEnforcedSandboxBackend(
            delegate=active.backend._delegate,  # noqa: SLF001
            limits=SandboxLimitProfile(
                name="test", combined_command_preview_bytes=8, command_timeout_s=1
            ),
        )
        request = _run_request()

        await policy.prepare_execution(request)
        output = await policy.aexecute(request.command)

        assert output.truncated
        assert "[sandbox: output truncated" in output.output

    async def test_attestation_and_registry_remain_fail_closed_with_typed_gap(
        self, tmp_path: Path
    ) -> None:
        client = _Client()
        provider = OpenAIHostedContainerProvider(config=_config(), client=client)
        with pytest.raises(SandboxError) as excinfo:
            await provider.attest(make_request())
        assert excinfo.value.code is SandboxErrorCode.SANDBOX_ISOLATION_UNVERIFIED
        assert not provider.isolation_ready

        config = RemoteSandboxConfig.from_env(
            {
                "RUNTIME_ENABLE_REMOTE_SANDBOX": "true",
                "RUNTIME_SANDBOX_PROVIDER": "openai_hosted_container",
            }
        )
        readiness = SandboxCapabilityReadiness.assess(config)
        assert not readiness.available
        assert (
            readiness.reason
            is SandboxReadinessReason.OPENAI_HOSTED_CONTAINER_CONTROL_GAP
        )
        with pytest.raises(SandboxError):
            SandboxProviderRegistry.from_config(
                config,
                overrides={SandboxProviderId.OPENAI_HOSTED_CONTAINER: provider},
            )

        service, cleanup = _service(
            provider=provider,
            config=_config(),
            root=tmp_path,  # type: ignore[arg-type]
        )
        with pytest.raises(SandboxError) as service_create:
            await service.create(make_request())
        assert (
            service_create.value.code is SandboxErrorCode.SANDBOX_ISOLATION_UNVERIFIED
        )
        assert client.containers.create_calls == []
        assert client.responses.create_calls == []
        assert await cleanup.list_pending() == ()
