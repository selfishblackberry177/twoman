import unittest
from unittest import mock

from twoman_http import RouteProvider
from twoman_transport import (
    AdaptiveTransport,
    LaneTransport,
    PROFILE_MANAGED_HOST_HTTP,
    PROFILE_MANAGED_HOST_WS,
    PROFILE_SHARED_HOST_SAFE,
    _apply_transport_profile,
    _extract_transport_capabilities,
    _instantiate_transport,
    _profile_candidates,
)


class TransportProfileTests(unittest.TestCase):
    def test_route_provider_websocket_url_uses_websocket_scheme(self):
        secure_provider = RouteProvider("https://host.example.com/darvazeh")
        insecure_provider = RouteProvider("http://host.example.com/darvazeh")
        self.assertEqual(secure_provider.ws_lane_url("ctl"), "wss://host.example.com/darvazeh/ctl")
        self.assertEqual(insecure_provider.ws_lane_url("data"), "ws://host.example.com/darvazeh/data")

    def test_extract_transport_capabilities_handles_wrapped_health_payload(self):
        capabilities = _extract_transport_capabilities(
            {
                "ok": True,
                "stats": {
                    "capabilities": {
                        "backend_family": "passenger_python",
                        "recommended_profile": PROFILE_SHARED_HOST_SAFE,
                    }
                },
            }
        )
        self.assertEqual(capabilities["backend_family"], "passenger_python")
        self.assertEqual(capabilities["recommended_profile"], PROFILE_SHARED_HOST_SAFE)

    def test_profile_candidates_prefer_ws_when_upstream_proxy_is_present(self):
        candidates = _profile_candidates(
            {
                "transport_profile": "auto",
                "transport": "http",
                "upstream_proxy_url": "socks5://127.0.0.1:1280",
            },
            {
                "backend_family": "node_selector",
                "recommended_profile": PROFILE_MANAGED_HOST_HTTP,
                "supported_profiles": [PROFILE_MANAGED_HOST_HTTP, PROFILE_MANAGED_HOST_WS],
            },
        )
        self.assertEqual(candidates[0], PROFILE_MANAGED_HOST_WS)

    def test_profile_candidates_prefer_ws_on_managed_host_without_upstream_proxy(self):
        candidates = _profile_candidates(
            {
                "transport_profile": "auto",
                "transport": "http",
                "upstream_proxy_url": "",
            },
            {
                "backend_family": "node_selector",
                "recommended_profile": PROFILE_MANAGED_HOST_HTTP,
                "supported_profiles": [PROFILE_MANAGED_HOST_HTTP, PROFILE_MANAGED_HOST_WS],
            },
        )
        self.assertEqual(candidates[0], PROFILE_MANAGED_HOST_WS)

    def test_websocket_transport_disables_ambient_proxy_autodiscovery_without_explicit_proxy(self):
        transport = _instantiate_transport(
            _apply_transport_profile(
                {
                    "transport": "http",
                    "broker_base_url": "https://host.example.com/darvazeh",
                    "client_token": "test-client-token",
                    "agent_token": "test-agent-token",
                    "http2_enabled": {"ctl": False, "data": False},
                    "upload_profiles": {},
                    "idle_repoll_delay_seconds": {},
                    "streaming_up_lanes": [],
                },
                "agent",
                {},
                PROFILE_MANAGED_HOST_WS,
            ),
            "agent",
            "agent-test",
            lambda frame, lane=None: None,
        )
        self.assertEqual(transport._websocket_proxy_arg(), None)

    def test_websocket_transport_passes_explicit_upstream_proxy(self):
        transport = _instantiate_transport(
            _apply_transport_profile(
                {
                    "transport": "http",
                    "broker_base_url": "https://host.example.com/darvazeh",
                    "client_token": "test-client-token",
                    "agent_token": "test-agent-token",
                    "upstream_proxy_url": "socks5h://127.0.0.1:1280",
                    "http2_enabled": {"ctl": False, "data": False},
                    "upload_profiles": {},
                    "idle_repoll_delay_seconds": {},
                    "streaming_up_lanes": [],
                },
                "agent",
                {},
                PROFILE_MANAGED_HOST_WS,
            ),
            "agent",
            "agent-test",
            lambda frame, lane=None: None,
        )
        self.assertEqual(transport._websocket_proxy_arg(), "socks5h://127.0.0.1:1280")

    def test_websocket_probe_omits_ssl_kwarg_for_verified_wss_routes(self):
        captured = {}

        class FakeWebSocket:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

        def fake_connect(url, **kwargs):
            captured["url"] = url
            captured["kwargs"] = kwargs
            return FakeWebSocket()

        transport = AdaptiveTransport(
            {
                "broker_base_url": "https://host.example.com/darvazeh",
                "client_token": "test-client-token",
                "agent_token": "test-agent-token",
                "verify_tls": True,
            },
            "agent",
            "agent-test",
            lambda frame, lane=None: None,
        )
        with mock.patch("twoman_transport.ws_connect", side_effect=fake_connect):
            ok, reason = __import__("asyncio").run(
                transport._probe_websocket_transport(
                    {
                        "broker_base_url": "https://host.example.com/darvazeh",
                        "client_token": "test-client-token",
                        "agent_token": "test-agent-token",
                        "verify_tls": True,
                        "http_timeout_seconds": 5,
                    }
                )
            )
        self.assertTrue(ok, reason)
        self.assertEqual(captured["url"], "wss://host.example.com/darvazeh/ctl")
        self.assertNotIn("ssl", captured["kwargs"])

    def test_apply_transport_profile_uses_role_specific_overrides(self):
        resolved = _apply_transport_profile(
            {
                "transport": "http",
                "http2_enabled": {"ctl": False, "data": False},
                "upload_profiles": {},
                "idle_repoll_delay_seconds": {},
                "streaming_up_lanes": ["ctl"],
            },
            "helper",
            {
                "profiles": {
                    PROFILE_MANAGED_HOST_HTTP: {
                        "helper": {
                            "http2_enabled": {"ctl": True, "data": False},
                        }
                    }
                }
            },
            PROFILE_MANAGED_HOST_HTTP,
        )
        self.assertEqual(resolved["transport"], "http")
        self.assertEqual(resolved["http2_enabled"]["ctl"], True)
        self.assertEqual(resolved["streaming_up_lanes"], [])

    def test_managed_host_http_helper_profile_uses_parallel_data_polls(self):
        resolved = _apply_transport_profile(
            {
                "transport": "http",
                "http2_enabled": {"ctl": False, "data": False},
                "upload_profiles": {},
                "idle_repoll_delay_seconds": {},
                "streaming_up_lanes": [],
            },
            "helper",
            {},
            PROFILE_MANAGED_HOST_HTTP,
        )
        self.assertEqual(resolved["down_parallelism"], {"data": 2})
        self.assertEqual(resolved["down_lanes"], ["data"])

    def test_managed_host_http_agent_profile_uses_short_lived_proxy_keepalives(self):
        resolved = _apply_transport_profile(
            {
                "transport": "http",
                "http2_enabled": {"ctl": False, "data": False},
                "upload_profiles": {},
                "idle_repoll_delay_seconds": {},
                "streaming_up_lanes": [],
            },
            "agent",
            {},
            PROFILE_MANAGED_HOST_HTTP,
        )
        self.assertEqual(resolved["proxy_keepalive_connections"], 2)
        self.assertEqual(resolved["proxy_keepalive_expiry_seconds"], 15.0)

    def test_shared_host_safe_agent_profile_extends_read_timeout_for_slow_hidden_routes(self):
        resolved = _apply_transport_profile(
            {
                "transport": "http",
                "http2_enabled": {"ctl": False, "data": False},
                "upload_profiles": {},
                "idle_repoll_delay_seconds": {},
                "streaming_up_lanes": [],
            },
            "agent",
            {},
            PROFILE_SHARED_HOST_SAFE,
        )
        self.assertEqual(resolved["down_read_timeout_seconds"], 20.0)
        self.assertEqual(resolved["down_lanes"], ["data"])
        self.assertEqual(resolved["down_parallelism"], {"data": 2})
        self.assertEqual(resolved["up_request_timeout_seconds"], {"ctl": 5.0, "data": 20.0})

    def test_shared_host_safe_helper_profile_disables_http2_control_uploads(self):
        resolved = _apply_transport_profile(
            {
                "transport": "http",
                "http2_enabled": {"ctl": True, "data": True},
                "upload_profiles": {},
                "idle_repoll_delay_seconds": {},
                "streaming_up_lanes": ["ctl"],
            },
            "helper",
            {},
            PROFILE_SHARED_HOST_SAFE,
        )
        self.assertEqual(resolved["http2_enabled"], {"ctl": False, "data": False})
        self.assertEqual(resolved["down_lanes"], ["data"])
        self.assertEqual(resolved["streaming_up_lanes"], [])
        self.assertEqual(resolved["up_request_timeout_seconds"], {"ctl": 5.0, "data": 20.0})

    def test_bridge_capabilities_can_raise_helper_data_parallelism(self):
        resolved = _apply_transport_profile(
            {
                "transport": "http",
                "http2_enabled": {"ctl": False, "data": False},
                "upload_profiles": {},
                "idle_repoll_delay_seconds": {},
                "streaming_up_lanes": [],
            },
            "helper",
            {
                "backend_family": "bridge_runtime",
                "profiles": {
                    PROFILE_SHARED_HOST_SAFE: {
                        "helper": {
                            "down_parallelism": {"data": 2},
                        }
                    }
                },
            },
            PROFILE_SHARED_HOST_SAFE,
        )
        self.assertEqual(resolved["down_parallelism"], {"data": 2})

    def test_bridge_capabilities_can_keep_agent_parallelism_for_shared_host_polling(self):
        resolved = _apply_transport_profile(
            {
                "transport": "http",
                "http2_enabled": {"ctl": False, "data": False},
                "upload_profiles": {},
                "idle_repoll_delay_seconds": {},
                "streaming_up_lanes": [],
            },
            "agent",
            {
                "backend_family": "bridge_runtime",
                "profiles": {
                    PROFILE_SHARED_HOST_SAFE: {
                        "agent": {
                            "down_parallelism": {"data": 2},
                        }
                    }
                },
            },
            PROFILE_SHARED_HOST_SAFE,
        )
        self.assertEqual(resolved["down_parallelism"], {"data": 2})

    def test_bridge_capabilities_can_move_agent_stream_control_to_priority_lane(self):
        resolved = _apply_transport_profile(
            {
                "transport": "http",
                "http2_enabled": {"ctl": False, "data": False},
                "upload_profiles": {},
                "idle_repoll_delay_seconds": {},
                "streaming_up_lanes": [],
            },
            "agent",
            {
                "backend_family": "bridge_runtime",
                "profiles": {
                    PROFILE_SHARED_HOST_SAFE: {
                        "agent": {
                            "stream_control_lane": "pri",
                        }
                    }
                },
            },
            PROFILE_SHARED_HOST_SAFE,
        )
        self.assertEqual(resolved["stream_control_lane"], "pri")

    def test_http_transport_uses_short_lived_keepalive_for_proxy_backed_lanes(self):
        transport = LaneTransport(
            base_url="https://host.example.com/darvazeh",
            token="test-token",
            role="agent",
            peer_id="agent-test",
            on_frame=lambda frame: None,
            upstream_proxy_url="socks5://127.0.0.1:1280",
        )
        down_client = transport._build_client("ctl", "down")
        up_client = transport._build_client("ctl", "up")
        down_pool = down_client._transport._pool
        up_pool = up_client._transport._pool
        try:
            self.assertEqual(down_pool._max_keepalive_connections, 2)
            self.assertEqual(down_pool._keepalive_expiry, 15.0)
            self.assertEqual(up_pool._max_keepalive_connections, 2)
            self.assertEqual(up_pool._keepalive_expiry, 15.0)
        finally:
            import asyncio
            asyncio.run(down_client.aclose())
            asyncio.run(up_client.aclose())

    def test_proxy_backed_agent_uses_parallel_data_down_polls(self):
        transport = LaneTransport(
            base_url="https://host.example.com/darvazeh",
            token="test-token",
            role="agent",
            peer_id="agent-test",
            on_frame=lambda frame, lane=None: None,
            collapse_data_lanes=True,
            down_lanes=["data"],
            upstream_proxy_url="socks5://127.0.0.1:1280",
        )
        self.assertEqual(transport.down_parallelism["data"], 2)
        self.assertEqual(transport.down_parallelism["ctl"], 1)

    def test_explicit_agent_parallelism_override_beats_proxy_default(self):
        transport = LaneTransport(
            base_url="https://host.example.com/darvazeh",
            token="test-token",
            role="agent",
            peer_id="agent-test",
            on_frame=lambda frame, lane=None: None,
            collapse_data_lanes=True,
            down_lanes=["data"],
            down_parallelism={"data": 1},
            upstream_proxy_url="socks5://127.0.0.1:1280",
        )
        self.assertEqual(transport.down_parallelism["data"], 1)

    def test_lane_transport_normalizes_stream_control_lane_override(self):
        transport = LaneTransport(
            base_url="https://host.example.com/darvazeh",
            token="test-token",
            role="agent",
            peer_id="agent-test",
            on_frame=lambda frame, lane=None: None,
            collapse_data_lanes=True,
            down_lanes=["data"],
            stream_control_lane="pri",
        )
        self.assertEqual(transport.stream_control_lane, "pri")

    def test_explicit_ws_profile_instantiates_websocket_transport_with_stream_control_lane(self):
        transport = _instantiate_transport(
            {
                "transport": "ws",
                "broker_base_url": "https://host.example.com/darvazeh",
                "client_token": "test-client-token",
                "agent_token": "test-agent-token",
                "http_timeout_seconds": 10,
                "max_batch_bytes": 65536,
                "flush_delay_seconds": 0.01,
                "verify_tls": True,
                "http2_enabled": {"ctl": False, "data": False},
                "upload_profiles": {},
                "streaming_up_lanes": [],
                "down_lanes": ["data"],
                "down_parallelism": {"data": 2},
                "up_request_timeout_seconds": {"ctl": 5.0, "data": 10.0},
                "stream_control_lane": "pri",
            },
            "helper",
            "helper-test",
            lambda frame, lane=None: None,
        )
        self.assertEqual(type(transport).__name__, "WebSocketLaneTransport")
        self.assertEqual(transport.stream_control_lane, "pri")

    def test_lane_transport_normalizes_up_request_timeout_overrides(self):
        transport = LaneTransport(
            base_url="https://host.example.com/darvazeh",
            token="test-token",
            role="helper",
            peer_id="helper-test",
            on_frame=lambda frame, lane=None: None,
            collapse_data_lanes=True,
            down_lanes=["data"],
            up_request_timeout_seconds={"ctl": 4, "pri": 9},
        )
        self.assertEqual(transport.up_request_timeout_seconds["ctl"], 4.0)
        self.assertEqual(transport.up_request_timeout_seconds["data"], 9.0)


if __name__ == "__main__":
    unittest.main()
