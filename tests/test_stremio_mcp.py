import asyncio
import contextlib
import io
import json
import logging
import re
import sys
import time
import unittest
from importlib.metadata import distribution
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, sentinel

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import stremio_mcp


# Synthetic values only. Nothing here is or resembles a live credential; they
# exist so a test can prove the value never reaches a log or a response.
SENTINEL_TMDB_KEY = "sentinel-tmdb-key-0000000000000000"
SENTINEL_TMDB_BEARER = "eyJzZW50aW5lbA.eyJzZW50aW5lbC1wYXlsb2Fk.c2VudGluZWwtc2ln"
SENTINEL_AUTH_KEY = "sentinel-stremio-auth-key-0000000000"
SENTINEL_VALUES = (SENTINEL_TMDB_KEY, SENTINEL_TMDB_BEARER, SENTINEL_AUTH_KEY)


@contextlib.contextmanager
def capture_all_logs():
    """Capture everything any logger emits, through production log wiring.

    The handler is attached before ``configure_logging()`` runs so it receives
    the same redaction wiring a real deployment installs.
    """
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(logging.Formatter("%(name)s %(levelname)s %(message)s"))

    root = logging.getLogger()
    previous_level = root.level
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    stremio_mcp.configure_logging()
    try:
        yield stream
    finally:
        root.removeHandler(handler)
        root.setLevel(previous_level)


def mock_http_client(handler, **kwargs):
    """Build the production HTTP client over a scripted transport."""
    return stremio_mcp.AsyncHTTPClient(
        transport=httpx.MockTransport(handler), **kwargs
    )


def json_handler(payload, status_code=200):
    async def handler(request):
        return httpx.Response(status_code, json=payload)

    return handler


def raising_handler(exception_factory):
    async def handler(request):
        raise exception_factory(request)

    return handler


def api_ok(result):
    return stremio_mcp.ApiResult(True, result=result)


def api_error(detail="category=timeout"):
    return stremio_mcp.ApiResult(False, detail=detail)


class SecretSentinelMixin:
    """Assert that no synthetic credential ever escapes into text."""

    def assertNoSecrets(self, text, context=""):
        for secret in SENTINEL_VALUES:
            self.assertNotIn(secret, text, f"secret leaked in {context}: {text!r}")
        self.assertNotIn("api_key=sentinel", text)
        self.assertNotIn("authKey=sentinel", text)


class RedactionTests(SecretSentinelMixin, unittest.TestCase):
    def setUp(self):
        stremio_mcp.clear_registered_secrets()
        self.addCleanup(stremio_mcp.clear_registered_secrets)

    def test_registered_credential_is_removed_from_arbitrary_text(self):
        stremio_mcp.register_secret(SENTINEL_TMDB_KEY)

        redacted = stremio_mcp.redact_secrets(
            f"boom while calling https://api.themoviedb.org/3/search/movie"
            f"?query=x&api_key={SENTINEL_TMDB_KEY}"
        )

        self.assertNoSecrets(redacted, "registered credential")
        self.assertIn(stremio_mcp.REDACTED, redacted)

    def test_secret_query_and_body_parameters_are_removed_without_registration(self):
        samples = (
            "https://api.themoviedb.org/3/search/movie?query=x&api_key=abcdef123456",
            'payload {"authKey": "abcdef123456", "collection": "libraryItem"}',
            "Authorization: Bearer abcdef123456789",
            "?access_token=abcdef123456&session_id=zzzzzzzzzzzz",
        )
        for sample in samples:
            with self.subTest(sample=sample):
                redacted = stremio_mcp.redact_secrets(sample)
                self.assertNotIn("abcdef123456", redacted)
                self.assertNotIn("zzzzzzzzzzzz", redacted)
                self.assertIn(stremio_mcp.REDACTED, redacted)

    def test_short_values_are_not_redacted_as_registered_secrets(self):
        stremio_mcp.register_secret("abc")

        self.assertEqual(stremio_mcp.redact_secrets("abc def"), "abc def")

    def test_log_filter_redacts_message_arguments_and_traceback(self):
        stremio_mcp.register_secret(SENTINEL_TMDB_KEY)

        with capture_all_logs() as stream:
            stremio_mcp.logger.error("interpolated %s", SENTINEL_TMDB_KEY)
            try:
                raise RuntimeError(
                    f"401 for url: https://api.themoviedb.org/3/search/movie"
                    f"?api_key={SENTINEL_TMDB_KEY}"
                )
            except RuntimeError as e:
                stremio_mcp.logger.error("failed: %s", e, exc_info=True)

        output = stream.getvalue()
        self.assertNoSecrets(output, "log output")
        self.assertIn("Traceback", output)
        self.assertIn(stremio_mcp.REDACTED, output)

    def test_upstream_http_logger_cannot_emit_a_credentialed_url(self):
        stremio_mcp.register_secret(SENTINEL_TMDB_KEY)

        httpx_logger = logging.getLogger("httpx")
        self.addCleanup(httpx_logger.setLevel, httpx_logger.level)

        with capture_all_logs() as stream:
            httpx_logger.setLevel(logging.INFO)
            httpx_logger.info(
                'HTTP Request: GET https://api.themoviedb.org/3/search/movie'
                '?api_key=%s "HTTP/1.1 401 Unauthorized"',
                SENTINEL_TMDB_KEY,
            )

        self.assertNoSecrets(stream.getvalue(), "httpx log output")

    def test_configure_logging_suppresses_upstream_request_lines(self):
        httpx_logger = logging.getLogger("httpx")
        self.addCleanup(httpx_logger.setLevel, httpx_logger.level)
        httpx_logger.setLevel(logging.DEBUG)

        stremio_mcp.configure_logging()

        self.assertGreaterEqual(httpx_logger.level, logging.WARNING)


class TMDBAuthorizationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        stremio_mcp.clear_registered_secrets()
        self.addCleanup(stremio_mcp.clear_registered_secrets)
        self.requests = []

    def _recording_handler(self):
        async def handler(request):
            self.requests.append(request)
            return httpx.Response(200, json={"results": []})

        return handler

    async def test_v4_read_access_token_is_sent_as_a_header_not_a_url(self):
        http = mock_http_client(self._recording_handler())
        client = stremio_mcp.TMDBClient(SENTINEL_TMDB_BEARER, http)

        self.assertTrue(client.uses_bearer_auth)
        await client.search_movie("Example")
        await http.aclose()

        request = self.requests[0]
        self.assertNotIn("api_key", str(request.url))
        self.assertNotIn(SENTINEL_TMDB_BEARER, str(request.url))
        self.assertEqual(
            request.headers["authorization"], f"Bearer {SENTINEL_TMDB_BEARER}"
        )

    async def test_legacy_v3_key_stays_a_query_parameter(self):
        http = mock_http_client(self._recording_handler())
        client = stremio_mcp.TMDBClient(SENTINEL_TMDB_KEY, http)

        self.assertFalse(client.uses_bearer_auth)
        await client.search_movie("Example")
        await http.aclose()

        request = self.requests[0]
        self.assertEqual(request.url.params["api_key"], SENTINEL_TMDB_KEY)
        self.assertNotIn("authorization", request.headers)


