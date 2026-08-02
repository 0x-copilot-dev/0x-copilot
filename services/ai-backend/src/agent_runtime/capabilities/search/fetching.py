"""Parallel, bounded, polite page fetching for the local extraction path.

Three bounds, all of them load-bearing:

* a **hard per-fetch timeout** — one slow origin must not decide the turn's
  latency;
* a **whole-search wall clock** — fetches still running when it expires are
  abandoned and their sources fall back to their snippets (PRD AC6);
* a **body cap** — a page is read up to a byte ceiling and then truncated,
  because a search result may point at something that is not a web page.

Politeness is the fourth: a concurrency cap, an identifiable client string, and
``robots.txt`` admission per origin.

The transport is a port. Production speaks httpx; tests inject a fake, which is
what keeps this package's tests free of network without also making them fake
the parallelism, the deadline arithmetic, or the URL guard — the parts that
actually break.
"""

from __future__ import annotations

import ipaddress
import logging
import time
from collections.abc import Callable, Sequence
from concurrent.futures import Future, ThreadPoolExecutor, wait
from typing import Final, Protocol, runtime_checkable
from urllib import robotparser
from urllib.parse import urlsplit, urlunsplit

from agent_runtime.capabilities.search.contracts import (
    FetchedPage,
    SearchFetchBudgets,
    SourceStatus,
    TransportResponse,
)

_LOGGER = logging.getLogger(__name__)

#: Identifies this client to every origin it fetches from. A fetch that cannot
#: be attributed is the impolite kind.
CLIENT_IDENTITY = "0xCopilot/1.0 (+https://0xcopilot.tech; desktop agent)"


@runtime_checkable
class PageTransportPort(Protocol):
    """Fetch one URL synchronously under a caller-supplied deadline."""

    def get(
        self, *, url: str, timeout_seconds: float, max_bytes: int
    ) -> TransportResponse:
        """Return the response, or raise for any transport-level failure."""
        ...


class RedirectTargetRefused(Exception):
    """A redirect pointed somewhere `FetchTargetPolicy` refuses.

    Raised rather than returned because it travels the same path as any other
    transport failure: `SearchFetcher.fetch_one` already converts an exception
    into `SourceStatus.FETCH_FAILED`, so a refused hop degrades to "this source
    yielded nothing" and the snippet fallback carries the result — never to a
    failed run, and never to the body actually being read.
    """

    def __init__(self, url: str) -> None:
        """Record the refused URL without putting it in a model-visible string."""

        super().__init__("redirect target refused by fetch policy")
        self.url = url


