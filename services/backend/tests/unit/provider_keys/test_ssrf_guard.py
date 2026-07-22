"""Unit tests for the custom-endpoint SSRF guard (decision D-2).

The guard is the load-bearing security piece: every custom OpenAI-compatible
``base_url`` a user supplies is validated here before it is probed or stored,
and again before any fetch. These tests are fully hermetic — a fake resolver
maps hostnames to IPs, so DNS-rebinding (a public hostname resolving to a
private address) is exercised without touching the network.
"""

from __future__ import annotations

import pytest

from backend_app.provider_keys.ssrf_guard import (
    SsrfBlockReason,
    SsrfGuard,
    SsrfValidationError,
)


def _resolver(mapping: dict[str, tuple[str, ...]]):
    """Return a fake host->IPs resolver; unknown hosts raise (NXDOMAIN)."""

    def resolve(host: str) -> tuple[str, ...]:
        try:
            return mapping[host]
        except KeyError as exc:  # NXDOMAIN → fail closed
            raise OSError("name resolution failed") from exc

    return resolve


# A public host both profiles resolve to a routable address.
_PUBLIC = {"api.vendor.example": ("93.184.216.34",)}


class TestHostedProfileBlocks:
    """allow_private_networks=False — hosted/team: https + public only."""

    def _guard(self, mapping: dict[str, tuple[str, ...]]) -> SsrfGuard:
        return SsrfGuard(allow_private_networks=False, resolver=_resolver(mapping))

    @pytest.mark.parametrize(
        "ip",
        [
            "169.254.169.254",  # cloud metadata (AWS/GCP IMDS)
            "127.0.0.1",  # loopback
            "10.0.0.5",  # private 10/8
            "172.16.3.9",  # private 172.16/12
            "192.168.1.1",  # private 192.168/16
            "100.64.0.1",  # CGNAT / shared address space
            "0.0.0.0",  # unspecified
            "::1",  # IPv6 loopback
            "fc00::1",  # IPv6 ULA fc00::/7
            "fe80::1",  # IPv6 link-local
            "::ffff:169.254.169.254",  # IPv4-mapped metadata (v6 smuggling)
            "224.0.0.1",  # multicast
        ],
    )
    def test_blocks_private_and_reserved_addresses(self, ip: str) -> None:
        guard = self._guard({"endpoint.example": (ip,)})
        with pytest.raises(SsrfValidationError) as excinfo:
            guard.check("https://endpoint.example/v1")
        assert excinfo.value.reason is SsrfBlockReason.BLOCKED_ADDRESS

    def test_allows_public_https(self) -> None:
        guard = self._guard(_PUBLIC)
        # No raise == allowed.
        guard.check("https://api.vendor.example/v1")

    def test_blocks_dns_rebinding(self) -> None:
        # A perfectly public-looking hostname that RESOLVES to a private IP —
        # the classic rebinding pivot. Must be blocked on the resolved address,
        # not the literal.
        guard = self._guard({"totally-public.example": ("10.1.2.3",)})
        with pytest.raises(SsrfValidationError) as excinfo:
            guard.check("https://totally-public.example/v1")
        assert excinfo.value.reason is SsrfBlockReason.BLOCKED_ADDRESS

    def test_blocks_when_any_resolved_ip_is_private(self) -> None:
        # Multi-record host: one public, one private. Must block — a rebinding
        # attack only needs one private answer to be honoured.
        guard = self._guard({"mixed.example": ("93.184.216.34", "192.168.0.9")})
        with pytest.raises(SsrfValidationError):
            guard.check("https://mixed.example/v1")

    def test_requires_https(self) -> None:
        guard = self._guard(_PUBLIC)
        with pytest.raises(SsrfValidationError) as excinfo:
            guard.check("http://api.vendor.example/v1")
        assert excinfo.value.reason is SsrfBlockReason.HTTPS_REQUIRED

    def test_rejects_non_http_scheme(self) -> None:
        guard = self._guard(_PUBLIC)
        for url in ("file:///etc/passwd", "gopher://api.vendor.example", "ftp://x"):
            with pytest.raises(SsrfValidationError) as excinfo:
                guard.check(url)
            assert excinfo.value.reason is SsrfBlockReason.UNSUPPORTED_SCHEME

    def test_rejects_userinfo_credentials(self) -> None:
        guard = self._guard(_PUBLIC)
        with pytest.raises(SsrfValidationError) as excinfo:
            guard.check("https://user:pass@api.vendor.example/v1")
        assert excinfo.value.reason is SsrfBlockReason.CREDENTIALS_IN_URL

    def test_rejects_missing_host(self) -> None:
        guard = self._guard(_PUBLIC)
        with pytest.raises(SsrfValidationError) as excinfo:
            guard.check("https:///v1")
        assert excinfo.value.reason is SsrfBlockReason.MISSING_HOST

    def test_unresolvable_host_fails_closed(self) -> None:
        guard = self._guard(_PUBLIC)
        with pytest.raises(SsrfValidationError) as excinfo:
            guard.check("https://does-not-exist.example/v1")
        assert excinfo.value.reason is SsrfBlockReason.UNRESOLVABLE_HOST

    def test_empty_url_rejected(self) -> None:
        guard = self._guard(_PUBLIC)
        with pytest.raises(SsrfValidationError):
            guard.check("")


