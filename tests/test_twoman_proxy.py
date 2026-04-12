import asyncio
import unittest
from unittest import mock

import twoman_proxy


class TwomanProxyTests(unittest.IsolatedAsyncioTestCase):
    def test_normalize_python_socks_proxy_url_converts_socks5h(self):
        self.assertEqual(
            twoman_proxy.normalize_python_socks_proxy_url("socks5h://127.0.0.1:1280"),
            "socks5://127.0.0.1:1280",
        )

    async def test_open_connection_via_proxy_uses_proxy_socket(self):
        fake_sock = object()
        fake_proxy = mock.Mock()
        fake_proxy.connect = mock.AsyncMock(return_value=fake_sock)
        reader = asyncio.StreamReader()
        writer = mock.Mock()

        with mock.patch.object(twoman_proxy, "Proxy") as proxy_cls, mock.patch.object(
            twoman_proxy.asyncio,
            "open_connection",
            mock.AsyncMock(return_value=(reader, writer)),
        ) as open_connection:
            proxy_cls.from_url.return_value = fake_proxy
            result = await twoman_proxy.open_connection_via_proxy(
                "socks5h://127.0.0.1:1280",
                "example.com",
                443,
                5.0,
            )

        proxy_cls.from_url.assert_called_once_with("socks5://127.0.0.1:1280")
        fake_proxy.connect.assert_awaited_once_with(dest_host="example.com", dest_port=443, timeout=5.0)
        open_connection.assert_awaited_once_with(sock=fake_sock)
        self.assertEqual(result, (reader, writer))


if __name__ == "__main__":
    unittest.main()
