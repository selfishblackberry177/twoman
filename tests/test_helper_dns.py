import asyncio
import time
import unittest
from unittest import mock

from local_client import helper


def build_dns_query(transaction_id):
    return (
        transaction_id
        + b"\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00"
        + b"\x07example\x03com\x00"
        + b"\x00\x01\x00\x01"
    )


def build_dns_aaaa_query(transaction_id):
    return (
        transaction_id
        + b"\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00"
        + b"\x07example\x03com\x00"
        + b"\x00\x1c\x00\x01"
    )


def build_dns_https_query(transaction_id):
    return (
        transaction_id
        + b"\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00"
        + b"\x07example\x03com\x00"
        + b"\x00\x41\x00\x01"
    )


class FakeRuntime:
    def __init__(self):
        self.config = {"vpn_dns_servers": ["1.1.1.1", "8.8.8.8"], "vpn_dns_proxy_ip": "198.18.0.2"}
        self.dns_query_timeout = 2.5
        self.dns_cache_ttl_seconds = 20.0
        self.dns_cache_max_entries = 256
        self.dns_semaphore = asyncio.Semaphore(8)
        self.dns_cache = {}
        self.dns_inflight = {}
        self.dns_cache_lock = asyncio.Lock()


class HelperDnsTests(unittest.IsolatedAsyncioTestCase):
    def test_dns_query_cache_key_ignores_transaction_id(self):
        self.assertEqual(
            helper.dns_query_cache_key(build_dns_query(b"\x12\x34")),
            helper.dns_query_cache_key(build_dns_query(b"\xab\xcd")),
        )

    async def test_resolve_dns_query_deduplicates_inflight_and_rewrites_response_ids(self):
        runtime = FakeRuntime()
        runtime.config["vpn_dns_servers"] = ["1.1.1.1"]
        release_response = asyncio.Event()
        upstream_calls = 0

        async def fake_tcp_dns_query(_runtime, host, port, payload):
            nonlocal upstream_calls
            upstream_calls += 1
            self.assertEqual(host, "1.1.1.1")
            self.assertEqual(port, 53)
            await release_response.wait()
            return helper.with_dns_transaction_id(payload, b"\xaa\xbb") + b"\x81\x80"

        first_query = build_dns_query(b"\x12\x34")
        second_query = build_dns_query(b"\x56\x78")
        with mock.patch("local_client.helper.tcp_dns_query", side_effect=fake_tcp_dns_query):
            first_task = asyncio.create_task(helper.resolve_dns_query(runtime, "1.1.1.1", first_query))
            second_task = asyncio.create_task(helper.resolve_dns_query(runtime, "1.1.1.1", second_query))
            await asyncio.sleep(0)
            release_response.set()
            first_response, second_response = await asyncio.gather(first_task, second_task)
            cached_response = await helper.resolve_dns_query(runtime, "1.1.1.1", build_dns_query(b"\x9a\xbc"))

        self.assertEqual(upstream_calls, 1)
        self.assertEqual(first_response[:2], b"\x12\x34")
        self.assertEqual(second_response[:2], b"\x56\x78")
        self.assertEqual(cached_response[:2], b"\x9a\xbc")

    async def test_resolve_dns_query_synthesizes_empty_aaaa_response_when_ipv4_is_preferred(self):
        runtime = FakeRuntime()
        runtime.config["vpn_filter_aaaa"] = True
        query = build_dns_aaaa_query(b"\x22\x33")
        with mock.patch("local_client.helper.tcp_dns_query") as tcp_dns_query:
            response = await helper.resolve_dns_query(runtime, "1.1.1.1", query)

        tcp_dns_query.assert_not_called()
        self.assertEqual(response[:2], b"\x22\x33")
        self.assertEqual(helper.dns_question_type(response), helper.DNS_TYPE_AAAA)
        self.assertEqual(response[6:12], b"\x00\x00\x00\x00\x00\x00")

    async def test_resolve_dns_query_synthesizes_empty_https_response_when_ipv4_is_preferred(self):
        runtime = FakeRuntime()
        runtime.config["vpn_filter_aaaa"] = True
        query = build_dns_https_query(b"\x44\x55")
        with mock.patch("local_client.helper.tcp_dns_query") as tcp_dns_query:
            response = await helper.resolve_dns_query(runtime, "1.1.1.1", query)

        tcp_dns_query.assert_not_called()
        self.assertEqual(response[:2], b"\x44\x55")
        self.assertEqual(helper.dns_question_type(response), helper.DNS_TYPE_HTTPS)
        self.assertEqual(response[6:12], b"\x00\x00\x00\x00\x00\x00")

    async def test_local_vpn_dns_proxy_ip_uses_configured_upstreams(self):
        runtime = FakeRuntime()
        runtime.config["vpn_dns_servers"] = ["1.1.1.1"]
        query = build_dns_query(b"\x12\x34")

        async def fake_tcp_dns_query(_runtime, host, port, payload):
            self.assertEqual(host, "1.1.1.1")
            self.assertEqual(port, 53)
            return helper.with_dns_transaction_id(payload, b"\xaa\xbb") + b"\x81\x80"

        with mock.patch("local_client.helper.tcp_dns_query", side_effect=fake_tcp_dns_query):
            response = await helper.resolve_dns_query(runtime, "198.18.0.2", query)

        self.assertEqual(response[:2], b"\x12\x34")

    async def test_resolve_dns_query_does_not_filter_aaaa_without_explicit_vpn_flag(self):
        runtime = FakeRuntime()
        runtime.config["vpn_dns_servers"] = ["1.1.1.1"]
        query = build_dns_aaaa_query(b"\x66\x77")

        async def fake_tcp_dns_query(_runtime, host, port, payload):
            self.assertEqual(host, "1.1.1.1")
            self.assertEqual(port, 53)
            return helper.with_dns_transaction_id(payload, b"\xaa\xbb") + b"\x81\x80"

        with mock.patch("local_client.helper.tcp_dns_query", side_effect=fake_tcp_dns_query):
            response = await helper.resolve_dns_query(runtime, "1.1.1.1", query)

        self.assertEqual(response[:2], b"\x66\x77")
        self.assertEqual(helper.dns_question_type(response), helper.DNS_TYPE_AAAA)

    async def test_resolve_dns_query_races_upstreams_and_returns_fast_success(self):
        runtime = FakeRuntime()
        query = build_dns_query(b"\x10\x20")
        start = time.monotonic()

        async def fake_tcp_dns_query(_runtime, host, port, payload):
            self.assertEqual(port, 53)
            if host == "1.1.1.1":
                await asyncio.sleep(0.25)
                raise asyncio.TimeoutError()
            if host == "8.8.8.8":
                await asyncio.sleep(0.01)
                return helper.with_dns_transaction_id(payload, b"\xaa\xbb") + b"\x81\x80"
            raise AssertionError("unexpected upstream host %s" % host)

        with mock.patch("local_client.helper.tcp_dns_query", side_effect=fake_tcp_dns_query):
            response = await helper.resolve_dns_query(runtime, "198.18.0.2", query)

        elapsed = time.monotonic() - start
        self.assertLess(elapsed, 0.2)
        self.assertEqual(response[:2], b"\x10\x20")
        self.assertEqual(helper.format_error_summary(asyncio.TimeoutError()), "TimeoutError")


if __name__ == "__main__":
    unittest.main()
