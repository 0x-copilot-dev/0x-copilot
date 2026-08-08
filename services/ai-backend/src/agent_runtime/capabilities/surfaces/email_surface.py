"""The ``email://`` producer — the one rung whose scheme is not its archetype.

``packages/surface-renderers`` has shipped an ``EmailRenderer`` since Phase 4:
508 lines of composer chrome (To / Cc / Subject / Body, a word diff, a Send
button), an adapter registered on ``email`` at import, and a ``matches`` of
``uri.startsWith("email://")``. **Nothing in the tree ever minted an
``email://`` surface**, so the adapter has never been reached in the product.
This module is the missing half.

Why a scheme override exists at all
-----------------------------------
:meth:`SurfaceProjector._build_uri` spells a surface's identity
``<archetype>://<server-slug>/<tool>/<id>``, and ``SurfaceArchetype`` is frozen
at ten values by ``surface_spec.schema.json`` — there is no ``email`` among
them, and adding one would change a cross-language contract, relicense the spec
*generator* (``IMPLEMENTED_SURFACE_ARCHETYPES``), and hand the model a tenth
archetype it has no business authoring. So the archetype stays ``record``: an
email draft genuinely IS a single resource drawn as labelled fields, and every
consumer that reads the archetype (``_ArchetypeKind.resolve`` → the ledger's
``surface.created.kind``, the tier-3 fallback) keeps getting a true answer.

What changes is the *presentation scheme* — the segment before ``://``, which
is what the client's ``SurfaceRegistry.resolveAdapter`` keys the renderer on.
The design already reserved it: ``surfaceHue.ts`` maps ``email → ember`` beside
``table → jade`` and ``record → indigo``, a scheme no producer could emit.

Three properties this deliberately keeps:

* **One identity.** The minted string is the ``surface_id`` the ledger writes,
  the key the surfaces endpoint serves, the canvas tab key, and the renderer
  mount key — one value, no codec, no wrapper. That invariant is what the
  four-hop trace in ``artifacts_and_surfaces.py`` exists to police, and it cost
  this project a week the last time a second identity space was minted.
* **Host-side, never model-authored.** The match is a deterministic structural
  test run by the runtime over the connector's own payload. ``SurfaceSpec``
  stays read-only and gains no ``email`` vocabulary, so no model output can
  route a payload to the composer.
* **The spec still ships.** :meth:`EmailDraftSurface.spec_for` binds To / Cc /
  Subject / Body against the same normalised state, so a client with no
  ``EmailRenderer`` registered (an older desktop build, the web host, the
  tier-3 floor) draws an honest record of the same four fields rather than the
  "no adapter registered for email" placeholder.

Why the state is normalised
---------------------------
``EmailRenderer`` is not spec-driven. Its contract is the literal
``EmailState = {to, cc, subject, body, autoSavedLabel?}`` it has always had, and
it reads those keys off ``state.data``. So the producer — not the renderer —
does the mapping, from whatever the connector called its fields onto those four.
That is the same direction of travel the write lane takes (the model maps
*fields*, never values): the vocabulary translation happens once, in the
runtime, over the connector's own keys.

**No value is invented.** A field the payload does not carry becomes the empty
string, which the composer draws as an empty row. A ``Cc`` the user never saw is
precisely the failure this surface must not have, so ``cc`` is read only from a
cc-named key and is never back-filled from recipients, reply-to, or anything
else.
"""

from __future__ import annotations

__operation_boundary__ = "presentation"

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from agent_runtime.capabilities.surfaces.spec_models import (
    SurfaceArchetype,
    SurfaceField,
    SurfaceFieldFormat,
    SurfaceSource,
    SurfaceSpec,
)


@dataclass(frozen=True)
class EmailDraftMatch:
    """A payload recognised as an email draft, in the form a surface needs.

    ``state`` is the ``EmailState`` the renderer reads — exactly four string
    keys, no more. ``spec`` binds those same four so the generic view degrades
    honestly. ``id_basis`` is the ORIGINAL decoded payload, kept beside the
    normalised state purely so the URI's id segment can still be derived from a
    draft id the connector supplied: the normalised state carries no id by
    design, and hashing it instead would mint a new tab every time the same
    draft is re-read.
    """

    state: dict[str, str]
    spec: SurfaceSpec
    id_basis: object