class TestDesktopProfileAllowsLocal:
    """allow_private_networks=True — single-user desktop: local is the point."""

    def _guard(self, mapping: dict[str, tuple[str, ...]]) -> SsrfGuard:
        return SsrfGuard(allow_private_networks=True, resolver=_resolver(mapping))

    def test_allows_loopback_http(self) -> None:
        guard = self._guard({"localhost": ("127.0.0.1",)})
        guard.check("http://localhost:11434/v1")  # local Ollama/LiteLLM

    def test_allows_private_lan(self) -> None:
        guard = self._guard({"nas.local": ("192.168.1.50",)})
        guard.check("http://nas.local:8000/v1")

    def test_allows_public_https(self) -> None:
        guard = self._guard(_PUBLIC)
        guard.check("https://api.vendor.example/v1")

    def test_still_rejects_non_http_scheme(self) -> None:
        # The private-network allowance never relaxes the scheme/userinfo gates.
        guard = self._guard({"localhost": ("127.0.0.1",)})
        with pytest.raises(SsrfValidationError) as excinfo:
            guard.check("file:///etc/passwd")
        assert excinfo.value.reason is SsrfBlockReason.UNSUPPORTED_SCHEME

    def test_still_rejects_userinfo(self) -> None:
        guard = self._guard({"localhost": ("127.0.0.1",)})
        with pytest.raises(SsrfValidationError) as excinfo:
            guard.check("http://user:pass@localhost/v1")
        assert excinfo.value.reason is SsrfBlockReason.CREDENTIALS_IN_URL


class TestDefaultResolverRealLiteral:
    """The default (getaddrinfo) resolver must classify IP literals offline —
    no fake resolver, no DNS, so this is safe and hermetic."""

    def test_blocks_metadata_ip_literal_hosted(self) -> None:
        guard = SsrfGuard(allow_private_networks=False)
        with pytest.raises(SsrfValidationError) as excinfo:
            guard.check("https://169.254.169.254/latest/meta-data")
        assert excinfo.value.reason is SsrfBlockReason.BLOCKED_ADDRESS

    def test_blocks_loopback_ip_literal_hosted(self) -> None:
        guard = SsrfGuard(allow_private_networks=False)
        with pytest.raises(SsrfValidationError):
            guard.check("https://127.0.0.1:8000/v1")

    def test_allows_loopback_ip_literal_desktop(self) -> None:
        guard = SsrfGuard(allow_private_networks=True)
        guard.check("http://127.0.0.1:11434/v1")