class TMDBFailureRedactionTests(SecretSentinelMixin, unittest.IsolatedAsyncioTestCase):
    """Fix 1: no failure mode may put the credential into a log or a result."""

    def _handler_for(self, fault):
        if fault == "http_status":
            return json_handler({"status_message": "Invalid API key"}, 401)
        if fault == "timeout":
            return raising_handler(
                lambda request: httpx.ReadTimeout("timed out", request=request)
            )
        if fault == "connection":
            return raising_handler(
                lambda request: httpx.ConnectError(
                    f"failed to reach {request.url}", request=request
                )
            )

        async def invalid_json(request):
            return httpx.Response(200, content=b"<html>not json</html>")

        return invalid_json

    async def asyncSetUp(self):
        stremio_mcp.clear_registered_secrets()
        self.addCleanup(stremio_mcp.clear_registered_secrets)

    async def test_every_tmdb_failure_keeps_the_credential_out_of_logs_and_errors(self):
        for fault in ("http_status", "timeout", "connection", "invalid_json"):
            for credential in (SENTINEL_TMDB_KEY, SENTINEL_TMDB_BEARER):
                with self.subTest(fault=fault, bearer=credential.startswith("eyJ")):
                    stremio_mcp.clear_registered_secrets()
                    http = mock_http_client(self._handler_for(fault))
                    client = stremio_mcp.TMDBClient(credential, http)

                    with capture_all_logs() as stream:
                        with self.assertRaises(stremio_mcp.HTTPClientError) as caught:
                            await client.search_movie("Example", year=1999)
                    await http.aclose()

                    error = caught.exception
                    self.assertEqual(error.category, fault)
                    self.assertNoSecrets(str(error), f"{fault} error text")
                    self.assertNoSecrets(error.summary(), f"{fault} summary")
                    self.assertNotIn("api_key", str(error))
                    self.assertNoSecrets(stream.getvalue(), f"{fault} logs")
                    self.assertNotIn("query=Example", stream.getvalue())

    async def test_external_id_failures_are_redacted_too(self):
        http = mock_http_client(self._handler_for("http_status"))
        client = stremio_mcp.TMDBClient(SENTINEL_TMDB_KEY, http)

        with capture_all_logs() as stream:
            with self.assertRaises(stremio_mcp.HTTPClientError) as caught:
                await client.get_external_ids("movie", 27205)
        await http.aclose()

        self.assertNoSecrets(str(caught.exception), "external id error")
        self.assertNoSecrets(stream.getvalue(), "external id logs")

    async def test_search_dispatch_returns_a_redacted_upstream_error(self):
        http = mock_http_client(self._handler_for("timeout"))
        original = stremio_mcp.tmdb_client
        stremio_mcp.tmdb_client = stremio_mcp.TMDBClient(SENTINEL_TMDB_KEY, http)
        self.addCleanup(setattr, stremio_mcp, "tmdb_client", original)

        with capture_all_logs() as stream:
            response = await stremio_mcp.call_tool(
                "search", {"query": "Example", "type": "movie"}
            )
        await http.aclose()

        text = response[0].text
        self.assertIn("upstream request failed", text)
        self.assertIn("category=timeout", text)
        self.assertNoSecrets(text, "dispatch response")
        self.assertNoSecrets(stream.getvalue(), "dispatch logs")

    async def test_auto_search_reports_a_half_outage_without_losing_results(self):
        async def handler(request):
            if request.url.path.endswith("/search/movie"):
                raise httpx.ReadTimeout("timed out", request=request)
            if request.url.path.endswith("/search/tv"):
                return httpx.Response(
                    200, json={"results": [{"id": 1396, "name": "Breaking Bad"}]}
                )
            return httpx.Response(200, json={"imdb_id": "tt0903747"})

        http = mock_http_client(handler)
        original = stremio_mcp.tmdb_client
        stremio_mcp.tmdb_client = stremio_mcp.TMDBClient(SENTINEL_TMDB_KEY, http)
        self.addCleanup(setattr, stremio_mcp, "tmdb_client", original)

        response = await stremio_mcp.call_tool("search", {"query": "Example"})
        await http.aclose()

        text = response[0].text
        self.assertIn("Breaking Bad", text)
        self.assertIn("tt0903747", text)
        self.assertIn("partial results", text)
        self.assertIn("movie search: category=timeout", text)
        self.assertNoSecrets(text, "partial search response")

    async def test_auto_search_reports_an_error_when_both_halves_fail(self):
        http = mock_http_client(self._handler_for("timeout"))
        original = stremio_mcp.tmdb_client
        stremio_mcp.tmdb_client = stremio_mcp.TMDBClient(SENTINEL_TMDB_KEY, http)
        self.addCleanup(setattr, stremio_mcp, "tmdb_client", original)

        response = await stremio_mcp.call_tool("search", {"query": "Example"})
        await http.aclose()

        self.assertIn("upstream request failed", response[0].text)
        self.assertNotIn("No results found", response[0].text)
        self.assertNoSecrets(response[0].text, "failed auto search response")

    async def test_unexpected_exception_text_is_redacted_before_returning(self):
        stremio_mcp.register_secret(SENTINEL_TMDB_KEY)
        original = stremio_mcp.tmdb_client
        failing = MagicMock()
        failing.search_movie = AsyncMock(
            side_effect=RuntimeError(f"boom api_key={SENTINEL_TMDB_KEY}")
        )
        stremio_mcp.tmdb_client = failing
        self.addCleanup(setattr, stremio_mcp, "tmdb_client", original)

        with capture_all_logs() as stream:
            response = await stremio_mcp.call_tool(
                "search", {"query": "Example", "type": "movie"}
            )

        self.assertNoSecrets(response[0].text, "catch-all response")
        self.assertNoSecrets(stream.getvalue(), "catch-all logs")


class StremioTransportRedactionTests(
    SecretSentinelMixin, unittest.IsolatedAsyncioTestCase
):
    async def asyncSetUp(self):
        stremio_mcp.clear_registered_secrets()
        self.addCleanup(stremio_mcp.clear_registered_secrets)

    async def test_stremio_failures_never_echo_the_auth_key(self):
        faults = {
            "timeout": raising_handler(
                lambda request: httpx.ReadTimeout("timed out", request=request)
            ),
            "http_status": json_handler({"error": "nope"}, 500),
            "api_error": json_handler({"error": {"message": "Session does not exist"}}),
        }
        for name, handler in faults.items():
            with self.subTest(fault=name):
                stremio_mcp.clear_registered_secrets()
                http = mock_http_client(handler)
                client = stremio_mcp.StremioAPIClient(SENTINEL_AUTH_KEY, http)

                with capture_all_logs() as stream:
                    result = await client._make_request("datastoreGet", {"all": True})
                await http.aclose()

                self.assertFalse(result.ok)
                self.assertNoSecrets(result.detail, f"{name} detail")
                self.assertNoSecrets(stream.getvalue(), f"{name} logs")

    async def test_api_error_reports_the_numeric_code_without_echoing_the_object(self):
        http = mock_http_client(
            json_handler(
                {
                    "error": {
                        "code": 1,
                        "message": f"session {SENTINEL_AUTH_KEY} expired",
                    }
                }
            )
        )
        client = stremio_mcp.StremioAPIClient(SENTINEL_AUTH_KEY, http)
        with capture_all_logs() as stream:
            result = await client._make_request("datastoreGet", {"all": True})
        await http.aclose()

        self.assertFalse(result.ok)
        self.assertIn("category=api_error", result.detail)
        self.assertIn("code=1", result.detail)
        self.assertNotIn("expired", result.detail)
        self.assertNoSecrets(result.detail, "api error detail")
        self.assertNoSecrets(stream.getvalue(), "api error logs")

    async def test_auth_key_is_never_placed_in_the_url(self):
        seen = []

        async def handler(request):
            seen.append(request)
            return httpx.Response(200, json={"result": []})

        http = mock_http_client(handler)
        client = stremio_mcp.StremioAPIClient(SENTINEL_AUTH_KEY, http)
        await client._make_request("datastoreGet", {"all": True})
        await http.aclose()

        self.assertNotIn(SENTINEL_AUTH_KEY, str(seen[0].url))
        self.assertEqual(
            json.loads(seen[0].content)["authKey"], SENTINEL_AUTH_KEY
        )


class AsyncHTTPClientTests(unittest.IsolatedAsyncioTestCase):
    """Fix 3: one bounded, cancellable, lifecycle-managed client."""

    async def test_all_four_timeouts_and_pool_limits_are_explicit(self):
        client = stremio_mcp.AsyncHTTPClient(
            connect_timeout=1.5,
            read_timeout=2.5,
            write_timeout=3.5,
            pool_timeout=4.5,
            max_connections=6,
        )

        self.assertEqual(client.timeout.connect, 1.5)
        self.assertEqual(client.timeout.read, 2.5)
        self.assertEqual(client.timeout.write, 3.5)
        self.assertEqual(client.timeout.pool, 4.5)
        self.assertEqual(client.limits.max_connections, 6)

        underlying = client._get_client()
        self.assertEqual(underlying.timeout.connect, 1.5)
        self.assertEqual(underlying.timeout.read, 2.5)
        await client.aclose()

    async def test_module_defaults_are_bounded(self):
        for value in (
            stremio_mcp.CONNECT_TIMEOUT,
            stremio_mcp.READ_TIMEOUT,
            stremio_mcp.WRITE_TIMEOUT,
            stremio_mcp.POOL_TIMEOUT,
        ):
            self.assertGreater(value, 0)
            self.assertLessEqual(value, 300)
        self.assertGreaterEqual(stremio_mcp.MAX_CONNECTIONS, 1)
        self.assertGreaterEqual(stremio_mcp.MAX_RESPONSE_BYTES, 1024)

    async def test_timeout_is_reported_as_a_typed_category(self):
        http = mock_http_client(
            raising_handler(
                lambda request: httpx.ConnectTimeout("slow", request=request)
            )
        )
        with self.assertRaises(stremio_mcp.HTTPClientError) as caught:
            await http.request_json("GET", "https://api.themoviedb.org/3/x")
        await http.aclose()

        self.assertEqual(caught.exception.category, "timeout")
        self.assertEqual(caught.exception.host, "api.themoviedb.org")

    async def test_oversized_responses_are_rejected_before_decoding(self):
        async def handler(request):
            return httpx.Response(200, content=b"x" * 8192)

        http = mock_http_client(handler, max_response_bytes=2048)
        with self.assertRaises(stremio_mcp.HTTPClientError) as caught:
            await http.request_json("GET", "https://api.strem.io/api/datastoreGet")
        await http.aclose()

        self.assertEqual(caught.exception.category, "response_too_large")

    async def test_per_request_size_limit_overrides_the_client_default(self):
        async def handler(request):
            return httpx.Response(200, json={"result": ["x" * 4096]})

        http = mock_http_client(handler, max_response_bytes=512)
        payload = await http.request_json(
            "GET", "https://api.strem.io/api/x", max_response_bytes=64 * 1024
        )
        await http.aclose()

        self.assertEqual(len(payload["result"][0]), 4096)

    async def test_cancellation_propagates_and_leaves_the_client_usable(self):
        started = asyncio.Event()

        async def handler(request):
            if request.url.path == "/slow":
                started.set()
                await asyncio.sleep(30)
            return httpx.Response(200, json={"ok": True})

        http = mock_http_client(handler)
        task = asyncio.create_task(
            http.request_json("GET", "https://api.themoviedb.org/slow")
        )
        await asyncio.wait_for(started.wait(), timeout=5)
        task.cancel()

        with self.assertRaises(asyncio.CancelledError):
            await task

        payload = await asyncio.wait_for(
            http.request_json("GET", "https://api.themoviedb.org/fast"), timeout=5
        )
        await http.aclose()

        self.assertEqual(payload, {"ok": True})

    async def test_unfollowed_redirects_are_typed_as_redirects(self):
        async def handler(request):
            return httpx.Response(
                302, headers={"Location": "https://example.invalid/"}, content=b""
            )

        http = mock_http_client(handler)
        with self.assertRaises(stremio_mcp.HTTPClientError) as caught:
            await http.request_json("GET", "https://api.themoviedb.org/3/x")
        await http.aclose()

        self.assertEqual(caught.exception.category, "redirect")
        self.assertEqual(caught.exception.status_code, 302)

    async def test_stream_errors_are_typed_as_connection_failures(self):
        async def handler(request):
            raise httpx.StreamError("stream broke")

        http = mock_http_client(handler)
        with self.assertRaises(stremio_mcp.HTTPClientError) as caught:
            await http.request_json("GET", "https://api.themoviedb.org/3/x")
        await http.aclose()

        self.assertEqual(caught.exception.category, "connection")
        self.assertEqual(caught.exception.host, "api.themoviedb.org")

    async def test_aclose_is_idempotent_and_blocks_later_use(self):
        http = mock_http_client(json_handler({"ok": True}))
        await http.request_json("GET", "https://api.themoviedb.org/3/x")
        await http.aclose()
        await http.aclose()

        with self.assertRaises(stremio_mcp.HTTPClientError) as caught:
            await http.request_json("GET", "https://api.themoviedb.org/3/x")
        self.assertEqual(caught.exception.category, "client_closed")

    async def test_shutdown_releases_the_shared_client(self):
        original = stremio_mcp.http_client
        self.addCleanup(setattr, stremio_mcp, "http_client", original)
        stremio_mcp.http_client = mock_http_client(json_handler({"ok": True}))

        await stremio_mcp.shutdown()

        self.assertIsNone(stremio_mcp.http_client)
        await stremio_mcp.shutdown()


class NetworkConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    """Fix 3: delayed network work must not stall unrelated MCP requests."""

    async def asyncSetUp(self):
        self.originals = (
            stremio_mcp.controller,
            stremio_mcp.tmdb_client,
            stremio_mcp.stremio_client,
        )

    async def asyncTearDown(self):
        (
            stremio_mcp.controller,
            stremio_mcp.tmdb_client,
            stremio_mcp.stremio_client,
        ) = self.originals

    async def test_device_controls_respond_while_a_search_is_stalled(self):
        release = asyncio.Event()
        in_flight = asyncio.Event()

        async def handler(request):
            in_flight.set()
            await release.wait()
            return httpx.Response(200, json={"results": []})

        http = mock_http_client(handler)
        stremio_mcp.tmdb_client = stremio_mcp.TMDBClient(SENTINEL_TMDB_KEY, http)

        controller = MagicMock()
        controller.get_tv_state = AsyncMock(return_value="on")
        controller.media_pause = AsyncMock(return_value=True)
        stremio_mcp.controller = controller

        search = asyncio.create_task(
            stremio_mcp.call_tool("search", {"query": "Example", "type": "movie"})
        )
        await asyncio.wait_for(in_flight.wait(), timeout=5)

        # The search is parked mid-request; unrelated device control must not
        # wait behind it.
        power = await asyncio.wait_for(
            stremio_mcp.call_tool(
                "tv_control", {"category": "power", "action": "status"}
            ),
            timeout=2,
        )
        playback = await asyncio.wait_for(
            stremio_mcp.call_tool(
                "tv_control", {"category": "playback", "action": "pause"}
            ),
            timeout=2,
        )

        self.assertEqual(power[0].text, "TV is on")
        self.assertEqual(playback[0].text, "Playback: pause")
        self.assertFalse(search.done())

        release.set()
        await asyncio.wait_for(search, timeout=5)
        await http.aclose()

    async def test_stalled_search_can_be_cancelled_without_leaking(self):
        in_flight = asyncio.Event()

        async def handler(request):
            in_flight.set()
            await asyncio.sleep(30)
            return httpx.Response(200, json={"results": []})

        http = mock_http_client(handler)
        stremio_mcp.tmdb_client = stremio_mcp.TMDBClient(SENTINEL_TMDB_KEY, http)

        task = asyncio.create_task(
            stremio_mcp.call_tool("search", {"query": "Example", "type": "movie"})
        )
        await asyncio.wait_for(in_flight.wait(), timeout=5)
        task.cancel()

        with self.assertRaises(asyncio.CancelledError):
            await task
        await http.aclose()

    async def test_external_id_fanout_is_concurrent_but_bounded(self):
        active = 0
        peak = 0

        async def handler(request):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            try:
                await asyncio.sleep(0.05)
                return httpx.Response(200, json={"imdb_id": "tt0000001"})
            finally:
                active -= 1

        http = mock_http_client(handler, max_connections=16)
        client = stremio_mcp.TMDBClient(
            SENTINEL_TMDB_KEY, http, max_concurrent_requests=3
        )

        started = time.monotonic()
        resolved = await client.get_external_ids_many("movie", list(range(9)))
        elapsed = time.monotonic() - started
        await http.aclose()

        self.assertEqual(len(resolved), 9)
        self.assertLessEqual(peak, 3)
        self.assertGreater(peak, 1, "fan-out did not run concurrently")
        # Nine serial 50ms calls would take ~450ms; three at a time take ~150ms.
        self.assertLess(elapsed, 0.40)

    async def test_one_failed_external_id_lookup_does_not_fail_the_search(self):
        async def handler(request):
            if "/7/" in request.url.path:
                return httpx.Response(500, json={"status_message": "nope"})
            return httpx.Response(200, json={"imdb_id": "tt0000001"})

        http = mock_http_client(handler)
        client = stremio_mcp.TMDBClient(SENTINEL_TMDB_KEY, http)

        resolved = await client.get_external_ids_many("movie", [1, 7])
        await http.aclose()

        self.assertEqual(list(resolved), [1])


class InitializationTests(unittest.TestCase):
    def tearDown(self):
        stremio_mcp.controller = None
        stremio_mcp.tmdb_client = None
        stremio_mcp.stremio_client = None
        stremio_mcp.http_client = None
        stremio_mcp.clear_registered_secrets()

    def test_initialize_without_configuration_leaves_clients_disabled(self):
        with patch.multiple(
            stremio_mcp,
            ANDROID_TV_HOST="",
            TMDB_API_KEY="",
            STREMIO_AUTH_KEY="",
        ):
            stremio_mcp.initialize()

        self.assertIsNone(stremio_mcp.controller)
        self.assertIsNone(stremio_mcp.tmdb_client)
        self.assertIsNone(stremio_mcp.stremio_client)

    def test_initialize_builds_configured_clients_without_connecting(self):
        with patch.multiple(
            stremio_mcp,
            ANDROID_TV_HOST="test.invalid",
            ANDROID_TV_PORT=5556,
            TMDB_API_KEY=SENTINEL_TMDB_KEY,
            STREMIO_AUTH_KEY=SENTINEL_AUTH_KEY,
        ):
            stremio_mcp.initialize()

        self.assertEqual(stremio_mcp.controller.host, "test.invalid")
        self.assertEqual(stremio_mcp.controller.port, 5556)
        self.assertEqual(stremio_mcp.tmdb_client.api_key, SENTINEL_TMDB_KEY)
        self.assertEqual(stremio_mcp.stremio_client.auth_key, SENTINEL_AUTH_KEY)
        self.assertIsNone(stremio_mcp.controller.device)

    def test_initialize_shares_one_http_client_and_registers_credentials(self):
        with patch.multiple(
            stremio_mcp,
            ANDROID_TV_HOST="",
            TMDB_API_KEY=SENTINEL_TMDB_KEY,
            STREMIO_AUTH_KEY=SENTINEL_AUTH_KEY,
        ):
            stremio_mcp.initialize()

        self.assertIsInstance(stremio_mcp.http_client, stremio_mcp.AsyncHTTPClient)
        self.assertIs(stremio_mcp.tmdb_client._http, stremio_mcp.http_client)
        self.assertIs(stremio_mcp.stremio_client._http, stremio_mcp.http_client)
        for secret in (SENTINEL_TMDB_KEY, SENTINEL_AUTH_KEY):
            self.assertIn(stremio_mcp.REDACTED, stremio_mcp.redact_secrets(secret))

    def test_bounded_config_helpers_reject_bad_values_without_echoing_them(self):
        with patch.dict("os.environ", {"STREMIO_MCP_READ_TIMEOUT": "not-a-number"}):
            with self.assertLogs(stremio_mcp.logger, level="WARNING") as logs:
                value = stremio_mcp._env_float(
                    "STREMIO_MCP_READ_TIMEOUT", 20.0, 0.1, 300.0
                )
        self.assertEqual(value, 20.0)
        self.assertNotIn("not-a-number", "".join(logs.output))

        with patch.dict("os.environ", {"STREMIO_MCP_MAX_CONNECTIONS": "99999"}):
            with self.assertLogs(stremio_mcp.logger, level="WARNING"):
                self.assertEqual(
                    stremio_mcp._env_int("STREMIO_MCP_MAX_CONNECTIONS", 8, 1, 64), 8
                )


class CliTests(unittest.TestCase):
    def test_cli_runs_the_stdio_server(self):
        main = MagicMock(return_value=sentinel.main_coroutine)
        with (
            patch.object(stremio_mcp, "main", main),
            patch.object(stremio_mcp.asyncio, "run") as asyncio_run,
        ):
            stremio_mcp.cli()

        main.assert_called_once_with()
        asyncio_run.assert_called_once_with(sentinel.main_coroutine)


def _project_dependencies(root: Path) -> list[str]:
    """Read ``[project].dependencies`` as text.

    ``tomllib`` is Python 3.11+ while this project supports 3.10, and no TOML
    parser is a declared dependency, so the array is scanned directly.
    """
    text = (root / "pyproject.toml").read_text(encoding="utf-8")
    table = re.search(
        r"^\[project\]\s*$(.*?)(?=^\[|\Z)", text, flags=re.MULTILINE | re.DOTALL
    )
    if table is None:
        raise AssertionError("pyproject.toml has no [project] table")
    array = re.search(
        r"^dependencies\s*=\s*\[(.*?)\]",
        table.group(1),
        flags=re.MULTILINE | re.DOTALL,
    )
    if array is None:
        raise AssertionError("[project] declares no dependencies array")
    return re.findall(r"[\"']([^\"']+)[\"']", array.group(1))


