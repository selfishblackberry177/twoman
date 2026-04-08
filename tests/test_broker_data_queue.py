import unittest
from unittest import mock

from host.runtime.http_broker_daemon import AsyncBrokerServer, PeerState


class BrokerDataQueueTests(unittest.IsolatedAsyncioTestCase):
    async def test_next_data_payload_prefers_pri_and_restores_bulk_when_both_wake(self):
        server = AsyncBrokerServer(
            "127.0.0.1",
            0,
            {
                "client_tokens": ["client"],
                "agent_tokens": ["agent"],
                "lane_profiles": {
                    "pri": {"max_bytes": 4096, "max_frames": 8, "hold_ms": 0, "pad_min": 0},
                    "bulk": {"max_bytes": 4096, "max_frames": 8, "hold_ms": 0, "pad_min": 0},
                },
            },
        )
        peer = PeerState("helper", "peer", "session")
        original_wait = __import__("asyncio").wait

        async def fake_wait(tasks, timeout=None, return_when=None):
            await peer.queues["pri"].put(b"pri-frame")
            await peer.queues["bulk"].put(b"bulk-frame")
            await original_wait(tasks, timeout=timeout, return_when=return_when)
            pri_task, bulk_task = tasks
            self.assertTrue(pri_task.done())
            self.assertTrue(bulk_task.done())
            return {pri_task}, {bulk_task}

        with mock.patch("host.runtime.http_broker_daemon.asyncio.wait", side_effect=fake_wait):
            payload, lane, frames, _hold_ms, _pad_bytes = await server._next_data_payload(peer, wait_timeout_ms=10)

        self.assertEqual(lane, "pri")
        self.assertEqual(payload, b"pri-frame")
        self.assertEqual(frames, 1)

        queued_bulk = peer.queues["bulk"].get_nowait()
        self.assertEqual(queued_bulk, b"bulk-frame")


if __name__ == "__main__":
    unittest.main()
