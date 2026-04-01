import unittest

from local_client.helper import authority_matches
from twoman_http import (
    RouteProvider,
    build_connection_headers,
    extract_connection_identity,
    parse_lane_path,
)


class ProtocolHardeningTests(unittest.TestCase):
    def test_build_connection_headers_prefers_bearer_and_cookies(self):
        headers = build_connection_headers(
            "secret-token",
            "helper",
            "peer-a",
            "session-a",
            {
                "auth_mode": "bearer",
                "legacy_custom_headers_enabled": False,
                "binary_media_type": "image/webp",
            },
        )
        self.assertEqual(headers["Authorization"], "Bearer secret-token")
        self.assertIn("twoman_role=helper", headers["Cookie"])
        self.assertNotIn("X-Relay-Token", headers)

    def test_extract_connection_identity_reads_bearer_and_cookie_identity(self):
        identity = extract_connection_identity(
            {
                "authorization": "Bearer secret-token",
                "cookie": "twoman_role=agent; twoman_peer=peer-b; twoman_session=session-b",
            },
            {"auth_mode": "bearer"},
        )
        self.assertEqual(identity["token"], "secret-token")
        self.assertEqual(identity["role"], "agent")
        self.assertEqual(identity["peer_label"], "peer-b")
        self.assertEqual(identity["peer_session_id"], "session-b")

    def test_route_provider_renders_dynamic_paths(self):
        provider = RouteProvider(
            "https://example.test/api/v1/telemetry",
            route_template="/{lane}/{direction}",
            health_template="/health",
        )
        self.assertEqual(
            provider.lane_url("ctl", "down"),
            "https://example.test/api/v1/telemetry/ctl/down",
        )
        self.assertEqual(
            provider.health_url(),
            "https://example.test/api/v1/telemetry/health",
        )

    def test_parse_lane_path_matches_configured_template(self):
        route = parse_lane_path(
            "/data/up",
            "/{lane}/{direction}",
        )
        self.assertIsNotNone(route)
        self.assertEqual(route["lane"], "data")
        self.assertEqual(route["direction"], "up")

    def test_authority_matches_requires_host_consistency(self):
        self.assertTrue(authority_matches("example.test", 443, "example.test:443"))
        self.assertFalse(authority_matches("example.test", 443, "other.test:443"))


if __name__ == "__main__":
    unittest.main()
