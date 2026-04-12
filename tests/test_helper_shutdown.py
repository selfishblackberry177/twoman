import unittest
from unittest import mock
import asyncio
import time

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


class _FakeWriter:
    def __init__(self):
        self.closed = False
        self.wait_closed_calls = 0
        self.writes = []
        self.eof_written = False

    def close(self):
        self.closed = True

    async def wait_closed(self):
        self.wait_closed_calls += 1
        self.closed = True

    def is_closing(self):
        return self.closed

    def write(self, _payload):
        self.writes.append(_payload)
        return None

    def write_eof(self):
        self.eof_written = True

    async def drain(self):
        return None


class _SlowWriter(_FakeWriter):
    async def wait_closed(self):
        self.wait_closed_calls += 1
        await asyncio.sleep(10)


class _BlockingReader:
    async def read(self, _size):
        await asyncio.sleep(10)


class _SequenceReader:
    def __init__(self, chunks):
        self.chunks = list(chunks)

    async def read(self, _size):
        await asyncio.sleep(0)
        if self.chunks:
            return self.chunks.pop(0)
        await asyncio.sleep(10)


class _FakeRuntime:
    def __init__(self):
        self.released_stream_ids = []

    async def release_stream(self, stream_id):
        self.released_stream_ids.append(stream_id)


class _FakeRelayStream:
    def __init__(self, stream_id=7):
        self.stream_id = stream_id
        self.recv_queue = asyncio.Queue()
        self.open_failed = ""
        self.closed = False
        self.local_write_bytes = 0
        self.local_write_count = 0
        self.recv_offset = 0
        self.send_offset = 0
        self.finish_calls = 0
        self.reset_reasons = []
        self.send_payloads = []
        self.send_closed = False

    async def open(self):
        return None

    async def send_data(self, payload):
        self.send_payloads.append(payload)
        self.send_offset += len(payload)

    async def finish(self):
        if self.send_closed or self.closed:
            return
        self.finish_calls += 1
        self.send_closed = True

    async def reset(self, reason):
        self.reset_reasons.append(reason)
        self.closed = True

    async def grant_window(self, _amount):
        return None


async def _stubborn_task():
    try:
        await asyncio.sleep(10)
    except asyncio.CancelledError:
        await asyncio.sleep(10)


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

    async def test_stop_closes_active_connection_writers(self):
        fake_transport = _FakeTransport()
        with mock.patch.object(helper, "create_transport", return_value=fake_transport):
            runtime = helper.HelperRuntime({"shutdown_stream_reset_grace_seconds": 0.0})
        writer = _FakeWriter()
        runtime.register_connection(None, writer)

        await runtime.stop()

        self.assertTrue(fake_transport.stopped)
        self.assertTrue(writer.closed)
        self.assertEqual(writer.wait_closed_calls, 1)

    async def test_stop_waits_for_live_connections_concurrently(self):
        fake_transport = _FakeTransport()
        with mock.patch.object(helper, "create_transport", return_value=fake_transport):
            runtime = helper.HelperRuntime({"shutdown_stream_reset_grace_seconds": 0.0})
        stubborn_tasks = [asyncio.create_task(_stubborn_task()) for _ in range(3)]
        for task in stubborn_tasks:
            runtime.register_connection(task, None)
        for _ in range(3):
            runtime.register_connection(None, _SlowWriter())

        started = time.monotonic()
        await runtime.stop()
        elapsed = time.monotonic() - started

        self.assertTrue(fake_transport.stopped)
        self.assertLess(elapsed, 2.5)

    async def test_cancelled_relay_resets_stream_instead_of_finishing(self):
        runtime = _FakeRuntime()
        stream = _FakeRelayStream()
        reader = _BlockingReader()
        writer = _FakeWriter()

        relay_task = asyncio.create_task(helper.relay_stream(runtime, stream, reader, writer))
        await asyncio.sleep(0.05)
        relay_task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await relay_task

        self.assertEqual(stream.finish_calls, 0)
        self.assertEqual(stream.reset_reasons, ["relay cancelled"])
        self.assertEqual(runtime.released_stream_ids, [stream.stream_id])
        self.assertTrue(writer.closed)

    async def test_local_half_close_keeps_remote_drain_alive(self):
        runtime = _FakeRuntime()
        stream = _FakeRelayStream()
        reader = _SequenceReader([b"client-hello", b""])
        writer = _FakeWriter()

        relay_task = asyncio.create_task(
            helper.relay_stream(runtime, stream, reader, writer, open_stream=False)
        )
        await asyncio.sleep(0.05)
        await stream.recv_queue.put(b"server-hello")
        await stream.recv_queue.put(None)
        await relay_task

        self.assertEqual(stream.send_payloads, [b"client-hello"])
        self.assertEqual(stream.finish_calls, 1)
        self.assertTrue(writer.eof_written)
        self.assertIn(b"server-hello", writer.writes)
        self.assertEqual(runtime.released_stream_ids, [stream.stream_id])


if __name__ == "__main__":
    unittest.main()
