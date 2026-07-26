"""OpenAI hosted-container adapter for D3's existing provider port.

The adapter uses OpenAI's Container and Responses/Code Interpreter APIs only.
It has no local-shell, workspace, or direct-host-write fallback: all transfer is
through container-file byte APIs and all execution is through the established
D3 coordinator/runtime path.

OpenAI documents container network policy, memory limits, files, lifecycle and
response cancellation.  It does *not* document every D3 attestation control
(notably CPU, process, file-count, wall-clock and host-credential controls), so
``isolation_ready`` is deliberately false.  The registry therefore keeps this
adapter unavailable to the model until an authoritative provider attestation can
cover those gaps.  The narrow transport remains real and SDK-backed so its API
translation is hermetically testable without a live provider call.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
import hashlib
import inspect
from typing import Any, Protocol

from deepagents.backends.protocol import (
    ExecuteResponse,
    FileDownloadResponse,
    FileUploadResponse,
)
from deepagents.backends.sandbox import BaseSandbox
from pydantic import Field

from agent_runtime.capabilities.sandbox.config import OpenAIHostedContainerConfig
from agent_runtime.capabilities.sandbox.contracts import (
    ManagedSandboxSession,
    SandboxCreateRequest,
    SandboxEgressPolicy,
    SandboxError,
    SandboxErrorCode,
    SandboxIsolationAttestation,
    SandboxProviderEvidence,
    SandboxProviderId,
    SandboxRunRequest,
    _utcnow,
)
from agent_runtime.capabilities.sandbox.ports import SandboxHandle
from agent_runtime.capabilities.sandbox.workspace_transfer import WorkspacePathValidator
from agent_runtime.execution.contracts import RuntimeContract
from agent_runtime.surfaces_v2.canonical_json import canonical_json_bytes


_CANONICAL_EXECUTION_PREFIX = "D3_CANONICAL_EXECUTION_V1\n"
_CODE_INTERPRETER_INSTRUCTIONS = (
    "Use Code Interpreter only. Process only the canonical D3 execution "
    "document supplied as input. Do not use network access. Read source bytes "
    "only from the listed container paths. If output files are requested, write "
    "only the listed deterministic filenames under /mnt/data. Return a compact "
    "plain-text completion summary."
)


class OpenAIHostedContainerClient(Protocol):
    """Minimal async OpenAI SDK surface used by this adapter.

    Deliberately structural so tests inject a small hermetic fake rather than
    importing or configuring the real SDK client.
    """

    containers: Any
    responses: Any


class OpenAIHostedContainerExecutionEvidence(RuntimeContract):
    """Concrete, credential-free identifiers observed during one execution."""

    container_id: str = Field(min_length=1, max_length=2048)
    container_request_id: str | None = Field(default=None, max_length=2048)
    owner_marker: str = Field(min_length=1, max_length=255)
    network_policy: SandboxEgressPolicy
    memory_limit: str | None = Field(default=None, max_length=32)
    input_file_ids: tuple[str, ...] = ()
    response_id: str | None = Field(default=None, max_length=2048)
    response_request_id: str | None = Field(default=None, max_length=2048)


@dataclass(frozen=True)
class _ContainerFile:
    file_id: str
    provider_path: str


@dataclass(frozen=True)
class _ExecutionContext:
    command: str
    deliverables: tuple[str, ...]


class OpenAIHostedContainerBackend(BaseSandbox):
    """Deep Agents backend façade backed exclusively by OpenAI hosted APIs.

    ``DeepAgentSandboxRuntime`` detects the async transfer helpers below.  The
    synchronous upload/download methods only bridge its existing ``to_thread``
    compatibility path to those same helpers; they do not create a second
    executor.  Synchronous command execution is refused so coordinator code
    cannot accidentally bypass bounded async cancellation handling.
    """

    def __init__(
        self,
        *,
        client: OpenAIHostedContainerClient,
        config: OpenAIHostedContainerConfig,
        container_id: str,
        owner_marker: str,
        container_request_id: str | None,
        network_policy: SandboxEgressPolicy,
        memory_limit: str | None,
    ) -> None:
        self._client = client
        self._config = config
        self._container_id = container_id
        self._owner_marker = owner_marker
        self._container_request_id = container_request_id
        self._network_policy = network_policy
        self._memory_limit = memory_limit
        self._input_files: dict[str, _ContainerFile] = {}
        self._execution_context: _ExecutionContext | None = None
        self._response_id: str | None = None
        self._response_request_id: str | None = None

    @property
    def id(self) -> str:
        """Opaque D3 backend identity: the provider's container ID."""

        return self._container_id

    @property
    def execution_evidence(self) -> OpenAIHostedContainerExecutionEvidence:
        """Return structured facts observed from concrete provider responses."""

        return OpenAIHostedContainerExecutionEvidence(
            container_id=self._container_id,
            container_request_id=self._container_request_id,
            owner_marker=self._owner_marker,
            network_policy=self._network_policy,
            memory_limit=self._memory_limit,
            input_file_ids=tuple(
                item.file_id for _path, item in sorted(self._input_files.items())
            ),
            response_id=self._response_id,
            response_request_id=self._response_request_id,
        )

    async def prepare_execution(self, request: SandboxRunRequest) -> None:
        """Bind one coordinator-approved command and declared deliverables."""

        context = _ExecutionContext(
            command=request.command,
            deliverables=tuple(
                WorkspacePathValidator.normalize(item.path)
                for item in request.deliverables
            ),
        )
        if self._execution_context is not None and self._execution_context != context:
            raise SandboxError(
                SandboxErrorCode.SANDBOX_MANIFEST_MISMATCH,
                "Sandbox execution was already bound to a different request.",
            )
        self._execution_context = context

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        """Refuse a sync execution path outside D3's coordinator runtime."""

        del command, timeout
        raise SandboxError(
            SandboxErrorCode.SANDBOX_EXECUTION_INDETERMINATE,
            "OpenAI hosted-container execution requires the D3 async coordinator.",
        )

    async def aexecute(
        self, command: str, *, timeout: int | None = None
    ) -> ExecuteResponse:
        """Run exactly one canonical coordinator command via Code Interpreter."""

        if self._execution_context is None:
            raise SandboxError(
                SandboxErrorCode.SANDBOX_MANIFEST_MISMATCH,
                "Sandbox execution requires a coordinator-approved request.",
            )
        if command != self._execution_context.command:
            raise SandboxError(
                SandboxErrorCode.SANDBOX_MANIFEST_MISMATCH,
                "Sandbox execution did not match the approved command.",
            )
        effective_timeout = timeout if timeout is not None and timeout > 0 else 120
        try:
            response = await asyncio.wait_for(
                self._execute_response(command), timeout=effective_timeout
            )
        except asyncio.CancelledError:
            await self._cancel_response()
            raise
        except TimeoutError:
            await self._cancel_response()
            raise
        return ExecuteResponse(
            output=_response_output_text(response),
            exit_code=0,
            truncated=False,
        )

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        """Bridge the established thread-based runtime to async byte transfer."""

        return asyncio.run(self.a_upload_files(files))

    async def a_upload_files(
        self, files: list[tuple[str, bytes]]
    ) -> list[FileUploadResponse]:
        """Upload only sealed byte payloads with deterministic safe filenames."""

        responses: list[FileUploadResponse] = []
        for raw_path, content in files:
            path = WorkspacePathValidator.normalize(raw_path)
            if not isinstance(content, bytes):
                raise SandboxError(
                    SandboxErrorCode.SANDBOX_UPLOAD_FAILED,
                    "Sandbox input must be verified bytes.",
                )
            try:
                created = await self._client.containers.files.create(
                    container_id=self._container_id,
                    file=(self._input_filename(path), content),
                )
                file_id = _required_string(created, "id")
                provider_path = _required_string(created, "path")
                if _value(created, "container_id") != self._container_id:
                    raise ValueError("provider returned a file for another container")
            except SandboxError:
                raise
            except Exception as exc:  # noqa: BLE001 - external SDK boundary
                raise SandboxError(
                    SandboxErrorCode.SANDBOX_UPLOAD_FAILED,
                    "The sandbox provider could not upload sealed input bytes.",
                ) from exc
            self._input_files[path] = _ContainerFile(
                file_id=file_id, provider_path=provider_path
            )
            responses.append(FileUploadResponse(path=path, error=None))
        return responses

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        """Bridge the established thread-based runtime to async output transfer."""

        return asyncio.run(self.a_download_files(paths))

    async def a_download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        """Download only explicitly declared, assistant-created deliverables."""

        normalized = tuple(WorkspacePathValidator.normalize(path) for path in paths)
        permitted = (
            set(self._execution_context.deliverables)
            if self._execution_context
            else set()
        )
        if any(path not in permitted for path in normalized):
            raise SandboxError(
                SandboxErrorCode.SANDBOX_DOWNLOAD_FAILED,
                "Sandbox output was not a declared deliverable.",
            )
        try:
            files = await self._list_container_files()
        except Exception as exc:  # noqa: BLE001 - external SDK boundary
            raise SandboxError(
                SandboxErrorCode.SANDBOX_DOWNLOAD_FAILED,
                "The sandbox provider could not enumerate declared deliverables.",
            ) from exc
        assistant_files = {
            _required_string(item, "path"): item
            for item in files
            if _value(item, "source") == "assistant"
        }
        results: list[FileDownloadResponse] = []
        for path in normalized:
            expected_filename = self._deliverable_filename(path)
            matches = [
                item
                for provider_path, item in assistant_files.items()
                if provider_path.rsplit("/", maxsplit=1)[-1] == expected_filename
            ]
            if len(matches) != 1:
                results.append(
                    FileDownloadResponse(
                        path=path, content=None, error="declared_deliverable_missing"
                    )
                )
                continue
            try:
                raw_content = await self._client.containers.files.content.retrieve(
                    container_id=self._container_id,
                    file_id=_required_string(matches[0], "id"),
                )
                content = await _binary_content(raw_content)
            except Exception:
                results.append(
                    FileDownloadResponse(
                        path=path,
                        content=None,
                        error="declared_deliverable_unavailable",
                    )
                )
                continue
            results.append(FileDownloadResponse(path=path, content=content, error=None))
        return results

    def _input_filename(self, virtual_path: str) -> str:
        return self._provider_filename("input", virtual_path)

    def _deliverable_filename(self, virtual_path: str) -> str:
        return self._provider_filename("deliverable", virtual_path)

    @staticmethod
    def _provider_filename(kind: str, virtual_path: str) -> str:
        digest = hashlib.sha256(virtual_path.encode("utf-8")).hexdigest()[:20]
        # Do not place an untrusted logical basename in a multipart filename.
        # The canonical execution document carries the virtual-path mapping;
        # this filename is an opaque ASCII provider transport identifier only.
        return f"d3-{kind}-{digest}.bin"

    async def _execute_response(self, command: str) -> object:
        response = await self._client.responses.create(
            model=self._config.model,
            tools=[{"type": "code_interpreter", "container": self._container_id}],
            tool_choice="required",
            background=True,
            max_output_tokens=self._config.max_output_tokens,
            instructions=_CODE_INTERPRETER_INSTRUCTIONS,
            input=self._canonical_execution_input(command),
        )
        self._record_response(response)
        while True:
            status = str(_value(response, "status") or "completed")
            if status == "completed":
                return response
            if status in {"failed", "cancelled", "incomplete"}:
                raise SandboxError(
                    SandboxErrorCode.SANDBOX_EXECUTION_INDETERMINATE,
                    "The sandbox provider did not confirm command completion.",
                )
            response_id = _required_string(response, "id")
            await asyncio.sleep(0.05)
            response = await self._client.responses.retrieve(response_id)
            self._record_response(response)

    def _canonical_execution_input(self, command: str) -> str:
        deliverables = (
            self._execution_context.deliverables if self._execution_context else ()
        )
        document = {
            "v": 1,
            "command": command,
            "inputs": [
                {
                    "virtual_path": path,
                    "container_path": item.provider_path,
                }
                for path, item in sorted(self._input_files.items())
            ],
            "deliverables": [
                {
                    "virtual_path": path,
                    "container_filename": self._deliverable_filename(path),
                }
                for path in deliverables
            ],
        }
        return _CANONICAL_EXECUTION_PREFIX + canonical_json_bytes(document).decode(
            "utf-8"
        )

    def _record_response(self, response: object) -> None:
        self._response_id = _required_string(response, "id")
        request_id = _value(response, "_request_id")
        self._response_request_id = request_id if isinstance(request_id, str) else None

    async def _cancel_response(self) -> None:
        if self._response_id is None:
            return
        try:
            await self._client.responses.cancel(self._response_id)
        except Exception:  # noqa: BLE001 - teardown will still delete container
            return

    async def _list_container_files(self) -> tuple[object, ...]:
        after: str | None = None
        files: list[object] = []
        while True:
            page_arguments: dict[str, object] = {
                "container_id": self._container_id,
                "limit": 100,
            }
            if after is not None:
                page_arguments["after"] = after
            page = await self._client.containers.files.list(**page_arguments)
            data = _value(page, "data")
            if not isinstance(data, (list, tuple)):
                raise ValueError("container file listing was malformed")
            files.extend(data)
            if not _value(page, "has_more"):
                return tuple(files)
            after = _required_string(page, "last_id")


