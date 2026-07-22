"""SSRF guard for user-supplied custom OpenAI-compatible endpoint base URLs.

The custom-endpoint BYOK flow (decision D-2) lets a user register an arbitrary
``base_url`` that the backend probes at validate time and the runtime calls on
every model request. That base_url is fully user-controlled, so it is a classic
Server-Side Request Forgery vector: a hosted / multi-tenant deployment must
never let it reach the cloud metadata endpoint (``169.254.169.254``), loopback,
or RFC-1918 private ranges — any of which would let a tenant pivot the backend
into the deployment's internal network.

This module is the single, dedicated, unit-tested chokepoint for that decision.
:class:`SsrfGuard` :

* rejects non-``http(s)`` schemes and embedded credentials (``user:pass@host``),
* requires ``https`` in hosted / team profiles (``http`` allowed only on the
  single-user desktop, where the endpoint is on the user's own machine),
* RESOLVES the hostname and validates EVERY resolved IP — not just an IP
  literal — so a public hostname that resolves to a private address
  (DNS-rebinding) is blocked,
* is PROFILE-AWARE via ``allow_private_networks``: ``True`` only on the
  single-user desktop (a self-hosted vLLM / LiteLLM / Ollama on the user's own
  box is the entire point there), which relaxes the private-range + ``http``
  checks; every other profile blocks private, loopback, link-local, reserved,
  unspecified, and multicast addresses.

Security invariant: the raised :class:`SsrfValidationError` carries only a
machine-readable ``reason`` code — never the URL, host, or any resolved IP that
could aid reconnaissance. Callers translate the reason into a generic 400.
"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import urlsplit


class SsrfBlockReason(StrEnum):
    """Machine-readable reason a base_url was rejected. NEVER carries the URL."""

    UNSUPPORTED_SCHEME = "unsupported_scheme"
    CREDENTIALS_IN_URL = "credentials_in_url"
    MISSING_HOST = "missing_host"
    HTTPS_REQUIRED = "https_required"
    BLOCKED_ADDRESS = "blocked_address"
    UNRESOLVABLE_HOST = "unresolvable_host"


class SsrfValidationError(ValueError):
    """Raised when a base_url fails the guard. The message is the reason code
    only — never any part of the URL, host, or resolved address."""

    def __init__(self, reason: SsrfBlockReason) -> None:
        super().__init__(reason.value)
        self.reason = reason


# A resolver maps a hostname to the tuple of IP-address strings it resolves to.
# Injected in tests so the guard is fully hermetic (no real DNS), and so
# DNS-rebinding — a public hostname that resolves to a private IP — is testable.
HostResolver = Callable[[str], tuple[str, ...]]


def _default_resolver(host: str) -> tuple[str, ...]:
    """Resolve ``host`` to every A/AAAA address via ``getaddrinfo``.

    Handles bare IP literals transparently (``getaddrinfo`` echoes them back),
    so the guard checks literals and hostnames through the same path.
    """

    infos = socket.getaddrinfo(host, None)
    addresses = [str(info[4][0]) for info in infos]
    # De-dupe while preserving order — a hostname commonly resolves to the same
    # address family more than once.
    return tuple(dict.fromkeys(addresses))


@dataclass(frozen=True)
class SsrfGuard:
    """Validate a user-supplied base_url against SSRF before any fetch/store.

    ``allow_private_networks`` is set ``True`` ONLY for the single-user desktop
    profile. ``resolver`` is injectable purely for tests; production uses the
    real ``getaddrinfo``-backed resolver.
    """

    allow_private_networks: bool = False
    resolver: HostResolver | None = None

    def check(self, base_url: str) -> None:
        """Raise :class:`SsrfValidationError` if ``base_url`` is unsafe.

        Returns ``None`` on success. Idempotent + side-effect-free apart from
        the DNS lookup, so it is safe to call at BOTH validate time and again
        immediately before a fetch.
        """

        parsed = urlsplit((base_url or "").strip())
        scheme = parsed.scheme.lower()
        if scheme not in ("http", "https"):
            raise SsrfValidationError(SsrfBlockReason.UNSUPPORTED_SCHEME)
        # ``username``/``password`` are the userinfo component. A credentialed
        # URL (``https://user:pass@host``) is never legitimate here and is a
        # common way to smuggle a different authority past naive parsers.
        if parsed.username or parsed.password:
            raise SsrfValidationError(SsrfBlockReason.CREDENTIALS_IN_URL)
        host = parsed.hostname  # lowercased; IPv6 brackets stripped
        if not host:
            raise SsrfValidationError(SsrfBlockReason.MISSING_HOST)
        if scheme == "http" and not self.allow_private_networks:
            # Plaintext http is only acceptable to a loopback/private endpoint,
            # which hosted/team deployments block outright — so require https.
            raise SsrfValidationError(SsrfBlockReason.HTTPS_REQUIRED)
        resolve = self.resolver or _default_resolver
        try:
            addresses = resolve(host)
        except Exception as exc:  # DNS failure, invalid host — fail closed.
            raise SsrfValidationError(SsrfBlockReason.UNRESOLVABLE_HOST) from exc
        if not addresses:
            raise SsrfValidationError(SsrfBlockReason.UNRESOLVABLE_HOST)
        for address in addresses:
            self._check_address(address)

    def _check_address(self, address: str) -> None:
        try:
            ip = ipaddress.ip_address(address)
        except ValueError as exc:
            raise SsrfValidationError(SsrfBlockReason.BLOCKED_ADDRESS) from exc
        # Collapse IPv4-mapped IPv6 (``::ffff:169.254.169.254``) to its IPv4
        # form so metadata/private ranges can't be smuggled through v6 syntax.
        mapped = getattr(ip, "ipv4_mapped", None)
        if mapped is not None:
            ip = mapped
        if self.allow_private_networks:
            # Desktop: a loopback/private endpoint is the whole point. Scheme +
            # userinfo were already validated above.
            return
        # Hosted / team: only genuinely public unicast addresses are allowed.
        # ``is_global`` is the positive signal; the explicit categories make the
        # blocked set unmistakable (and resilient to ``is_global`` edge cases) —
        # 127.0.0.0/8, ::1, 10/8, 172.16/12, 192.168/16, fc00::/7, 169.254/16
        # (incl. the 169.254.169.254 cloud-metadata IP), fe80::/10, 0.0.0.0/::,
        # and reserved/multicast ranges.
        blocked = (
            not ip.is_global
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_private
            or ip.is_unspecified
            or ip.is_reserved
            or ip.is_multicast
        )
        if blocked:
            raise SsrfValidationError(SsrfBlockReason.BLOCKED_ADDRESS)


__all__ = [
    "HostResolver",
    "SsrfBlockReason",
    "SsrfGuard",
    "SsrfValidationError",
]
