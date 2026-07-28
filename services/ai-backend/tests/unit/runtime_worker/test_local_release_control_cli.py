from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path

import httpx
import pytest

from agent_runtime.harness_quality.evaluation_contracts import (
    HarnessManifest,
    HarnessManifestPointer,
)
from agent_runtime.surfaces_v2.canonical_json import canonical_json_sha256
from runtime_worker.local_release_control_cli import (
    LocalReleaseControlCliError,
    LocalReleaseExportResult,
    LocalReleaseVerifyResult,
    execute_local_release_control_cli,
)


_TOKEN = "explicit-local-service-token"
_TOKEN_ENV = "TEST_LOCAL_RELEASE_TOKEN"
_BASE_ARGS = (
    "--base-url",
    "http://127.0.0.1:8000",
    "--service-token-env",
    _TOKEN_ENV,
)


def _manifest() -> HarnessManifest:
    now = datetime.now(timezone.utc)
    signed_payload: dict[str, object] = {
        "schema_version": 1,
        "manifest_id": "manifest-cli-1",
        "revision": "release-r1",
        "assignments": [
            {
                "variant_ref": "harness://control-r1",
                "variant_digest": "a" * 64,
                "allocation_basis_points": 10_000,
            }
        ],
        "fallback_variant_ref": "harness://control-r1",
        "assignment_revision": "assignment-r1",
        "source_report_ref": "paired-report://report-r1",
        "previous_manifest_ref": None,
        "issued_at": (now - timedelta(minutes=2)).isoformat().replace("+00:00", "Z"),
        "not_before": (now - timedelta(minutes=1)).isoformat().replace("+00:00", "Z"),
        "expires_at": (now + timedelta(days=1)).isoformat().replace("+00:00", "Z"),
        "key_id": "release-key-1",
        "signature_algorithm": "ed25519",
    }
    return HarnessManifest(
        **signed_payload,
        payload_digest=canonical_json_sha256(signed_payload),
        signature_b64=base64.b64encode(b"x" * 64).decode("ascii"),
    )


def _pointer() -> HarnessManifestPointer:
    now = datetime.now(timezone.utc)
    values: dict[str, object] = {
        "pointer_version": 1,
        "manifest_id": "manifest-cli-1",
        "manifest_revision": "release-r1",
        "manifest_payload_digest": _manifest().payload_digest,
        "activation_decision_id": "decision-cli-1",
        "previous_manifest_ref": None,
        "updated_at": now,
    }
    return HarnessManifestPointer(
        **values,
        pointer_digest=HarnessManifestPointer.digest_for(**values),
    )


def _write_manifest(path: Path) -> None:
    path.write_text(_manifest().model_dump_json(), encoding="utf-8")