def _project_dependency(root: Path, name: str) -> str:
    """Return the PEP 508 dependency string for ``name`` from pyproject.toml."""
    wanted = name.casefold()
    matches = [
        dep
        for dep in _project_dependencies(root)
        if re.split(r"[<>=!~\[]", dep, maxsplit=1)[0].strip().casefold() == wanted
    ]
    if len(matches) != 1:
        raise AssertionError(f"expected one {name!r} dependency, found {matches!r}")
    return matches[0]


def _requirement_clauses(requirement: str) -> set:
    """Return the ``(operator, version)`` clauses declared by a requirement."""
    return set(
        re.findall(r"(>=|<=|==|!=|~=|>|<)\s*([0-9][0-9A-Za-z.\-_]*)", requirement)
    )


class ReleaseMetadataTests(unittest.TestCase):
    def test_registry_metadata_matches_the_python_distribution(self):
        root = Path(__file__).resolve().parents[1]
        server = json.loads((root / "server.json").read_text())
        package = distribution("stremio-mcp-server")
        console_scripts = {
            entry_point.name: entry_point.value
            for entry_point in package.entry_points
            if entry_point.group == "console_scripts"
        }

        self.assertEqual(server["name"], "io.github.netixc/stremio-mcp")
        self.assertEqual(server["version"], package.version)
        self.assertEqual(server["packages"][0]["identifier"], package.metadata["Name"])
        self.assertEqual(server["packages"][0]["version"], package.version)
        self.assertEqual(server["packages"][0]["runtimeHint"], "uvx")
        self.assertEqual(
            console_scripts,
            {
                "stremio-mcp": "stremio_mcp:cli",
                "stremio-mcp-server": "stremio_mcp:cli",
            },
        )
        self.assertIn(
            "<!-- mcp-name: io.github.netixc/stremio-mcp -->",
            (root / "README.md").read_text(),
        )

    def test_mcp_dependency_stays_on_stable_v1(self):
        """Pin the official SDK to v1 until a deliberate v2 migration.

        Fail-before (unchanged ``mcp>=1.28.1``) accepted ``2.0.0``; the upper
        bound must reject it while still accepting the locked v1 baseline.
        """
        root = Path(__file__).resolve().parents[1]
        requirement = _project_dependency(root, "mcp")
        self.assertEqual(requirement, "mcp>=1.28.1,<2")
        self.assertEqual(
            _requirement_clauses(requirement),
            {(">=", "1.28.1"), ("<", "2")},
            f"{requirement!r} must floor at the locked v1 baseline and exclude 2.x",
        )


class DispatchTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.original_globals = (
            stremio_mcp.controller,
            stremio_mcp.tmdb_client,
            stremio_mcp.stremio_client,
        )
        stremio_mcp.controller = None
        stremio_mcp.tmdb_client = None
        stremio_mcp.stremio_client = None

    async def asyncTearDown(self):
        (
            stremio_mcp.controller,
            stremio_mcp.tmdb_client,
            stremio_mcp.stremio_client,
        ) = self.original_globals

    async def test_list_tools_exposes_the_documented_dispatch_surface(self):
        tools = await stremio_mcp.list_tools()
        self.assertEqual(
            [tool.name for tool in tools],
            ["search", "play", "library", "tv_control", "playback_status"],
        )

    async def test_unconfigured_dispatch_returns_actionable_errors(self):
        cases = (
            ("search", {"query": "Example"}, "TMDB_API_KEY"),
            ("play", {"imdb_id": "tt0000001"}, "ANDROID_TV_HOST"),
            ("library", {"action": "list"}, "STREMIO_AUTH_KEY"),
            ("tv_control", {"category": "power", "action": "status"}, "ANDROID_TV_HOST"),
            ("playback_status", {}, "ANDROID_TV_HOST"),
        )
        for name, arguments, expected in cases:
            with self.subTest(name=name):
                response = await stremio_mcp.call_tool(name, arguments)
                self.assertIn(expected, response[0].text)

    async def test_unknown_tool_is_rejected(self):
        response = await stremio_mcp.call_tool("not-a-tool", {})
        self.assertEqual(response[0].text, "Unknown tool: not-a-tool")


class LibraryReadOutcomeTests(unittest.IsolatedAsyncioTestCase):
    """Fix 2: reads distinguish found, authoritative not-found, and error."""

    def setUp(self):
        self.client = stremio_mcp.StremioAPIClient("test-auth", MagicMock())

    async def test_transport_and_api_failures_are_errors_not_absence(self):
        for detail in (
            "category=timeout host=api.strem.io",
            "category=http_status host=api.strem.io status=500",
            "category=invalid_json host=api.strem.io",
            "category=connection host=api.strem.io",
            "category=api_error kind=dict",
        ):
            with self.subTest(detail=detail):
                self.client._make_request = AsyncMock(return_value=api_error(detail))

                read = await self.client.read_library_item("tt1375666")

                self.assertTrue(read.is_error)
                self.assertFalse(read.is_not_found)
                self.assertIsNone(read.item)
                self.assertIn(detail, read.detail)

    async def test_empty_success_is_an_authoritative_not_found(self):
        for empty in ([], {}, {"libraryItem": []}):
            with self.subTest(empty=empty):
                self.client._make_request = AsyncMock(return_value=api_ok(empty))

                read = await self.client.read_library_item("tt1375666")

                self.assertTrue(read.is_not_found)
                self.assertIsNone(read.item)

    async def test_exact_identity_match_is_found(self):
        item = {"_id": "tt1375666", "type": "movie", "name": "Inception"}
        self.client._make_request = AsyncMock(return_value=api_ok([item]))

        read = await self.client.read_library_item("tt1375666")

        self.assertTrue(read.is_found)
        self.assertIs(read.item, item)

    async def test_mismatched_identity_is_an_error_not_a_result(self):
        self.client._make_request = AsyncMock(
            return_value=api_ok([{"_id": "tt0111161", "type": "movie"}])
        )

        read = await self.client.read_library_item("tt1375666")

        self.assertTrue(read.is_error)
        self.assertIn("identity mismatch", read.detail)
        self.assertIsNone(read.item)

    async def test_duplicate_rows_are_rejected(self):
        row = {"_id": "tt1375666", "type": "movie"}
        self.client._make_request = AsyncMock(return_value=api_ok([row, dict(row)]))

        read = await self.client.read_library_item("tt1375666")

        self.assertTrue(read.is_error)
        self.assertIn("duplicate", read.detail)

    async def test_extra_unrequested_rows_are_rejected(self):
        self.client._make_request = AsyncMock(
            return_value=api_ok(
                [
                    {"_id": "tt1375666", "type": "movie"},
                    {"_id": "tt0111161", "type": "movie"},
                ]
            )
        )

        read = await self.client.read_library_item("tt1375666")

        self.assertTrue(read.is_error)
        self.assertIn("extra rows", read.detail)

    async def test_unexpected_response_shapes_are_errors(self):
        for payload in ("a string", 42, {"libraryItem": "nope"}, {"unexpected": 1}):
            with self.subTest(payload=payload):
                self.client._make_request = AsyncMock(return_value=api_ok(payload))

                read = await self.client.read_library_item("tt1375666")

                self.assertTrue(read.is_error)

    async def test_non_dict_rows_are_errors(self):
        self.client._make_request = AsyncMock(return_value=api_ok(["tt1375666"]))

        read = await self.client.read_library_item("tt1375666")

        self.assertTrue(read.is_error)

    async def test_malformed_id_is_rejected_without_a_request(self):
        self.client._make_request = AsyncMock()

        read = await self.client.read_library_item("not-an-imdb-id")

        self.assertTrue(read.is_error)
        self.client._make_request.assert_not_awaited()

    async def test_error_erasing_compat_wrappers_are_gone(self):
        for name in (
            "get_library_item",
            "get_library",
            "get_continue_watching",
            "search_library",
        ):
            with self.subTest(name=name):
                self.assertFalse(hasattr(self.client, name))

    async def test_library_list_read_separates_empty_from_unavailable(self):
        self.client._make_request = AsyncMock(return_value=api_ok([]))
        empty = await self.client.read_library()
        self.assertTrue(empty.ok)
        self.assertEqual(empty.items, [])

        self.client._make_request = AsyncMock(return_value=api_error())
        unavailable = await self.client.read_library()
        self.assertFalse(unavailable.ok)
        self.assertEqual(unavailable.items, [])

    async def test_read_library_can_exclude_removed_items(self):
        self.client._make_request = AsyncMock(
            return_value=api_ok(
                [
                    {"_id": "tt0000001", "name": "Active", "removed": False},
                    {"_id": "tt0000002", "name": "Removed", "removed": True},
                ]
            )
        )

        read = await self.client.read_library(active_only=True)

        self.assertTrue(read.ok)
        self.assertEqual([item["name"] for item in read.items], ["Active"])


