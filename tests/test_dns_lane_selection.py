import unittest

from hidden_server.agent import RemoteStream
from local_client.helper import ProxyStream
from twoman_protocol import LANE_BULK, LANE_PRI


class DummyAgent:
    pass


class DummyHelper:
    pass


class DnsLaneSelectionTests(unittest.TestCase):
    def test_helper_uses_priority_lane_for_small_payloads(self):
        stream = ProxyStream(DummyHelper(), 1, "1.1.1.1", 53)
        self.assertEqual(stream._data_lane(40), LANE_PRI)

    def test_helper_keeps_normal_tcp_payloads_on_data_lanes(self):
        stream = ProxyStream(DummyHelper(), 1, "example.com", 443)
        self.assertEqual(stream._data_lane(40), LANE_PRI)
        stream.send_offset = 128 * 1024
        self.assertEqual(stream._data_lane(40), LANE_BULK)

    def test_agent_uses_priority_lane_for_small_payloads(self):
        stream = RemoteStream(DummyAgent(), 1)
        stream.target_host = "8.8.8.8"
        stream.target_port = 53
        self.assertEqual(stream._data_lane(64), LANE_PRI)


if __name__ == "__main__":
    unittest.main()
