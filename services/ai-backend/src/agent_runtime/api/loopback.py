"""Shared fail-closed authorization for development-only loopback controls."""

from __future__ import annotations

from ipaddress import ip_address


class LoopbackAuthorizationError(RuntimeError):
    """A local-control peer is not a literal loopback address."""


def is_literal_loopback(host: str) -> bool:
    """Reject DNS names and forwarded-host assertions; accept IP literals only."""

    try:
        return ip_address(host).is_loopback
    except ValueError:
        return False


def require_literal_loopback(host: str) -> None:
    if not is_literal_loopback(host):
        raise LoopbackAuthorizationError("local control peer is not loopback")


__all__ = (
    "LoopbackAuthorizationError",
    "is_literal_loopback",
    "require_literal_loopback",
)
