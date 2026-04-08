from __future__ import annotations

import argparse
import asyncio
import base64
import contextlib
import ipaddress
import json
import logging
from logging.handlers import RotatingFileHandler
import os
import socket
import struct
import sys
from typing import Iterable


LOGGER = logging.getLogger("twoman.desktop.gateway")
HTTP_HEADER_LIMIT = 64 * 1024


def configure_logging(log_path: str) -> None:
    if LOGGER.handlers:
        return
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    LOGGER.setLevel(logging.INFO)
    LOGGER.propagate = False
    file_handler = RotatingFileHandler(log_path, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    LOGGER.addHandler(file_handler)
    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setLevel(logging.WARNING)
    console_handler.setFormatter(logging.Formatter("[gateway] %(levelname)s %(message)s"))
    LOGGER.addHandler(console_handler)


def _encode_socks_address(host: str) -> tuple[int, bytes]:
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        encoded = host.encode("idna")
        if len(encoded) > 255:
            raise ValueError("domain name too long for socks")
        return 3, bytes([len(encoded)]) + encoded
    if isinstance(address, ipaddress.IPv4Address):
        return 1, address.packed
    return 4, address.packed


async def _relay_streams(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while True:
            chunk = await reader.read(65536)
            if not chunk:
                break
            writer.write(chunk)
            await writer.drain()
    finally:
        with contextlib.suppress(Exception):
            writer.close()
            await writer.wait_closed()


class AuthenticatedSocksGateway:
    """Authenticated SOCKS5 or HTTP proxy listener that forwards into a local Twoman proxy."""

    def __init__(
        self,
        protocol: str,
        listen_host: str,
        listen_port: int,
        username: str,
        password: str,
        target_host: str,
        target_port: int,
    ) -> None:
        self.protocol = protocol.strip().lower()
        self.listen_host = listen_host
        self.listen_port = listen_port
        self.username = username
        self.password = password
        self.target_host = target_host
        self.target_port = target_port
        self.server: asyncio.base_events.Server | None = None

    async def start(self) -> None:
        self.server = await asyncio.start_server(self.handle_client, self.listen_host, self.listen_port)
        LOGGER.info(
            "gateway started protocol=%s listen=%s:%s upstream=%s:%s",
            self.protocol,
            self.listen_host,
            self.listen_port,
            self.target_host,
            self.target_port,
        )

    async def serve(self) -> None:
        if self.server is None:
            await self.start()
        assert self.server is not None
        async with self.server:
            await self.server.serve_forever()

    async def stop(self) -> None:
        if self.server is None:
            return
        self.server.close()
        await self.server.wait_closed()
        LOGGER.info("gateway stopped listen=%s:%s", self.listen_host, self.listen_port)

    async def handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        if self.protocol == "http":
            await self.handle_http_client(reader, writer)
            return
        await self.handle_socks_client(reader, writer)

    async def handle_socks_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername")
        try:
            version, method_count = await reader.readexactly(2)
            if version != 5:
                raise RuntimeError("expected socks5")
            methods = await reader.readexactly(method_count)
            if 2 not in methods:
                writer.write(b"\x05\xff")
                await writer.drain()
                return
            writer.write(b"\x05\x02")
            await writer.drain()

            auth_version = (await reader.readexactly(1))[0]
            if auth_version != 1:
                raise RuntimeError("unsupported auth version")
            username_length = (await reader.readexactly(1))[0]
            username = (await reader.readexactly(username_length)).decode("utf-8")
            password_length = (await reader.readexactly(1))[0]
            password = (await reader.readexactly(password_length)).decode("utf-8")
            if username != self.username or password != self.password:
                writer.write(b"\x01\x01")
                await writer.drain()
                LOGGER.warning("gateway auth failed peer=%s user=%s", peer, username)
                return
            writer.write(b"\x01\x00")
            await writer.drain()

            request_header = await reader.readexactly(4)
            version, command, _reserved, address_type = request_header
            if version != 5 or command != 1:
                await self._reply_error(writer, 7)
                return
            address_bytes, host = await self._read_address(reader, address_type)
            port_bytes = await reader.readexactly(2)
            port = struct.unpack("!H", port_bytes)[0]

            upstream_reader, upstream_writer = await asyncio.open_connection(self.target_host, self.target_port)
            upstream_writer.write(b"\x05\x01\x00")
            await upstream_writer.drain()
            upstream_method_reply = await upstream_reader.readexactly(2)
            if upstream_method_reply != b"\x05\x00":
                raise RuntimeError("upstream socks rejected no-auth method")

            upstream_writer.write(b"\x05\x01\x00" + bytes([address_type]) + address_bytes + port_bytes)
            await upstream_writer.drain()
            upstream_reply = await upstream_reader.readexactly(4)
            reply_version, reply_code, _reply_reserved, reply_atyp = upstream_reply
            if reply_version != 5:
                raise RuntimeError("invalid upstream socks reply")
            reply_address, _ = await self._read_address(upstream_reader, reply_atyp)
            reply_port = await upstream_reader.readexactly(2)
            writer.write(upstream_reply + reply_address + reply_port)
            await writer.drain()
            if reply_code != 0:
                LOGGER.warning("gateway connect failed peer=%s target=%s:%s code=%s", peer, host, port, reply_code)
                return

            LOGGER.info("gateway connect ok peer=%s target=%s:%s", peer, host, port)
            await asyncio.gather(
                _relay_streams(reader, upstream_writer),
                _relay_streams(upstream_reader, writer),
            )
        except asyncio.IncompleteReadError:
            LOGGER.warning("gateway client disconnected early peer=%s", peer)
        except Exception as error:
            LOGGER.warning("gateway request failed peer=%s error=%s", peer, error)
            with contextlib.suppress(Exception):
                await self._reply_error(writer, 1)
        finally:
            with contextlib.suppress(Exception):
                writer.close()
                await writer.wait_closed()

    async def handle_http_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername")
        upstream_writer = None
        try:
            request_line, headers, rest = await self._read_http_request(reader)
            username, password = self._read_http_proxy_auth(headers)
            if username != self.username or password != self.password:
                LOGGER.warning("gateway http auth failed peer=%s user=%s", peer, username)
                await self._write_http_error(
                    writer,
                    407,
                    "Proxy Authentication Required",
                    [
                        ("Proxy-Authenticate", 'Basic realm="Twoman"'),
                        ("Content-Length", "0"),
                    ],
                )
                return

            upstream_reader, upstream_writer = await asyncio.open_connection(self.target_host, self.target_port)
            upstream_writer.write(self._build_upstream_http_request(request_line, headers, rest))
            await upstream_writer.drain()
            LOGGER.info("gateway http ok peer=%s request=%s", peer, request_line)
            await asyncio.gather(
                _relay_streams(reader, upstream_writer),
                _relay_streams(upstream_reader, writer),
            )
        except asyncio.IncompleteReadError:
            LOGGER.warning("gateway client disconnected early peer=%s", peer)
        except RuntimeError as error:
            LOGGER.warning("gateway request failed peer=%s error=%s", peer, error)
            with contextlib.suppress(Exception):
                await self._write_http_error(
                    writer,
                    407 if "proxy auth" in str(error).lower() else 400,
                    "Proxy Authentication Required" if "proxy auth" in str(error).lower() else "Bad Request",
                    [
                        ("Proxy-Authenticate", 'Basic realm="Twoman"'),
                        ("Content-Length", "0"),
                    ]
                    if "proxy auth" in str(error).lower()
                    else [("Content-Length", "0")],
                )
        except Exception as error:
            LOGGER.warning("gateway request failed peer=%s error=%s", peer, error)
            with contextlib.suppress(Exception):
                await self._write_http_error(
                    writer,
                    502,
                    "Bad Gateway",
                    [("Content-Length", "0")],
                )
        finally:
            if upstream_writer is not None:
                with contextlib.suppress(Exception):
                    upstream_writer.close()
                    await upstream_writer.wait_closed()
            with contextlib.suppress(Exception):
                writer.close()
                await writer.wait_closed()

    async def _reply_error(self, writer: asyncio.StreamWriter, error_code: int) -> None:
        writer.write(b"\x05" + bytes([error_code]) + b"\x00\x01" + socket.inet_aton("0.0.0.0") + struct.pack("!H", 0))
        await writer.drain()

    async def _write_http_error(
        self,
        writer: asyncio.StreamWriter,
        status_code: int,
        reason: str,
        headers: list[tuple[str, str]],
    ) -> None:
        lines = [f"HTTP/1.1 {status_code} {reason}\r\n"]
        for name, value in headers:
            lines.append(f"{name}: {value}\r\n")
        lines.append("Connection: close\r\n\r\n")
        writer.write("".join(lines).encode("iso-8859-1"))
        await writer.drain()

    async def _read_http_request(
        self,
        reader: asyncio.StreamReader,
    ) -> tuple[str, list[tuple[str, str]], bytes]:
        payload = bytearray()
        while b"\r\n\r\n" not in payload:
            chunk = await reader.read(4096)
            if not chunk:
                raise RuntimeError("client closed before sending request headers")
            payload.extend(chunk)
            if len(payload) > HTTP_HEADER_LIMIT:
                raise RuntimeError("http request headers too large")
        header_block, _, rest = bytes(payload).partition(b"\r\n\r\n")
        lines = header_block.decode("iso-8859-1").split("\r\n")
        if not lines or not lines[0]:
            raise RuntimeError("missing http request line")
        headers: list[tuple[str, str]] = []
        for line in lines[1:]:
            if not line:
                continue
            if ":" not in line:
                raise RuntimeError("invalid http header")
            name, value = line.split(":", 1)
            headers.append((name.strip(), value.strip()))
        return lines[0], headers, rest

    def _read_http_proxy_auth(self, headers: list[tuple[str, str]]) -> tuple[str, str]:
        auth_value = ""
        for name, value in headers:
            if name.lower() == "proxy-authorization":
                auth_value = value
                break
        if not auth_value:
            raise RuntimeError("missing proxy authorization")
        scheme, _, encoded = auth_value.partition(" ")
        if scheme.lower() != "basic" or not encoded.strip():
            raise RuntimeError("unsupported proxy auth scheme")
        try:
            decoded = base64.b64decode(encoded.strip(), validate=True).decode("utf-8")
        except Exception as error:
            raise RuntimeError("invalid proxy auth encoding") from error
        username, separator, password = decoded.partition(":")
        if not separator:
            raise RuntimeError("invalid proxy auth payload")
        return username, password

    def _build_upstream_http_request(
        self,
        request_line: str,
        headers: list[tuple[str, str]],
        rest: bytes,
    ) -> bytes:
        filtered_headers = []
        for name, value in headers:
            if name.lower() in {"proxy-authorization"}:
                continue
            filtered_headers.append(f"{name}: {value}\r\n")
        payload = request_line + "\r\n" + "".join(filtered_headers) + "\r\n"
        return payload.encode("iso-8859-1") + rest

    async def _read_address(
        self,
        reader: asyncio.StreamReader,
        address_type: int,
    ) -> tuple[bytes, str]:
        if address_type == 1:
            packed = await reader.readexactly(4)
            return packed, socket.inet_ntoa(packed)
        if address_type == 3:
            length = (await reader.readexactly(1))[0]
            name = await reader.readexactly(length)
            return bytes([length]) + name, name.decode("utf-8")
        if address_type == 4:
            packed = await reader.readexactly(16)
            return packed, str(ipaddress.IPv6Address(packed))
        raise RuntimeError("unsupported address type")


def run_gateway_from_config(config_path: str) -> None:
    with open(config_path, "r", encoding="utf-8") as handle:
        config = json.load(handle)
    configure_logging(str(config["log_path"]))
    gateway = AuthenticatedSocksGateway(
        protocol=str(config.get("protocol", "socks")),
        listen_host=str(config["listen_host"]),
        listen_port=int(config["listen_port"]),
        username=str(config["username"]),
        password=str(config["password"]),
        target_host=str(config["target_host"]),
        target_port=int(config["target_port"]),
    )

    async def runner() -> None:
        await gateway.serve()

    try:
        asyncio.run(runner())
    except KeyboardInterrupt:
        LOGGER.info("gateway interrupted")


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run an authenticated Twoman SOCKS share")
    parser.add_argument("--config", required=True)
    args = parser.parse_args(list(argv) if argv is not None else None)
    run_gateway_from_config(args.config)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