class _Alias:
    """The connector-side spellings each ``EmailState`` slot is read from.

    Normalised (lower-case, alphanumerics only) so ``to_recipients``,
    ``toRecipients`` and ``ToRecipients`` are one entry rather than three.

    Chosen from what real mail APIs actually call these fields. The sets are
    deliberately narrow: a wider ``body`` vocabulary (``description``,
    ``summary``) would start matching issue trackers and ticket systems, and a
    false positive here does not render a slightly wrong table — it opens a
    composer with a Send button over a payload that is not a message.
    """

    TO: Final[frozenset[str]] = frozenset(
        {"to", "toaddress", "toaddresses", "torecipients", "recipient", "recipients"}
    )
    CC: Final[frozenset[str]] = frozenset(
        {"cc", "ccaddress", "ccaddresses", "ccrecipients", "carboncopy"}
    )
    SUBJECT: Final[frozenset[str]] = frozenset({"subject", "subjectline"})
    BODY: Final[frozenset[str]] = frozenset(
        {"body", "bodytext", "bodyplain", "plaintext", "text", "message"}
    )


class _StateKey:
    """The ``EmailState`` keys. Mirrors ``EmailRenderer.tsx``'s interface."""

    TO: Final[str] = "to"
    CC: Final[str] = "cc"
    SUBJECT: Final[str] = "subject"
    BODY: Final[str] = "body"


class _Label:
    """The composer's row labels, reused by the spec so both views agree."""

    TO: Final[str] = "To"
    CC: Final[str] = "Cc"
    SUBJECT: Final[str] = "Subject"
    BODY: Final[str] = "Body"


