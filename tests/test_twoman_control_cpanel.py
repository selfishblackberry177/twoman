from __future__ import annotations

import unittest
from unittest.mock import patch

import httpx

from twoman_control.cpanel import CpanelClient
from twoman_control.models import BACKEND_PASSENGER


def _response(method: str, url: str, payload: dict) -> httpx.Response:
    request = httpx.Request(method, url)
    return httpx.Response(200, request=request, json=payload)


class CpanelClientTests(unittest.TestCase):
    def test_api_get_retries_transient_transport_errors(self) -> None:
        client = CpanelClient(
            base_url="https://host.example.com:2083",
            username="cpanel-user",
            password="cpanel-pass",
            cpanel_home="/home/cpanel-user",
        )
        url = "https://host.example.com:2083/execute/PassengerApps/list_applications"
        responses = [
            httpx.ConnectError("reset by peer"),
            _response("GET", url, {"status": 1, "data": {}}),
        ]

        with patch("twoman_control.cpanel.httpx.request", side_effect=responses) as request_mock:
            response = client._api_get("PassengerApps/list_applications")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(request_mock.call_count, 2)

    def test_passenger_supported_survives_single_reset(self) -> None:
        client = CpanelClient(
            base_url="https://host.example.com:2083",
            username="cpanel-user",
            password="cpanel-pass",
            cpanel_home="/home/cpanel-user",
        )
        url = "https://host.example.com:2083/execute/PassengerApps/list_applications"
        responses = [
            httpx.ReadError("reset by peer"),
            _response("GET", url, {"status": 1, "data": {"example": {"base_uri": "/darvazeh"}}}),
        ]

        with patch("twoman_control.cpanel.httpx.request", side_effect=responses):
            capability = client.passenger_supported()

        self.assertEqual(capability.key, BACKEND_PASSENGER)
        self.assertTrue(capability.available)

    def test_verify_public_tls_uses_configured_proxy(self) -> None:
        client = CpanelClient(
            base_url="https://host.example.com:2083",
            username="cpanel-user",
            password="cpanel-pass",
            cpanel_home="/home/cpanel-user",
            proxy_url="socks5://127.0.0.1:1280",
        )

        with patch("twoman_control.cpanel.httpx.request", return_value=_response("GET", "https://host.example.com", {"ok": True})) as request_mock:
            self.assertTrue(client.verify_public_tls("https://host.example.com"))

        self.assertEqual(request_mock.call_args.kwargs.get("proxy"), "socks5h://127.0.0.1:1280")


if __name__ == "__main__":
    unittest.main()
