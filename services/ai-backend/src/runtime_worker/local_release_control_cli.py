"""Fail-closed CLI for the development/dogfood release-control API.

This module is deliberately only a client. It cannot sign manifests, compute a
promotion decision, or access an evaluation repository directly. The runtime
API remains the sole policy and persistence boundary.

Example::

    python -m runtime_worker.local_release_control_cli \
        --base-url http://127.0.0.1:8000 \
        --service-token-env ENTERPRISE_SERVICE_TOKEN \
        verify --manifest-path /absolute/path/manifest.json
"""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Mapping, Sequence
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import stat
import sys
from typing import Annotated, Any
from urllib.parse import urlsplit, urlunsplit

import httpx
from pydantic import Field

from agent_runtime.execution.contracts import RuntimeContract
from agent_runtime.harness_quality.evaluation_contracts import (
    HarnessManifest,
    HarnessManifestPointer,
)
from agent_runtime.release.local_control import (
    LocalReleaseControlPolicy,
    ReleaseControlError,
    ReleaseControlProfile,
)


_API_PREFIX = "/internal/dev/evaluation/releases"
_SERVICE_TOKEN_HEADER = "x-enterprise-service-token"
_MAX_MANIFEST_BYTES = 1_048_576
_MAX_JSON_RESPONSE_BYTES = 1_048_576
_MAX_EXPORT_BYTES = 32 * 1_048_576
_MAX_PATH_LENGTH = 4_096
_MAX_TOKEN_BYTES = 16_384
_READ_CHUNK_BYTES = 64 * 1_024


class LocalReleaseControlCliError(RuntimeError):
    """A CLI input, transport, response, or local file violated the contract."""


class LocalReleaseVerifyResult(RuntimeContract):
    """Bounded response returned by the verify endpoint."""

    manifest_ref: str = Field(min_length=1, max_length=512)
    verification_digest: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]


class LocalReleaseExportResult(RuntimeContract):
    """Metadata for an export written atomically by the CLI."""

    output_path: str = Field(min_length=1, max_length=_MAX_PATH_LENGTH)
    payload_digest: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    size: int = Field(ge=0, le=_MAX_EXPORT_BYTES)


