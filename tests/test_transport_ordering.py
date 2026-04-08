import unittest

from twoman_protocol import FRAME_DATA, Frame, LANE_DATA
from twoman_transport import LaneTransport


class TransportOrderingTests(unittest.IsolatedAsyncioTestCase):
    async def test_collapsed_data_overflow_requeue_stays_at_front(self):
        transport = LaneTransport(
            base_url="https://example.invalid/base",
            token="test-token",
            role="helper",
            peer_id="helper-test",
            on_frame=lambda frame, lane: None,
            collapse_data_lanes=True,
            idle_repoll_delay_seconds={"ctl": 0.05, "data": 0.1},
            protocol_config={},
        )
        first = Frame(FRAME_DATA, stream_id=1, offset=0, payload=b"first")
        overflow = Frame(FRAME_DATA, stream_id=1, offset=5, payload=b"overflow")
        later = Frame(FRAME_DATA, stream_id=1, offset=13, payload=b"later")

        await transport.send_frame("pri", first)
        await transport.send_frame("pri", overflow)
        await transport.send_frame("pri", later)

        dequeued = await transport._next_outbound_frame(LANE_DATA)
        self.assertEqual(dequeued.offset, first.offset)

        overflow_frame = await transport._next_outbound_frame(LANE_DATA)
        self.assertEqual(overflow_frame.offset, overflow.offset)
        await transport._requeue_frame(LANE_DATA, overflow_frame)

        replayed = await transport._next_outbound_frame(LANE_DATA)
        self.assertEqual(replayed.offset, overflow.offset)

        after = await transport._next_outbound_frame(LANE_DATA)
        self.assertEqual(after.offset, later.offset)


if __name__ == "__main__":
    unittest.main()
