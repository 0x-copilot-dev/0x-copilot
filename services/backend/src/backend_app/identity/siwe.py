"""Sign-In-With-Ethereum (EIP-4361 + EIP-191).

Flow (mirrors the OIDC authorize → callback state machine, minus the IdP
round-trip):

1. ``POST /v1/auth/siwe/nonce``  — client sends ``{address, chain_id}``;
   we mint a single-use nonce bound to that address + chain (TTL 5 min).
2. The wallet ``personal_sign``s the EIP-4361 message the client builds
   (see :func:`build_siwe_message` for the exact template).
3. ``POST /v1/auth/siwe/verify`` — we parse the message strictly, check
   the chain allowlist, the domain/URI binding, the time window, recover
   the signer via EIP-191 (``eth_account``), consume the nonce atomically
   (constant-time compared), then link-or-provision the wallet and mint a
   session — the same ``SessionService`` mint every other login uses.

Self-signup reuses :func:`backend_app.identity.provisioning
.provision_personal_org` — the exact helper the global Google provider
uses — gated by the deployment profile's ``allow_self_signup`` toggle.

Addresses are stored lowercase everywhere; EIP-55 checksumming is used
for display strings (org / user display names, audit metadata).
"""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlsplit

from eth_account import Account
from eth_account.messages import encode_defunct
from eth_utils import is_checksum_address, to_checksum_address

from backend_app.contracts import (
    IdentityAuditEventRecord,
    LoginAttemptKind,
    LoginAttemptOutcome,
    LoginAttemptRecord,
    OrganizationMemberSource,
    OrganizationRecord,
    SiweNonceRecord,
    SiweNonceResult,
    SiweVerifyResult,
    UserRecord,
    WalletIdentityRecord,
)
from backend_app.identity.lockout import LockoutService
from backend_app.identity.login_email_first import InMemoryRateLimiter
from backend_app.identity.mfa import MfaService
from backend_app.identity.provisioning import (
    PersonalOrgSlugExhausted,
    provision_personal_org,
)
from backend_app.identity.sessions import SessionService
from backend_app.identity.siwe_store import SiweStore
from backend_app.identity.store import IdentityStore


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# The one statement this deployment signs. Baked into build + parse so a
# message signed for another product (or a tampered statement) never
# verifies here.
SIWE_STATEMENT = "Sign in to Atlas"

# Reserved pseudo-provider id: lands in sessions.auth_provider_id and
# audit metadata exactly like "google" does for the OIDC global ramp.
# (No auth_providers row — sessions.auth_provider_id carries no FK.)
SIWE_PROVIDER_ID = "siwe"

# Sentinel org id for pre-identity bookkeeping (flag-off login attempts),
# mirroring GOOGLE_GLOBAL_ORG_ID. Real users never live in this org.
SIWE_GLOBAL_ORG_ID = "org_global_siwe"

ENV_SIWE_ALLOWED_CHAIN_IDS = "SIWE_ALLOWED_CHAIN_IDS"
ENV_SIWE_ORIGIN = "SIWE_ORIGIN"

# Ethereum mainnet, Base, Arbitrum One, Robinhood Chain.
DEFAULT_ALLOWED_CHAIN_IDS = (1, 8453, 42161, 4663)

# Single-use nonce TTL. Contract cap is 10 minutes; five is plenty for a
# wallet-popup round-trip.
NONCE_TTL_SECONDS = 5 * 60

# Nonce alphabet is EIP-4361 ``alphanumeric`` — token_hex satisfies it.
_NONCE_BYTES = 16

# Rate limits for the unauthenticated nonce mint (mirrors the magic-link
# start limits: tight per-IP burst window + a slower per-address window).
_NONCE_RATE_PER_MIN_IP = 30
_NONCE_RATE_PER_HOUR_ADDRESS = 60

_ADDRESS_PATTERN = re.compile(r"0x[0-9a-fA-F]{40}")
_CHAIN_ID_PATTERN = re.compile(r"[0-9]{1,15}")
# EIP-4361 nonce: 8+ alphanumeric characters.
_NONCE_PATTERN = re.compile(r"[a-zA-Z0-9]{8,}")


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Errors — one class per wire detail code (routes map 1:1)
# ---------------------------------------------------------------------------


class SiweError(RuntimeError):
    """Base class; ``detail`` is the stable wire code."""

    detail = "siwe_error"


class SiweAddressInvalid(SiweError):
    detail = "invalid_address"


