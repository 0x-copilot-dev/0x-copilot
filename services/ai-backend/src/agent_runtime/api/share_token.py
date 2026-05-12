"""Share token mint and verify for conversation sharing.

Tokens are bearer secrets carried in the share URL. We hash with sha256 and
store the digest in ``conversation_shares.share_token_hash`` (same pattern
as ``scim_tokens.token_hash``). The plaintext is returned exactly once at
create time.

We deliberately do **not** route through ``backend.token_vault.TokenVault``:
TokenVault wraps tokens we need to *re-present* to a third party (OAuth
refresh tokens). Share tokens are compared on the way in via hash equality,
never re-presented; vaulting them would force a KMS dependency for no
benefit and add latency to the recipient lookup.

The ``ShareTokenSecret`` wrapper redacts on ``repr`` / ``str`` so that the
plaintext doesn't accidentally land in logs, OTel spans, or audit metadata.
Call sites that genuinely need the plaintext call ``.expose()`` explicitly
(those are the create-response writer and the share-URL builder, both
audited).
"""

from __future__ import annotations

import hashlib
import secrets


class _Token:
    """Token shape constants — kept here so the SQL column comments stay in sync."""

    PREFIX = "s_"
    """Namespace: lets log greppers spot share tokens vs SCIM tokens vs OAuth."""

    BODY_BYTES = 24
    """``secrets.token_urlsafe(24)`` → 32 base64-url chars, ≥ 192 bits of entropy."""

    PREFIX_LEN_FOR_UI = 8
    """Plaintext characters surfaced to UI after ``s_`` (matches SCIM ``token_prefix``)."""


class ShareTokenSecret(str):
    """A wrapped str that redacts on repr/format/log to prevent accidental leakage.

    The class subclasses ``str`` so existing string APIs accept it, but
    overrides ``__repr__`` / ``__str__`` to surface only the prefix. Any
    code path that needs the real plaintext must call ``.expose()`` —
    that's the deliberate, grep-able opt-in.
    """

    __slots__ = ()

    def __repr__(self) -> str:
        return f"ShareTokenSecret({self.prefix()}…)"

    def __format__(self, format_spec: str) -> str:  # noqa: D401
        # ``f"{token}"`` and ``"{}".format(token)`` both route through here.
        return self.__repr__()

    def prefix(self) -> str:
        """Return ``s_`` + first ``PREFIX_LEN_FOR_UI`` plaintext chars."""

        cutoff = len(_Token.PREFIX) + _Token.PREFIX_LEN_FOR_UI
        return str.__str__(self)[:cutoff]

    def expose(self) -> str:
        """Explicit unwrap. Caller is responsible for not logging the result."""

        return str.__str__(self)


class ShareTokenIssuer:
    """Mints + verifies bearer tokens for conversation shares.

    Pure stateless — every method is ``staticmethod``. Lives in a class
    only so the call sites read ``ShareTokenIssuer.mint()`` /
    ``.hash(plaintext)`` instead of free functions, which keeps the
    "this is a security boundary, not a string utility" intent visible.
    """

    @staticmethod
    def mint() -> tuple[ShareTokenSecret, str, str]:
        """Generate one share token. Returns ``(plaintext, sha256_hex, prefix)``.

        The plaintext is wrapped in :class:`ShareTokenSecret` so accidental
        ``logger.info(f"{token=}")`` calls produce a redacted form. Callers
        must ``.expose()`` to put the plaintext on the wire.
        """

        body = secrets.token_urlsafe(_Token.BODY_BYTES)
        plaintext = ShareTokenSecret(f"{_Token.PREFIX}{body}")
        digest = ShareTokenIssuer.hash(plaintext.expose())
        return plaintext, digest, plaintext.prefix()

    @staticmethod
    def hash(plaintext: str) -> str:
        """Return the canonical sha256 hex digest used for column lookup."""

        return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()
