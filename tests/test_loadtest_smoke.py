"""CI-safe local harness smoke. Uses SQLite and one tablet. No Cloud Run."""

import tempfile
import time
import unittest
from pathlib import Path


class LoadtestSmokeTest(unittest.TestCase):
    def test_one_tablet_receives_hello_ack(self):
        from loadtest.harness import config_for, run_config

        config = config_for("smoke")
        self.assertEqual("sqlite", config.backend)
        self.assertFalse(config.instrument)
        with tempfile.TemporaryDirectory(prefix="r2c-load-smoke-") as directory:
            started = time.monotonic()
            result = run_config(config, Path(directory))
            elapsed = time.monotonic() - started
        self.assertLess(elapsed, 90, "smoke wall clock should stay well under two minutes")
        self.assertGreaterEqual(result["hello_acks"], 1)
        self.assertGreaterEqual(result["hello_ack"]["count"], 1)
        self.assertIsNotNone(result["hello_ack"]["p50_ms"])
