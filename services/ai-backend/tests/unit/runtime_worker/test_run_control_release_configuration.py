from __future__ import annotations

import base64
import json

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from runtime_worker.run_control import RunControlAssignment
from runtime_worker.run_control_release_configuration import (
    RunControlReleaseConfigurationError,
    load_run_control_release_configuration,
)


def _payload() -> dict[str, object]:
    public_key = (
        Ed25519PrivateKey.generate()
        .public_key()
        .public_bytes(
            Encoding.Raw,
            PublicFormat.Raw,
        )
    )
    assignment = RunControlAssignment.safe_active_v1()
    return {
        "schema_version": 1,
        "release_profile": "production",
        "verification_keys": [
            {
                "key_id": "release-key-v1",
                "public_key_b64": base64.b64encode(public_key).decode("ascii"),
            }
        ],
        "assignments": [assignment.model_dump(mode="json")],
        "development_override": None,
    }


def test_configuration_loads_canonical_public_keys_and_assignment_catalog(
    tmp_path,
) -> None:
    path = tmp_path / "release.json"
    path.write_text(json.dumps(_payload()), encoding="utf-8")

    configuration = load_run_control_release_configuration(path)

    assert tuple(configuration.verification_key_map()) == ("release-key-v1",)
    assert tuple(configuration.assignment_catalog()) == (
        RunControlAssignment.safe_active_v1().harness_variant_ref,
    )


def test_configuration_rejects_symlink_oversize_and_noncanonical_key(
    tmp_path,
) -> None:
    target = tmp_path / "release.json"
    target.write_text(json.dumps(_payload()), encoding="utf-8")
    link = tmp_path / "release-link.json"
    link.symlink_to(target)
    with pytest.raises(RunControlReleaseConfigurationError, match="non-symlink"):
        load_run_control_release_configuration(link)

    with pytest.raises(RunControlReleaseConfigurationError, match="byte limit"):
        load_run_control_release_configuration(target, maximum_bytes=1)

    invalid = _payload()
    invalid["verification_keys"][0]["public_key_b64"] = base64.b64encode(
        b"too-short"
    ).decode("ascii")
    target.write_text(json.dumps(invalid), encoding="utf-8")
    with pytest.raises(RunControlReleaseConfigurationError, match="invalid"):
        load_run_control_release_configuration(target)


def test_configuration_rejects_production_development_override(tmp_path) -> None:
    payload = _payload()
    payload["development_override"] = {
        "profile": "development",
        "explicitly_enabled": True,
        "variant_ref": "harness://active-safe-v1",
        "rationale": "local canary",
    }
    path = tmp_path / "release.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(RunControlReleaseConfigurationError, match="invalid"):
        load_run_control_release_configuration(path)
