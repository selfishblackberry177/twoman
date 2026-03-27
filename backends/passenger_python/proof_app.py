#!/usr/bin/env python3

import json
import os
import time


def json_response(start_response, payload, status="200 OK"):
    body = json.dumps(payload).encode("utf-8")
    start_response(status, [("Content-Type", "application/json"), ("Content-Length", str(len(body)))])
    return [body]


def application(environ, start_response):
    path = environ.get("PATH_INFO", "/") or "/"
    if path == "/health":
        return json_response(
            start_response,
            {
                "ok": True,
                "pid": os.getpid(),
                "time": time.time(),
                "env": {
                    "LSAPI_CHILDREN": os.environ.get("LSAPI_CHILDREN"),
                    "LSAPI_AVOID_FORK": os.environ.get("LSAPI_AVOID_FORK"),
                    "PROOF_ENV": os.environ.get("PROOF_ENV"),
                },
            },
        )
    if path == "/pid":
        return json_response(start_response, {"pid": os.getpid()})
    if path == "/stream":
        start_response("200 OK", [("Content-Type", "text/plain")])

        def generate():
            for index in range(60):
                yield ("tick=%d pid=%d ts=%f\n" % (index, os.getpid(), time.time())).encode("utf-8")
                time.sleep(0.2)

        return generate()
    if path == "/upload_probe":
        length = int(environ.get("CONTENT_LENGTH", "0") or "0")
        body = environ["wsgi.input"].read(length) if length > 0 else b""
        return json_response(start_response, {"ok": True, "pid": os.getpid(), "time": time.time(), "bytes": len(body)})
    return json_response(start_response, {"error": "not found", "path": path}, status="404 Not Found")
