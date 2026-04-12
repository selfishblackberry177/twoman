import asyncio
import time
import unittest
from unittest import mock

from local_client import helper
from twoman_dns import parse_dns_query_frame_payload
from twoman_protocol import FRAME_DNS_QUERY, LANE_PRI


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
        self.dns_requests = {}
        self.dns_cache_lock = asyncio.Lock()
        self._next_dns_request_id = 2

    def allocate_dns_request_id(self):
        request_id = self._next_dns_request_id
        self._next_dns_request_id += 2
        return request_id


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

        async def fake_query_dns_transport(_runtime, host, payload):
            nonlocal upstream_calls
            upstream_calls += 1
            self.assertEqual(host, "1.1.1.1")
            await release_response.wait()
            return helper.with_dns_transaction_id(payload, b"\xaa\xbb") + b"\x81\x80"

        first_query = build_dns_query(b"\x12\x34")
        second_query = build_dns_query(b"\x56\x78")
        with mock.patch("local_client.helper.query_dns_transport", side_effect=fake_query_dns_transport):
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
        with mock.patch("local_client.helper.query_dns_transport") as query_dns_transport:
            response = await helper.resolve_dns_query(runtime, "1.1.1.1", query)

        query_dns_transport.assert_not_called()
        self.assertEqual(response[:2], b"\x22\x33")
        self.assertEqual(helper.dns_question_type(response), helper.DNS_TYPE_AAAA)
        self.assertEqual(response[6:12], b"\x00\x00\x00\x00\x00\x00")

    async def test_resolve_dns_query_synthesizes_empty_https_response_when_ipv4_is_preferred(self):
        runtime = FakeRuntime()
        runtime.config["vpn_filter_aaaa"] = True
        query = build_dns_https_query(b"\x44\x55")
        with mock.patch("local_client.helper.query_dns_transport") as query_dns_transport:
            response = await helper.resolve_dns_query(runtime, "1.1.1.1", query)

        query_dns_transport.assert_not_called()
        self.assertEqual(response[:2], b"\x44\x55")
        self.assertEqual(helper.dns_question_type(response), helper.DNS_TYPE_HTTPS)
        self.assertEqual(response[6:12], b"\x00\x00\x00\x00\x00\x00")

    async def test_local_vpn_dns_proxy_ip_is_forwarded_to_agent_dns_protocol(self):
        runtime = FakeRuntime()
        runtime.config["vpn_dns_servers"] = ["1.1.1.1"]
        query = build_dns_query(b"\x12\x34")

        async def fake_query_dns_transport(_runtime, host, payload):
            self.assertEqual(host, "198.18.0.2")
            return helper.with_dns_transaction_id(payload, b"\xaa\xbb") + b"\x81\x80"

        with mock.patch("local_client.helper.query_dns_transport", side_effect=fake_query_dns_transport):
            response = await helper.resolve_dns_query(runtime, "198.18.0.2", query)

        self.assertEqual(response[:2], b"\x12\x34")

    async def test_resolve_dns_query_does_not_filter_aaaa_without_explicit_vpn_flag(self):
        runtime = FakeRuntime()
        runtime.config["vpn_dns_servers"] = ["1.1.1.1"]
        query = build_dns_aaaa_query(b"\x66\x77")

        async def fake_query_dns_transport(_runtime, host, payload):
            self.assertEqual(host, "1.1.1.1")
            return helper.with_dns_transaction_id(payload, b"\xaa\xbb") + b"\x81\x80"

        with mock.patch("local_client.helper.query_dns_transport", side_effect=fake_query_dns_transport):
            response = await helper.resolve_dns_query(runtime, "1.1.1.1", query)

        self.assertEqual(response[:2], b"\x66\x77")
        self.assertEqual(helper.dns_question_type(response), helper.DNS_TYPE_AAAA)

    async def test_query_dns_transport_sends_dns_query_frame_on_priority_lane(self):
        runtime = FakeRuntime()
        sent = []

        class FakeTransport:
            async def send_frame(self, lane, frame):
                sent.append((lane, frame))
                details = parse_dns_query_frame_payload(frame.payload)
                self_test_response = helper.with_dns_transaction_id(details["dns_payload"], b"\xaa\xbb") + b"\x81\x80"
                runtime.dns_requests[frame.stream_id].set_result(self_test_response)

        runtime.transport = FakeTransport()
        query = build_dns_query(b"\x10\x20")
        response = await helper.query_dns_transport(runtime, "198.18.0.2", query)

        self.assertEqual(len(sent), 1)
        lane, frame = sent[0]
        self.assertEqual(lane, LANE_PRI)
        self.assertEqual(frame.type_id, FRAME_DNS_QUERY)
        details = parse_dns_query_frame_payload(frame.payload)
        self.assertEqual(details["target_host"], "198.18.0.2")
        self.assertEqual(details["dns_payload"], query)
        self.assertEqual(response[:2], b"\xaa\xbb")
        self.assertEqual(helper.format_error_summary(asyncio.TimeoutError()), "TimeoutError")


if __name__ == "__main__":
    unittest.main()
