import unittest
from unittest import mock

from hidden_server import agent


class _FakeTransport:
    def __init__(self):
        self.peer_session_id = "test-session"
        self.event_handler = None

    async def start(self):
        return None

    async def stop(self):
        return None


class HiddenAgentConnectTests(unittest.IsolatedAsyncioTestCase):
    async def test_ipv6_literal_fails_fast_when_disabled(self):
        with mock.patch.object(agent, "create_transport", return_value=_FakeTransport()):
            runtime = agent.AgentRuntime({"disable_ipv6_origin": True, "prefer_ipv4": True})

        with self.assertRaisesRegex(RuntimeError, "ipv6 origin disabled"):
            await runtime.open_origin_connection("2001:db8::1", 443)


if __name__ == "__main__":
    unittest.main()