class LibraryMutationFailClosedTests(unittest.IsolatedAsyncioTestCase):
    """Fix 2: every mutation aborts on an ambiguous or mismatched read."""

    def setUp(self):
        self.client = stremio_mcp.StremioAPIClient("test-auth", MagicMock())

    def _existing(self, **overrides):
        item = {
            "_id": "tt1375666",
            "name": "Inception",
            "type": "movie",
            "removed": False,
            "state": {"video_id": "tt1375666", "timeOffset": 1234},
        }
        item.update(overrides)
        return item

    async def test_add_aborts_when_the_initial_read_fails(self):
        self.client._make_request = AsyncMock(return_value=api_error())
        self.client.read_cinemeta_meta = AsyncMock()

        success, status, item = await self.client.add_to_library("movie", "tt1375666")

        self.assertFalse(success)
        self.assertIn("library read failed", status)
        self.assertIsNone(item)
        self.client.read_cinemeta_meta.assert_not_awaited()
        # Crucially: no write, so existing watch state cannot be overwritten.
        self.assertEqual(
            [call.args[0] for call in self.client._make_request.await_args_list],
            ["datastoreGet"],
        )

    async def test_add_aborts_when_the_read_returns_a_different_item(self):
        self.client._make_request = AsyncMock(
            return_value=api_ok([{"_id": "tt0111161", "type": "movie"}])
        )
        self.client.read_cinemeta_meta = AsyncMock()

        success, status, _ = await self.client.add_to_library("movie", "tt1375666")

        self.assertFalse(success)
        self.assertIn("identity mismatch", status)
        self.client.read_cinemeta_meta.assert_not_awaited()

    async def test_add_aborts_when_the_read_returns_duplicate_rows(self):
        row = {"_id": "tt1375666", "type": "movie", "removed": True}
        self.client._make_request = AsyncMock(return_value=api_ok([row, dict(row)]))
        self.client.read_cinemeta_meta = AsyncMock()

        success, status, _ = await self.client.add_to_library("movie", "tt1375666")

        self.assertFalse(success)
        self.assertIn("duplicate", status)
        self.client.read_cinemeta_meta.assert_not_awaited()

    async def test_add_rejects_an_existing_item_of_the_wrong_type(self):
        self.client._make_request = AsyncMock(
            return_value=api_ok([self._existing(removed=True)])
        )
        self.client.read_cinemeta_meta = AsyncMock()

        success, status, _ = await self.client.add_to_library("series", "tt1375666")

        self.assertFalse(success)
        self.assertEqual(status, "type mismatch")
        self.client.read_cinemeta_meta.assert_not_awaited()

    async def test_add_distinguishes_missing_metadata_from_an_outage(self):
        self.client._make_request = AsyncMock(return_value=api_ok([]))
        self.client.read_cinemeta_meta = AsyncMock(
            return_value=stremio_mcp.MetaRead(
                stremio_mcp.ReadStatus.ERROR, detail="category=timeout"
            )
        )

        success, status, _ = await self.client.add_to_library("movie", "tt1375666")

        self.assertFalse(success)
        self.assertIn("metadata unavailable", status)

    async def test_add_does_not_rewrite_an_active_item(self):
        existing = self._existing()
        self.client._make_request = AsyncMock(return_value=api_ok([existing]))
        self.client.read_cinemeta_meta = AsyncMock()

        success, status, item = await self.client.add_to_library("movie", "tt1375666")

        self.assertTrue(success)
        self.assertEqual(status, "already in library")
        self.assertIs(item, existing)
        self.assertEqual(len(self.client._make_request.await_args_list), 1)

    async def test_add_readds_a_removed_item_and_preserves_watch_state(self):
        existing = self._existing(removed=True)
        state = existing["state"]
        persisted = {**existing, "removed": False}
        self.client._make_request = AsyncMock(
            side_effect=[api_ok([existing]), api_ok({}), api_ok([persisted])]
        )
        self.client.read_cinemeta_meta = AsyncMock(
            return_value=stremio_mcp.MetaRead(
                stremio_mcp.ReadStatus.FOUND,
                meta={"id": "tt1375666", "name": "Inception", "type": "movie"},
            )
        )

        success, status, item = await self.client.add_to_library("movie", "tt1375666")

        self.assertTrue(success)
        self.assertEqual(status, "re-added")
        self.assertIs(item["state"], state)
        written = self.client._make_request.await_args_list[1].args[1]["changes"][0]
        self.assertIs(written["state"], state)

    async def test_add_fails_when_the_write_request_fails(self):
        self.client._make_request = AsyncMock(
            side_effect=[api_ok([]), api_error("category=timeout")]
        )
        self.client.read_cinemeta_meta = AsyncMock(
            return_value=stremio_mcp.MetaRead(
                stremio_mcp.ReadStatus.FOUND,
                meta={"id": "tt1375666", "name": "Inception", "type": "movie"},
            )
        )

        success, status, _ = await self.client.add_to_library("movie", "tt1375666")

        self.assertFalse(success)
        self.assertIn("write failed", status)

    async def test_add_fails_when_verification_is_unavailable(self):
        self.client._make_request = AsyncMock(
            side_effect=[api_ok([]), api_ok({}), api_error("category=timeout")]
        )
        self.client.read_cinemeta_meta = AsyncMock(
            return_value=stremio_mcp.MetaRead(
                stremio_mcp.ReadStatus.FOUND,
                meta={"id": "tt1375666", "name": "Inception", "type": "movie"},
            )
        )

        success, status, _ = await self.client.add_to_library("movie", "tt1375666")

        self.assertFalse(success)
        self.assertIn("verification unavailable", status)

    async def test_concurrent_state_change_between_write_and_verify_is_reported(self):
        # A read/write race: the item is absent at read time, but by the time
        # the write is verified another writer has stored different state.
        raced = {
            "_id": "tt1375666",
            "name": "Inception",
            "type": "movie",
            "removed": False,
            "state": {"video_id": "tt1375666", "timeOffset": 999999},
        }
        self.client._make_request = AsyncMock(
            side_effect=[api_ok([]), api_ok({}), api_ok([raced])]
        )
        self.client.read_cinemeta_meta = AsyncMock(
            return_value=stremio_mcp.MetaRead(
                stremio_mcp.ReadStatus.FOUND,
                meta={"id": "tt1375666", "name": "Inception", "type": "movie"},
            )
        )

        success, status, _ = await self.client.add_to_library("movie", "tt1375666")

        self.assertFalse(success)
        self.assertIn("state conflict", status)

    async def test_server_added_state_fields_do_not_fail_a_good_write(self):
        item = {
            "_id": "tt1375666",
            "type": "movie",
            "removed": False,
            "state": {"timeOffset": 0, "flaggedWatched": 0},
        }
        persisted = dict(item, state={**item["state"], "serverOnlyField": "x"})
        self.client._make_request = AsyncMock(
            side_effect=[api_ok({}), api_ok([persisted])]
        )

        written, detail = await self.client.put_library_item(item)

        self.assertTrue(written)
        self.assertEqual(detail, "verified")

    async def test_a_dropped_intended_state_key_still_fails_verification(self):
        item = {
            "_id": "tt1375666",
            "type": "movie",
            "removed": False,
            "state": {"timeOffset": 0, "flaggedWatched": 0},
        }
        persisted = dict(item, state={"timeOffset": 0})
        self.client._make_request = AsyncMock(
            side_effect=[api_ok({}), api_ok([persisted])]
        )

        written, detail = await self.client.put_library_item(item)

        self.assertFalse(written)
        self.assertIn("state conflict", detail)

    async def test_verification_returning_a_different_item_is_rejected(self):
        item = {"_id": "tt1375666", "type": "movie", "removed": False, "state": {}}
        self.client._make_request = AsyncMock(
            side_effect=[api_ok({}), api_ok([{"_id": "tt0111161", "type": "movie"}])]
        )

        written, detail = await self.client.put_library_item(item)

        self.assertFalse(written)
        self.assertIn("unavailable", detail)

    async def test_remove_aborts_when_the_read_fails(self):
        self.client._make_request = AsyncMock(return_value=api_error())

        success, status, item = await self.client.remove_from_library(
            "movie", "tt1375666"
        )

        self.assertFalse(success)
        self.assertIn("library read failed", status)
        self.assertIsNone(item)
        self.assertEqual(
            [call.args[0] for call in self.client._make_request.await_args_list],
            ["datastoreGet"],
        )

    async def test_remove_never_soft_deletes_a_different_returned_item(self):
        other = {"_id": "tt0111161", "name": "Shawshank", "type": "movie",
                 "removed": False, "state": {}}
        self.client._make_request = AsyncMock(return_value=api_ok([other]))

        success, status, item = await self.client.remove_from_library(
            "movie", "tt1375666"
        )

        self.assertFalse(success)
        self.assertIn("identity mismatch", status)
        self.assertIsNone(item)
        self.assertFalse(other["removed"])
        self.assertEqual(
            [call.args[0] for call in self.client._make_request.await_args_list],
            ["datastoreGet"],
        )

    async def test_remove_reports_authoritative_not_found_without_writing(self):
        self.client._make_request = AsyncMock(return_value=api_ok([]))

        success, status, _ = await self.client.remove_from_library(
            "movie", "tt1375666"
        )

        self.assertFalse(success)
        self.assertEqual(status, "not found")
        self.assertEqual(len(self.client._make_request.await_args_list), 1)

    async def test_remove_rejects_a_type_mismatch_without_writing(self):
        self.client._make_request = AsyncMock(return_value=api_ok([self._existing()]))

        success, status, _ = await self.client.remove_from_library(
            "series", "tt1375666"
        )

        self.assertFalse(success)
        self.assertEqual(status, "type mismatch")
        self.assertEqual(len(self.client._make_request.await_args_list), 1)

    async def test_remove_is_idempotent_for_an_already_removed_item(self):
        existing = self._existing(removed=True)
        self.client._make_request = AsyncMock(return_value=api_ok([existing]))

        success, status, item = await self.client.remove_from_library(
            "movie", "tt1375666"
        )

        self.assertTrue(success)
        self.assertEqual(status, "already removed")
        self.assertIs(item, existing)
        self.assertEqual(len(self.client._make_request.await_args_list), 1)

    async def test_remove_soft_deletes_and_preserves_watch_state(self):
        existing = self._existing()
        state = existing["state"]
        persisted = {**existing, "removed": True}
        self.client._make_request = AsyncMock(
            side_effect=[api_ok([existing]), api_ok({}), api_ok([persisted])]
        )

        success, status, item = await self.client.remove_from_library(
            "movie", "tt1375666"
        )

        self.assertTrue(success)
        self.assertEqual(status, "removed")
        self.assertTrue(item["removed"])
        self.assertIs(item["state"], state)
        written = self.client._make_request.await_args_list[1].args[1]["changes"][0]
        self.assertTrue(written["removed"])
        self.assertIs(written["state"], state)

    async def test_invalid_targets_never_reach_the_network(self):
        self.client._make_request = AsyncMock()
        for content_type, imdb_id in (
            ("movie", "not-an-imdb-id"),
            ("tv", "tt1375666"),
            ("movie", ""),
        ):
            with self.subTest(content_type=content_type, imdb_id=imdb_id):
                for mutate in (self.client.add_to_library, self.client.remove_from_library):
                    success, status, item = await mutate(content_type, imdb_id)
                    self.assertFalse(success)
                    self.assertEqual(status, "invalid target")
                    self.assertIsNone(item)
        self.client._make_request.assert_not_awaited()

    async def test_put_rejects_an_item_without_a_valid_identity(self):
        self.client._make_request = AsyncMock()

        written, detail = await self.client.put_library_item({"_id": "bogus"})

        self.assertFalse(written)
        self.assertEqual(detail, "invalid item identity")
        self.client._make_request.assert_not_awaited()


