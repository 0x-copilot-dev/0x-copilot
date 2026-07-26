"""Server-derived trust facts for the connector consent card.

The card the user sees before granting a connector reads, in the design:

    Read-only · OAuth on linear.app · revoke anytime

Every clause in that line is a promise. If any of it were derived from model
output, an agent could ask for write access under a "Read-only" label, or name a
sign-in host different from the one the browser is about to open. So the scope
comes from the connector row and the host comes from the *issued* auth URL, and
a clause with no trustworthy source is omitted rather than guessed.

Both connector-auth surfaces use this: the blocking ``auth_mcp`` gate and the
non-blocking ``suggest_mcp_connector`` suggestion.
"""

from __future__ import annotations

from urllib.parse import urlparse


class ConnectorTrustLine:
    """Derive the consent card's trust clauses from server-owned inputs only."""

    @staticmethod
    def auth_host(auth_url: str) -> str:
        """Return the host the user will actually sign in at, or ``""`` if unknown.

        Empty is a meaningful answer, not a failure: catalog suggestions and
        providers without OAuth have no auth session yet, and naming a plausible
        host we haven't issued a redirect to would be the exact lie this
        function exists to prevent.
        """

        if not auth_url:
            return ""
        try:
            host = urlparse(auth_url).hostname or ""
        except ValueError:
            # A malformed URL means a broken auth session, not a card to decorate.
            return ""
        return host.removeprefix("www.")