class HttpxPageTransport:
    """The production transport: streamed, byte-capped, identified httpx GETs."""

    class Headers:
        USER_AGENT = "User-Agent"
        ACCEPT = "Accept"
        CONTENT_TYPE = "content-type"
        LOCATION = "location"

    class Values:
        ACCEPT = "text/html,application/xhtml+xml"
        DEFAULT_ENCODING = "utf-8"
        DECODE_ERRORS = "replace"
        METHOD = "GET"

    #: How many redirects to follow before giving up. httpx's own default is 20;
    #: a search result that needs more than a handful of hops is not a page we
    #: want, and every extra hop is another target to re-validate.
    MAX_REDIRECTS: Final[int] = 5

    def __init__(
        self,
        *,
        identity: str = CLIENT_IDENTITY,
        targets: "FetchTargetPolicy | None" = None,
    ) -> None:
        """Bind the client identity and the per-hop target policy.

        ``targets`` is not optional in spirit: see :meth:`get`. It defaults so a
        caller cannot construct a transport with NO guard by omission.
        """

        self._identity = identity
        self._targets = targets or FetchTargetPolicy()

    def get(
        self, *, url: str, timeout_seconds: float, max_bytes: int
    ) -> TransportResponse:
        """Stream ``url`` up to ``max_bytes`` and decode it to text.

        A fresh client per fetch rather than the shared
        :class:`~agent_runtime.capabilities.http_pool.BackendHttpPool`: that pool
        is an *async* client scoped to ai-backend → backend calls, and these
        fetches are synchronous and go to four unrelated public origins, where
        there is no connection to reuse anyway.
        """

        import httpx  # noqa: PLC0415 - lazy: only an active fetch path needs it

        headers = {
            self.Headers.USER_AGENT: self._identity,
            self.Headers.ACCEPT: self.Values.ACCEPT,
        }
        # `follow_redirects=False` plus a manual loop, because
        # `FetchTargetPolicy` runs only on the URL the caller handed us and
        # httpx follows a redirect INTERNALLY — so the guard saw hop 1 and
        # nothing else. A public page answering `302 -> http://127.0.0.1:8100/`
        # walked straight past the check this policy's own docstring describes,
        # and the loopback body reached the extractor and the on-disk cache.
        # Confirmed against a local server before this changed, not theorised.
        #
        # One GET per hop, and the body is read only on the hop that is not a
        # redirect — a non-redirecting URL still costs exactly one request.
        with httpx.Client(
            follow_redirects=False, timeout=timeout_seconds, headers=headers
        ) as client:
            target = url
            for _hop in range(self.MAX_REDIRECTS + 1):
                with client.stream(self.Values.METHOD, target) as response:
                    location = (
                        response.headers.get(self.Headers.LOCATION)
                        if response.is_redirect
                        else None
                    )
                    if not location:
                        # Terminal — including a redirect status with no
                        # Location, which has nowhere to go and is not worth a
                        # separate failure mode.
                        body = bytearray()
                        truncated = False
                        for chunk in response.iter_bytes():
                            body.extend(chunk)
                            if len(body) >= max_bytes:
                                truncated = True
                                break
                        return TransportResponse(
                            status_code=response.status_code,
                            content_type=response.headers.get(
                                self.Headers.CONTENT_TYPE, ""
                            ),
                            text=self._decode(bytes(body), response.charset_encoding),
                            truncated=truncated,
                        )
                    next_target = str(response.url.join(location))
                if not self._targets.allows(next_target):
                    raise RedirectTargetRefused(next_target)
                target = next_target
            raise RedirectTargetRefused(target)

    @classmethod
    def _decode(cls, body: bytes, encoding: str | None) -> str:
        """Decode ``body``, tolerating a missing or bogus declared encoding."""

        try:
            return body.decode(
                encoding or cls.Values.DEFAULT_ENCODING, errors=cls.Values.DECODE_ERRORS
            )
        except LookupError:
            return body.decode(
                cls.Values.DEFAULT_ENCODING, errors=cls.Values.DECODE_ERRORS
            )


class FetchTargetPolicy:
    """Decide whether a URL handed to us by a search engine may be fetched.

    Search results are untrusted input. On the desktop the fetch runs on the
    user's own machine, next to their own loopback services — so a result
    pointing at ``http://127.0.0.1:8100/...`` or a link-local metadata address
    would turn ``web_search`` into a request forwarder for whatever the model
    was talked into searching for. Literal private/loopback hosts are refused
    here; DNS rebinding is out of scope and stated as such rather than implied.
    """

    class Values:
        ALLOWED_SCHEMES = ("http", "https")
        BLOCKED_HOSTNAMES = ("localhost",)

    def allows(self, url: str) -> bool:
        """Return whether ``url`` is a public http(s) target."""

        try:
            parts = urlsplit(url)
        except ValueError:
            return False
        if parts.scheme not in self.Values.ALLOWED_SCHEMES:
            return False
        host = (parts.hostname or "").strip().lower()
        if not host or host in self.Values.BLOCKED_HOSTNAMES:
            return False
        return self._is_public(host)

    @staticmethod
    def _is_public(host: str) -> bool:
        """Return whether a host literal is a public address (names pass)."""

        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            return True
        return not (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_reserved
            or address.is_multicast
        )


