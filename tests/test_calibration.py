import tempfile
import time
import unittest
from pathlib import Path

from server import Manager
from session import Metrics, SessionRecorder, apply_calibration


class CalibrationTests(unittest.TestCase):
    def test_zero_stays_zero(self):
        self.assertEqual(apply_calibration(0.0, 30.0), 0.0)
        self.assertEqual(apply_calibration(-1.0, 30.0), 0.0)

    def test_positive_value_gets_offset(self):
        self.assertAlmostEqual(apply_calibration(90.0, 30.0), 120.0)
        self.assertAlmostEqual(apply_calibration(25.0, 2.5), 27.5)

    def test_negative_offset_does_not_go_below_zero(self):
        self.assertEqual(apply_calibration(2.0, -10.0), 0.0)

    def test_maximum_clamps_corrected_value(self):
        self.assertAlmostEqual(apply_calibration(1190.0, 30.0, 1200.0), 1200.0)

    def test_offset_ramps_in_at_low_values(self):
        self.assertAlmostEqual(
            apply_calibration(2.0, 30.0, full_at=20.0), 5.0
        )
        self.assertAlmostEqual(
            apply_calibration(10.0, 30.0, full_at=20.0), 25.0
        )
        self.assertAlmostEqual(
            apply_calibration(4.0, 2.5, full_at=10.0), 5.0
        )


class EffectiveMetricsTests(unittest.TestCase):
    def _manager(self, now: float) -> Manager:
        manager = Manager.__new__(Manager)
        manager.config = {"power_offset_w": 30.0, "speed_offset_kmh": 2.5}
        manager.latest = Metrics(power_w=90.0, speed_kmh=25.0, cadence_rpm=80.0)
        manager.field_at = {
            "power_w": now,
            "speed_kmh": now,
            "cadence_rpm": now,
        }
        return manager

    def test_offsets_reach_live_metrics(self):
        metrics = self._manager(time.monotonic()).effective_metrics()
        self.assertAlmostEqual(metrics.power_w, 120.0)
        self.assertAlmostEqual(metrics.speed_kmh, 27.5)
        self.assertAlmostEqual(metrics.cadence_rpm, 80.0)

    def test_stale_values_do_not_get_offset(self):
        metrics = self._manager(time.monotonic() - 60.0).effective_metrics()
        self.assertEqual(metrics.power_w, 0.0)
        self.assertEqual(metrics.speed_kmh, 0.0)
        self.assertEqual(metrics.cadence_rpm, 0.0)

    def test_coast_down_values_do_not_get_full_offset(self):
        manager = self._manager(time.monotonic())
        manager.latest = Metrics(power_w=2.0, speed_kmh=4.0, cadence_rpm=0.0)
        metrics = manager.effective_metrics()
        self.assertAlmostEqual(metrics.power_w, 5.0)
        self.assertAlmostEqual(metrics.speed_kmh, 5.0)

    def test_recorder_stores_calibrated_values(self):
        manager = self._manager(time.monotonic())
        metrics = manager.effective_metrics()
        with tempfile.TemporaryDirectory() as directory:
            recorder = SessionRecorder(
                Path(directory),
                calibration=manager.calibration(),
            )
            recorder._last_monotonic = time.monotonic() - 1.0
            recorder.add(metrics)
        self.assertAlmostEqual(recorder.samples[-1]["power_w"], 120.0)
        self.assertAlmostEqual(recorder.samples[-1]["speed_kmh"], 27.5)
        self.assertEqual(
            recorder.calibration,
            {"power_offset_w": 30.0, "speed_offset_kmh": 2.5},
        )

    def test_stats_include_calories_and_watts_per_kilo(self):
        with tempfile.TemporaryDirectory() as directory:
            recorder = SessionRecorder(Path(directory), weight_kg=82.0)
            recorder.elapsed_s = 3600.0
            recorder._power_integral = 200.0 * 3600.0
            recorder.max_power_w = 300.0
            stats = recorder.stats()
        self.assertAlmostEqual(stats["calories_kcal"], 720.0)
        self.assertAlmostEqual(stats["avg_wkg"], 2.44)
        self.assertAlmostEqual(stats["max_wkg"], 3.66)


if __name__ == "__main__":
    unittest.main()
