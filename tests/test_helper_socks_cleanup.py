import struct
import unittest
from unittest import mock

from local_client import helper


class _FakeReader:
    def __init__(self, chunks, read_result=b""):
        self._chunks = list(chunks)
        self._read_result = read_result

    async def readexactly(self, count):
        chunk = self._chunks.pop(0)
        if len(chunk) != count:
            raise AssertionError("expected %s bytes, got %s" % (count, len(chunk)))
        return chunk

    async def read(self, _count=-1):
        return self._read_result


class _FakeWriter:
    def __init__(self):
        self.buffer = bytearray()
        self.closed = False

    def get_extra_info(self, _name):
        return ("127.0.0.1", 50000)

    def write(self, data):
        self.buffer.extend(data)

    async def drain(self):
        return None

    def is_closing(self):
        return self.closed

    def close(self):
        self.closed = True

    async def wait_closed(self):
        self.closed = True


class _FakeUdpAssociation:
    def __init__(self):
        self.closed = False

    def sockname(self):
        return ("127.0.0.1", 20000)

    def close(self):
        self.closed = True


class HelperSocksCleanupTests(unittest.IsolatedAsyncioTestCase):
    async def test_udp_associate_closes_control_socket_and_association(self):
        runtime = object()
        writer = _FakeWriter()
        association = _FakeUdpAssociation()
        reader = _FakeReader(
            [
                b"\x05\x01",
                b"\x00",
                b"\x05\x03\x00\x03",
                b"\x07",
                b"example" + struct.pack("!H", 53),
            ],
            read_result=b"",
        )

        with mock.patch.object(helper.SocksUdpAssociation, "create", return_value=association):
            await helper.handle_socks(runtime, reader, writer)

        self.assertTrue(association.closed)
        self.assertTrue(writer.closed)
        self.assertEqual(writer.buffer[:2], b"\x05\x00")

    async def test_rejected_auth_method_closes_writer(self):
        runtime = object()
        writer = _FakeWriter()
        reader = _FakeReader([b"\x05\x01", b"\x02"])

        await helper.handle_socks(runtime, reader, writer)

        self.assertTrue(writer.closed)
        self.assertEqual(writer.buffer, b"\x05\xff")


if __name__ == "__main__":
    unittest.main()
