import unittest
from unittest import mock

from hidden_server import agent
from twoman_protocol import FRAME_DNS_RESPONSE, LANE_PRI


class _FakeTransport:
    def __init__(self):
        self.peer_session_id = "test-session"
        self.event_handler = None
        self.sent_frames = []

    async def start(self):
        return None

    async def stop(self):
        return None

    async def send_frame(self, lane, frame):
        self.sent_frames.append((lane, frame))


class HiddenAgentConnectTests(unittest.IsolatedAsyncioTestCase):
    async def test_ipv6_literal_fails_fast_when_disabled(self):
        with mock.patch.object(agent, "create_transport", return_value=_FakeTransport()):
            runtime = agent.AgentRuntime({"disable_ipv6_origin": True, "prefer_ipv4": True})

        with self.assertRaisesRegex(RuntimeError, "ipv6 origin disabled"):
            await runtime.open_origin_connection("2001:db8::1", 443)

    async def test_handle_dns_query_responds_on_priority_lane(self):
        fake_transport = _FakeTransport()
        with mock.patch.object(agent, "create_transport", return_value=fake_transport):
            runtime = agent.AgentRuntime({"prefer_ipv4": True})

        with mock.patch.object(agent, "resolve_dns_via_upstreams", return_value=("1.1.1.1", b"\x12\x34\x81\x80")):
            await runtime._handle_dns_query(19, "8.8.8.8", b"\x12\x34\x01\x00")

        self.assertEqual(len(fake_transport.sent_frames), 1)
        lane, frame = fake_transport.sent_frames[0]
        self.assertEqual(lane, LANE_PRI)
        self.assertEqual(frame.type_id, FRAME_DNS_RESPONSE)
        self.assertEqual(frame.stream_id, 19)
        self.assertEqual(frame.payload, b"\x12\x34\x81\x80")


if __name__ == "__main__":
    unittest.main()
