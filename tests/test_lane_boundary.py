import types
import unittest

from twoman_protocol import LANE_BULK, LANE_PRI

from hidden_server.agent import PRI_LIMIT as AGENT_PRI_LIMIT
from hidden_server.agent import RemoteStream
from local_client.helper import PRI_LIMIT as HELPER_PRI_LIMIT
from local_client.helper import ProxyStream


class LaneBoundaryTests(unittest.TestCase):
    def test_helper_keeps_small_chunks_on_pri_before_threshold(self):
        stream = types.SimpleNamespace(send_offset=HELPER_PRI_LIMIT - 128)
        lane = ProxyStream._data_lane(stream, 128)
        self.assertEqual(lane, LANE_PRI)

    def test_helper_moves_crossing_chunk_to_bulk(self):
        stream = types.SimpleNamespace(send_offset=HELPER_PRI_LIMIT - 128)
        lane = ProxyStream._data_lane(stream, 256)
        self.assertEqual(lane, LANE_BULK)

    def test_agent_moves_crossing_chunk_to_bulk(self):
        stream = types.SimpleNamespace(send_offset=AGENT_PRI_LIMIT - 64)
        lane = RemoteStream._data_lane(stream, 128)
        self.assertEqual(lane, LANE_BULK)


if __name__ == "__main__":
    unittest.main()
