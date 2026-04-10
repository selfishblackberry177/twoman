import importlib.util
import sys
import unittest
from pathlib import Path


ANDROID_PYTHON_DIR = Path("/home/shahab/dev/hobby/mintm/android-client/app/src/main/python")


def load_android_transport_module():
    module_name = "android_twoman_transport_test"
    spec = importlib.util.spec_from_file_location(module_name, ANDROID_PYTHON_DIR / "twoman_transport.py")
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(ANDROID_PYTHON_DIR))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module


class AndroidTransportOrderingTests(unittest.IsolatedAsyncioTestCase):
    async def test_collapsed_data_overflow_requeue_stays_at_front(self):
        android_transport = load_android_transport_module()
        transport = android_transport.LaneTransport(
            base_url="https://example.invalid/base",
            token="test-token",
            role="helper",
            peer_id="helper-test",
            on_frame=lambda frame, lane: None,
            collapse_data_lanes=True,
            idle_repoll_delay_seconds={"ctl": 0.05, "data": 0.1},
            protocol_config={},
        )
        first = android_transport.Frame(android_transport.FRAME_DATA, stream_id=1, offset=0, payload=b"first")
        overflow = android_transport.Frame(android_transport.FRAME_DATA, stream_id=1, offset=5, payload=b"overflow")
        later = android_transport.Frame(android_transport.FRAME_DATA, stream_id=1, offset=13, payload=b"later")

        await transport.send_frame("pri", first)
        await transport.send_frame("pri", overflow)
        await transport.send_frame("pri", later)

        dequeued = await transport._next_outbound_frame(android_transport.LANE_DATA)
        self.assertEqual(dequeued.offset, first.offset)

        overflow_frame = await transport._next_outbound_frame(android_transport.LANE_DATA)
        self.assertEqual(overflow_frame.offset, overflow.offset)
        await transport._requeue_frame(android_transport.LANE_DATA, overflow_frame)

        replayed = await transport._next_outbound_frame(android_transport.LANE_DATA)
        self.assertEqual(replayed.offset, overflow.offset)

        after = await transport._next_outbound_frame(android_transport.LANE_DATA)
        self.assertEqual(after.offset, later.offset)


if __name__ == "__main__":
    unittest.main()
