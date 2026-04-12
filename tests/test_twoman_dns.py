import asyncio
import time
import unittest
from unittest import mock

import twoman_dns


class TwomanDnsTests(unittest.IsolatedAsyncioTestCase):
    async def test_query_dns_upstream_falls_back_to_tcp_when_udp_is_truncated(self):
        with mock.patch("twoman_dns.udp_dns_query", return_value=b"\x12\x34\x82\x00\x00\x01\x00\x00\x00\x00\x00\x00"), \
             mock.patch("twoman_dns.tcp_dns_query", return_value=b"\x12\x34\x81\x80\x00\x01\x00\x01\x00\x00\x00\x00") as tcp_dns_query:
            response = await twoman_dns.query_dns_upstream("1.1.1.1", 53, b"query", 1.0)

        tcp_dns_query.assert_awaited_once()
        self.assertEqual(response[:2], b"\x12\x34")

    async def test_query_dns_upstream_falls_back_to_tcp_when_udp_fails(self):
        with mock.patch("twoman_dns.udp_dns_query", side_effect=asyncio.TimeoutError()), \
             mock.patch("twoman_dns.tcp_dns_query", return_value=b"\xaa\xbb\x81\x80\x00\x01\x00\x01\x00\x00\x00\x00") as tcp_dns_query:
            response = await twoman_dns.query_dns_upstream("8.8.8.8", 53, b"query", 1.0)

        tcp_dns_query.assert_awaited_once()
        self.assertEqual(response[:2], b"\xaa\xbb")

    async def test_resolve_dns_via_upstreams_returns_fast_success(self):
        start = time.monotonic()

        async def fake_query_dns_upstream(host, _port, _payload, _timeout):
            if host == "1.1.1.1":
                await asyncio.sleep(0.25)
                raise asyncio.TimeoutError()
            if host == "8.8.8.8":
                await asyncio.sleep(0.01)
                return b"\x12\x34\x81\x80\x00\x01\x00\x01\x00\x00\x00\x00"
            raise AssertionError("unexpected upstream host %s" % host)

        with mock.patch("twoman_dns.query_dns_upstream", side_effect=fake_query_dns_upstream):
            upstream_host, response = await twoman_dns.resolve_dns_via_upstreams(
                ["1.1.1.1", "8.8.8.8"],
                b"query",
                1.0,
            )

        elapsed = time.monotonic() - start
        self.assertLess(elapsed, 0.2)
        self.assertEqual(upstream_host, "8.8.8.8")
        self.assertEqual(response[:2], b"\x12\x34")


if __name__ == "__main__":
    unittest.main()
