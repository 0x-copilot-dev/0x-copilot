"""Development-only local release-control policy and CLI contracts."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Sequence

from pydantic import Field, model_validator

from agent_runtime.api.loopback import is_literal_loopback
from agent_runtime.execution.contracts import RuntimeContract


class ReleaseControlError(RuntimeError):
    """A local release-control request is outside its trusted boundary."""


class ReleaseControlProfile(StrEnum):
    """Deployment profiles relevant to local release control."""

    DEVELOPMENT = "development"
    DOGFOOD = "dogfood"
    PRODUCTION = "production"


class ReleaseControlCommandName(StrEnum):
    """Supported local control commands.

    None of these commands signs a manifest or computes a production
    promotion. Promotion evidence and signatures are build/deployment inputs.
    """

    VERIFY = "verify"
    EXPORT = "export"
    OVERRIDE = "override"
    ROLLBACK = "rollback"


class ReleaseControlCommand(RuntimeContract):
    """Validated transport-neutral command produced by the local CLI."""

    name: ReleaseControlCommandName
    manifest_ref: str | None = Field(default=None, min_length=1, max_length=512)
    target_manifest_digest: str | None = Field(
        default=None,
        pattern=r"^[a-f0-9]{64}$",
    )
    output_path: str | None = Field(default=None, min_length=1, max_length=4_096)
    rationale: str | None = Field(default=None, min_length=1, max_length=2_000)

    @model_validator(mode="after")
    def _command_fields_match_action(self) -> "ReleaseControlCommand":
        if self.name is ReleaseControlCommandName.VERIFY:
            if self.manifest_ref is None:
                raise ValueError("verify requires manifest_ref")
            if any(
                value is not None
                for value in (
                    self.target_manifest_digest,
                    self.output_path,
                    self.rationale,
                )
            ):
                raise ValueError("verify accepts only manifest_ref")
        if self.name is ReleaseControlCommandName.EXPORT:
            if self.output_path is None:
                raise ValueError("export requires output_path")
            if any(
                value is not None
                for value in (
                    self.manifest_ref,
                    self.target_manifest_digest,
                    self.rationale,
                )
            ):
                raise ValueError("export accepts only output_path")
        if self.name in {
            ReleaseControlCommandName.OVERRIDE,
            ReleaseControlCommandName.ROLLBACK,
        }:
            if self.target_manifest_digest is None:
                raise ValueError(f"{self.name.value} requires target_manifest_digest")
            if self.rationale is None:
                raise ValueError(f"{self.name.value} requires rationale")
            if self.manifest_ref is not None or self.output_path is not None:
                raise ValueError(
                    f"{self.name.value} accepts only target digest and rationale"
                )
        return self


class LocalReleaseControlPolicy(RuntimeContract):
    """Fail-closed policy for a development/dogfood control listener."""

    profile: ReleaseControlProfile
    explicitly_enabled: bool = False
    bind_host: Annotated[str, Field(min_length=1, max_length=64)] = "127.0.0.1"

    @model_validator(mode="after")
    def _enabled_listener_is_development_loopback_only(
        self,
    ) -> "LocalReleaseControlPolicy":
        if not self.explicitly_enabled:
            return self
        if self.profile is ReleaseControlProfile.PRODUCTION:
            raise ValueError("local release control cannot be enabled in production")
        if not is_literal_loopback(self.bind_host):
            raise ValueError(
                "local release control must bind a literal loopback address"
            )
        return self

    def authorize_peer(self, peer_host: str) -> None:
        """Authorize one connection without DNS or forwarded-host trust."""

        if not self.explicitly_enabled:
            raise ReleaseControlError("local release control is disabled")
        if self.profile is ReleaseControlProfile.PRODUCTION:
            raise ReleaseControlError(
                "local release control is unavailable in production"
            )
        if not is_literal_loopback(self.bind_host):
            raise ReleaseControlError("local release control bind is not loopback")
        if not is_literal_loopback(peer_host):
            raise ReleaseControlError("local release control peer is not loopback")

    def authorize_command(self, command: ReleaseControlCommand) -> None:
        """Reject mutation commands outside explicit development/dogfood."""

        self.authorize_peer(self.bind_host)
        if command.name in {
            ReleaseControlCommandName.OVERRIDE,
            ReleaseControlCommandName.ROLLBACK,
        } and self.profile not in {
            ReleaseControlProfile.DEVELOPMENT,
            ReleaseControlProfile.DOGFOOD,
        }:
            raise ReleaseControlError("release mutation command is not locally allowed")


def parse_release_control_command(argv: Sequence[str]) -> ReleaseControlCommand:
    """Parse the deliberately small offline CLI command grammar."""

    if not argv:
        raise ReleaseControlError("release control command is required")
    try:
        name = ReleaseControlCommandName(argv[0])
    except ValueError as exc:
        raise ReleaseControlError(
            f"unknown release control command: {argv[0]}"
        ) from exc

    fields = _parse_flags(argv[1:])
    allowed = {
        "--manifest-ref": "manifest_ref",
        "--target-manifest-digest": "target_manifest_digest",
        "--output-path": "output_path",
        "--rationale": "rationale",
    }
    unknown = sorted(set(fields) - set(allowed))
    if unknown:
        raise ReleaseControlError(f"unsupported release control option: {unknown[0]}")
    try:
        return ReleaseControlCommand(
            name=name,
            **{allowed[flag]: value for flag, value in fields.items()},
        )
    except ValueError as exc:
        raise ReleaseControlError(str(exc)) from exc


def _parse_flags(argv: Sequence[str]) -> dict[str, str]:
    if len(argv) % 2:
        raise ReleaseControlError("release control options require explicit values")
    parsed: dict[str, str] = {}
    for index in range(0, len(argv), 2):
        flag = argv[index]
        value = argv[index + 1]
        if not flag.startswith("--") or not value:
            raise ReleaseControlError("release control options must be --flag value")
        if flag in parsed:
            raise ReleaseControlError(f"duplicate release control option: {flag}")
        parsed[flag] = value
    return parsed


__all__ = (
    "LocalReleaseControlPolicy",
    "ReleaseControlCommand",
    "ReleaseControlCommandName",
    "ReleaseControlError",
    "ReleaseControlProfile",
    "parse_release_control_command",
)
