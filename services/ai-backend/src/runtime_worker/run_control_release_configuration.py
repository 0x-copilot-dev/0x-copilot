"""Bounded deployment-owned configuration for signed run-control releases."""

from __future__ import annotations

import base64
import binascii
import json
import os
from pathlib import Path
import stat
from typing import Literal

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import Field, field_validator, model_validator

from agent_runtime.execution.contracts import RuntimeContract
from agent_runtime.release.assignment import DevelopmentReleaseOverride
from agent_runtime.release.local_control import ReleaseControlProfile
from runtime_worker.run_control import RunControlAssignment


class RunControlReleaseConfigurationError(RuntimeError):
    """The immutable release configuration file is absent or malformed."""


class RunControlReleaseVerificationKey(RuntimeContract):
    key_id: str = Field(min_length=1, max_length=160)
    public_key_b64: str = Field(min_length=40, max_length=128)

    def public_key(self) -> Ed25519PublicKey:
        try:
            raw = base64.b64decode(self.public_key_b64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise RunControlReleaseConfigurationError(
                "release verification key encoding is invalid"
            ) from exc
        if len(raw) != 32 or base64.b64encode(raw).decode("ascii") != (
            self.public_key_b64
        ):
            raise RunControlReleaseConfigurationError(
                "release verification key must be canonical raw Ed25519"
            )
        try:
            return Ed25519PublicKey.from_public_bytes(raw)
        except ValueError as exc:
            raise RunControlReleaseConfigurationError(
                "release verification key is invalid"
            ) from exc


class RunControlReleaseDeploymentConfiguration(RuntimeContract):
    """Public keys and complete variant catalog shipped by the deployment."""

    schema_version: Literal[1] = 1
    release_profile: ReleaseControlProfile = ReleaseControlProfile.PRODUCTION
    verification_keys: tuple[RunControlReleaseVerificationKey, ...] = ()
    assignments: tuple[RunControlAssignment, ...] = ()
    development_override: DevelopmentReleaseOverride | None = None

    @field_validator("verification_keys")
    @classmethod
    def _verification_keys_are_unique_and_sorted(
        cls,
        value: tuple[RunControlReleaseVerificationKey, ...],
    ) -> tuple[RunControlReleaseVerificationKey, ...]:
        identities = tuple(item.key_id for item in value)
        if identities != tuple(sorted(set(identities))):
            raise ValueError("release verification keys must be unique and sorted")
        return value

    @field_validator("assignments")
    @classmethod
    def _assignments_are_unique_and_sorted(
        cls,
        value: tuple[RunControlAssignment, ...],
    ) -> tuple[RunControlAssignment, ...]:
        refs = tuple(item.harness_variant_ref for item in value)
        if refs != tuple(sorted(set(refs))):
            raise ValueError("release assignments must be unique and sorted")
        return value

    @model_validator(mode="after")
    def _override_matches_release_profile(
        self,
    ) -> "RunControlReleaseDeploymentConfiguration":
        override = self.development_override
        if override is not None and override.profile != self.release_profile.value:
            raise ValueError("development override profile must match release profile")
        if (
            override is not None
            and self.release_profile is ReleaseControlProfile.PRODUCTION
        ):
            raise ValueError("production release configuration cannot override")
        return self

    def verification_key_map(self) -> dict[str, Ed25519PublicKey]:
        return {item.key_id: item.public_key() for item in self.verification_keys}

    def assignment_catalog(self) -> dict[str, RunControlAssignment]:
        return {item.harness_variant_ref: item for item in self.assignments}


def load_run_control_release_configuration(
    path_value: str | Path,
    *,
    maximum_bytes: int = 1_048_576,
) -> RunControlReleaseDeploymentConfiguration:
    """Load one explicit regular file without following a mutable symlink."""

    path = Path(path_value)
    if not path.is_absolute() or path == Path("/"):
        raise RunControlReleaseConfigurationError(
            "release configuration path must be explicit and absolute"
        )
    encoded = _read_regular_file_without_following_symlink(
        path,
        maximum_bytes=maximum_bytes,
    )
    try:
        payload = json.loads(encoded)
        configuration = RunControlReleaseDeploymentConfiguration.model_validate(payload)
        # Decode every public key at startup, even before a manifest references it.
        configuration.verification_key_map()
        return configuration
    except RunControlReleaseConfigurationError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise RunControlReleaseConfigurationError(
            "release configuration content is invalid"
        ) from exc


def _read_regular_file_without_following_symlink(
    path: Path,
    *,
    maximum_bytes: int,
) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = -1
    try:
        descriptor = os.open(path, flags)
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise RunControlReleaseConfigurationError(
                "release configuration must be a regular non-symlink file"
            )
        if metadata.st_size <= 0 or metadata.st_size > maximum_bytes:
            raise RunControlReleaseConfigurationError(
                "release configuration file is empty or exceeds its byte limit"
            )
        chunks: list[bytes] = []
        remaining = maximum_bytes + 1
        while remaining > 0:
            chunk = os.read(descriptor, min(64 * 1_024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        encoded = b"".join(chunks)
        if not encoded or len(encoded) > maximum_bytes:
            raise RunControlReleaseConfigurationError(
                "release configuration file is empty or exceeds its byte limit"
            )
        return encoded
    except RunControlReleaseConfigurationError:
        raise
    except OSError as exc:
        raise RunControlReleaseConfigurationError(
            "release configuration must be a regular non-symlink file"
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


__all__ = (
    "RunControlReleaseConfigurationError",
    "RunControlReleaseDeploymentConfiguration",
    "RunControlReleaseVerificationKey",
    "load_run_control_release_configuration",
)