class LibraryFaultInjectionTests(unittest.IsolatedAsyncioTestCase):
    """Fix 2 + 3: real transport faults reach the mutation boundary as errors."""

    async def test_transport_faults_abort_mutations_at_the_http_boundary(self):
        faults = {
            "timeout": raising_handler(
                lambda request: httpx.ReadTimeout("timed out", request=request)
            ),
            "connection": raising_handler(
                lambda request: httpx.ConnectError("no route", request=request)
            ),
            "http_status": json_handler({"error": "nope"}, 503),
            "invalid_json": None,
            "api_error": json_handler({"error": {"message": "bad session"}}),
        }

        async def invalid_json(request):
            return httpx.Response(200, content=b"<html>")

        faults["invalid_json"] = invalid_json

        for name, handler in faults.items():
            with self.subTest(fault=name):
                calls = []

                async def recording(request, handler=handler):
                    calls.append(request)
                    return await handler(request)

                http = mock_http_client(recording)
                client = stremio_mcp.StremioAPIClient(SENTINEL_AUTH_KEY, http)

                added = await client.add_to_library("movie", "tt1375666")
                removed = await client.remove_from_library("movie", "tt1375666")
                await http.aclose()

                self.assertFalse(added[0])
                self.assertFalse(removed[0])
                self.assertIn("library read failed", added[1])
                self.assertIn("library read failed", removed[1])
                # Only the two reads happened; nothing was ever written.
                self.assertEqual(len(calls), 2)
                for request in calls:
                    self.assertTrue(str(request.url).endswith("/api/datastoreGet"))

    async def test_empty_success_add_writes_once_and_verifies(self):
        state = []

        async def handler(request):
            body = json.loads(request.content)
            if request.url.path == "/api/datastorePut":
                state.extend(body["changes"])
                return httpx.Response(200, json={"result": {}})
            return httpx.Response(200, json={"result": list(state)})

        async def cinemeta(request):
            return httpx.Response(
                200,
                json={"meta": {"id": "tt1375666", "name": "Inception", "type": "movie"}},
            )

        async def router(request):
            if request.url.host == "v3-cinemeta.strem.io":
                return await cinemeta(request)
            return await handler(request)

        http = mock_http_client(router)
        client = stremio_mcp.StremioAPIClient(SENTINEL_AUTH_KEY, http)

        success, status, item = await client.add_to_library("movie", "tt1375666")
        await http.aclose()

        self.assertTrue(success)
        self.assertEqual(status, "added")
        self.assertEqual(item["_id"], "tt1375666")
        self.assertEqual(len(state), 1)


class LibraryMutationDispatchTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.original_client = stremio_mcp.stremio_client
        stremio_mcp.stremio_client = MagicMock()

    async def asyncTearDown(self):
        stremio_mcp.stremio_client = self.original_client

    async def test_library_schema_exposes_safe_mutation_actions(self):
        tools = await stremio_mcp.list_tools()
        library_tool = next(tool for tool in tools if tool.name == "library")

        self.assertEqual(
            library_tool.inputSchema["properties"]["action"]["enum"],
            ["list", "continue", "search", "check", "add", "remove"],
        )
        self.assertIn("imdb_id", library_tool.inputSchema["properties"])
        self.assertIn("active_only", library_tool.inputSchema["properties"])

    async def test_add_requires_direct_imdb_id_and_type(self):
        stremio_mcp.stremio_client.add_to_library = AsyncMock()

        response = await stremio_mcp.call_tool(
            "library", {"action": "add", "query": "Inception"}
        )

        self.assertIn("imdb_id", response[0].text)
        stremio_mcp.stremio_client.add_to_library.assert_not_awaited()

    async def test_check_reports_soft_deleted_item_without_mutating(self):
        stremio_mcp.stremio_client.read_library_item = AsyncMock(
            return_value=stremio_mcp.LibraryRead(
                stremio_mcp.ReadStatus.FOUND,
                item={
                    "_id": "tt1375666",
                    "name": "Inception",
                    "type": "movie",
                    "removed": True,
                },
            )
        )
        stremio_mcp.stremio_client.add_to_library = AsyncMock()
        stremio_mcp.stremio_client.remove_from_library = AsyncMock()

        response = await stremio_mcp.call_tool(
            "library", {"action": "check", "imdb_id": "tt1375666"}
        )

        self.assertIn("removed in library", response[0].text)
        stremio_mcp.stremio_client.read_library_item.assert_awaited_once_with(
            "tt1375666"
        )
        stremio_mcp.stremio_client.add_to_library.assert_not_awaited()
        stremio_mcp.stremio_client.remove_from_library.assert_not_awaited()

    async def test_check_reports_an_unavailable_library_distinctly(self):
        stremio_mcp.stremio_client.read_library_item = AsyncMock(
            return_value=stremio_mcp.LibraryRead(
                stremio_mcp.ReadStatus.ERROR, detail="category=timeout"
            )
        )

        response = await stremio_mcp.call_tool(
            "library", {"action": "check", "imdb_id": "tt1375666"}
        )

        self.assertIn("library unavailable", response[0].text)
        self.assertNotIn("Not found", response[0].text)

    async def test_list_distinguishes_an_empty_library_from_an_outage(self):
        stremio_mcp.stremio_client.read_library = AsyncMock(
            return_value=stremio_mcp.LibraryListRead(True, items=[])
        )
        empty = await stremio_mcp.call_tool("library", {"action": "list"})
        self.assertIn("empty", empty[0].text)

        stremio_mcp.stremio_client.read_library = AsyncMock(
            return_value=stremio_mcp.LibraryListRead(False, detail="category=timeout")
        )
        unavailable = await stremio_mcp.call_tool("library", {"action": "list"})
        self.assertIn("library unavailable", unavailable[0].text)

    async def test_add_dispatches_direct_target(self):
        stremio_mcp.stremio_client.add_to_library = AsyncMock(
            return_value=(
                True,
                "added",
                {"_id": "tt1375666", "name": "Inception", "type": "movie"},
            )
        )

        response = await stremio_mcp.call_tool(
            "library",
            {"action": "add", "type": "movie", "imdb_id": "tt1375666"},
        )

        self.assertIn("Inception: added", response[0].text)
        stremio_mcp.stremio_client.add_to_library.assert_awaited_once_with(
            "movie", "tt1375666"
        )

    async def test_play_from_library_aborts_when_the_library_read_fails(self):
        original_controller = stremio_mcp.controller
        controller = MagicMock()
        controller.play_content = AsyncMock(return_value=True)
        stremio_mcp.controller = controller
        self.addCleanup(setattr, stremio_mcp, "controller", original_controller)
        stremio_mcp.stremio_client.read_library_search = AsyncMock(
            return_value=stremio_mcp.LibraryListRead(False, detail="category=timeout")
        )

        response = await stremio_mcp.call_tool(
            "play",
            {"source": "library", "query": "Inception", "type": "movie"},
        )

        self.assertIn("library unavailable", response[0].text)
        controller.play_content.assert_not_awaited()


class BuildLibraryItemTests(unittest.TestCase):
    def setUp(self):
        self.client = stremio_mcp.StremioAPIClient("test-auth", MagicMock())

    def test_build_library_item_uses_stremio_id_and_preserves_watch_state(self):
        existing_state = {"video_id": "tt0000001:1:2", "timeOffset": 1234}
        existing = {
            "_id": "tt0000001",
            "_ctime": "2025-01-01T00:00:00Z",
            "state": existing_state,
            "removed": True,
        }
        meta = {
            "id": "tt0000001",
            "name": "Example",
            "type": "series",
            "poster": "https://example.invalid/poster.jpg",
        }
        self.client._utc_now = MagicMock(return_value="2026-01-01T00:00:00Z")

        item = self.client.build_library_item(meta, existing)

        self.assertEqual(item["_id"], "tt0000001")
        self.assertNotIn("id", item)
        self.assertFalse(item["removed"])
        self.assertIs(item["state"], existing_state)
        self.assertEqual(item["_ctime"], "2025-01-01T00:00:00Z")
        self.assertEqual(item["_mtime"], "2026-01-01T00:00:00Z")


