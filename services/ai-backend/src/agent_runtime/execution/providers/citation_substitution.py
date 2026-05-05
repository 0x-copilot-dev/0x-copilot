"""Provider-agnostic citation substitution (PR 1.1 follow-up D scaffold).

Both the Anthropic ``citations_delta`` adapter and the OpenAI Responses
``output_text.done`` annotation adapter need the same primitive: given a
text fragment plus a list of source descriptors that the provider says
were grounded *into that fragment*, register each source with the active
:class:`CitationLedger` and rewrite the fragment so the citation tokens
appear inline.

This module is the seam they share. It owns nothing provider-specific —
just the registration loop and the token substitution algorithm — so the
adapters reduce to a small input adapter (provider event → list of
``CitationCandidate``) plus calling :func:`substitute_citations`.

When the active ledger is unbound (citations disabled, no run context),
the function returns the input text unchanged. Tools / providers stay
correct without citations; chips simply don't appear.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from agent_runtime.capabilities.citations import CitationLedger, SourceRef


@dataclass(frozen=True)
class CitationCandidate:
    """One source the provider says is grounded into a span of the output.

    ``span`` is the inclusive-exclusive ``(start, end)`` character range in
    the *original* text fragment that this citation grounds. The
    substitution algorithm applies tokens in span order, walking
    right-to-left so earlier replacements don't shift later spans.

    For end-of-turn drainers (OpenAI Responses) the spans cover the
    entire assembled output text. For interleaved deltas (Anthropic) each
    candidate's span is the span of the surrounding text delta.
    """

    source: SourceRef
    span: tuple[int, int]


class CitationSubstitution:
    """Stateless substitution helper. The class wraps the rewriting algorithm
    so the adapters only ever talk to one well-named entry point.
    """

    @classmethod
    async def apply(
        cls,
        *,
        text: str,
        candidates: Sequence[CitationCandidate],
    ) -> str:
        """Register every candidate, then rewrite ``text`` with their tokens.

        Substitution is idempotent on the candidate's source key: a
        provider that emits the same ``(connector, doc_id)`` twice in
        one turn yields the same token both times, no duplicate event,
        no duplicate row.

        Returns the rewritten text. When no ledger is bound, returns
        ``text`` unchanged so the runtime path stays correct.
        """

        ledger = CitationLedger.active()
        if ledger is None or not candidates:
            return text
        # Register left-to-right so ordinals reflect document order, then
        # apply the rewrites right-to-left so each insertion keeps earlier
        # spans valid.
        forward = sorted(candidates, key=lambda candidate: candidate.span[0])
        tokens: list[tuple[CitationCandidate, str]] = []
        for candidate in forward:
            token = await ledger.register(candidate.source)
            if not token:
                continue
            tokens.append((candidate, token))
        rewritten = text
        for candidate, token in reversed(tokens):
            _, end = cls._clamp_span(candidate.span, len(rewritten))
            rewritten = rewritten[:end] + token + rewritten[end:]
        return rewritten

    @staticmethod
    def _clamp_span(span: tuple[int, int], length: int) -> tuple[int, int]:
        start, end = span
        start = max(0, min(start, length))
        end = max(start, min(end, length))
        return start, end
