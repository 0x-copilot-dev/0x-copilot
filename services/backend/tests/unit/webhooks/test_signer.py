"""Signer tests — HMAC sign + verify round trip + replay + tamper.

connectors-prd §9 (Routines §9.7 Q6). The constants live as the single
source of truth in :mod:`backend_app.webhooks.signer`; these tests
pin them so a future change requires a deliberate doc + audit update.
"""

from __future__ import annotations

import hashlib
import hmac

import pytest

from backend_app.webhooks.signer import (
    HMAC_ALGO,
    SIGNATURE_HEADER,
    TIMESTAMP_HEADER,
    TIMESTAMP_MAX_SKEW_S,
    sign,
    verify,
)


_BODY = b'{"event":"routine.fire","run_id":"run_abc"}'
_SECRET = b"super-secret-32-bytes-or-more-here!!"
_NOW = 1_758_000_000  # arbitrary fixed unix-seconds


class TestConstants:
    def test_algorithm_is_hmac_sha256(self) -> None:
        assert HMAC_ALGO == "hmac-sha256"

    def test_signature_header(self) -> None:
        assert SIGNATURE_HEADER == "X-Atlas-Routine-Signature"

    def test_timestamp_header(self) -> None:
        assert TIMESTAMP_HEADER == "X-Atlas-Signature-Timestamp"

    def test_skew_window_is_five_minutes(self) -> None:
        assert TIMESTAMP_MAX_SKEW_S == 300


class TestSignRoundTrip:
    def test_sign_returns_prefixed_header(self) -> None:
        header = sign(body=_BODY, secret=_SECRET, ts=_NOW)
        assert header.startswith("hmac-sha256=")

    def test_sign_envelope_matches_receiver_snippet(self) -> None:
        """Mirrors the receiver verification snippet in connectors-prd
        §9.4: HMAC over ``body + str(ts).encode()`` using ``secret`` and
        SHA-256."""

        expected = hmac.new(
            _SECRET, _BODY + str(_NOW).encode("ascii"), hashlib.sha256
        ).hexdigest()
        header = sign(body=_BODY, secret=_SECRET, ts=_NOW)
        assert header == f"hmac-sha256={expected}"

    def test_verify_accepts_valid_signature(self) -> None:
        header = sign(body=_BODY, secret=_SECRET, ts=_NOW)
        assert verify(
            body=_BODY,
            sig_header=header,
            ts_header=str(_NOW),
            secret=_SECRET,
            now=_NOW,
        )


class TestReplayProtection:
    def test_old_timestamp_rejected(self) -> None:
        header = sign(body=_BODY, secret=_SECRET, ts=_NOW)
        # 301 seconds in the past → outside the skew window.
        assert not verify(
            body=_BODY,
            sig_header=header,
            ts_header=str(_NOW),
            secret=_SECRET,
            now=_NOW + TIMESTAMP_MAX_SKEW_S + 1,
        )

    def test_future_timestamp_rejected(self) -> None:
        header = sign(body=_BODY, secret=_SECRET, ts=_NOW + TIMESTAMP_MAX_SKEW_S + 1)
        assert not verify(
            body=_BODY,
            sig_header=header,
            ts_header=str(_NOW + TIMESTAMP_MAX_SKEW_S + 1),
            secret=_SECRET,
            now=_NOW,
        )

    def test_boundary_skew_accepted(self) -> None:
        """At exactly TIMESTAMP_MAX_SKEW_S boundary, verify accepts."""

        header = sign(body=_BODY, secret=_SECRET, ts=_NOW)
        assert verify(
            body=_BODY,
            sig_header=header,
            ts_header=str(_NOW),
            secret=_SECRET,
            now=_NOW + TIMESTAMP_MAX_SKEW_S,
        )

    def test_malformed_timestamp_rejected(self) -> None:
        header = sign(body=_BODY, secret=_SECRET, ts=_NOW)
        assert not verify(
            body=_BODY,
            sig_header=header,
            ts_header="not-a-number",
            secret=_SECRET,
            now=_NOW,
        )


class TestTamperDetection:
    def test_modified_body_fails(self) -> None:
        header = sign(body=_BODY, secret=_SECRET, ts=_NOW)
        tampered = _BODY + b"&extra=1"
        assert not verify(
            body=tampered,
            sig_header=header,
            ts_header=str(_NOW),
            secret=_SECRET,
            now=_NOW,
        )

    def test_wrong_secret_fails(self) -> None:
        header = sign(body=_BODY, secret=_SECRET, ts=_NOW)
        assert not verify(
            body=_BODY,
            sig_header=header,
            ts_header=str(_NOW),
            secret=b"different-secret-32-bytes-or-more!!",
            now=_NOW,
        )

    def test_missing_signature_fails(self) -> None:
        assert not verify(
            body=_BODY,
            sig_header=None,
            ts_header=str(_NOW),
            secret=_SECRET,
            now=_NOW,
        )

    def test_missing_timestamp_fails(self) -> None:
        header = sign(body=_BODY, secret=_SECRET, ts=_NOW)
        assert not verify(
            body=_BODY,
            sig_header=header,
            ts_header=None,
            secret=_SECRET,
            now=_NOW,
        )

    def test_wrong_prefix_fails(self) -> None:
        """Receiver rejects unknown algorithm prefixes (defense-in-depth)."""

        digest = hmac.new(
            _SECRET, _BODY + str(_NOW).encode("ascii"), hashlib.sha256
        ).hexdigest()
        assert not verify(
            body=_BODY,
            sig_header=f"sha256={digest}",  # missing the hmac- prefix
            ts_header=str(_NOW),
            secret=_SECRET,
            now=_NOW,
        )


class TestInputValidation:
    def test_sign_rejects_str_body(self) -> None:
        with pytest.raises(TypeError):
            sign(body="not-bytes", secret=_SECRET, ts=_NOW)  # type: ignore[arg-type]

    def test_sign_rejects_str_secret(self) -> None:
        with pytest.raises(TypeError):
            sign(body=_BODY, secret="not-bytes", ts=_NOW)  # type: ignore[arg-type]


class TestPRDVerificationSnippet:
    """Run the verification snippet rendered on the wizard's "Verify"
    step (connectors-prd §9.4) against our signer. The snippet uses
    ``time.time() - int(ts_header) > 300`` for the skew check; we
    inject a fixed clock instead of patching time."""

    def test_prd_snippet_accepts_our_signature(self) -> None:
        secret = b"deploy-receiver-secret"
        body = b'{"hello":"world"}'
        header = sign(body=body, secret=secret, ts=_NOW)

        # Receiver-side verification — copy of §9.4 with the clock
        # injected to keep the test deterministic.
        def verify_receiver(
            body: bytes,
            sig_header: str,
            ts_header: str,
            secret: bytes,
            *,
            now: int,
        ) -> bool:
            if abs(now - int(ts_header)) > 300:
                return False
            algo, signature = sig_header.split("=", 1)
            if algo != "hmac-sha256":
                return False
            expected = hmac.new(
                secret, body + ts_header.encode(), hashlib.sha256
            ).hexdigest()
            return hmac.compare_digest(expected, signature)

        assert verify_receiver(
            body=body, sig_header=header, ts_header=str(_NOW), secret=secret, now=_NOW
        )

        # And our verify accepts the snippet-shape headers.
        assert verify(
            body=body,
            sig_header=header,
            ts_header=str(_NOW),
            secret=secret,
            now=_NOW,
        )