class SiweChainNotAllowed(SiweError):
    detail = "chain_not_allowed"


class SiweMessageInvalid(SiweError):
    """Message does not parse as the expected strict EIP-4361 shape."""

    detail = "message_invalid"


class SiweDomainMismatch(SiweError):
    detail = "domain_mismatch"


class SiweExpiredMessage(SiweError):
    detail = "expired_message"


class SiweNonceInvalid(SiweError):
    detail = "nonce_invalid"


class SiweNonceExpired(SiweError):
    detail = "nonce_expired"


class SiweSignatureInvalid(SiweError):
    detail = "signature_invalid"


class SiweSelfSignupDisabled(SiweError):
    detail = "self_signup_disabled"


class SiweUserNotProvisioned(SiweError):
    """Linked wallet points at a deleted/disabled user."""

    detail = "user_not_provisioned"


class SiweRateLimited(SiweError):
    detail = "rate_limited"

    def __init__(self, retry_after_seconds: int = 30) -> None:
        super().__init__("rate limited")
        self.retry_after_seconds = retry_after_seconds


# ---------------------------------------------------------------------------
# Address helpers
# ---------------------------------------------------------------------------


def normalize_wallet_address(value: object) -> str:
    """Validate ``0x`` + 40 hex and return the lowercase form.

    Mixed-case input must be a valid EIP-55 checksum (a wrong-case
    address is a typo'd or tampered address, not a different wallet).
    All-lowercase / all-uppercase hex carries no checksum and is accepted
    as-is per EIP-55.
    """

    if not isinstance(value, str):
        raise SiweAddressInvalid("address must be a string")
    text = value.strip()
    if not _ADDRESS_PATTERN.fullmatch(text):
        raise SiweAddressInvalid("address must be 0x + 40 hex characters")
    hex_part = text[2:]
    if hex_part != hex_part.lower() and hex_part != hex_part.upper():
        if not is_checksum_address(text):
            raise SiweAddressInvalid("address fails EIP-55 checksum")
    return text.lower()


def display_address(address: str) -> str:
    """EIP-55 checksummed form — the only form users should ever see."""

    return to_checksum_address(address)


def truncated_display_address(address: str) -> str:
    """``0xAbCd…1234`` — first 6 + last 4 of the EIP-55 form."""

    checksummed = display_address(address)
    return f"{checksummed[:6]}…{checksummed[-4:]}"


# ---------------------------------------------------------------------------
# EIP-4361 message build + strict parse
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SiweMessage:
    domain: str
    address: str  # exactly as it appears in the message (display case)
    statement: str
    uri: str
    version: str
    chain_id: int
    nonce: str
    issued_at: str
    expiration_time: str
    not_before: str | None = None
    request_id: str | None = None
    resources: tuple[str, ...] = ()

    @property
    def address_lower(self) -> str:
        return self.address.lower()


def build_siwe_message(
    *,
    domain: str,
    address: str,
    uri: str,
    chain_id: int,
    nonce: str,
    issued_at: datetime,
    expiration_time: datetime,
    statement: str = SIWE_STATEMENT,
) -> str:
    """Render the exact EIP-4361 text this deployment signs.

    Field order and line format are load-bearing: :func:`parse_siwe_message`
    accepts precisely this shape. The address is rendered EIP-55
    checksummed — the form wallets display.
    """

    checksummed = display_address(normalize_wallet_address(address))
    return (
        f"{domain} wants you to sign in with your Ethereum account:\n"
        f"{checksummed}\n"
        f"\n"
        f"{statement}\n"
        f"\n"
        f"URI: {uri}\n"
        f"Version: 1\n"
        f"Chain ID: {chain_id}\n"
        f"Nonce: {nonce}\n"
        f"Issued At: {_format_timestamp(issued_at)}\n"
        f"Expiration Time: {_format_timestamp(expiration_time)}"
    )