class RobotsPolicy:
    """Per-origin ``robots.txt`` admission, cached for this policy's lifetime.

    Lifetime is one RUN, not one process, and the difference is worth stating
    because the earlier wording claimed the latter:
    ``WebSearchToolRegistry()`` is constructed inline per run
    (``runtime_worker/dependencies.py``), so each run builds a fresh policy and
    re-fetches ``robots.txt`` for every origin it touches. That is the honest
    bound. A genuinely process-wide cache would need an eviction policy — an
    unbounded per-origin map in a long-lived worker is a leak, not an
    optimisation — so the smaller scope is deliberate, not accidental.

    Fails **open**: a robots.txt that 404s, times out, or will not parse leaves
    the origin allowed. This is a user-directed fetch of a page the user's agent
    was just told about, not a crawl, and an unreachable policy file is not
    evidence of a prohibition — treating it as one would silently disable the
    feature for every origin behind a flaky edge.
    """

    class Values:
        PATH = "/robots.txt"
        MAX_BYTES = 65_536
        TIMEOUT_SECONDS = 3.0
        EMPTY_PATH = ""

    def __init__(
        self,
        *,
        transport: PageTransportPort,
        timeout_seconds: float | None = None,
    ) -> None:
        """Bind the transport used to read ``robots.txt`` and its own timeout."""

        self._transport = transport
        self._timeout_seconds = (
            timeout_seconds
            if timeout_seconds is not None
            else self.Values.TIMEOUT_SECONDS
        )
        self._parsers: dict[str, robotparser.RobotFileParser | None] = {}

    def allows(self, url: str, *, identity: str = CLIENT_IDENTITY) -> bool:
        """Return whether ``identity`` may fetch ``url`` per its origin's robots.txt."""

        parts = urlsplit(url)
        origin = urlunsplit(
            (
                parts.scheme,
                parts.netloc,
                self.Values.EMPTY_PATH,
                self.Values.EMPTY_PATH,
                self.Values.EMPTY_PATH,
            )
        )
        parser = self._parser(origin)
        if parser is None:
            return True
        try:
            return parser.can_fetch(identity, url)
        except Exception:  # noqa: BLE001 - a malformed rule must not block the fetch
            return True

    def _parser(self, origin: str) -> robotparser.RobotFileParser | None:
        """Return the cached parser for ``origin``; ``None`` means allow-all."""

        if origin in self._parsers:
            return self._parsers[origin]
        parser: robotparser.RobotFileParser | None = None
        try:
            response = self._transport.get(
                url=f"{origin}{self.Values.PATH}",
                timeout_seconds=self._timeout_seconds,
                max_bytes=self.Values.MAX_BYTES,
            )
            if response.status_code < 400 and response.text:
                parser = robotparser.RobotFileParser()
                parser.parse(response.text.splitlines())
        except Exception:  # noqa: BLE001 - see the fail-open note on the class
            parser = None
        self._parsers[origin] = parser
        return parser