class NativeAdbControllerTests(unittest.IsolatedAsyncioTestCase):
    async def test_connect_uses_native_adb_for_configured_target(self):
        controller = stremio_mcp.StremioController("test.invalid", 37139)
        controller._run_adb = AsyncMock(
            return_value=(0, "connected to test.invalid:37139", "")
        )

        self.assertTrue(await controller.connect())

        controller._run_adb.assert_awaited_once_with(
            "connect", "test.invalid:37139"
        )
        self.assertEqual(controller.device, "test.invalid:37139")

    async def test_connect_classifies_representative_adb_failures(self):
        cases = (
            (
                stremio_mcp.AdbFailureCategory.UNAUTHORIZED,
                "error: device unauthorized",
            ),
            (
                stremio_mcp.AdbFailureCategory.OFFLINE,
                "error: device offline",
            ),
            (
                stremio_mcp.AdbFailureCategory.UNREACHABLE,
                "failed to connect to '10.0.0.8:37139': No route to host",
            ),
            (
                stremio_mcp.AdbFailureCategory.TIMEOUT,
                "ADB command timed out",
            ),
            (
                stremio_mcp.AdbFailureCategory.AMBIGUOUS_NETWORK,
                "failed to connect to '10.0.0.8:37139': Operation timed out",
            ),
            (
                stremio_mcp.AdbFailureCategory.AMBIGUOUS_NETWORK,
                "failed to connect: Connection refused",
            ),
        )

        for category, stderr in cases:
            with self.subTest(category=category):
                controller = stremio_mcp.StremioController("10.0.0.8", 37139)
                controller._run_adb = AsyncMock(return_value=(1, "", stderr))

                self.assertFalse(await controller.connect())
                self.assertEqual(controller.last_failure.category, category)
                self.assertNotIn("10.0.0.8", controller.last_failure.user_message())
                self.assertNotIn("37139", controller.last_failure.user_message())

    async def test_connect_failure_logs_category_without_endpoint_or_stderr(self):
        controller = stremio_mcp.StremioController("10.0.0.8", 37139)
        controller._run_adb = AsyncMock(
            return_value=(
                1,
                "",
                "failed to connect to '10.0.0.8:37139': No route to host; "
                "secret-token-12345678",
            )
        )

        with capture_all_logs() as stream:
            self.assertFalse(await controller.connect())

        output = stream.getvalue()
        self.assertIn("category=unreachable", output)
        self.assertNotIn("10.0.0.8:37139", output)
        self.assertNotIn("secret-token-12345678", output)
        self.assertNotIn("No route to host", output)

    async def test_shell_failure_invalidates_device_and_next_call_reconnects(self):
        controller = stremio_mcp.StremioController("test.invalid", 37139)
        controller.device = "test.invalid:37139"
        controller._run_adb = AsyncMock(
            side_effect=[
                (1, "", "error: closed"),
                (0, "connected to test.invalid:37139", ""),
                (0, "", ""),
            ]
        )

        self.assertEqual(await controller.send_shell_command("dumpsys power"), "")
        self.assertIsNone(controller.device)
        self.assertEqual(controller.last_failure.category, stremio_mcp.AdbFailureCategory.TRANSPORT)

        self.assertTrue(await controller.send_key_event(24, delay=0))
        self.assertEqual(controller.device, "test.invalid:37139")
        self.assertEqual(controller._run_adb.await_count, 3)

    async def test_failed_connect_attempts_are_serialized_and_cooled_down(self):
        controller = stremio_mcp.StremioController("test.invalid", 37139)
        controller._run_adb = AsyncMock(
            return_value=(1, "", "failed to connect: Connection refused")
        )

        results = await asyncio.gather(
            controller.connect(), controller.connect(), controller.connect()
        )

        self.assertEqual(results, [False, False, False])
        controller._run_adb.assert_awaited_once_with("connect", "test.invalid:37139")

    async def test_set_volume_reports_shell_failure(self):
        controller = stremio_mcp.StremioController("test.invalid", 37139)
        controller.device = "test.invalid:37139"
        controller._run_adb = AsyncMock(return_value=(1, "", "error: device offline"))

        self.assertFalse(await controller.set_volume(8))
        self.assertEqual(controller.last_failure.category, stremio_mcp.AdbFailureCategory.OFFLINE)

    async def test_set_volume_does_not_treat_empty_mocked_failure_as_success(self):
        controller = stremio_mcp.StremioController("test.invalid", 37139)
        controller._run_shell = AsyncMock(return_value=(False, ""))

        self.assertFalse(await controller.set_volume(8))

    async def test_set_volume_failure_survives_concurrent_successful_shell(self):
        controller = stremio_mcp.StremioController("test.invalid", 37139)
        controller.device = "test.invalid:37139"
        started = asyncio.Event()

        async def fake_run_adb(*args):
            if args[-1].startswith("media volume"):
                started.set()
                await asyncio.sleep(0)
                return 1, "", "error: device offline"
            await started.wait()
            return 0, "state=ON\n", ""

        controller._run_adb = AsyncMock(side_effect=fake_run_adb)

        volume_result, shell_result = await asyncio.gather(
            controller.set_volume(8), controller.send_shell_command("dumpsys power")
        )

        self.assertFalse(volume_result)
        self.assertEqual(shell_result, "state=ON")

    async def test_send_shell_command_targets_connected_device(self):
        controller = stremio_mcp.StremioController("test.invalid", 37139)
        controller.device = "test.invalid:37139"
        controller._run_adb = AsyncMock(return_value=(0, "state=ON\n", ""))

        result = await controller.send_shell_command("dumpsys power")

        self.assertEqual(result, "state=ON")
        controller._run_adb.assert_awaited_once_with(
            "-s", "test.invalid:37139", "shell", "dumpsys power"
        )

    async def test_tv_state_supports_android_wakefulness_output(self):
        controller = stremio_mcp.StremioController("test.invalid", 37139)
        controller.send_shell_command = AsyncMock(
            return_value="mWakefulness=Awake\nmWakefulnessChanging=false"
        )

        self.assertEqual(await controller.get_tv_state(), "on")
        controller.send_shell_command.assert_awaited_once_with("dumpsys power")

    async def test_playback_status_supports_named_android_state(self):
        controller = stremio_mcp.StremioController("test.invalid", 37139)
        controller.send_shell_command = AsyncMock(
            side_effect=[
                (
                    "PlayerMediaSession com.stremio.one/PlayerMediaSession\n"
                    "  ownerPid=12273, ownerUid=10084, userId=0\n"
                    "  package=com.stremio.one\n"
                    "  active=true\n"
                    "  state=PlaybackState {state=PLAYING(3), position=5796, "
                    "buffered position=12000, speed=1.0}\n"
                    "  metadata: size=4, description=Inception, null, null"
                ),
                # Live AudioTrack corroborates claimed PLAYING.
                (
                    "  players:\n"
                    "  AudioPlaybackConfiguration piid:151 type:android.media.AudioTrack "
                    "u/pid:10084/12273 state:started attr:AudioAttributes: usage=USAGE_MEDIA\n"
                ),
                (
                    "Recent extractors, most recent first:\n"
                    "track {mime: video/hevc, dura: (int64_t) 1000}"
                ),
            ]
        )

        status = await controller.get_playback_status()

        self.assertTrue(status["playing"])
        self.assertEqual(status["state"], "playing")
        self.assertEqual(status["position"], 5796)
        self.assertIsNone(status["duration"])
        self.assertEqual(status["title"], "Inception")

    async def test_playback_status_estimates_position_and_extractor_duration(self):
        controller = stremio_mcp.StremioController("test.invalid", 37139)
        controller.send_shell_command = AsyncMock(
            side_effect=[
                (
                    "PlayerMediaSession com.stremio.one/PlayerMediaSession\n"
                    "  ownerPid=12273, ownerUid=10084, userId=0\n"
                    "  package=com.stremio.one\n"
                    "  active=true\n"
                    "  state=PlaybackState {state=PLAYING(3), position=5796, "
                    "buffered position=0, speed=1.0, updated=907375568}\n"
                    "  metadata: size=4, description=Inception, null, null\n"
                    "    BluetoothMediaBrowserService com.android.bluetooth/Service (userId=0)\n"
                    "      active=true\n"
                    "      state=PlaybackState {state=ERROR(7), position=0, "
                    "buffered position=0, speed=0.0, updated=128501}"
                ),
                (
                    "  AudioPlaybackConfiguration piid:151 type:android.media.AudioTrack "
                    "u/pid:10084/12273 state:started attr:AudioAttributes: usage=USAGE_MEDIA\n"
                ),
                "907573.69 2452711.11",
                (
                    "Recent extractors, most recent first:\n"
                    "track {mime: video/hevc, dura: (int64_t) 8887891384}"
                ),
            ]
        )

        status = await controller.get_playback_status()

        self.assertEqual(status["position"], 203918)
        self.assertEqual(status["duration"], 8887891)
        self.assertTrue(status["playing"])
        self.assertEqual(status["state"], "playing")

    async def test_playback_status_demotes_stale_playing_without_audio(self):
        """Exo-error/stale path: session says PLAYING but no live AudioTrack."""
        controller = stremio_mcp.StremioController("test.invalid", 37139)
        controller.send_shell_command = AsyncMock(
            side_effect=[
                (
                    "PlayerMediaSession com.stremio.one/PlayerMediaSession\n"
                    "  ownerPid=17104, ownerUid=10084, userId=0\n"
                    "  package=com.stremio.one\n"
                    "  active=true\n"
                    "  state=PlaybackState {state=PLAYING(3), position=0, "
                    "buffered position=0, speed=1.0, updated=90477645, "
                    "actions=770, custom actions=[], active item id=-1, error=null}\n"
                    "  metadata: size=4, description=Big Buck Bunny, null, null\n"
                ),
                # No started track for Stremio uid — player error / stalled.
                (
                    "  players:\n"
                    "  AudioPlaybackConfiguration piid:95 type:android.media.SoundPool "
                    "u/pid:1000/775 state:idle attr:AudioAttributes: "
                    "usage=USAGE_ASSISTANCE_SONIFICATION\n"
                ),
                (
                    "Recent extractors, most recent first:\n"
                    "track {mime: video/avc, dura: (int64_t) 634534000}"
                ),
            ]
        )

        status = await controller.get_playback_status()

        self.assertFalse(status["playing"])
        self.assertEqual(status["state"], "stalled")
        self.assertEqual(status["app"], "Stremio")
        self.assertEqual(status["title"], "Big Buck Bunny")
        # Must not extrapolate a fake advancing clock from the stale snapshot.
        self.assertEqual(status["position"], 0)
        self.assertEqual(status["duration"], 634534)

    async def test_playback_status_keeps_paused_without_started_audio(self):
        controller = stremio_mcp.StremioController("test.invalid", 37139)
        controller.send_shell_command = AsyncMock(
            side_effect=[
                (
                    "PlayerMediaSession com.stremio.one/PlayerMediaSession\n"
                    "  ownerPid=17104, ownerUid=10084, userId=0\n"
                    "  package=com.stremio.one\n"
                    "  active=true\n"
                    "  state=PlaybackState {state=PAUSED(2), position=56000, "
                    "buffered position=0, speed=1.0, updated=90530060}\n"
                    "  metadata: size=4, description=Big Buck Bunny, null, null\n"
                ),
                (
                    "Recent extractors, most recent first:\n"
                    "track {mime: video/avc, dura: (int64_t) 634534000}"
                ),
            ]
        )

        status = await controller.get_playback_status()

        self.assertFalse(status["playing"])
        self.assertEqual(status["state"], "paused")
        self.assertEqual(status["position"], 56000)

    async def test_media_stop_does_not_report_success_when_session_keeps_playing(self):
        """Pre-fix KEYCODE_MEDIA_STOP success alone left VLC playing."""
        controller = stremio_mcp.StremioController("test.invalid", 37139)
        controller.device = "test.invalid:37139"
        controller._run_shell = AsyncMock(return_value=(True, ""))
        controller.send_key_event = AsyncMock(return_value=True)
        controller.media_pause = AsyncMock(return_value=True)
        controller.nav_back = AsyncMock(return_value=True)
        # After every stop attempt the session still claims playing with live audio.
        still_playing = {
            "playing": True,
            "app": "Stremio",
            "title": "Big Buck Bunny",
            "position": 66000,
            "duration": 634000,
            "state": "playing",
        }
        controller._read_session_status = AsyncMock(
            return_value=(still_playing, {})
        )

        with patch("stremio_mcp.asyncio.sleep", new_callable=AsyncMock):
            self.assertFalse(await controller.media_stop())
        controller.send_key_event.assert_awaited()
        controller._run_shell.assert_any_await("am force-stop com.stremio.one")
        self.assertIn("still reports active playback", controller.last_stop_failure)

    async def test_media_stop_succeeds_after_force_stop_clears_session(self):
        controller = stremio_mcp.StremioController("test.invalid", 37139)
        controller.device = "test.invalid:37139"
        controller._run_shell = AsyncMock(return_value=(True, ""))
        controller.send_key_event = AsyncMock(return_value=True)
        controller.media_pause = AsyncMock(return_value=True)
        controller.nav_back = AsyncMock(return_value=True)

        playing = {
            "playing": True,
            "app": "Stremio",
            "title": "Big Buck Bunny",
            "position": 66000,
            "duration": 634000,
            "state": "playing",
        }
        cleared = {
            "playing": False,
            "app": None,
            "title": None,
            "position": None,
            "duration": None,
            "state": "stopped",
        }
        # key stop → still playing; pause+back → still playing; force-stop → cleared
        controller._read_session_status = AsyncMock(
            side_effect=[(playing, {}), (playing, {}), (cleared, {})]
        )

        with patch("stremio_mcp.asyncio.sleep", new_callable=AsyncMock):
            self.assertTrue(await controller.media_stop())
        controller._run_shell.assert_any_await("am force-stop com.stremio.one")
        controller._run_shell.assert_any_await("cmd media_session dispatch stop")
        self.assertIsNone(controller.last_stop_failure)

    async def test_media_stop_fails_closed_on_buffering_session(self):
        controller = stremio_mcp.StremioController("test.invalid", 37139)
        controller.device = "test.invalid:37139"
        controller._run_shell = AsyncMock(return_value=(True, ""))
        controller.send_key_event = AsyncMock(return_value=True)
        controller.media_pause = AsyncMock(return_value=True)
        controller.nav_back = AsyncMock(return_value=True)
        # BUFFERING(6) matches no explicit branch: not playing, not stopped.
        buffering = {
            "playing": False,
            "app": "Stremio",
            "title": "Big Buck Bunny",
            "position": 66000,
            "duration": 634000,
            "state": "unknown",
        }
        controller._read_session_status = AsyncMock(return_value=(buffering, {}))

        with patch("stremio_mcp.asyncio.sleep", new_callable=AsyncMock):
            self.assertFalse(await controller.media_stop())
        self.assertIn("state=unknown", controller.last_stop_failure)

    async def test_buffering_session_is_not_reported_as_stopped(self):
        controller = stremio_mcp.StremioController("test.invalid", 37139)
        controller.device = "test.invalid:37139"
        dump = (
            "  Sessions Stack - have 1 sessions:\n"
            "    PlayerMediaSession com.stremio.one/PlayerMediaSession (userId=0)\n"
            "      ownerPid=12273, ownerUid=10084, userId=0\n"
            "      package=com.stremio.one\n"
            "      active=true\n"
            "      state=PlaybackState {state=BUFFERING(6), position=5796, "
            "buffered position=0, speed=0.0, updated=100000}\n"
            "    OtherSession com.other.app/Session (userId=0)\n"
            "      ownerPid=1, ownerUid=99999, userId=0\n"
        )
        controller.send_shell_command = AsyncMock(return_value=dump)

        status, meta = await controller._read_session_status()

        self.assertEqual(status["state"], "unknown")
        self.assertFalse(status["playing"])
        self.assertEqual(meta["owner_uid"], 10084)
        self.assertFalse(await controller._is_playback_stopped())

    async def test_stop_verification_avoids_extractor_and_uptime_dumps(self):
        controller = stremio_mcp.StremioController("test.invalid", 37139)
        controller.device = "test.invalid:37139"
        commands: list[str] = []

        async def fake_shell(command: str) -> str:
            commands.append(command)
            return ""

        controller.send_shell_command = AsyncMock(side_effect=fake_shell)

        self.assertTrue(await controller._is_playback_stopped())
        self.assertEqual(commands, ["dumpsys media_session"])

    async def test_stop_post_condition_failure_is_not_reported_as_adb_failure(self):
        original_controller = stremio_mcp.controller
        controller = stremio_mcp.StremioController("10.0.0.8", 37139)
        controller.media_stop = AsyncMock(return_value=False)
        controller.last_stop_failure = (
            "Stop failed: the Stremio media session still reports active "
            "playback (state=playing) after media-session stop, pause and "
            "back, and force-stop."
        )
        stremio_mcp.controller = controller
        self.addCleanup(setattr, stremio_mcp, "controller", original_controller)

        response = await stremio_mcp.call_tool(
            "tv_control", {"category": "playback", "action": "stop"}
        )

        self.assertIn("still reports active playback", response[0].text)
        self.assertNotIn("ADB failure", response[0].text)
        self.assertNotIn("10.0.0.8", response[0].text)

    async def test_send_intent_passes_uri_as_a_distinct_adb_argument(self):
        controller = stremio_mcp.StremioController("test.invalid", 37139)
        controller.device = "test.invalid:37139"
        controller._run_adb = AsyncMock(return_value=(0, "Starting: Intent", ""))
        uri = "stremio:///detail/movie/tt1375666/tt1375666"

        self.assertTrue(await controller.send_intent(uri))

        controller._run_adb.assert_awaited_once_with(
            "-s",
            "test.invalid:37139",
            "shell",
            "am",
            "start",
            "-a",
            "android.intent.action.VIEW",
            "-d",
            uri,
        )