class OpenAIHostedContainerProvider:
    """D3 provider-port implementation using OpenAI hosted containers.

    The transport supports concrete container lifecycle, sealed byte transfer,
    Code Interpreter execution, declared output retrieval and cancellation.
    It is intentionally not a production-attested provider yet: no code path
    can register it as model-visible while the public API lacks D3's full
    isolation/quota proof surface.
    """

    def __init__(
        self,
        *,
        config: OpenAIHostedContainerConfig,
        client: OpenAIHostedContainerClient | None = None,
    ) -> None:
        self._config = config
        self._client = client
        self._by_idempotency: dict[str, SandboxHandle] = {}
        self._owner_by_ref: dict[str, str] = {}
        self._create_lock = asyncio.Lock()

    @property
    def isolation_ready(self) -> bool:
        """Fail closed until OpenAI can evidence every D3 attestation control."""

        return False

    @property
    def unavailability_reason(self) -> str:
        """Safe typed posture used by readiness reporting and operators."""

        return "openai_hosted_container_control_gap"

    async def attest(
        self, request: SandboxCreateRequest
    ) -> SandboxIsolationAttestation:
        """Refuse to turn documented partial controls into a D3 attestation."""

        del request
        raise SandboxError(
            SandboxErrorCode.SANDBOX_ISOLATION_UNVERIFIED,
            "OpenAI hosted containers cannot verify every required D3 isolation control.",
        )

    async def create(self, request: SandboxCreateRequest) -> SandboxHandle:
        """Create an explicitly network-constrained OpenAI container.

        This real SDK path is reachable only through direct integration tests
        while ``isolation_ready`` remains false; the registry never exposes it
        to a model or production D3 coordinator.
        """

        self._validate_request(request)
        async with self._create_lock:
            existing = self._by_idempotency.get(request.idempotency_key)
            if existing is not None:
                return existing
            owner_marker = self._owner_marker(request.owner_tag)
            try:
                created = await self._sdk_client().containers.create(
                    name=owner_marker,
                    expires_after={
                        "anchor": "last_active_at",
                        "minutes": self._config.container_ttl_minutes,
                    },
                    memory_limit=self._config.memory_limit,
                    network_policy=self._network_policy_payload(),
                )
                container_id = _required_string(created, "id")
                actual_policy = _provider_network_policy(created)
                actual_memory = _value(created, "memory_limit")
                actual_expiry_minutes = _provider_expiry_minutes(created)
                actual_name = _value(created, "name")
                if (
                    actual_policy != self._config.network_policy
                    or actual_memory != self._config.memory_limit
                    or actual_expiry_minutes != self._config.container_ttl_minutes
                    or actual_name != owner_marker
                ):
                    await self._delete_quietly(container_id)
                    raise ValueError(
                        "provider response did not match requested controls"
                    )
            except SandboxError:
                raise
            except Exception as exc:  # noqa: BLE001 - external SDK boundary
                raise SandboxError(
                    SandboxErrorCode.SANDBOX_PROVISION_FAILED,
                    "The sandbox provider could not provision a controlled container.",
                ) from exc
            now = _utcnow()
            request_id = _value(created, "_request_id")
            session = ManagedSandboxSession(
                session_id=request.operation_id,
                provider=SandboxProviderId.OPENAI_HOSTED_CONTAINER,
                provider_session_ref=container_id,
                owner_tag=request.owner_tag,
                created_at=now,
                expires_at=now + timedelta(minutes=actual_expiry_minutes),
                cleanup_state="active",
                provider_evidence=SandboxProviderEvidence(
                    provider=SandboxProviderId.OPENAI_HOSTED_CONTAINER,
                    resource_id=container_id,
                    owner_marker=owner_marker,
                    status=str(_value(created, "status") or "unknown"),
                    network_policy=actual_policy,
                    memory_limit=_optional_string(actual_memory),
                    expires_after_minutes=actual_expiry_minutes,
                    provider_request_id=request_id
                    if isinstance(request_id, str)
                    else None,
                ),
            )
            backend = OpenAIHostedContainerBackend(
                client=self._sdk_client(),
                config=self._config,
                container_id=container_id,
                owner_marker=owner_marker,
                container_request_id=request_id
                if isinstance(request_id, str)
                else None,
                network_policy=actual_policy,
                memory_limit=_optional_string(actual_memory),
            )
            handle = SandboxHandle(session=session, backend=backend)
            self._by_idempotency[request.idempotency_key] = handle
            self._owner_by_ref[container_id] = request.owner_tag
            return handle

    async def status(self, provider_session_ref: str) -> ManagedSandboxSession:
        """Map concrete container status to D3's durable session projection."""

        try:
            container = await self._sdk_client().containers.retrieve(
                provider_session_ref
            )
            actual_policy = _provider_network_policy(container)
            container_id = _required_string(container, "id")
            actual_memory = _optional_string(_value(container, "memory_limit"))
            actual_expiry_minutes = _provider_expiry_minutes(container)
            if (
                actual_policy != self._config.network_policy
                or actual_memory != self._config.memory_limit
                or actual_expiry_minutes != self._config.container_ttl_minutes
            ):
                raise ValueError("provider session no longer has required controls")
        except Exception as exc:  # noqa: BLE001 - do not expose SDK body/details
            raise SandboxError(
                SandboxErrorCode.SANDBOX_SESSION_EXPIRED,
                "The sandbox provider session is unavailable.",
            ) from exc
        now = _utcnow()
        status = str(_value(container, "status") or "unknown")
        return ManagedSandboxSession(
            session_id=provider_session_ref,
            provider=SandboxProviderId.OPENAI_HOSTED_CONTAINER,
            provider_session_ref=container_id,
            owner_tag=self._owner_by_ref.get(container_id, "openai-container"),
            created_at=now,
            expires_at=now + timedelta(minutes=actual_expiry_minutes),
            cleanup_state="deleted" if status == "deleted" else "active",
            provider_evidence=SandboxProviderEvidence(
                provider=SandboxProviderId.OPENAI_HOSTED_CONTAINER,
                resource_id=container_id,
                owner_marker=str(_value(container, "name") or "unknown"),
                status=status,
                network_policy=actual_policy,
                memory_limit=actual_memory,
                expires_after_minutes=actual_expiry_minutes,
                provider_request_id=(
                    _value(container, "_request_id")
                    if isinstance(_value(container, "_request_id"), str)
                    else None
                ),
            ),
        )

    async def terminate(self, provider_session_ref: str) -> None:
        """Delete the exact D3-authorized ref; only a confirmed 404 is a no-op.

        The file-native cleanup authority stores the opaque ref before asking
        this method to delete it.  A provider transport failure must propagate
        so that authority keeps its durable cleanup duty pending instead of
        falsely recording teardown as complete.
        """

        try:
            await self._sdk_client().containers.delete(provider_session_ref)
        except Exception as exc:  # noqa: BLE001 - external SDK boundary
            if _is_not_found(exc):
                return
            raise SandboxError(
                SandboxErrorCode.SANDBOX_CLEANUP_PENDING,
                "The sandbox provider could not confirm container deletion.",
            ) from exc

    async def list_owned_sessions(
        self, owner_tag: str
    ) -> tuple[ManagedSandboxSession, ...]:
        """List only containers carrying D3's deterministic owner marker."""

        marker = self._owner_marker(owner_tag)
        after: str | None = None
        sessions: list[ManagedSandboxSession] = []
        while True:
            try:
                page_arguments: dict[str, object] = {"name": marker, "limit": 100}
                if after is not None:
                    page_arguments["after"] = after
                page = await self._sdk_client().containers.list(**page_arguments)
                data = _value(page, "data")
                if not isinstance(data, (list, tuple)):
                    raise ValueError("container listing was malformed")
            except Exception as exc:  # noqa: BLE001 - external SDK boundary
                raise SandboxError(
                    SandboxErrorCode.SANDBOX_PROVIDER_UNCONFIGURED,
                    "The sandbox provider could not enumerate owned containers.",
                ) from exc
            for container in data:
                if _value(container, "name") != marker:
                    continue
                container_id = _required_string(container, "id")
                status = str(_value(container, "status") or "unknown")
                expiry_minutes = _maybe_provider_expiry_minutes(container)
                sessions.append(
                    ManagedSandboxSession(
                        session_id=container_id,
                        provider=SandboxProviderId.OPENAI_HOSTED_CONTAINER,
                        provider_session_ref=container_id,
                        owner_tag=owner_tag,
                        created_at=_utcnow(),
                        expires_at=_utcnow()
                        + timedelta(
                            minutes=expiry_minutes or self._config.container_ttl_minutes
                        ),
                        cleanup_state="deleted" if status == "deleted" else "active",
                        provider_evidence=SandboxProviderEvidence(
                            provider=SandboxProviderId.OPENAI_HOSTED_CONTAINER,
                            resource_id=container_id,
                            owner_marker=marker,
                            status=status,
                            network_policy=_provider_network_policy(container),
                            memory_limit=_optional_string(
                                _value(container, "memory_limit")
                            ),
                            expires_after_minutes=expiry_minutes,
                            provider_request_id=(
                                _value(container, "_request_id")
                                if isinstance(_value(container, "_request_id"), str)
                                else None
                            ),
                        ),
                    )
                )
            if not _value(page, "has_more"):
                return tuple(sessions)
            after = _required_string(page, "last_id")

    def _validate_request(self, request: SandboxCreateRequest) -> None:
        if request.secret_refs:
            raise SandboxError(
                SandboxErrorCode.SANDBOX_POLICY_UNSUPPORTED,
                "OpenAI hosted containers do not accept D3 secret leases.",
            )
        if request.egress != self._config.network_policy:
            raise SandboxError(
                SandboxErrorCode.SANDBOX_POLICY_UNSUPPORTED,
                "The requested egress policy is not the configured provider policy.",
            )

    @staticmethod
    def _owner_marker(owner_tag: str) -> str:
        return "d3-" + hashlib.sha256(owner_tag.encode("utf-8")).hexdigest()[:48]

    def _network_policy_payload(self) -> dict[str, object]:
        if self._config.network_policy.is_deny_all():
            return {"type": "disabled"}
        return {
            "type": "allowlist",
            "allowed_domains": list(self._config.network_policy.destinations),
        }

    def _sdk_client(self) -> OpenAIHostedContainerClient:
        if self._client is None:
            # Delayed import/client construction means startup/readiness never
            # reads a credential or contacts OpenAI.  Normal tests inject a
            # hermetic client and never take this branch.
            from openai import AsyncOpenAI  # allow-direct-llm-import: D3 API

            self._client = AsyncOpenAI()
        return self._client

    async def _delete_quietly(self, container_id: str) -> None:
        try:
            await self._sdk_client().containers.delete(container_id)
        except Exception:  # noqa: BLE001 - delete is deliberately idempotent
            return