class PageFetcher:
    """Fetch candidate pages in parallel under a per-fetch and whole-search bound."""

    class Values:
        HTML_CONTENT_TYPES = ("text/html", "application/xhtml+xml")
        #: A response with no content-type at all is attempted rather than
        #: refused: plenty of real pages omit it, and the extractor is the
        #: cheaper place to discover the body is not markup.
        EMPTY_CONTENT_TYPE = ""
        HTTP_ERROR_FLOOR = 400
        THREAD_NAME_PREFIX = "web-search-fetch"

    class Log:
        FAILED = "search.fetch_failed url=%s"
        ABANDONED = "search.fetch_abandoned url=%s reason=wall_clock_budget"

    def __init__(
        self,
        *,
        budgets: SearchFetchBudgets | None = None,
        transport: PageTransportPort | None = None,
        robots: RobotsPolicy | None = None,
        targets: FetchTargetPolicy | None = None,
        identity: str = CLIENT_IDENTITY,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Bind budgets and the fetch-path collaborators (all injectable for tests).

        ``robots`` defaults to a policy over the same transport. Passing
        ``robots=None`` explicitly is not "no robots" — a caller that wants no
        robots check passes a policy that allows everything, so the default can
        never be lost by accident.
        """

        self._budgets = budgets or SearchFetchBudgets()
        self._transport = transport or HttpxPageTransport(identity=identity)
        self._robots = robots or RobotsPolicy(transport=self._transport)
        self._targets = targets or FetchTargetPolicy()
        self._identity = identity
        self._clock = clock

    def fetch(self, urls: Sequence[str], *, deadline: float) -> dict[str, FetchedPage]:
        """Fetch every URL in parallel, returning one outcome per **unique** URL.

        ``deadline`` is an absolute reading of this fetcher's clock. Anything
        still running when it passes is abandoned with ``FETCH_TIMEOUT`` — the
        caller reads that as "use the snippet", so a stalled origin costs the
        turn nothing beyond the budget it was already given.
        """

        targets = tuple(dict.fromkeys(url for url in urls if url))
        if not targets:
            return {}
        pool = ThreadPoolExecutor(
            max_workers=min(self._budgets.max_parallel_fetches, len(targets)),
            thread_name_prefix=self.Values.THREAD_NAME_PREFIX,
        )
        try:
            pending: dict[Future[FetchedPage], str] = {
                pool.submit(self.fetch_one, url, deadline=deadline): url
                for url in targets
            }
            done, unfinished = wait(pending, timeout=self._remaining(deadline))
            pages = {
                pending[future]: self._settled(pending[future], future)
                for future in done
            }
            for future in unfinished:
                url = pending[future]
                future.cancel()
                _LOGGER.info(self.Log.ABANDONED, url)
                pages[url] = FetchedPage(url=url, failure=SourceStatus.FETCH_TIMEOUT)
            return pages
        finally:
            # Never wait: the point of the wall clock is that a straggler stops
            # costing the turn latency. Each in-flight request is still bounded
            # by its own per-fetch timeout, so no thread outlives that.
            pool.shutdown(wait=False, cancel_futures=True)

    def fetch_one(self, url: str, *, deadline: float) -> FetchedPage:
        """Fetch a single URL, reporting every failure as a value."""

        if not self._targets.allows(url):
            return FetchedPage(url=url, failure=SourceStatus.UNSUPPORTED_URL)
        remaining = self._remaining(deadline)
        if remaining <= 0:
            return FetchedPage(url=url, failure=SourceStatus.FETCH_TIMEOUT)
        if not self._robots.allows(url, identity=self._identity):
            return FetchedPage(url=url, failure=SourceStatus.ROBOTS_DISALLOWED)
        timeout = min(
            self._budgets.per_fetch_timeout_seconds, self._remaining(deadline)
        )
        if timeout <= 0:
            return FetchedPage(url=url, failure=SourceStatus.FETCH_TIMEOUT)
        try:
            response = self._transport.get(
                url=url,
                timeout_seconds=timeout,
                max_bytes=self._budgets.max_page_bytes,
            )
        except Exception:  # noqa: BLE001 - transport failure is a fallback, not an error
            _LOGGER.info(self.Log.FAILED, url, exc_info=True)
            return FetchedPage(url=url, failure=SourceStatus.FETCH_FAILED)
        return self._page(url, response)

    def _page(self, url: str, response: TransportResponse) -> FetchedPage:
        """Map a transport response onto the fetch outcome for one source."""

        if response.status_code >= self.Values.HTTP_ERROR_FLOOR:
            return FetchedPage(url=url, failure=SourceStatus.FETCH_FAILED)
        if not self._is_html(response.content_type):
            return FetchedPage(url=url, failure=SourceStatus.NOT_HTML)
        if not response.text:
            return FetchedPage(url=url, failure=SourceStatus.FETCH_FAILED)
        return FetchedPage(url=url, html=response.text)

    def _is_html(self, content_type: str) -> bool:
        """Return whether ``content_type`` is markup this pipeline can extract."""

        declared = content_type.split(";")[0].strip().lower()
        if declared == self.Values.EMPTY_CONTENT_TYPE:
            return True
        return declared in self.Values.HTML_CONTENT_TYPES

    def _settled(self, url: str, future: Future[FetchedPage]) -> FetchedPage:
        """Read a completed future, turning a raised worker into a fallback."""

        try:
            return future.result()
        except Exception:  # noqa: BLE001 - a crashed worker is one lost source
            _LOGGER.warning(self.Log.FAILED, url, exc_info=True)
            return FetchedPage(url=url, failure=SourceStatus.FETCH_FAILED)

    def _remaining(self, deadline: float) -> float:
        """Return the seconds left before the whole-search wall clock expires."""

        return max(deadline - self._clock(), 0.0)


__all__ = (
    "CLIENT_IDENTITY",
    "FetchTargetPolicy",
    "HttpxPageTransport",
    "PageFetcher",
    "PageTransportPort",
    "RobotsPolicy",
)