@pytest.mark.asyncio
async def test_verify_reads_bounded_manifest_and_sends_authenticated_request(
    tmp_path: Path,
) -> None:
    manifest_path = tmp_path / "manifest.json"
    _write_manifest(manifest_path)
    observed: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        observed.append(request)
        request_manifest = HarnessManifest.model_validate_json(request.content)
        return httpx.Response(
            200,
            json={
                "manifest_ref": request_manifest.manifest_ref,
                "verification_digest": "b" * 64,
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await execute_local_release_control_cli(
            (*_BASE_ARGS, "verify", "--manifest-path", str(manifest_path)),
            environ={_TOKEN_ENV: _TOKEN},
            http_client=client,
        )

    assert isinstance(result, LocalReleaseVerifyResult)
    assert (
        result.manifest_ref
        == HarnessManifest.model_validate_json(manifest_path.read_bytes()).manifest_ref
    )
    assert len(observed) == 1
    assert observed[0].url == (
        "http://127.0.0.1:8000/internal/dev/evaluation/releases/verify"
    )
    assert observed[0].headers["x-enterprise-service-token"] == _TOKEN


@pytest.mark.asyncio
@pytest.mark.parametrize("command", ["install", "rollback"])
async def test_mutation_commands_use_server_bound_scope_and_explicit_decision(
    command: str,
    tmp_path: Path,
) -> None:
    manifest_path = tmp_path / "manifest.json"
    _write_manifest(manifest_path)
    observed_payloads: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        observed_payloads.append(json.loads(request.content))
        return httpx.Response(
            200,
            json=_pointer().model_dump(mode="json"),
            headers={"x-runtime-restart-required": "true"},
        )

    command_args: tuple[str, ...]
    if command == "install":
        command_args = (
            "install",
            "--manifest-path",
            str(manifest_path),
            "--activation-decision-id",
            "decision-cli-1",
        )
    else:
        command_args = (
            "rollback",
            "--target-manifest-id",
            "manifest-cli-1",
            "--target-manifest-revision",
            "release-r1",
            "--activation-decision-id",
            "decision-cli-1",
            "--rationale",
            "restore verified predecessor",
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await execute_local_release_control_cli(
            (*_BASE_ARGS, *command_args),
            environ={_TOKEN_ENV: _TOKEN},
            http_client=client,
        )

    assert isinstance(result, HarnessManifestPointer)
    assert "scope" not in observed_payloads[0]
    assert observed_payloads[0]["activation_decision_id"] == "decision-cli-1"
    if command == "rollback":
        assert observed_payloads[0]["rationale"] == "restore verified predecessor"
    else:
        assert "manifest" in observed_payloads[0]


@pytest.mark.asyncio
async def test_mutation_rejects_server_that_omits_restart_contract(
    tmp_path: Path,
) -> None:
    manifest_path = tmp_path / "manifest.json"
    _write_manifest(manifest_path)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_pointer().model_dump(mode="json"))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(
            LocalReleaseControlCliError,
            match="restart requirement",
        ):
            await execute_local_release_control_cli(
                (
                    *_BASE_ARGS,
                    "install",
                    "--manifest-path",
                    str(manifest_path),
                    "--activation-decision-id",
                    "decision-cli-1",
                ),
                environ={_TOKEN_ENV: _TOKEN},
                http_client=client,
            )


@pytest.mark.asyncio
async def test_export_is_digest_verified_and_created_atomically(
    tmp_path: Path,
) -> None:
    output_path = tmp_path / "evaluation-export.json"
    export_payload = b'{"schema_version":1,"records":[]}'
    digest = hashlib.sha256(export_payload).hexdigest()

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=export_payload,
            headers={
                "content-type": ("application/vnd.0xcopilot.evaluation-export+json"),
                "x-content-sha256": digest,
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await execute_local_release_control_cli(
            (
                *_BASE_ARGS,
                "export",
                "--output-path",
                str(output_path),
            ),
            environ={_TOKEN_ENV: _TOKEN},
            http_client=client,
        )

    assert isinstance(result, LocalReleaseExportResult)
    assert result.payload_digest == digest
    assert result.size == len(export_payload)
    assert output_path.read_bytes() == export_payload
    assert output_path.stat().st_mode & 0o777 == 0o600


@pytest.mark.asyncio
async def test_export_never_overwrites_an_existing_path(tmp_path: Path) -> None:
    output_path = tmp_path / "evaluation-export.json"
    output_path.write_bytes(b"owned")
    export_payload = b'{"records":[]}'

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=export_payload,
            headers={
                "content-type": ("application/vnd.0xcopilot.evaluation-export+json"),
                "x-content-sha256": hashlib.sha256(export_payload).hexdigest(),
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(
            LocalReleaseControlCliError,
            match="already exists",
        ):
            await execute_local_release_control_cli(
                (
                    *_BASE_ARGS,
                    "export",
                    "--output-path",
                    str(output_path),
                ),
                environ={_TOKEN_ENV: _TOKEN},
                http_client=client,
            )

    assert output_path.read_bytes() == b"owned"


@pytest.mark.asyncio
async def test_manifest_and_export_paths_reject_symlink_traversal(
    tmp_path: Path,
) -> None:
    real_directory = tmp_path / "real"
    real_directory.mkdir()
    manifest_path = real_directory / "manifest.json"
    _write_manifest(manifest_path)
    linked_directory = tmp_path / "linked"
    linked_directory.symlink_to(real_directory, target_is_directory=True)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(500),
        )
    ) as client:
        with pytest.raises(LocalReleaseControlCliError, match="contains a symlink"):
            await execute_local_release_control_cli(
                (
                    *_BASE_ARGS,
                    "verify",
                    "--manifest-path",
                    str(linked_directory / "manifest.json"),
                ),
                environ={_TOKEN_ENV: _TOKEN},
                http_client=client,
            )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "base_url",
    [
        "http://localhost:8000",
        "http://192.0.2.10:8000",
        "https://127.0.0.1:8000",
        "http://127.0.0.1:8000/path",
        "http://user@127.0.0.1:8000",
        "http://127.0.0.1",
    ],
)
async def test_client_rejects_every_non_literal_or_ambiguous_origin(
    base_url: str,
    tmp_path: Path,
) -> None:
    manifest_path = tmp_path / "manifest.json"
    _write_manifest(manifest_path)

    with pytest.raises(LocalReleaseControlCliError, match="base URL"):
        await execute_local_release_control_cli(
            (
                "--base-url",
                base_url,
                "--service-token-env",
                _TOKEN_ENV,
                "verify",
                "--manifest-path",
                str(manifest_path),
            ),
            environ={_TOKEN_ENV: _TOKEN},
        )


@pytest.mark.asyncio
async def test_cli_requires_explicit_nonempty_service_token_env(
    tmp_path: Path,
) -> None:
    manifest_path = tmp_path / "manifest.json"
    _write_manifest(manifest_path)

    with pytest.raises(LocalReleaseControlCliError, match="environment variable"):
        await execute_local_release_control_cli(
            (*_BASE_ARGS, "verify", "--manifest-path", str(manifest_path)),
            environ={},
        )


@pytest.mark.asyncio
async def test_redirect_and_export_digest_mismatch_fail_closed(
    tmp_path: Path,
) -> None:
    output_path = tmp_path / "export.json"

    def redirect(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(307, headers={"location": "http://example.com/"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(redirect)) as client:
        with pytest.raises(LocalReleaseControlCliError, match="HTTP 307"):
            await execute_local_release_control_cli(
                (
                    *_BASE_ARGS,
                    "export",
                    "--output-path",
                    str(output_path),
                ),
                environ={_TOKEN_ENV: _TOKEN},
                http_client=client,
            )

    payload = b'{"records":[]}'

    def corrupt(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=payload,
            headers={
                "content-type": ("application/vnd.0xcopilot.evaluation-export+json"),
                "x-content-sha256": "0" * 64,
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(corrupt)) as client:
        with pytest.raises(LocalReleaseControlCliError, match="digest"):
            await execute_local_release_control_cli(
                (
                    *_BASE_ARGS,
                    "export",
                    "--output-path",
                    str(output_path),
                ),
                environ={_TOKEN_ENV: _TOKEN},
                http_client=client,
            )
    assert not output_path.exists()


@pytest.mark.asyncio
async def test_response_byte_ceiling_is_enforced_before_json_parse(
    tmp_path: Path,
) -> None:
    manifest_path = tmp_path / "manifest.json"
    _write_manifest(manifest_path)

    def oversized(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"x",
            headers={
                "content-type": "application/json",
                "content-length": str(1_048_577),
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(oversized)) as client:
        with pytest.raises(LocalReleaseControlCliError, match="byte limit"):
            await execute_local_release_control_cli(
                (*_BASE_ARGS, "verify", "--manifest-path", str(manifest_path)),
                environ={_TOKEN_ENV: _TOKEN},
                http_client=client,
            )
