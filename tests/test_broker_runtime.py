import unittest
from unittest import mock

from host.runtime.http_broker_daemon import AsyncBrokerServer, BrokerState, _broker_capabilities
from twoman_crypto import TransportCipher
from twoman_http import build_connection_headers, expected_binary_media_type
from twoman_protocol import (
    FRAME_DNS_QUERY,
    FRAME_DNS_RESPONSE,
    FRAME_FIN,
    FRAME_OPEN,
    FRAME_OPEN_FAIL,
    FRAME_WINDOW,
    Frame,
    LANE_CTL,
)


class _FakeWriter:
    def __init__(self):
        self.closed = False
        self.buffer = bytearray()

    def write(self, payload):
        self.buffer.extend(payload)
        return None

    async def drain(self):
        return None

    def close(self):
        self.closed = True

    async def wait_closed(self):
        self.closed = True


class BrokerRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_agent_stream_ids_start_from_randomized_space_and_skip_in_use_ids(self):
        with mock.patch("host.runtime.http_broker_daemon.random.randint", return_value=17):
            state = BrokerState({"client_tokens": ["client-token"], "agent_tokens": ["agent-token"]})
        state.streams_by_agent[17] = object()
        state.streams_by_agent[18] = object()

        first = state._allocate_agent_stream_id_locked()
        second = state._allocate_agent_stream_id_locked()

        self.assertEqual(first, 19)
        self.assertEqual(second, 20)

    async def test_handle_connection_decrypts_with_authenticated_token(self):
        config = {
            "client_tokens": ["old-client-token", "new-client-token"],
            "agent_tokens": ["agent-token"],
        }
        server = AsyncBrokerServer("127.0.0.1", 0, config)
        plaintext = b"test-control-payload"
        iv = bytes(range(16))
        cipher = TransportCipher(b"new-client-token", iv)
        encrypted_body = iv + cipher.process(plaintext)
        headers = build_connection_headers("new-client-token", "helper", "desktop", "helper-session", config)
        headers["content-type"] = expected_binary_media_type(config)
        writer = _FakeWriter()

        server._read_request = mock.AsyncMock(return_value=("POST /ctl/up HTTP/1.1", headers, encrypted_body))
        server._handle_up = mock.AsyncMock()

        await server.handle_connection(object(), writer)

        server._handle_up.assert_awaited_once_with(writer, "helper", "helper-session", "ctl", plaintext)
        peer = server.state.peers[("helper", "helper-session")]
        self.assertEqual(peer.auth_token, "new-client-token")
        self.assertTrue(writer.closed)

    async def test_agent_role_uses_longer_down_wait_window(self):
        server = AsyncBrokerServer(
            "127.0.0.1",
            0,
            {
                "client_tokens": ["client-token"],
                "agent_tokens": ["agent-token"],
                "down_wait_ms": {"ctl": 250, "data": 250},
                "down_wait_ms_by_role": {"agent": {"ctl": 10000, "data": 3000}},
            },
        )

        self.assertEqual(server._down_wait_ms_for("helper", "ctl"), 250)
        self.assertEqual(server._down_wait_ms_for("helper", "data"), 250)
        self.assertEqual(server._down_wait_ms_for("agent", "ctl"), 10000)
        self.assertEqual(server._down_wait_ms_for("agent", "data"), 3000)

    async def test_broker_capabilities_publish_agent_read_timeout_for_long_polls(self):
        capabilities = _broker_capabilities(
            {
                "backend_family": "passenger_python",
                "down_wait_ms": {"ctl": 250, "data": 250},
                "down_wait_ms_by_role": {"agent": {"ctl": 10000, "data": 3000}},
            }
        )

        self.assertEqual(capabilities["backend_family"], "passenger_python")
        self.assertEqual(
            capabilities["profiles"]["shared_host_safe"]["agent"]["down_read_timeout_seconds"],
            20.0,
        )

    async def test_broker_capabilities_keep_agent_parallelism_for_shared_host_polling(self):
        capabilities = _broker_capabilities(
            {
                "backend_family": "passenger_python",
                "agent_down_combined_data_lane": True,
                "streaming_data_down_agent": True,
            }
        )

        self.assertEqual(
            capabilities["profiles"]["shared_host_safe"]["agent"]["down_parallelism"],
            {"data": 2},
        )

    async def test_bridge_capabilities_increase_helper_parallelism_for_combined_data_downlink(self):
        capabilities = _broker_capabilities(
            {
                "backend_family": "bridge_runtime",
                "helper_down_combined_data_lane": True,
            }
        )

        self.assertEqual(
            capabilities["profiles"]["shared_host_safe"]["helper"]["down_parallelism"],
            {"data": 2},
        )

    async def test_bridge_capabilities_keep_agent_parallelism_for_shared_host_polling(self):
        capabilities = _broker_capabilities(
            {
                "backend_family": "bridge_runtime",
                "agent_down_combined_data_lane": True,
                "streaming_data_down_agent": True,
            }
        )

        self.assertEqual(
            capabilities["profiles"]["shared_host_safe"]["agent"]["down_parallelism"],
            {"data": 2},
        )

    async def test_bridge_capabilities_move_agent_stream_control_to_priority_lane(self):
        capabilities = _broker_capabilities(
            {
                "backend_family": "bridge_runtime",
                "agent_down_combined_data_lane": True,
            }
        )

        self.assertEqual(
            capabilities["profiles"]["shared_host_safe"]["agent"]["stream_control_lane"],
            "pri",
        )

    async def test_passenger_capabilities_move_agent_stream_control_to_priority_lane(self):
        capabilities = _broker_capabilities(
            {
                "backend_family": "passenger_python",
                "agent_down_combined_data_lane": True,
            }
        )

        self.assertEqual(
            capabilities["profiles"]["shared_host_safe"]["agent"]["stream_control_lane"],
            "pri",
        )

    async def test_shared_host_backends_force_agent_data_downlink_to_polling(self):
        server = AsyncBrokerServer(
            "127.0.0.1",
            0,
            {
                "backend_family": "bridge_runtime",
                "client_tokens": ["client-token"],
                "agent_tokens": ["agent-token"],
                "streaming_ctl_down_helper": False,
                "streaming_data_down_helper": False,
                "streaming_ctl_down_agent": True,
                "streaming_data_down_agent": True,
            },
        )

        self.assertFalse(server.streaming_ctl_down_helper)
        self.assertFalse(server.streaming_data_down_helper)
        self.assertTrue(server.streaming_ctl_down_agent)
        self.assertFalse(server.streaming_data_down_agent)

    async def test_combined_agent_down_lane_routes_control_frames_over_priority_queue(self):
        state = BrokerState(
            {
                "client_tokens": ["client-token"],
                "agent_tokens": ["agent-token"],
                "agent_down_combined_data_lane": True,
            }
        )
        await state.ensure_peer("helper", "desktop", "helper-session", "client-token")
        await state.ensure_peer("agent", "hidden", "agent-session", "agent-token")
        queued_frames = []

        async def fake_queue(role, peer_session_id, lane, frame):
            queued_frames.append((role, peer_session_id, lane, frame))
            return True

        state.queue_frame = fake_queue
        await state._handle_open("helper-session", Frame(FRAME_OPEN, stream_id=17, payload=b"payload"))
        await state.handle_frame("helper", "helper-session", LANE_CTL, Frame(FRAME_WINDOW, stream_id=17, offset=7))

        self.assertEqual(queued_frames[0][0], "agent")
        self.assertEqual(queued_frames[0][2], "pri")
        self.assertEqual(queued_frames[0][3].type_id, FRAME_OPEN)
        self.assertEqual(queued_frames[1][0], "agent")
        self.assertEqual(queued_frames[1][2], "pri")
        self.assertEqual(queued_frames[1][3].type_id, FRAME_WINDOW)

    async def test_combined_helper_down_lane_routes_control_frames_over_priority_queue(self):
        state = BrokerState(
            {
                "client_tokens": ["client-token"],
                "agent_tokens": ["agent-token"],
                "helper_down_combined_data_lane": True,
            }
        )
        await state.ensure_peer("helper", "desktop", "helper-session", "client-token")
        await state.ensure_peer("agent", "hidden", "agent-session", "agent-token")
        queued_frames = []

        async def fake_queue(role, peer_session_id, lane, frame):
            queued_frames.append((role, peer_session_id, lane, frame))
            return True

        state.queue_frame = fake_queue
        await state._handle_open("helper-session", Frame(FRAME_OPEN, stream_id=17, payload=b"payload"))
        stream = state.streams_by_helper[("helper-session", 17)]
        await state.handle_frame("agent", "agent-session", LANE_CTL, Frame(FRAME_WINDOW, stream_id=stream.agent_stream_id, offset=7))

        self.assertEqual(queued_frames[0][0], "agent")
        self.assertEqual(queued_frames[0][2], LANE_CTL)
        self.assertEqual(queued_frames[0][3].type_id, FRAME_OPEN)
        self.assertEqual(queued_frames[1][0], "helper")
        self.assertEqual(queued_frames[1][2], "pri")
        self.assertEqual(queued_frames[1][3].type_id, FRAME_WINDOW)

    async def test_handle_open_rolls_back_stream_when_agent_queue_rejects_open(self):
        state = BrokerState({"client_tokens": ["client-token"], "agent_tokens": ["agent-token"]})
        await state.ensure_peer("helper", "desktop", "helper-session", "client-token")
        await state.ensure_peer("agent", "hidden", "agent-session", "agent-token")
        queued_frames = []

        async def fake_queue(role, peer_session_id, lane, frame):
            queued_frames.append((role, peer_session_id, lane, frame))
            return role == "helper"

        state.queue_frame = fake_queue
        await state._handle_open("helper-session", Frame(FRAME_OPEN, stream_id=17, payload=b"payload"))

        self.assertEqual(state.streams_by_helper, {})
        self.assertEqual(state.streams_by_agent, {})
        helper_peer = state.peers[("helper", "helper-session")]
        agent_peer = state.peers[("agent", "agent-session")]
        self.assertEqual(helper_peer.active_streams, 0)
        self.assertEqual(agent_peer.active_streams, 0)
        self.assertEqual(len(queued_frames), 2)
        self.assertEqual(queued_frames[0][0], "agent")
        self.assertEqual(queued_frames[0][2], LANE_CTL)
        self.assertEqual(queued_frames[0][3].type_id, FRAME_OPEN)
        self.assertEqual(queued_frames[1][0], "helper")
        self.assertEqual(queued_frames[1][2], LANE_CTL)
        self.assertEqual(queued_frames[1][3].type_id, FRAME_OPEN_FAIL)

    async def test_handle_frame_waits_for_window_ack_before_dropping_finished_stream(self):
        state = BrokerState({"client_tokens": ["client-token"], "agent_tokens": ["agent-token"]})
        await state.ensure_peer("helper", "desktop", "helper-session", "client-token")
        await state.ensure_peer("agent", "hidden", "agent-session", "agent-token")
        queued_frames = []

        async def fake_queue(role, peer_session_id, lane, frame):
            queued_frames.append((role, peer_session_id, lane, frame))
            return True

        state.queue_frame = fake_queue
        await state._handle_open("helper-session", Frame(FRAME_OPEN, stream_id=17, payload=b"payload"))

        stream = state.streams_by_helper[("helper-session", 17)]
        agent_stream_id = stream.agent_stream_id

        await state.handle_frame("helper", "helper-session", LANE_CTL, Frame(FRAME_FIN, stream_id=17, offset=5))
        self.assertIn(("helper-session", 17), state.streams_by_helper)
        self.assertTrue(stream.helper_fin_seen)
        self.assertFalse(stream.agent_fin_seen)

        await state.handle_frame("agent", "agent-session", LANE_CTL, Frame(FRAME_FIN, stream_id=agent_stream_id, offset=7))
        self.assertIn(("helper-session", 17), state.streams_by_helper)
        self.assertEqual(stream.helper_fin_offset, 5)
        self.assertEqual(stream.agent_fin_offset, 7)

        await state.handle_frame("helper", "helper-session", LANE_CTL, Frame(FRAME_WINDOW, stream_id=17, offset=7))
        self.assertIn(("helper-session", 17), state.streams_by_helper)

        await state.handle_frame("agent", "agent-session", LANE_CTL, Frame(FRAME_WINDOW, stream_id=agent_stream_id, offset=5))
        self.assertEqual(state.streams_by_helper, {})
        self.assertEqual(state.streams_by_agent, {})

        self.assertEqual(
            [entry[3].type_id for entry in queued_frames],
            [FRAME_OPEN, FRAME_FIN, FRAME_FIN, FRAME_WINDOW, FRAME_WINDOW],
        )

    async def test_handle_frame_maps_dns_queries_without_creating_stream_state(self):
        state = BrokerState({"client_tokens": ["client-token"], "agent_tokens": ["agent-token"]})
        await state.ensure_peer("helper", "desktop", "helper-session", "client-token")
        await state.ensure_peer("agent", "hidden", "agent-session", "agent-token")
        queued_frames = []

        async def fake_queue(role, peer_session_id, lane, frame):
            queued_frames.append((role, peer_session_id, lane, frame))
            return True

        state.queue_frame = fake_queue
        await state.handle_frame("helper", "helper-session", "pri", Frame(FRAME_DNS_QUERY, stream_id=41, payload=b"query"))

        self.assertEqual(state.streams_by_helper, {})
        self.assertEqual(len(state.dns_queries_by_helper), 1)
        query = state.dns_queries_by_helper[("helper-session", 41)]
        self.assertEqual(queued_frames[0][0], "agent")
        self.assertEqual(queued_frames[0][2], "pri")
        self.assertEqual(queued_frames[0][3].type_id, FRAME_DNS_QUERY)
        self.assertEqual(queued_frames[0][3].stream_id, query.agent_request_id)

        await state.handle_frame(
            "agent",
            "agent-session",
            "pri",
            Frame(FRAME_DNS_RESPONSE, stream_id=query.agent_request_id, payload=b"response"),
        )

        self.assertEqual(len(state.dns_queries_by_helper), 0)
        self.assertEqual(len(state.dns_queries_by_agent), 0)
        self.assertEqual(queued_frames[1][0], "helper")
        self.assertEqual(queued_frames[1][2], "pri")
        self.assertEqual(queued_frames[1][3].type_id, FRAME_DNS_RESPONSE)
        self.assertEqual(queued_frames[1][3].stream_id, 41)

    async def test_handle_ctl_down_returns_no_content_when_idle(self):
        server = AsyncBrokerServer(
            "127.0.0.1",
            0,
            {
                "client_tokens": ["client-token"],
                "agent_tokens": ["agent-token"],
                "down_wait_ms": {"ctl": 0, "data": 0},
            },
        )
        peer = await server.state.ensure_peer("helper", "desktop", "helper-session", "client-token")
        writer = _FakeWriter()

        await server._handle_ctl_down(writer, peer)

        self.assertTrue(bytes(writer.buffer).startswith(b"HTTP/1.1 204 No Content\r\n"))
        self.assertEqual(server.state.metrics["down_responses"][LANE_CTL], 0)

    async def test_handle_data_down_returns_no_content_when_idle(self):
        server = AsyncBrokerServer(
            "127.0.0.1",
            0,
            {
                "client_tokens": ["client-token"],
                "agent_tokens": ["agent-token"],
                "down_wait_ms": {"ctl": 0, "data": 0},
            },
        )
        peer = await server.state.ensure_peer("agent", "hidden", "agent-session", "agent-token")
        writer = _FakeWriter()

        await server._handle_data_down(writer, peer)

        self.assertTrue(bytes(writer.buffer).startswith(b"HTTP/1.1 204 No Content\r\n"))
        self.assertEqual(server.state.metrics["down_responses"]["pri"], 0)
        self.assertEqual(server.state.metrics["down_responses"]["bulk"], 0)


if __name__ == "__main__":
    unittest.main()
