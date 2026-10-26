import csv
import tempfile
import unittest
from pathlib import Path

from session import SessionRecorder, build_tcx, tcx_from_csv


class TcxTests(unittest.TestCase):
    def setUp(self):
        self.samples = [
            {
                "timestamp": "2026-10-23T17:56:39",
                "elapsed_s": 0.1,
                "power_w": 0.0,
                "speed_kmh": 0.0,
                "cadence_rpm": 0.0,
                "distance_km": 0.0,
            },
            {
                "timestamp": "2026-10-23T17:56:40",
                "elapsed_s": 1.1,
                "power_w": 100.0,
                "speed_kmh": 25.0,
                "cadence_rpm": 80.0,
                "distance_km": 0.007,
            },
        ]

    def test_build_tcx_contains_streams_and_local_time(self):
        tcx = build_tcx(self.samples, started_at="2026-10-23T17:56:39")
        self.assertIn("<Id>2026-10-23T17:56:39Z</Id>", tcx)
        self.assertIn("<TotalTimeSeconds>1.1</TotalTimeSeconds>", tcx)
        self.assertIn("<DistanceMeters>7.0</DistanceMeters>", tcx)
        self.assertIn("<Watts>100</Watts>", tcx)
        self.assertIn("<Cadence>80</Cadence>", tcx)
        self.assertEqual(tcx.count("<Trackpoint>"), 2)
        self.assertIn('Sport="Biking"', tcx)

    def test_empty_samples_are_rejected(self):
        with self.assertRaises(ValueError):
            build_tcx([])

    def test_tcx_from_csv(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "session.csv"
            with path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle)
                writer.writerow(
                    [
                        "timestamp",
                        "elapsed_s",
                        "power_w",
                        "speed_kmh",
                        "cadence_rpm",
                        "distance_km",
                    ]
                )
                writer.writerow(
                    ["2026-10-23T17:56:39", "0.1", "0.0", "0.0", "0.0", "0.0"]
                )
                writer.writerow(
                    ["2026-10-23T17:56:40", "1.1", "100.0", "25.0", "80.0", "0.007"]
                )
            tcx = tcx_from_csv(path)
        self.assertIn("<Id>2026-10-23T17:56:39Z</Id>", tcx)
        self.assertIn("<Watts>100</Watts>", tcx)
        self.assertEqual(tcx.count("<Trackpoint>"), 2)

    def test_workout_laps_split_the_test_interval(self):
        tcx = build_tcx(
            self.samples,
            started_at="2026-10-23T17:56:39",
            laps=[
                {"label": "Riscaldamento", "duration_s": 1.0},
                {"label": "Test FTP", "duration_s": 1.0},
            ],
        )
        self.assertEqual(tcx.count("<Lap "), 2)
        self.assertEqual(tcx.count("<Trackpoint>"), 2)
        self.assertEqual(tcx.count("<TotalTimeSeconds>1.0</TotalTimeSeconds>"), 2)

    def test_finalize_writes_tcx(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            recorder = SessionRecorder(path)
            recorder.elapsed_s = 10.0
            recorder.distance_m = 100.0
            recorder.max_power_w = 100.0
            recorder.max_speed_kmh = 25.0
            recorder.max_cadence_rpm = 80.0
            recorder.samples = [
                {
                    "timestamp": "2026-10-23T17:56:39",
                    "elapsed_s": 1.0,
                    "power_w": 50.0,
                    "speed_kmh": 20.0,
                    "cadence_rpm": 70.0,
                    "distance_km": 0.01,
                },
                {
                    "timestamp": "2026-10-23T17:56:40",
                    "elapsed_s": 2.0,
                    "power_w": 75.0,
                    "speed_kmh": 22.0,
                    "cadence_rpm": 75.0,
                    "distance_km": 0.02,
                },
                {
                    "timestamp": "2026-10-23T17:56:41",
                    "elapsed_s": 3.0,
                    "power_w": 100.0,
                    "speed_kmh": 25.0,
                    "cadence_rpm": 80.0,
                    "distance_km": 0.03,
                },
            ]
            summary = recorder.finalize()
            tcx_path = path / f"{recorder.session_id}.tcx"
            self.assertIsNotNone(summary)
            self.assertTrue(tcx_path.is_file())
            self.assertIn("<Watts>100</Watts>", tcx_path.read_text())


if __name__ == "__main__":
    unittest.main()
