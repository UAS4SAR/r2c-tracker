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

    def test_each_hot_tablet_has_its_own_zone_id(self):
        from loadtest.harness import _orgs, _specs

        organizations = _orgs(2, 1, 16, "idle")
        tokens = {
            (org["designator"], name): "token"
            for org in organizations
            for name in org["tablet_names"]
        }
        specs = [spec for spec, _upload in _specs(organizations, tokens, 4, 4, False)]
        hot = [spec for spec in specs if spec.role == "hot"]
        self.assertEqual(16, len(hot))
        self.assertEqual(16, len({spec.zone_id for spec in hot}))
        zone = hot[:4]
        self.assertEqual(1, len({round(spec.lat, 2) for spec in zone}))
        for spec in zone:
            self.assertEqual(
                {other.remote_id for other in zone if other is not spec},
                set(spec.peer_remote_ids),
            )
            self.assertGreater(abs(spec.lat - hot[4].lat), 0.01)