class LocalReleaseControlHttpClient:
    """Authenticated, redirect-free client pinned to one literal loopback URL."""

    def __init__(
        self,
        *,
        base_url: str,
        service_token: str,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = _validated_loopback_base_url(base_url)
        self._service_token = _validated_service_token(service_token)
        self._owns_client = http_client is None
        self._http_client = http_client or httpx.AsyncClient(
            trust_env=False,
            follow_redirects=False,
            timeout=httpx.Timeout(5.0),
            limits=httpx.Limits(max_connections=2, max_keepalive_connections=1),
        )

    async def __aenter__(self) -> "LocalReleaseControlHttpClient":
        return self

    async def __aexit__(
        self,
        _exc_type: object,
        _exc: object,
        _traceback: object,
    ) -> None:
        if self._owns_client:
            await self._http_client.aclose()

    async def verify(self, manifest: HarnessManifest) -> LocalReleaseVerifyResult:
        payload, _headers = await self._post_bounded(
            "/verify",
            manifest.model_dump(mode="json"),
            maximum_bytes=_MAX_JSON_RESPONSE_BYTES,
            expected_media_type="application/json",
        )
        return _validate_json_response(payload, LocalReleaseVerifyResult)

    async def install(
        self,
        *,
        manifest: HarnessManifest,
        activation_decision_id: str,
    ) -> HarnessManifestPointer:
        payload, headers = await self._post_bounded(
            "/install",
            {
                "manifest": manifest.model_dump(mode="json"),
                "activation_decision_id": activation_decision_id,
            },
            maximum_bytes=_MAX_JSON_RESPONSE_BYTES,
            expected_media_type="application/json",
        )
        _require_restart_contract(headers)
        return _validate_json_response(payload, HarnessManifestPointer)

    async def rollback(
        self,
        *,
        target_manifest_id: str,
        target_manifest_revision: str,
        activation_decision_id: str,
        rationale: str,
    ) -> HarnessManifestPointer:
        payload, headers = await self._post_bounded(
            "/rollback",
            {
                "target_manifest_id": target_manifest_id,
                "target_manifest_revision": target_manifest_revision,
                "activation_decision_id": activation_decision_id,
                "rationale": rationale,
            },
            maximum_bytes=_MAX_JSON_RESPONSE_BYTES,
            expected_media_type="application/json",
        )
        _require_restart_contract(headers)
        return _validate_json_response(payload, HarnessManifestPointer)

    async def export(self) -> tuple[bytes, str]:
        payload, headers = await self._post_bounded(
            "/export",
            {},
            maximum_bytes=_MAX_EXPORT_BYTES,
            expected_media_type="application/vnd.0xcopilot.evaluation-export+json",
        )
        supplied_digest = headers.get("x-content-sha256", "")
        actual_digest = hashlib.sha256(payload).hexdigest()
        if not hmac.compare_digest(supplied_digest, actual_digest):
            raise LocalReleaseControlCliError(
                "release-control export digest is absent or invalid"
            )
        return payload, actual_digest

    async def _post_bounded(
        self,
        route: str,
        json_payload: object,
        *,
        maximum_bytes: int,
        expected_media_type: str,
    ) -> tuple[bytes, Mapping[str, str]]:
        url = f"{self._base_url}{_API_PREFIX}{route}"
        try:
            async with self._http_client.stream(
                "POST",
                url,
                headers={_SERVICE_TOKEN_HEADER: self._service_token},
                json=json_payload,
                follow_redirects=False,
            ) as response:
                if response.status_code != 200:
                    raise LocalReleaseControlCliError(
                        "release-control API rejected the request "
                        f"(HTTP {response.status_code})"
                    )
                _require_media_type(response, expected_media_type)
                _reject_oversized_content_length(response, maximum_bytes)
                body = bytearray()
                async for chunk in response.aiter_bytes(_READ_CHUNK_BYTES):
                    body.extend(chunk)
                    if len(body) > maximum_bytes:
                        raise LocalReleaseControlCliError(
                            "release-control API response exceeds its byte limit"
                        )
                return bytes(body), dict(response.headers)
        except LocalReleaseControlCliError:
            raise
        except httpx.HTTPError as exc:
            raise LocalReleaseControlCliError(
                "release-control API is unavailable"
            ) from exc


async def execute_local_release_control_cli(
    argv: Sequence[str],
    *,
    environ: Mapping[str, str] | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> RuntimeContract:
    """Parse and execute one CLI command, returning content-free result metadata."""

    args = _build_argument_parser().parse_args(tuple(argv))
    environment = os.environ if environ is None else environ
    token = environment.get(args.service_token_env, "")
    if not token:
        raise LocalReleaseControlCliError(
            "the configured service-token environment variable is empty"
        )

    async with LocalReleaseControlHttpClient(
        base_url=args.base_url,
        service_token=token,
        http_client=http_client,
    ) as client:
        if args.command == "verify":
            return await client.verify(_load_manifest(args.manifest_path))
        if args.command == "install":
            return await client.install(
                manifest=_load_manifest(args.manifest_path),
                activation_decision_id=args.activation_decision_id,
            )
        if args.command == "rollback":
            return await client.rollback(
                target_manifest_id=args.target_manifest_id,
                target_manifest_revision=args.target_manifest_revision,
                activation_decision_id=args.activation_decision_id,
                rationale=args.rationale,
            )
        if args.command == "export":
            payload, digest = await client.export()
            output_path = _write_new_file_atomically(args.output_path, payload)
            return LocalReleaseExportResult(
                output_path=str(output_path),
                payload_digest=digest,
                size=len(payload),
            )
    raise LocalReleaseControlCliError("unsupported release-control command")


def main(argv: Sequence[str] | None = None) -> int:
    """Run the executable CLI without ever printing tokens or export payloads."""

    try:
        result = asyncio.run(
            execute_local_release_control_cli(
                sys.argv[1:] if argv is None else argv,
            )
        )
    except (LocalReleaseControlCliError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            result.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


def _build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m runtime_worker.local_release_control_cli",
        description=(
            "Verify, install, roll back, or export a local harness release through "
            "the authenticated loopback-only runtime API."
        ),
    )
    parser.add_argument(
        "--base-url",
        required=True,
        help="Explicit HTTP URL with a literal loopback IP and port.",
    )
    parser.add_argument(
        "--service-token-env",
        required=True,
        help="Name of the environment variable containing the service token.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    verify = commands.add_parser("verify")
    verify.add_argument("--manifest-path", required=True)

    install = commands.add_parser("install")
    install.add_argument("--manifest-path", required=True)
    install.add_argument("--activation-decision-id", required=True)

    rollback = commands.add_parser("rollback")
    rollback.add_argument("--target-manifest-id", required=True)
    rollback.add_argument("--target-manifest-revision", required=True)
    rollback.add_argument("--activation-decision-id", required=True)
    rollback.add_argument("--rationale", required=True)

    export = commands.add_parser("export")
    export.add_argument("--output-path", required=True)
    return parser


def _validated_loopback_base_url(value: str) -> str:
    if len(value) > 512:
        raise LocalReleaseControlCliError("release-control base URL is too long")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise LocalReleaseControlCliError(
            "release-control base URL is invalid"
        ) from exc
    if (
        parsed.scheme != "http"
        or parsed.hostname is None
        or port is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise LocalReleaseControlCliError(
            "release-control base URL must be an explicit loopback HTTP origin"
        )
    try:
        policy = LocalReleaseControlPolicy(
            profile=ReleaseControlProfile.DEVELOPMENT,
            explicitly_enabled=True,
            bind_host=parsed.hostname,
        )
        policy.authorize_peer(parsed.hostname)
    except (ReleaseControlError, ValueError) as exc:
        raise LocalReleaseControlCliError(
            "release-control base URL must use a literal loopback IP"
        ) from exc
    return urlunsplit(("http", parsed.netloc, "", "", ""))


def _validated_service_token(value: str) -> str:
    encoded = value.encode("utf-8")
    if (
        not value
        or value != value.strip()
        or len(encoded) > _MAX_TOKEN_BYTES
        or "\r" in value
        or "\n" in value
    ):
        raise LocalReleaseControlCliError("service token is invalid")
    return value


def _load_manifest(path_value: str) -> HarnessManifest:
    payload = _read_bounded_regular_file(
        path_value,
        maximum_bytes=_MAX_MANIFEST_BYTES,
    )
    try:
        return HarnessManifest.model_validate_json(payload)
    except ValueError as exc:
        raise LocalReleaseControlCliError("manifest content is invalid") from exc


def _read_bounded_regular_file(path_value: str, *, maximum_bytes: int) -> bytes:
    path, parent_fd = _open_safe_parent(path_value)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    file_fd = -1
    try:
        file_fd = os.open(path.name, flags, dir_fd=parent_fd)
        metadata = os.fstat(file_fd)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_size <= 0
            or metadata.st_size > maximum_bytes
        ):
            raise LocalReleaseControlCliError(
                "input file is empty, non-regular, or exceeds its byte limit"
            )
        body = bytearray()
        while True:
            chunk = os.read(file_fd, min(_READ_CHUNK_BYTES, maximum_bytes + 1))
            if not chunk:
                break
            body.extend(chunk)
            if len(body) > maximum_bytes:
                raise LocalReleaseControlCliError("input file exceeds its byte limit")
        return bytes(body)
    except LocalReleaseControlCliError:
        raise
    except OSError as exc:
        raise LocalReleaseControlCliError("input file is unavailable") from exc
    finally:
        if file_fd >= 0:
            os.close(file_fd)
        os.close(parent_fd)


def _write_new_file_atomically(path_value: str, payload: bytes) -> Path:
    if len(payload) > _MAX_EXPORT_BYTES:
        raise LocalReleaseControlCliError("export exceeds its byte limit")
    path, parent_fd = _open_safe_parent(path_value)
    temporary_name = f".release-export-{secrets.token_hex(16)}"
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    file_fd = -1
    linked = False
    try:
        file_fd = os.open(temporary_name, flags, 0o600, dir_fd=parent_fd)
        view = memoryview(payload)
        written = 0
        while written < len(view):
            count = os.write(file_fd, view[written:])
            if count <= 0:
                raise LocalReleaseControlCliError("export write did not progress")
            written += count
        os.fsync(file_fd)
        os.close(file_fd)
        file_fd = -1
        os.link(
            temporary_name,
            path.name,
            src_dir_fd=parent_fd,
            dst_dir_fd=parent_fd,
            follow_symlinks=False,
        )
        linked = True
        os.fsync(parent_fd)
        return path
    except LocalReleaseControlCliError:
        raise
    except FileExistsError as exc:
        raise LocalReleaseControlCliError("export output path already exists") from exc
    except OSError as exc:
        raise LocalReleaseControlCliError("export could not be written") from exc
    finally:
        if file_fd >= 0:
            os.close(file_fd)
        try:
            os.unlink(temporary_name, dir_fd=parent_fd)
        except FileNotFoundError:
            pass
        except OSError:
            if not linked:
                pass
        os.close(parent_fd)


def _open_safe_parent(path_value: str) -> tuple[Path, int]:
    if not path_value or len(path_value) > _MAX_PATH_LENGTH:
        raise LocalReleaseControlCliError("file path is absent or too long")
    path = Path(path_value)
    parts = path.parts
    if (
        not path.is_absolute()
        or path == Path("/")
        or not path.name
        or any(part in {"", ".", ".."} for part in parts[1:])
    ):
        raise LocalReleaseControlCliError(
            "file path must be explicit, absolute, and normalized"
        )
    directory_flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    current_fd = -1
    try:
        current_fd = os.open("/", directory_flags)
        for component in parts[1:-1]:
            next_fd = os.open(component, directory_flags, dir_fd=current_fd)
            os.close(current_fd)
            current_fd = next_fd
        return path, current_fd
    except OSError as exc:
        if current_fd >= 0:
            os.close(current_fd)
        raise LocalReleaseControlCliError(
            "file path parent is unavailable or contains a symlink"
        ) from exc


def _require_media_type(response: httpx.Response, expected: str) -> None:
    media_type = response.headers.get("content-type", "").split(";", 1)[0].strip()
    if media_type != expected:
        raise LocalReleaseControlCliError(
            "release-control API returned an unexpected media type"
        )


def _require_restart_contract(headers: Mapping[str, str]) -> None:
    if headers.get("x-runtime-restart-required", "").lower() != "true":
        raise LocalReleaseControlCliError(
            "release-control mutation response omitted the restart requirement"
        )


def _reject_oversized_content_length(
    response: httpx.Response,
    maximum_bytes: int,
) -> None:
    raw_length = response.headers.get("content-length")
    if raw_length is None:
        return
    try:
        content_length = int(raw_length)
    except ValueError as exc:
        raise LocalReleaseControlCliError(
            "release-control API returned an invalid content length"
        ) from exc
    if content_length < 0 or content_length > maximum_bytes:
        raise LocalReleaseControlCliError(
            "release-control API response exceeds its byte limit"
        )


def _validate_json_response(
    payload: bytes,
    model_type: type[RuntimeContract],
) -> Any:
    try:
        return model_type.model_validate_json(payload)
    except ValueError as exc:
        raise LocalReleaseControlCliError(
            "release-control API returned an invalid response"
        ) from exc


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = (
    "LocalReleaseControlCliError",
    "LocalReleaseControlHttpClient",
    "LocalReleaseExportResult",
    "LocalReleaseVerifyResult",
    "execute_local_release_control_cli",
    "main",
)
