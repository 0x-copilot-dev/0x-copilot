from __future__ import annotations

import contextlib
import http.server
import socketserver
import threading
from collections.abc import Iterator

import time

import pytest

from agent_runtime.capabilities.search.contracts import (
    SearchFetchBudgets,
    SourceStatus,
    TransportResponse,
)
from agent_runtime.capabilities.search.fetching import (
    FetchTargetPolicy,
    HttpxPageTransport,
    RedirectTargetRefused,
    PageFetcher,
    RobotsPolicy,
)


class RecordingTransport:
    """A page transport that answers from a script and records every call."""

    HTML = "text/html; charset=utf-8"

    def __init__(
        self,
        *,
        responses: dict[str, TransportResponse] | None = None,
        default: TransportResponse | None = None,
        error: Exception | None = None,
        delay_seconds: float = 0.0,
        release: threading.Event | None = None,
    ) -> None:
        self.responses = responses or {}
        self.default = default
        self.error = error
        self.delay_seconds = delay_seconds
        self.release = release
        self.calls: list[dict[str, object]] = []
        self.in_flight = 0
        self.peak_in_flight = 0
        self._lock = threading.Lock()

    def get(
        self, *, url: str, timeout_seconds: float, max_bytes: int
    ) -> TransportResponse:
        with self._lock:
            self.calls.append(
                {
                    "url": url,
                    "timeout_seconds": timeout_seconds,
                    "max_bytes": max_bytes,
                }
            )
            self.in_flight += 1
            self.peak_in_flight = max(self.peak_in_flight, self.in_flight)
        try:
            if self.release is not None:
                self.release.wait(timeout=5.0)
            elif self.delay_seconds:
                time.sleep(self.delay_seconds)
            if self.error is not None:
                raise self.error
            if url in self.responses:
                return self.responses[url]
            if self.default is not None:
                return self.default
            return TransportResponse(
                status_code=200, content_type=self.HTML, text=f"<html>{url}</html>"
            )
        finally:
            with self._lock:
                self.in_flight -= 1

    @property
    def fetched_urls(self) -> list[str]:
        return [str(call["url"]) for call in self.calls]


class AllowAllRobots:
    """Robots policy stand-in: the fetch tests are not testing robots."""

    def allows(self, url: str, *, identity: str = "") -> bool:
        return True


class FetcherMixin:
    URLS = (
        "https://example.test/a",
        "https://example.test/b",
        "https://example.test/c",
    )

    def _fetcher(self, transport: RecordingTransport, **kwargs: object) -> PageFetcher:
        options: dict[str, object] = {
            "transport": transport,
            "robots": AllowAllRobots(),
        }
        options.update(kwargs)
        return PageFetcher(**options)  # type: ignore[arg-type]


class TestFetchTargetPolicy:
    @pytest.mark.parametrize(
        "url",
        [
            "https://example.test/page",
            "http://example.test/page",
            "https://sub.domain.example/page?q=1",
        ],
    )
    def test_allows_public_http_targets(self, url: str) -> None:
        assert FetchTargetPolicy().allows(url) is True

    @pytest.mark.parametrize(
        "url",
        [
            # A search result is untrusted input, and on the desktop this fetch
            # runs next to the user's own loopback services.
            "http://127.0.0.1:8100/internal/v1/secrets",
            "http://localhost:8200/v1/me/profile",
            "http://10.0.0.5/admin",
            "http://192.168.1.1/",
            "http://169.254.169.254/latest/meta-data/",
            "http://[::1]/",
            "file:///etc/passwd",
            "ftp://example.test/file",
            "not-a-url",
            "",
        ],
    )
    def test_refuses_non_public_or_non_http_targets(self, url: str) -> None:
        assert FetchTargetPolicy().allows(url) is False


