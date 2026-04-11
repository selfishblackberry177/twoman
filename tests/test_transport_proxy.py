import importlib.util
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from twoman_transport import LaneTransport


REPO_ROOT = Path(__file__).resolve().parents[1]
ANDROID_PYTHON_DIR = REPO_ROOT / "android-client" / "app" / "src" / "main" / "python"


def load_android_transport_module():
    module_name = "android_twoman_transport_proxy_test"
    spec = importlib.util.spec_from_file_location(module_name, ANDROID_PYTHON_DIR / "twoman_transport.py")
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(ANDROID_PYTHON_DIR))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module


class TransportProxyTests(unittest.TestCase):
    def test_root_transport_build_client_uses_proxy_url(self) -> None:
        captured = {}

        def fake_async_client(**kwargs):
            captured.update(kwargs)
            return object()

        with patch("twoman_transport.httpx.AsyncClient", side_effect=fake_async_client):
            transport = LaneTransport(
                base_url="https://host.example.com/darvazeh",
                token="test-token",
                role="agent",
                peer_id="agent-main",
                on_frame=lambda frame, lane: None,
                upstream_proxy_url="socks5://127.0.0.1:1280",
                idle_repoll_delay_seconds={"ctl": 0.05, "data": 0.1},
                protocol_config={},
            )
            transport._build_client("ctl", "up")

        self.assertEqual(captured.get("proxy"), "socks5://127.0.0.1:1280")

    def test_android_transport_build_client_uses_proxy_url(self) -> None:
        android_transport = load_android_transport_module()
        captured = {}

        def fake_async_client(**kwargs):
            captured.update(kwargs)
            return object()

        with patch.object(android_transport.httpx, "AsyncClient", side_effect=fake_async_client):
            transport = android_transport.LaneTransport(
                base_url="https://host.example.com/darvazeh",
                token="test-token",
                role="helper",
                peer_id="helper-main",
                on_frame=lambda frame, lane: None,
                upstream_proxy_url="socks5://127.0.0.1:1280",
                idle_repoll_delay_seconds={"ctl": 0.05, "data": 0.1},
                protocol_config={},
            )
            transport._build_client("ctl", "down")

        self.assertEqual(captured.get("proxy"), "socks5://127.0.0.1:1280")


if __name__ == "__main__":
    unittest.main()
