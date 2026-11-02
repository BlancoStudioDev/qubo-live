import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

import server
from session import Metrics
from sleep import SleepRecorder, list_sleep_sessions, load_sleep_session


class SleepRecorderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data_dir = Path(self.temp.name) / "sleep_sessions"

    def _fill(self, recorder: SleepRecorder, seconds: int = 3600) -> None:
        for second in range(1, seconds + 1):
            recorder.add(
                Metrics(heart_rate_bpm=52.0 + (second % 7)),
                elapsed_s=float(second),
            )

    def test_night_is_saved_with_stats_series_and_csv(self):
        recorder = SleepRecorder(
            self.data_dir,
            started_at=datetime(2026, 11, 3, 23, 0, 0),
        )
        self._fill(recorder, seconds=8 * 3600)
        summary = recorder.finalize(quality=4, notes="cena leggera")

        self.assertIsNotNone(summary)
        self.assertEqual(summary["samples"], 8 * 3600)
        self.assertEqual(summary["duration_s"], 8 * 3600)
        self.assertEqual(summary["quality"], 4)
        self.assertEqual(summary["notes"], "cena leggera")
        self.assertGreaterEqual(summary["score"], 60)
        self.assertTrue(summary["tips"])
        self.assertTrue((self.data_dir / f"{summary['id']}.json").is_file())
        self.assertTrue((self.data_dir / f"{summary['id']}.csv").is_file())

        loaded = load_sleep_session(self.data_dir, str(summary["id"]))
        self.assertIn("series", loaded)
        self.assertGreater(len(loaded["series"]), 0)

    def test_short_session_is_not_saved(self):
        recorder = SleepRecorder(
            self.data_dir,
            started_at=datetime(2026, 11, 3, 23, 0, 0),
        )
        self._fill(recorder, seconds=20)
        self.assertIsNone(recorder.finalize())
        self.assertEqual(list_sleep_sessions(self.data_dir), [])

    def test_list_is_sorted_and_omits_series(self):
        first = SleepRecorder(
            self.data_dir,
            started_at=datetime(2026, 11, 2, 23, 0, 0),
        )
        self._fill(first, seconds=600)
        first.finalize()
        second = SleepRecorder(
            self.data_dir,
            started_at=datetime(2026, 11, 3, 23, 0, 0),
        )
        self._fill(second, seconds=600)
        second.finalize()

        sessions = list_sleep_sessions(self.data_dir)
        self.assertEqual(len(sessions), 2)
        self.assertGreater(sessions[0]["started_at"], sessions[1]["started_at"])
        self.assertNotIn("series", sessions[0])


class SleepManagerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.sleep_dir = root / "sleep_sessions"
        self.data_dir = root / "trainer_sessions"
        self.config_file = root / "trainer_config.json"
        self.workouts_file = root / "workouts.json"
        patcher = mock.patch.multiple(
            server,
            DATA_DIR=self.data_dir,
            SLEEP_DATA_DIR=self.sleep_dir,
            SIM_SLEEP_DATA_DIR=root / "sim_sleep_sessions",
            CONFIG_FILE=self.config_file,
            WORKOUTS_FILE=self.workouts_file,
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.temp.cleanup)
        self.manager = server.Manager()
        # Avoid spawning caffeinate during the test and allow a fake strap.
        self.manager.simulate = True

    def test_sleep_lifecycle_saves_a_local_night(self):
        self.manager.hr_status = "connected"
        started = self.manager.start_sleep()
        self.assertTrue(started["active"])
        recorder = self.manager.sleep_recorder
        self.assertIsNotNone(recorder)
        for second in range(1, 3601):
            recorder.add(
                Metrics(heart_rate_bpm=50.0 + (second % 5)),
                elapsed_s=float(second),
            )

        stopped = self.manager.stop_sleep(quality=5, notes="test")
        summary = stopped["stopped_sleep"]
        self.assertIsNotNone(summary)
        self.assertFalse(stopped["active"])
        self.assertIsNone(self.manager.sleep_recorder)
        self.assertEqual(summary["samples"], 3600)
        self.assertTrue((self.sleep_dir / f"{summary['id']}.json").is_file())
        self.assertEqual(len(list_sleep_sessions(self.sleep_dir)), 1)

    def test_cannot_start_sleep_without_strap(self):
        self.manager.simulate = False
        self.manager.hr_status = "idle"
        with self.assertRaises(ValueError):
            self.manager.start_sleep()

    def test_starting_a_workout_closes_the_night(self):
        self.manager.hr_status = "connected"
        self.manager.start_sleep()
        recorder = self.manager.sleep_recorder
        for second in range(1, 301):
            recorder.add(
                Metrics(heart_rate_bpm=51.0),
                elapsed_s=float(second),
            )
        self.manager.start_workout(self.manager.workouts[0]["id"])

        self.assertFalse(self.manager.sleep_active)
        self.assertIsNone(self.manager.sleep_recorder)
        self.assertEqual(len(list_sleep_sessions(self.sleep_dir)), 1)

    def test_state_contains_current_series_and_last_summary(self):
        self.manager.hr_status = "connected"
        self.manager.start_sleep()
        recorder = self.manager.sleep_recorder
        for second in range(1, 301):
            recorder.add(
                Metrics(heart_rate_bpm=52.0),
                elapsed_s=float(second),
            )
        state = self.manager.sleep_snapshot()
        self.assertTrue(state["active"])
        self.assertIn("series", state["current"])
        self.assertGreater(len(state["current"]["series"]), 0)
        self.assertTrue(state["hr"]["connected"])


if __name__ == "__main__":
    unittest.main()
