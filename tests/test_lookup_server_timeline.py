"""Contracts for the opt-in MP server diagnostic wrapper."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
from types import ModuleType, SimpleNamespace
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from lookup_server_timeline import install_timeline


class LookupServerTimelineTests(unittest.TestCase):
    def make_module(self, completed):
        class LookupModule:
            def __init__(self):
                self._prefetch_job_lock = threading.Lock()
                self._prefetch_jobs = {"request": object()}
                session = SimpleNamespace(lookup_ipc_key=SimpleNamespace(request_id="request"))
                self._ctx = SimpleNamespace(
                    session_manager=SimpleNamespace(get=lambda request_id: session))
                self.calls = []

            def lookup(self, key):
                self.calls.append("lookup")

            def _register_prefetch_job(self, job):
                self.calls.append("register")

            def query_prefetch_status(self, request_id):
                self.calls.append("query")
                if completed:
                    self._prefetch_jobs.pop(request_id)
                    return 17
                return None

            def free_lookup_locks(self, key, tp_size):
                self.calls.append("free")

            def end_session(self, request_id):
                self.calls.append("end")

        LookupModule.end_session.request_type = "END_SESSION"
        LookupModule.end_session.handler_type = "BLOCKING"
        module = ModuleType("lmcache.v1.multiprocess.modules.lookup")
        module.LookupModule = LookupModule
        return module

    def run_wrapper(self, completed):
        module = self.make_module(completed)
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.dict(sys.modules, {module.__name__: module}), mock.patch.dict(
                os.environ, {"CACHEPILOT_LOOKUP_SERVER_RELEASE_CANCELLED": "1"}
            ):
                install_timeline(directory)
                instance = module.LookupModule()
                instance.end_session("request")
            rows = [json.loads(line) for line in
                    next(Path(directory).glob("server-*.jsonl")).read_text().splitlines()]
        return module.LookupModule, instance, rows

    def test_completed_job_is_consumed_before_end_session(self):
        module, instance, rows = self.run_wrapper(completed=True)
        self.assertEqual(instance.calls, ["query", "free", "end"])
        self.assertFalse(instance._prefetch_jobs)
        self.assertEqual(module.end_session.request_type, "END_SESSION")
        self.assertEqual(module.end_session.handler_type, "BLOCKING")
        self.assertEqual(module.end_session.__name__, "end_session")
        self.assertEqual([row["hit_chunks"] for row in rows
                          if row["event"] == "diagnostic_release"], [17])

    def test_pending_job_is_only_recorded(self):
        _, instance, rows = self.run_wrapper(completed=False)
        self.assertEqual(instance.calls, ["query", "end"])
        self.assertIn("request", instance._prefetch_jobs)
        self.assertIn("diagnostic_release_pending", [row["event"] for row in rows])

    def test_release_flag_requires_server_trace(self):
        script = Path(__file__).resolve().parents[1] / "scripts/remote_lookup_cancel_smoke.py"
        result = subprocess.run(
            [sys.executable, str(script), "--output", "unused", "--release-cancelled"],
            capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 2)
        self.assertIn("--release-cancelled requires --trace-server", result.stderr)


if __name__ == "__main__":
    unittest.main()