class AdbToolFailureTests(unittest.IsolatedAsyncioTestCase):
    async def test_tool_response_surfaces_redacted_actionable_failure(self):
        original_controller = stremio_mcp.controller
        controller = stremio_mcp.StremioController("10.0.0.8", 37139)
        controller._run_adb = AsyncMock(
            return_value=(
                1,
                "",
                "error: device unauthorized at 10.0.0.8:37139",
            )
        )
        stremio_mcp.controller = controller
        self.addCleanup(setattr, stremio_mcp, "controller", original_controller)

        response = await stremio_mcp.call_tool(
            "tv_control", {"category": "volume", "action": "up"}
        )

        self.assertIn("category=unauthorized", response[0].text)
        self.assertIn("accept the debugging prompt", response[0].text)
        self.assertNotIn("10.0.0.8", response[0].text)
        self.assertNotIn("37139", response[0].text)


class DeepLinkTests(unittest.IsolatedAsyncioTestCase):
    async def test_series_deep_link_is_built_without_adb(self):
        controller = stremio_mcp.StremioController("test.invalid")
        controller.send_intent = AsyncMock(return_value=True)
        controller.send_key_event = AsyncMock(return_value=True)

        result = await controller.play_content(
            "series", "tt1234567", season=2, episode=3, auto_press_play=False
        )

        self.assertTrue(result)
        controller.send_intent.assert_awaited_once_with(
            "stremio:///detail/series/tt1234567/tt1234567:2:3"
        )
        controller.send_key_event.assert_not_awaited()

    async def test_series_requires_season_and_episode(self):
        controller = stremio_mcp.StremioController("test.invalid")
        with self.assertRaisesRegex(ValueError, "Season and episode"):
            await controller.play_content("series", "tt1234567", auto_press_play=False)


if __name__ == "__main__":
    unittest.main()
