#!/usr/bin/env python3

import json
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _body(self) -> bytes:
        length = int(self.headers.get("Content-Length", "0") or "0")
        return self.rfile.read(length) if length > 0 else b""

    def _respond(self) -> None:
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.path == "/blob":
            params = urllib.parse.parse_qs(parsed.query)
            size = int(params.get("bytes", ["1048576"])[0])
            size = max(0, min(size, 32 * 1024 * 1024))
            body = (b"twoman-benchmark-" * ((size // 17) + 1))[:size]
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        payload = {
            "method": self.command,
            "path": self.path,
            "headers": {key: value for key, value in self.headers.items()},
            "body": self._body().decode("utf-8", errors="replace"),
        }
        body = json.dumps(payload, sort_keys=True).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        self._respond()

    def do_POST(self) -> None:
        self._respond()

    def log_message(self, format: str, *args) -> None:
        return


def main() -> None:
    server = HTTPServer(("127.0.0.1", 19090), Handler)
    server.serve_forever()


if __name__ == "__main__":
    main()
