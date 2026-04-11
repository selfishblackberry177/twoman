import unittest
from unittest import mock

from host.runtime.http_broker_daemon import AsyncBrokerServer, BrokerState
from twoman_crypto import TransportCipher
from twoman_http import build_connection_headers, expected_binary_media_type
from twoman_protocol import FRAME_FIN, FRAME_OPEN, FRAME_OPEN_FAIL, FRAME_WINDOW, Frame, LANE_CTL


class _FakeWriter:
    def __init__(self):
        self.closed = False

    def write(self, _payload):
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


if __name__ == "__main__":
    unittest.main()