def _format_timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return (
        value.astimezone(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def parse_siwe_message(text: str) -> SiweMessage:
    """Strict EIP-4361 parse of the exact template this deployment issues.

    Enforces: preamble line, address format (EIP-55 when mixed case),
    blank separators, non-empty statement, ``Version: 1``, numeric chain
    id, alphanumeric nonce (8+), RFC 3339 ``Issued At`` and mandatory
    ``Expiration Time``, optional ``Not Before`` / ``Request ID`` /
    ``Resources`` trailer in EIP-4361 order, nothing after the last
    field. Raises :class:`SiweMessageInvalid` on any deviation.
    """

    if not isinstance(text, str) or not text.strip():
        raise SiweMessageInvalid("empty message")
    lines = text.split("\n")
    if len(lines) < 10:
        raise SiweMessageInvalid("message too short for the required fields")

    preamble = " wants you to sign in with your Ethereum account:"
    if not lines[0].endswith(preamble):
        raise SiweMessageInvalid("first line must be the EIP-4361 preamble")
    domain = lines[0][: -len(preamble)]
    if not domain or domain != domain.strip() or " " in domain:
        raise SiweMessageInvalid("domain must be a bare RFC 3986 authority")

    if lines[1] != lines[1].strip():
        raise SiweMessageInvalid("address line carries stray whitespace")
    try:
        normalize_wallet_address(lines[1])
    except SiweAddressInvalid as exc:
        raise SiweMessageInvalid(f"address line: {exc}") from exc

    if lines[2] != "":
        raise SiweMessageInvalid("expected blank line after the address")
    statement = lines[3]
    if not statement:
        raise SiweMessageInvalid("statement is required")
    if lines[4] != "":
        raise SiweMessageInvalid("expected blank line after the statement")

    fields: list[tuple[str, str]] = []
    index = 5
    while index < len(lines):
        line = lines[index]
        if line == "Resources:":
            # EIP-4361 resources header: bare "Resources:" then "- uri" lines.
            fields.append(("Resources", ""))
            index += 1
            break
        name, sep, value = line.partition(": ")
        if not sep or not value:
            raise SiweMessageInvalid(f"malformed field line: {line!r}")
        fields.append((name, value))
        index += 1

    resources: list[str] = []
    while index < len(lines):
        line = lines[index]
        if not line.startswith("- ") or not line[2:]:
            raise SiweMessageInvalid("unexpected trailing content in message")
        resources.append(line[2:])
        index += 1

    required_order = ["URI", "Version", "Chain ID", "Nonce", "Issued At"]
    optional_order = ["Expiration Time", "Not Before", "Request ID", "Resources"]
    names = [name for name, _ in fields]
    if names[: len(required_order)] != required_order:
        raise SiweMessageInvalid(
            "fields must appear in EIP-4361 order: "
            "URI, Version, Chain ID, Nonce, Issued At, ..."
        )
    trailer = names[len(required_order) :]
    cursor = 0
    for name in trailer:
        try:
            position = optional_order.index(name, cursor)
        except ValueError:
            raise SiweMessageInvalid(
                f"unexpected or out-of-order field: {name!r}"
            ) from None
        cursor = position + 1
    values = dict(fields)

    if "Expiration Time" not in values:
        raise SiweMessageInvalid("Expiration Time is required")

    version = values["Version"]
    if version != "1":
        raise SiweMessageInvalid("Version must be exactly '1'")

    chain_raw = values["Chain ID"]
    if not _CHAIN_ID_PATTERN.fullmatch(chain_raw):
        raise SiweMessageInvalid("Chain ID must be a positive integer")

    nonce = values["Nonce"]
    if not _NONCE_PATTERN.fullmatch(nonce):
        raise SiweMessageInvalid("Nonce must be 8+ alphanumeric characters")

    issued_at = values["Issued At"]
    _parse_timestamp(issued_at, field_name="Issued At")
    _parse_timestamp(values["Expiration Time"], field_name="Expiration Time")
    if "Not Before" in values:
        _parse_timestamp(values["Not Before"], field_name="Not Before")

    uri = values["URI"]
    if not uri or urlsplit(uri).scheme == "":
        raise SiweMessageInvalid("URI must be an absolute RFC 3986 URI")

    return SiweMessage(
        domain=domain,
        # Casing preserved verbatim; normalized form via .address_lower.
        address=lines[1],
        statement=statement,
        uri=uri,
        version=version,
        chain_id=int(chain_raw),
        nonce=nonce,
        issued_at=issued_at,
        expiration_time=values["Expiration Time"],
        not_before=values.get("Not Before"),
        request_id=values.get("Request ID"),
        resources=tuple(resources),
    )


def _parse_timestamp(value: str, *, field_name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SiweMessageInvalid(f"{field_name} is not RFC 3339: {value!r}") from exc
    if parsed.tzinfo is None:
        raise SiweMessageInvalid(f"{field_name} must carry a timezone offset")
    return parsed


# ---------------------------------------------------------------------------
# Chain allowlist
# ---------------------------------------------------------------------------


def parse_allowed_chain_ids(raw: str | None) -> frozenset[int]:
    """Parse ``SIWE_ALLOWED_CHAIN_IDS`` (comma-separated ints).

    Empty/unset falls back to the documented default. Garbage fails loudly
    — a typo'd allowlist must not silently lock every chain out (or in).
    """

    if raw is None or not raw.strip():
        return frozenset(DEFAULT_ALLOWED_CHAIN_IDS)
    chain_ids: set[int] = set()
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        if not token.isdigit():
            raise ValueError(
                f"{ENV_SIWE_ALLOWED_CHAIN_IDS} entries must be integers; got {token!r}"
            )
        chain_ids.add(int(token))
    if not chain_ids:
        raise ValueError(f"{ENV_SIWE_ALLOWED_CHAIN_IDS} resolved to an empty set")
    return frozenset(chain_ids)


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class SiweService:
    """Nonce mint + verify + link-or-provision + session mint."""

    def __init__(
        self,
        *,
        identity_store: IdentityStore,
        siwe_store: SiweStore,
        sessions: SessionService,
        expected_origin: str,
        allowed_chain_ids: frozenset[int] | None = None,
        lockout: LockoutService | None = None,
        mfa: MfaService | None = None,
        allow_self_signup: bool = False,
        rate_limiter: InMemoryRateLimiter | None = None,
        nonce_ttl_seconds: int = NONCE_TTL_SECONDS,
    ) -> None:
        self._identity_store = identity_store
        self._siwe_store = siwe_store
        self._sessions = sessions
        self._lockout = lockout
        self._mfa = mfa
        self._allow_self_signup = allow_self_signup
        self._rate_limiter = rate_limiter
        self._nonce_ttl_seconds = min(nonce_ttl_seconds, 10 * 60)
        self._allowed_chain_ids = allowed_chain_ids or frozenset(
            DEFAULT_ALLOWED_CHAIN_IDS
        )
        origin = urlsplit(expected_origin)
        if not origin.scheme or not origin.netloc:
            raise ValueError(
                f"{ENV_SIWE_ORIGIN} must be an absolute origin "
                f"(scheme://host[:port]); got {expected_origin!r}"
            )
        self._expected_scheme = origin.scheme.lower()
        self._expected_authority = origin.netloc.lower()

    # Nonce ---------------------------------------------------------------
    def mint_nonce(
        self,
        *,
        address: str,
        chain_id: int,
        ip: str | None = None,
        user_agent: str | None = None,
    ) -> SiweNonceResult:
        normalized = normalize_wallet_address(address)
        if chain_id not in self._allowed_chain_ids:
            raise SiweChainNotAllowed(f"chain {chain_id} is not allowlisted")
        self._enforce_nonce_rate_limit(address=normalized, ip=ip)
        record = SiweNonceRecord(
            nonce=secrets.token_hex(_NONCE_BYTES),
            address=normalized,
            chain_id=chain_id,
            expires_at=_now() + timedelta(seconds=self._nonce_ttl_seconds),
            ip=ip,
            user_agent=user_agent,
        )
        self._siwe_store.create_nonce(record)
        return SiweNonceResult(nonce=record.nonce, expires_at=record.expires_at)

    def _enforce_nonce_rate_limit(self, *, address: str, ip: str | None) -> None:
        if self._rate_limiter is None:
            return
        retry = self._rate_limiter.hit(
            scope="auth.siwe.nonce.ip",
            key=ip or "anon",
            window_seconds=60,
            limit=_NONCE_RATE_PER_MIN_IP,
        )
        if retry:
            raise SiweRateLimited(retry_after_seconds=retry)
        retry_address = self._rate_limiter.hit(
            scope="auth.siwe.nonce.address",
            key=address,
            window_seconds=60 * 60,
            limit=_NONCE_RATE_PER_HOUR_ADDRESS,
        )
        if retry_address:
            raise SiweRateLimited(retry_after_seconds=retry_address)

    # Verify ----------------------------------------------------------------
    def verify(
        self,
        *,
        message: str,
        signature: str,
        ip: str | None = None,
        user_agent: str | None = None,
    ) -> SiweVerifyResult:
        parsed = parse_siwe_message(message)

        if parsed.statement != SIWE_STATEMENT:
            raise SiweMessageInvalid(f"statement must be exactly {SIWE_STATEMENT!r}")

        if parsed.chain_id not in self._allowed_chain_ids:
            self._record_attempt(
                org_id=None,
                outcome=LoginAttemptOutcome.PROVIDER_REJECTED,
                ip=ip,
                user_agent=user_agent,
                failure_reason="chain_not_allowed",
            )
            raise SiweChainNotAllowed(f"chain {parsed.chain_id} is not allowlisted")

        self._check_domain_binding(parsed, ip=ip, user_agent=user_agent)
        self._check_time_window(parsed, ip=ip, user_agent=user_agent)
        recovered = self._recover_signer(
            message=message,
            signature=signature,
            parsed=parsed,
            ip=ip,
            user_agent=user_agent,
        )
        self._consume_nonce(parsed, ip=ip, user_agent=user_agent)

        user, provisioned = self._link_or_provision(
            address=recovered,
            chain_id=parsed.chain_id,
            ip=ip,
            user_agent=user_agent,
        )
        if self._lockout is not None:
            self._lockout.check_or_raise(org_id=user.org_id, user_id=user.user_id)
        session, mfa_required = self._mint_session(user=user)
        if not provisioned:
            self._identity_store.append_identity_audit(
                self._audit_event(
                    org_id=user.org_id,
                    user=user,
                    action="siwe.verify_succeeded",
                    metadata={
                        "address": display_address(recovered),
                        "chain_id": parsed.chain_id,
                        "provisioned": False,
                    },
                    ip=ip,
                    user_agent=user_agent,
                )
            )
        self._record_attempt(
            org_id=user.org_id,
            user_id=user.user_id,
            outcome=LoginAttemptOutcome.SUCCESS,
            ip=ip,
            user_agent=user_agent,
        )
        if self._lockout is not None:
            self._lockout.record_success(org_id=user.org_id, user_id=user.user_id)
        return SiweVerifyResult(
            user_id=user.user_id,
            session_id=session.session_id,
            bearer_token=session.bearer_token,
            expires_at=session.expires_at,
            return_to=None,
            requires_mfa=mfa_required,
        )

    # Verify helpers ---------------------------------------------------------
    def _check_domain_binding(
        self, parsed: SiweMessage, *, ip: str | None, user_agent: str | None
    ) -> None:
        uri_parts = urlsplit(parsed.uri)
        domain_ok = parsed.domain.lower() == self._expected_authority
        uri_ok = (
            uri_parts.scheme.lower() == self._expected_scheme
            and uri_parts.netloc.lower() == self._expected_authority
        )
        if domain_ok and uri_ok:
            return
        self._record_attempt(
            org_id=None,
            outcome=LoginAttemptOutcome.PROVIDER_REJECTED,
            ip=ip,
            user_agent=user_agent,
            failure_reason="domain_mismatch",
        )
        raise SiweDomainMismatch(
            f"message bound to {parsed.domain!r} / {parsed.uri!r}; "
            f"this deployment serves "
            f"{self._expected_scheme}://{self._expected_authority}"
        )

    # Tolerated clock skew for "Issued At arrived from the future".
    _ISSUED_AT_MAX_SKEW = timedelta(minutes=5)

    def _check_time_window(
        self, parsed: SiweMessage, *, ip: str | None, user_agent: str | None
    ) -> None:
        now = _now()
        issued_at = _parse_timestamp(parsed.issued_at, field_name="Issued At")
        expiration = _parse_timestamp(
            parsed.expiration_time, field_name="Expiration Time"
        )
        not_before = (
            _parse_timestamp(parsed.not_before, field_name="Not Before")
            if parsed.not_before is not None
            else None
        )
        if (
            expiration <= now
            or issued_at > now + self._ISSUED_AT_MAX_SKEW
            or (not_before is not None and not_before > now)
        ):
            self._record_attempt(
                org_id=None,
                outcome=LoginAttemptOutcome.EXPIRED_TOKEN,
                ip=ip,
                user_agent=user_agent,
                failure_reason="expired_message",
            )
            raise SiweExpiredMessage("message is outside its validity window")

    def _recover_signer(
        self,
        *,
        message: str,
        signature: str,
        parsed: SiweMessage,
        ip: str | None,
        user_agent: str | None,
    ) -> str:
        try:
            recovered = Account.recover_message(
                encode_defunct(text=message), signature=signature
            )
        except Exception as exc:  # eth_account raises several ValueError kin
            self._reject_signature(ip=ip, user_agent=user_agent)
            raise SiweSignatureInvalid(f"signature does not recover: {exc}") from exc
        recovered_lower = recovered.lower()
        if recovered_lower != parsed.address_lower:
            self._reject_signature(ip=ip, user_agent=user_agent)
            raise SiweSignatureInvalid(
                "signature recovers a different address than the message claims"
            )
        return recovered_lower

    def _reject_signature(self, *, ip: str | None, user_agent: str | None) -> None:
        self._record_attempt(
            org_id=None,
            outcome=LoginAttemptOutcome.PROVIDER_REJECTED,
            ip=ip,
            user_agent=user_agent,
            failure_reason="signature_invalid",
        )
        if self._lockout is not None:
            self._lockout.record_failure(org_id=None, user_id=None, email=None)

    def _consume_nonce(
        self, parsed: SiweMessage, *, ip: str | None, user_agent: str | None
    ) -> SiweNonceRecord:
        record = self._siwe_store.consume_nonce(nonce=parsed.nonce)
        if record is None or not secrets.compare_digest(record.nonce, parsed.nonce):
            self._record_attempt(
                org_id=None,
                outcome=LoginAttemptOutcome.INVALID_TOKEN,
                ip=ip,
                user_agent=user_agent,
                failure_reason="nonce_invalid",
            )
            raise SiweNonceInvalid("nonce is unknown or already used")
        if record.expires_at <= _now():
            self._record_attempt(
                org_id=None,
                outcome=LoginAttemptOutcome.EXPIRED_TOKEN,
                ip=ip,
                user_agent=user_agent,
                failure_reason="nonce_expired",
            )
            raise SiweNonceExpired("nonce has expired; request a fresh one")
        if record.address != parsed.address_lower or record.chain_id != parsed.chain_id:
            self._record_attempt(
                org_id=None,
                outcome=LoginAttemptOutcome.INVALID_TOKEN,
                ip=ip,
                user_agent=user_agent,
                failure_reason="nonce_binding_mismatch",
            )
            raise SiweNonceInvalid("nonce was issued for a different address or chain")
        return record

    # Link or provision -------------------------------------------------------
    def _link_or_provision(
        self,
        *,
        address: str,
        chain_id: int,
        ip: str | None,
        user_agent: str | None,
    ) -> tuple[UserRecord, bool]:
        existing = self._siwe_store.get_wallet_identity(address=address)
        if existing is not None:
            user = self._identity_store.get_user(
                org_id=existing.org_id, user_id=existing.user_id
            )
            if user is None:
                self._record_attempt(
                    org_id=existing.org_id,
                    outcome=LoginAttemptOutcome.UNKNOWN_USER,
                    ip=ip,
                    user_agent=user_agent,
                    failure_reason="linked wallet points at a deleted user",
                )
                raise SiweUserNotProvisioned(
                    "linked wallet identity points at a deleted user"
                )
            return user, False

        if not self._allow_self_signup:
            self._record_attempt(
                org_id=SIWE_GLOBAL_ORG_ID,
                outcome=LoginAttemptOutcome.UNKNOWN_USER,
                ip=ip,
                user_agent=user_agent,
                failure_reason="self-signup disabled by deployment profile",
            )
            raise SiweSelfSignupDisabled(
                "wallet not linked and self-signup is disabled for this deployment"
            )

        truncated = truncated_display_address(address)
        checksummed = display_address(address)

        def _signup_audit_events(
            org: OrganizationRecord, user: UserRecord
        ) -> list[IdentityAuditEventRecord]:
            return [
                self._audit_event(
                    org_id=org.org_id,
                    user=user,
                    action="siwe.self_signup_org_created",
                    metadata={
                        "org_slug": org.slug,
                        "address": checksummed,
                        "chain_id": chain_id,
                    },
                    ip=ip,
                    user_agent=user_agent,
                ),
                self._audit_event(
                    org_id=org.org_id,
                    user=user,
                    action="siwe.user_provisioned",
                    metadata={"address": checksummed, "chain_id": chain_id},
                    ip=ip,
                    user_agent=user_agent,
                ),
            ]

        try:
            org, user = provision_personal_org(
                identity_store=self._identity_store,
                org_display_name=f"{truncated}'s Workspace",
                slug_base=f"{address[:6]}-{address[-4:]}",
                # Wallets carry no email. users.primary_email is NOT NULL, so
                # anchor a syntactically valid, undeliverable placeholder on
                # the reserved .invalid TLD (RFC 2606). Never verified.
                primary_email=f"{address}@wallet.invalid",
                user_display_name=truncated,
                email_verified_at=None,
                member_source=OrganizationMemberSource.SIWE,
                audit_events=_signup_audit_events,
            )
        except PersonalOrgSlugExhausted as exc:  # pragma: no cover - 32 collisions
            raise SiweError(str(exc)) from exc

        self._siwe_store.create_wallet_identity(
            WalletIdentityRecord(
                address=address,
                org_id=org.org_id,
                user_id=user.user_id,
                chain_id=chain_id,
            )
        )
        return user, True

    # Session mint (mirrors OidcService._mint_session) -------------------------
    def _mint_session(self, *, user: UserRecord) -> tuple[Any, bool]:
        role_records = self._identity_store.list_role_assignments(
            org_id=user.org_id, user_id=user.user_id
        )
        role_names: list[str] = []
        permission_scopes: set[str] = set()
        for assignment in role_records:
            role = self._identity_store.get_role(role_id=assignment.role_id)
            if role is None:
                continue
            role_names.append(role.name)
            permission_scopes.update(role.permission_scopes)
        if not role_names:
            role_names = ["employee"]
            employee = self._identity_store.get_role_by_name(
                org_id=None, name="employee"
            )
            if employee is not None:
                permission_scopes.update(employee.permission_scopes)
        mfa_required = (
            self._mfa is not None
            and self._mfa.policy_requires_mfa(org_id=user.org_id)
            and self._mfa.has_enabled_factor(org_id=user.org_id, user_id=user.user_id)
        )
        session_scopes: tuple[str, ...] = (
            ("mfa:pending",) if mfa_required else tuple(sorted(permission_scopes))
        )
        result = self._sessions.create(
            org_id=user.org_id,
            user_id=user.user_id,
            roles=tuple(role_names),
            permission_scopes=session_scopes,
            auth_provider_id=SIWE_PROVIDER_ID,
            device_label="siwe",
        )
        return result, mfa_required

    # Bookkeeping ----------------------------------------------------------
    def _record_attempt(
        self,
        *,
        org_id: str | None,
        outcome: LoginAttemptOutcome,
        ip: str | None,
        user_agent: str | None,
        user_id: str | None = None,
        failure_reason: str | None = None,
    ) -> None:
        self._identity_store.append_login_attempt(
            LoginAttemptRecord(
                org_id=org_id,
                user_id=user_id,
                auth_kind=LoginAttemptKind.SIWE,
                outcome=outcome,
                ip=ip,
                user_agent=user_agent,
                failure_reason=failure_reason,
            )
        )

    @staticmethod
    def _audit_event(
        *,
        org_id: str,
        user: UserRecord | None,
        action: str,
        metadata: dict[str, Any],
        ip: str | None = None,
        user_agent: str | None = None,
    ) -> IdentityAuditEventRecord:
        return IdentityAuditEventRecord(
            org_id=org_id,
            actor_user_id=user.user_id if user else None,
            subject_user_id=user.user_id if user else None,
            action=action,
            metadata={
                **metadata,
                "provider_id": SIWE_PROVIDER_ID,
                "provider_kind": "siwe",
            },
            request_ip=ip,
            user_agent=user_agent,
        )


__all__ = [
    "DEFAULT_ALLOWED_CHAIN_IDS",
    "ENV_SIWE_ALLOWED_CHAIN_IDS",
    "ENV_SIWE_ORIGIN",
    "NONCE_TTL_SECONDS",
    "SIWE_GLOBAL_ORG_ID",
    "SIWE_PROVIDER_ID",
    "SIWE_STATEMENT",
    "SiweAddressInvalid",
    "SiweChainNotAllowed",
    "SiweDomainMismatch",
    "SiweError",
    "SiweExpiredMessage",
    "SiweMessage",
    "SiweMessageInvalid",
    "SiweNonceExpired",
    "SiweNonceInvalid",
    "SiweRateLimited",
    "SiweSelfSignupDisabled",
    "SiweService",
    "SiweSignatureInvalid",
    "SiweUserNotProvisioned",
    "build_siwe_message",
    "display_address",
    "normalize_wallet_address",
    "parse_allowed_chain_ids",
    "parse_siwe_message",
    "truncated_display_address",
]