class TestPageFetcher(FetcherMixin):
    def test_fetches_every_url_and_returns_one_page_each(self) -> None:
        transport = RecordingTransport()

        pages = self._fetcher(transport).fetch(
            self.URLS, deadline=time.monotonic() + 5.0
        )

        assert set(pages) == set(self.URLS)
        assert all(page.failure is None for page in pages.values())
        assert sorted(transport.fetched_urls) == sorted(self.URLS)

    def test_fetches_a_duplicated_url_once(self) -> None:
        transport = RecordingTransport()

        pages = self._fetcher(transport).fetch(
            (self.URLS[0], self.URLS[0]), deadline=time.monotonic() + 5.0
        )

        assert list(pages) == [self.URLS[0]]
        assert transport.fetched_urls == [self.URLS[0]]

    def test_honours_the_parallel_fetch_cap(self) -> None:
        transport = RecordingTransport(delay_seconds=0.05)
        budgets = SearchFetchBudgets(max_parallel_fetches=2)

        self._fetcher(transport, budgets=budgets).fetch(
            self.URLS, deadline=time.monotonic() + 5.0
        )

        assert transport.peak_in_flight <= 2

    def test_runs_fetches_in_parallel_rather_than_in_sequence(self) -> None:
        transport = RecordingTransport(delay_seconds=0.2)

        started = time.monotonic()
        self._fetcher(transport).fetch(self.URLS, deadline=time.monotonic() + 5.0)
        elapsed = time.monotonic() - started

        # Three 0.2s fetches in sequence would take 0.6s.
        assert elapsed < 0.5

    def test_per_fetch_timeout_is_capped_by_the_remaining_wall_clock(self) -> None:
        transport = RecordingTransport()
        budgets = SearchFetchBudgets(
            per_fetch_timeout_seconds=30.0, wall_clock_seconds=2.0
        )
        now = time.monotonic()

        self._fetcher(transport, budgets=budgets).fetch(
            (self.URLS[0],), deadline=now + 1.0
        )

        assert float(transport.calls[0]["timeout_seconds"]) <= 1.0

    def test_slow_fetch_is_abandoned_when_the_wall_clock_expires(self) -> None:
        release = threading.Event()
        transport = RecordingTransport(release=release)
        try:
            pages = self._fetcher(transport).fetch(
                (self.URLS[0],), deadline=time.monotonic() + 0.05
            )
        finally:
            release.set()

        assert pages[self.URLS[0]].failure is SourceStatus.FETCH_TIMEOUT

    def test_expired_budget_skips_the_transport_entirely(self) -> None:
        transport = RecordingTransport()

        pages = self._fetcher(transport).fetch(
            (self.URLS[0],), deadline=time.monotonic() - 1.0
        )

        assert pages[self.URLS[0]].failure is SourceStatus.FETCH_TIMEOUT
        assert transport.calls == []

    def test_transport_failure_becomes_a_typed_fallback(self) -> None:
        transport = RecordingTransport(error=OSError("connection reset"))

        pages = self._fetcher(transport).fetch(
            (self.URLS[0],), deadline=time.monotonic() + 5.0
        )

        assert pages[self.URLS[0]].failure is SourceStatus.FETCH_FAILED

    def test_http_error_status_becomes_a_typed_fallback(self) -> None:
        transport = RecordingTransport(
            default=TransportResponse(status_code=503, content_type="text/html")
        )

        pages = self._fetcher(transport).fetch(
            (self.URLS[0],), deadline=time.monotonic() + 5.0
        )

        assert pages[self.URLS[0]].failure is SourceStatus.FETCH_FAILED

    def test_non_markup_response_is_not_sent_to_the_extractor(self) -> None:
        transport = RecordingTransport(
            default=TransportResponse(
                status_code=200, content_type="application/pdf", text="%PDF-1.7"
            )
        )

        pages = self._fetcher(transport).fetch(
            (self.URLS[0],), deadline=time.monotonic() + 5.0
        )

        assert pages[self.URLS[0]].failure is SourceStatus.NOT_HTML

    def test_missing_content_type_is_attempted(self) -> None:
        transport = RecordingTransport(
            default=TransportResponse(
                status_code=200, content_type="", text="<html>ok</html>"
            )
        )

        pages = self._fetcher(transport).fetch(
            (self.URLS[0],), deadline=time.monotonic() + 5.0
        )

        assert pages[self.URLS[0]].failure is None

    def test_non_public_target_is_refused_before_any_request(self) -> None:
        transport = RecordingTransport()

        pages = self._fetcher(transport).fetch(
            ("http://127.0.0.1:8100/internal",), deadline=time.monotonic() + 5.0
        )

        assert (
            pages["http://127.0.0.1:8100/internal"].failure
            is SourceStatus.UNSUPPORTED_URL
        )
        assert transport.calls == []

    def test_body_cap_is_passed_to_the_transport(self) -> None:
        transport = RecordingTransport()
        budgets = SearchFetchBudgets(max_page_bytes=1_234)

        self._fetcher(transport, budgets=budgets).fetch(
            (self.URLS[0],), deadline=time.monotonic() + 5.0
        )

        assert transport.calls[0]["max_bytes"] == 1_234

    def test_empty_url_list_does_no_work(self) -> None:
        transport = RecordingTransport()

        assert self._fetcher(transport).fetch((), deadline=time.monotonic() + 5.0) == {}
        assert transport.calls == []


