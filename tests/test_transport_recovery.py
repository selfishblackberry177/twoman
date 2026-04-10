import asyncio
import contextlib
import unittest

import twoman_transport
from twoman_crypto import TransportCipher
from twoman_protocol import FRAME_PING, Frame, LANE_CTL, encode_frame


class _FakeStreamResponse:
    def __init__(self, chunks, delays=None):
        self.status_code = 200
        self.headers = {"content-type": "image/webp"}
        self._chunks = list(chunks)
        self._delays = list(delays or [])

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def raise_for_status(self):
        return None

    async def aiter_bytes(self):
        for index, chunk in enumerate(self._chunks):
            if index < len(self._delays):
                await asyncio.sleep(self._delays[index])
            yield chunk
        while True:
            await asyncio.sleep(10)


class _FakeClient:
    def __init__(self, responses):
        self._responses = list(responses)
        self.stream_calls = 0

    def stream(self, method, url, headers=None):
        del method, url, headers
        self.stream_calls += 1
        if self._responses:
            return self._responses.pop(0)
        return _FakeStreamResponse([])

    async def aclose(self):
        return None


def _encrypted_ping(token, offset):
    payload = encode_frame(Frame(FRAME_PING, offset=offset))
    iv = bytes(range(16))
    cipher = TransportCipher(token.encode("utf-8"), iv)
    return iv + cipher.process(payload)


async def _async_noop(frame, lane):
    del frame, lane
    return None


async def _wait_for(predicate):
    while not predicate():
        await asyncio.sleep(0.01)


class TransportRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_down_loop_times_out_silent_stream_and_retries(self):
        transport = twoman_transport.LaneTransport(
            base_url="https://example.invalid/base",
            token="test-token",
            role="agent",
            peer_id="agent-test",
            on_frame=_async_noop,
            collapse_data_lanes=True,
            idle_repoll_delay_seconds={"ctl": 0.0, "data": 0.0},
            protocol_config={"down_read_timeout_seconds": 0.05, "down_stream_max_session_seconds": 60.0},
        )
        events = []
        first = _FakeStreamResponse([_encrypted_ping("test-token", 1)], delays=[0.0])
        second = _FakeStreamResponse([_encrypted_ping("test-token", 2)], delays=[0.0])
        fake_client = _FakeClient([first, second])
        transport.clients[(LANE_CTL, "down")] = fake_client
        transport._backoff_after_error = lambda direction, lane: 0.0

        def report(kind, **fields):
            event = {"kind": kind, **fields}
            events.append(event)
            if kind == "transport_down_error":
                transport.stop_event.set()

        transport._report_event = report

        task = asyncio.create_task(transport._down_loop(LANE_CTL))
        await asyncio.wait_for(
            _wait_for(lambda: any(event.get("kind") == "transport_down_error" for event in events)),
            timeout=1.0,
        )
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

        self.assertGreaterEqual(fake_client.stream_calls, 1)
        self.assertTrue(any(event.get("kind") == "transport_down_error" for event in events))
        self.assertIn("down stream chunk timeout", repr(events[-1].get("error", "")))

    async def test_down_loop_rotates_long_lived_streams(self):
        transport = twoman_transport.LaneTransport(
            base_url="https://example.invalid/base",
            token="test-token",
            role="agent",
            peer_id="agent-test",
            on_frame=_async_noop,
            collapse_data_lanes=True,
            idle_repoll_delay_seconds={"ctl": 0.0, "data": 0.0},
            protocol_config={"down_read_timeout_seconds": 0.2, "down_stream_max_session_seconds": 0.005},
        )
        events = []
        rotating = _FakeStreamResponse(
            [
                _encrypted_ping("test-token", 1),
                _encrypted_ping("test-token", 2),
            ],
            delays=[0.0, 0.01],
        )
        replacement = _FakeStreamResponse([_encrypted_ping("test-token", 4)], delays=[0.0])
        fake_client = _FakeClient([rotating, replacement])
        transport.clients[(LANE_CTL, "down")] = fake_client

        def report(kind, **fields):
            event = {"kind": kind, **fields}
            events.append(event)
            if kind == "transport_down_rotate":
                transport.stop_event.set()

        transport._report_event = report
        task = asyncio.create_task(transport._down_loop(LANE_CTL))
        await asyncio.wait_for(task, timeout=1.0)

        self.assertTrue(any(event.get("kind") == "transport_down_rotate" for event in events))
        self.assertGreaterEqual(fake_client.stream_calls, 1)


if __name__ == "__main__":
    unittest.main()
