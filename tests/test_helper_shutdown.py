import unittest
from unittest import mock

from twoman_protocol import FRAME_RST, LANE_CTL
import local_client.helper as helper


class _FakeTransport:
    def __init__(self):
        self.peer_session_id = "test-session"
        self.event_handler = None
        self.sent_frames = []
        self.stopped = False

    async def start(self):
        return None

    async def stop(self):
        self.stopped = True

    async def send_frame(self, lane, frame):
        self.sent_frames.append((lane, frame))


class HelperShutdownTests(unittest.IsolatedAsyncioTestCase):
    async def test_stop_resets_open_streams_before_transport_stop(self):
        fake_transport = _FakeTransport()
        with mock.patch.object(helper, "create_transport", return_value=fake_transport):
            runtime = helper.HelperRuntime({"shutdown_stream_reset_grace_seconds": 0.0})
        first = runtime.new_stream("example.com", 443)
        second = runtime.new_stream("www.google.com", 443)
        second.closed = True

        await runtime.stop()

        self.assertTrue(fake_transport.stopped)
        self.assertEqual(len(fake_transport.sent_frames), 1)
        lane, frame = fake_transport.sent_frames[0]
        self.assertEqual(lane, LANE_CTL)
        self.assertEqual(frame.type_id, FRAME_RST)
        self.assertEqual(frame.stream_id, first.stream_id)


if __name__ == "__main__":
    unittest.main()