class TestRobotsPolicy(FetcherMixin):
    DISALLOW_ALL = "User-agent: *\nDisallow: /\n"
    ALLOW_ALL = "User-agent: *\nDisallow:\n"

    def _robots_transport(self, body: str) -> RecordingTransport:
        return RecordingTransport(
            responses={
                "https://example.test/robots.txt": TransportResponse(
                    status_code=200, content_type="text/plain", text=body
                )
            }
        )

    def test_disallowed_url_is_refused(self) -> None:
        transport = self._robots_transport(self.DISALLOW_ALL)

        assert RobotsPolicy(transport=transport).allows(self.URLS[0]) is False

    def test_allowed_url_passes(self) -> None:
        transport = self._robots_transport(self.ALLOW_ALL)

        assert RobotsPolicy(transport=transport).allows(self.URLS[0]) is True

    def test_robots_is_read_once_per_origin(self) -> None:
        transport = self._robots_transport(self.ALLOW_ALL)
        policy = RobotsPolicy(transport=transport)

        policy.allows(self.URLS[0])
        policy.allows(self.URLS[1])

        assert transport.fetched_urls == ["https://example.test/robots.txt"]

    def test_unreachable_robots_fails_open(self) -> None:
        # A user-directed fetch is not a crawl: an unreachable policy file is
        # not evidence of a prohibition, and treating it as one would disable
        # the feature for every origin behind a flaky edge.
        transport = RecordingTransport(error=OSError("timeout"))

        assert RobotsPolicy(transport=transport).allows(self.URLS[0]) is True

    def test_missing_robots_fails_open(self) -> None:
        transport = RecordingTransport(
            default=TransportResponse(status_code=404, content_type="text/plain")
        )

        assert RobotsPolicy(transport=transport).allows(self.URLS[0]) is True

    def test_fetcher_refuses_a_disallowed_page(self) -> None:
        transport = self._robots_transport(self.DISALLOW_ALL)
        fetcher = PageFetcher(
            transport=transport, robots=RobotsPolicy(transport=transport)
        )

        pages = fetcher.fetch((self.URLS[0],), deadline=time.monotonic() + 5.0)

        assert pages[self.URLS[0]].failure is SourceStatus.ROBOTS_DISALLOWED
        assert self.URLS[0] not in transport.fetched_urls


class TestHttpxPageTransportDecoding:
    def test_unknown_declared_encoding_falls_back_to_utf8(self) -> None:
        decoded = HttpxPageTransport._decode("héllo".encode(), "not-a-real-charset")

        assert decoded == "héllo"

    def test_undecodable_bytes_are_replaced_rather_than_raising(self) -> None:
        decoded = HttpxPageTransport._decode(b"\xff\xfe bad", None)

        assert "bad" in decoded


class TestRedirectsAreValidatedPerHop:
    """The SSRF hole: the guard ran on hop 1 and httpx followed the rest.

    `FetchTargetPolicy`'s docstring names this exact threat — a result pointing
    at `http://127.0.0.1:8100/...` turning `web_search` into a request
    forwarder — and the guard genuinely refuses that URL when it is handed over
    directly. But `follow_redirects=True` meant a PUBLIC url answering
    `302 -> loopback` was followed inside httpx, with nothing re-checking the
    target: the loopback body reached the extractor and the on-disk cache.
    Reproduced against a local server before the fix.
    """

    SECRET = "LOOPBACK-BODY-THAT-MUST-NEVER-BE-READ"

    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - stdlib callback name
            port = self.server.server_address[1]
            if self.path == "/redirect":
                self.send_response(302)
                self.send_header("Location", f"http://127.0.0.1:{port}/secret")
                self.end_headers()
                return
            body = TestRedirectsAreValidatedPerHop.SECRET.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: object) -> None:
            """Keep the test output clean."""

    @contextlib.contextmanager
    def _server(self) -> Iterator[int]:
        with socketserver.TCPServer(("127.0.0.1", 0), self._Handler) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                yield int(server.server_address[1])
            finally:
                server.shutdown()

    def test_the_loopback_target_is_refused_when_handed_over_directly(self) -> None:
        with self._server() as port:
            assert (
                FetchTargetPolicy().allows(f"http://127.0.0.1:{port}/secret") is False
            )

    def test_a_redirect_to_loopback_is_refused_and_the_body_never_read(self) -> None:
        with self._server() as port:
            with pytest.raises(RedirectTargetRefused) as caught:
                HttpxPageTransport().get(
                    url=f"http://127.0.0.1:{port}/redirect",
                    timeout_seconds=5,
                    max_bytes=100_000,
                )

        assert "127.0.0.1" in caught.value.url
        assert self.SECRET not in str(caught.value)

    def test_the_refusal_carries_no_body_to_the_model(self) -> None:
        # The exception message is fixed text; the URL rides a field so a caller
        # that logs `str(exc)` cannot leak the target either.
        error = RedirectTargetRefused("http://127.0.0.1:9/secret")

        assert str(error) == "redirect target refused by fetch policy"