class EmailDraftSurface:
    """Recognise an email draft in a connector payload and shape it.

    Total and pure: every entry point is safe over an untrusted payload of any
    shape and returns ``None`` rather than raising. Called on the tool-result
    path, where an exception would fail the tool call itself.
    """

    #: The presentation scheme. ``EmailRenderer``'s adapter matches
    #: ``uri.startsWith("email://")``; ``surfaceHue.ts`` maps it to ember.
    SCHEME: Final[str] = "email"

    #: An email draft is a single resource drawn as labelled fields.
    ARCHETYPE: Final[SurfaceArchetype] = SurfaceArchetype.RECORD

    #: Provenance for a draft whose call named no connector. Mirrors
    #: :data:`~agent_runtime.capabilities.surfaces.infer.INFERRED_SOURCE`'s
    #: role: a last-resort label, never something a user normally reads.
    FALLBACK_SOURCE: Final[SurfaceSource] = SurfaceSource(server="email", tool="draft")

    #: Envelope keys peeled before matching. ``EnvelopeUnwrapper`` already
    #: removes the generic ones (``result`` / ``data`` / ``structured_content``);
    #: these are the draft-specific single-object wrappers a mail connector adds
    #: on top, which it deliberately leaves alone because they are not in its
    #: wrapper vocabulary.
    _DRAFT_WRAPPERS: Final[frozenset[str]] = frozenset(
        {"draft", "email", "message", "reply", "compose", "composed"}
    )

    #: How many recipients are joined into the composer's single-line To/Cc row
    #: before the rest are summarised. A list-valued recipients field is common
    #: and unbounded; the composer row is one line.
    _MAX_RECIPIENTS: Final[int] = 8

    _NON_ALNUM: Final[re.Pattern[str]] = re.compile(r"[^a-z0-9]+")
    _RECIPIENT_SEPARATOR: Final[str] = ", "

    # -- entry point ---------------------------------------------------------

    @classmethod
    def match(
        cls, payload: object, *, source: SurfaceSource | None = None
    ) -> EmailDraftMatch | None:
        """Return the email surface for ``payload``, or ``None``.

        ``None`` for anything that is not unambiguously a draft. The bar is all
        three of a recipient, a subject and a body — two of the three is an
        issue with a title and a description, and drawing that as a composer
        would put a Send button over something nobody meant to send.
        """

        document = cls._unwrap(payload)
        if not isinstance(document, Mapping):
            return None
        by_slot = cls._slots(document)
        if by_slot is None:
            return None
        return EmailDraftMatch(
            state=by_slot,
            spec=cls.spec_for(source),
            id_basis=document,
        )

    @classmethod
    def spec_for(cls, source: SurfaceSource | None) -> SurfaceSpec:
        """The record spec that binds the normalised state's four fields.

        Its paths are the ``EmailState`` keys, so it resolves against exactly
        the value shipped as ``state.data`` — the invariant
        :meth:`SurfaceProjector._decoded` exists to protect, applied to this
        rung. ``title_path`` is the subject, which is what a mail client titles
        a draft by.
        """

        return SurfaceSpec(
            spec_version=1,
            archetype=cls.ARCHETYPE,
            source=source or cls.FALLBACK_SOURCE,
            title_path=_StateKey.SUBJECT,
            subtitle_path=_StateKey.TO,
            fields=[
                SurfaceField(
                    label=_Label.TO,
                    path=_StateKey.TO,
                    format=SurfaceFieldFormat.USER,
                ),
                SurfaceField(
                    label=_Label.CC,
                    path=_StateKey.CC,
                    format=SurfaceFieldFormat.USER,
                ),
                SurfaceField(label=_Label.SUBJECT, path=_StateKey.SUBJECT),
                SurfaceField(label=_Label.BODY, path=_StateKey.BODY),
            ],
        )

    # -- structural recognition ----------------------------------------------

    @classmethod
    def _unwrap(cls, payload: object) -> object:
        """Peel one draft-specific single-object wrapper, at most.

        One level, not a loop: the generic envelopes are already gone by the
        time a payload reaches here, and an unbounded walk over an untrusted
        document is how a self-similar payload becomes a hot loop.
        """

        if not isinstance(payload, Mapping):
            return payload
        keys = [key for key in payload if isinstance(key, str)]
        if len(keys) != 1:
            return payload
        if cls._normalized(keys[0]) not in cls._DRAFT_WRAPPERS:
            return payload
        inner = payload[keys[0]]
        return inner if isinstance(inner, Mapping) else payload

    @classmethod
    def _slots(cls, document: Mapping[str, object]) -> dict[str, str] | None:
        """Map the document's keys onto the four ``EmailState`` slots.

        ``None`` unless a recipient, a subject AND a body all resolved to
        non-empty text. ``cc`` is optional and defaults to the empty string —
        it is the one slot that must never be inferred from anything but a
        cc-named key.
        """

        to = cls._first(document, _Alias.TO)
        subject = cls._first(document, _Alias.SUBJECT)
        body = cls._first(document, _Alias.BODY)
        if not to or not subject or not body:
            return None
        return {
            _StateKey.TO: to,
            _StateKey.CC: cls._first(document, _Alias.CC) or "",
            _StateKey.SUBJECT: subject,
            _StateKey.BODY: body,
        }

    @classmethod
    def _first(
        cls, document: Mapping[str, object], aliases: frozenset[str]
    ) -> str | None:
        """The first key in ``document`` whose name is in ``aliases``, as text.

        Iterates the document rather than the alias set so a payload carrying
        two spellings resolves by ITS declaration order — deterministic, and
        not decided by the arbitrary order of a ``frozenset``.
        """

        for key, value in document.items():
            if not isinstance(key, str):
                continue
            if cls._normalized(key) not in aliases:
                continue
            text = cls._as_text(value)
            if text:
                return text
        return None

    # -- value flattening ----------------------------------------------------

    @classmethod
    def _as_text(cls, value: object) -> str | None:
        """Flatten a slot value to the single string the composer row draws.

        Handles the three shapes a mail payload uses for a recipient: a bare
        string, a list of them, and the ``{"name": …, "email": …}`` object form.
        Anything else (a number, a nested document, ``None``) is not a slot
        value and answers ``None``, which fails the match rather than printing
        a repr into a To row.
        """

        if isinstance(value, str):
            return value.strip() or None
        if isinstance(value, Mapping):
            return cls._address_of(value)
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            return cls._addresses_of(value)
        return None

    @classmethod
    def _address_of(cls, value: Mapping[str, object]) -> str | None:
        """One recipient object as text: its address, else its display name."""

        for key in ("email", "address", "emailaddress", "name", "displayname"):
            for raw_key, raw_value in value.items():
                if not isinstance(raw_key, str):
                    continue
                if cls._normalized(raw_key) != key:
                    continue
                if isinstance(raw_value, str) and raw_value.strip():
                    return raw_value.strip()
        return None

    @classmethod
    def _addresses_of(cls, values: Sequence[object]) -> str | None:
        """A recipient list as one comma-joined row, capped and then counted.

        The cap is not cosmetic. The composer's To row is a single line and the
        list is connector-supplied, so an unbounded join is an unbounded string
        on a surface whose Send button must stay reachable — the same shrink
        rule the write-gate header carries, applied at the producer.
        """

        parts = [text for text in (cls._as_text(item) for item in values) if text]
        if not parts:
            return None
        if len(parts) <= cls._MAX_RECIPIENTS:
            return cls._RECIPIENT_SEPARATOR.join(parts)
        shown = parts[: cls._MAX_RECIPIENTS]
        return (
            f"{cls._RECIPIENT_SEPARATOR.join(shown)}"
            f" +{len(parts) - cls._MAX_RECIPIENTS} more"
        )

    @classmethod
    def _normalized(cls, key: str) -> str:
        """Lower-case, alphanumerics only — the form the alias sets are keyed by."""

        return cls._NON_ALNUM.sub("", key.lower())


__all__ = ["EmailDraftMatch", "EmailDraftSurface"]