def _value(value: object, name: str) -> object | None:
    if isinstance(value, Mapping):
        return value.get(name)
    return getattr(value, name, None)


def _required_string(value: object, name: str) -> str:
    candidate = _value(value, name)
    if not isinstance(candidate, str) or not candidate:
        raise ValueError(f"provider response missing {name}")
    return candidate


def _optional_string(value: object | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("provider response contained an invalid string value")
    return value


def _provider_network_policy(value: object) -> SandboxEgressPolicy:
    raw = _value(value, "network_policy")
    policy_type = _value(raw, "type")
    if policy_type == "disabled":
        return SandboxEgressPolicy(mode="deny_all")
    if policy_type == "allowlist":
        domains = _value(raw, "allowed_domains")
        if not isinstance(domains, (list, tuple)) or not all(
            isinstance(item, str) for item in domains
        ):
            raise ValueError("provider allowlist was malformed")
        return SandboxEgressPolicy(mode="allowlist", destinations=tuple(domains))
    raise ValueError("provider response omitted an enforceable network policy")


def _provider_expiry_minutes(value: object) -> int:
    raw_expiry = _value(value, "expires_after")
    if _value(raw_expiry, "anchor") != "last_active_at":
        raise ValueError("provider response omitted the required expiry anchor")
    minutes = _value(raw_expiry, "minutes")
    if isinstance(minutes, bool) or not isinstance(minutes, int) or minutes < 1:
        raise ValueError("provider response contained an invalid expiry duration")
    return minutes


def _maybe_provider_expiry_minutes(value: object) -> int | None:
    try:
        return _provider_expiry_minutes(value)
    except ValueError:
        return None


def _is_not_found(exc: Exception) -> bool:
    """Recognize an SDK 404 without depending on an SDK exception type."""

    return _value(exc, "status_code") == 404


def _response_output_text(response: object) -> str:
    output_text = _value(response, "output_text")
    if isinstance(output_text, str):
        return output_text
    output = _value(response, "output")
    if not isinstance(output, (list, tuple)):
        return ""
    text: list[str] = []
    for item in output:
        content = _value(item, "content")
        if not isinstance(content, (list, tuple)):
            continue
        for part in content:
            if _value(part, "type") != "output_text":
                continue
            value = _value(part, "text")
            if isinstance(value, str):
                text.append(value)
    return "".join(text)


async def _binary_content(value: object) -> bytes:
    """Normalize the SDK's binary wrapper without a host file hop."""

    if isinstance(value, bytes):
        return value
    content = _value(value, "content")
    if isinstance(content, bytes):
        return content
    reader = getattr(value, "read", None)
    if callable(reader):
        content = reader()
        if inspect.isawaitable(content):
            content = await content
        if isinstance(content, bytes):
            return content
    raise ValueError("container output was not bytes")


__all__ = (
    "OpenAIHostedContainerBackend",
    "OpenAIHostedContainerClient",
    "OpenAIHostedContainerExecutionEvidence",
    "OpenAIHostedContainerProvider",
)
