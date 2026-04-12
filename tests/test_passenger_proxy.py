import importlib.util
import pathlib
import tempfile
import types
import unittest
from unittest import mock


MODULE_PATH = pathlib.Path(__file__).resolve().parents[1] / "host" / "passenger_python" / "passenger_proxy.py"


def load_passenger_proxy():
    spec = importlib.util.spec_from_file_location("test_passenger_proxy_module", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class PassengerProxyTests(unittest.TestCase):
    def setUp(self):
        self.module = load_passenger_proxy()
        self.tempdir = tempfile.TemporaryDirectory()
        root = pathlib.Path(self.tempdir.name)
        self.module.RUNTIME_DIR = str(root)
        self.module.SOCKET_PATH = str(root / "broker.sock")
        self.module.PID_PATH = str(root / "broker.pid")
        self.module.LOCK_PATH = str(root / "broker.lock")
        pathlib.Path(self.module.SOCKET_PATH).touch()

    def tearDown(self):
        self.tempdir.cleanup()

    def test_failed_health_probe_does_not_restart_live_daemon(self):
        with mock.patch.object(self.module, "read_pid", return_value=123), \
             mock.patch.object(self.module, "process_is_alive", return_value=True), \
             mock.patch.object(self.module, "ping_daemon", return_value=False), \
             mock.patch.object(self.module, "stop_pid") as stop_pid, \
             mock.patch.object(self.module.subprocess, "Popen") as popen:
            self.module.ensure_daemon_running(perform_healthcheck=True)
        stop_pid.assert_not_called()
        popen.assert_not_called()

    def test_force_restart_restarts_live_daemon_after_connect_failure(self):
        process = types.SimpleNamespace(pid=456, poll=lambda: None)
        with mock.patch.object(self.module, "read_pid", return_value=123), \
             mock.patch.object(self.module, "process_is_alive", return_value=True), \
             mock.patch.object(self.module, "ping_daemon", return_value=True), \
             mock.patch.object(self.module, "stop_pid") as stop_pid, \
             mock.patch.object(self.module.subprocess, "Popen", return_value=process) as popen:
            self.module.ensure_daemon_running(perform_healthcheck=True, force_restart=True)
        stop_pid.assert_called_once_with(123)
        popen.assert_called_once()


if __name__ == "__main__":
    unittest.main()
