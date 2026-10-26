import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import server
from session import Metrics, list_sessions


class SessionLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.data_dir = root / "trainer_sessions"
        self.config_file = root / "trainer_config.json"
        self.workouts_file = root / "workouts.json"
        patcher = mock.patch.multiple(
            server,
            DATA_DIR=self.data_dir,
            CONFIG_FILE=self.config_file,
            WORKOUTS_FILE=self.workouts_file,
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.temp.cleanup)
        self.manager = server.Manager()
        self.workout_id = self.manager.workouts[0]["id"]

    def _start_and_fill(self, samples: int = 4) -> None:
        self.manager.start_workout(self.workout_id)
        for _ in range(samples):
            self.manager.recorder._last_monotonic -= 2.0
            self.manager._record_sample(
                Metrics(power_w=150.0, speed_kmh=30.0, cadence_rpm=85.0)
            )

    def test_pause_freezes_recording_and_resume_resets_the_clock(self):
        self._start_and_fill(samples=3)
        elapsed_before = self.manager.recorder.elapsed_s
        samples_before = len(self.manager.recorder.samples)

        self.manager.pause_workout()
        self.assertTrue(self.manager.session_paused)
        self.manager._record_sample(
            Metrics(power_w=200.0, speed_kmh=31.0, cadence_rpm=90.0)
        )
        self.assertEqual(len(self.manager.recorder.samples), samples_before)
        self.assertAlmostEqual(self.manager.recorder.elapsed_s, elapsed_before)

        # Simulate a long pause, then check that resume does not count it.
        self.manager.recorder._last_monotonic = time.monotonic() - 30.0
        self.manager.resume_workout()
        self.assertFalse(self.manager.session_paused)
        self.assertGreater(
            self.manager.recorder._last_monotonic, time.monotonic() - 0.5
        )

        self.manager.recorder._last_monotonic -= 1.0
        self.manager._record_sample(
            Metrics(power_w=200.0, speed_kmh=31.0, cadence_rpm=90.0)
        )
        self.assertGreater(self.manager.recorder.elapsed_s, elapsed_before)
        self.assertLess(self.manager.recorder.elapsed_s, elapsed_before + 2.0)

    def test_conclude_saves_the_session_and_does_not_start_another(self):
        self._start_and_fill(samples=4)
        active_id = self.manager.recorder.session_id

        state = self.manager.stop_workout()

        summary = state["stopped_session"]
        self.assertIsNotNone(summary)
        self.assertEqual(summary["id"], active_id)
        self.assertFalse(state["session_active"])
        self.assertFalse(state["session_paused"])
        self.assertIsNone(state["session_id"])
        self.assertEqual(self.manager.recorder.samples, [])
        self.assertFalse(self.manager.recorder.finalized_path)
        self.assertEqual(len(list_sessions(self.data_dir)), 1)
        for suffix in (".csv", ".json", ".tcx"):
            self.assertTrue((self.data_dir / f"{active_id}{suffix}").exists())

    def test_starting_a_workout_replaces_an_open_session(self):
        self.manager.start_session()
        first_id = self.manager.recorder.session_id
        self.manager.recorder._last_monotonic -= 2.0
        self.manager._record_sample(
            Metrics(power_w=120.0, speed_kmh=25.0, cadence_rpm=80.0)
        )

        state = self.manager.start_workout(self.workout_id)

        self.assertTrue(state["session_active"])
        self.assertNotEqual(state["session_id"], first_id)
        self.assertEqual(len(self.manager.recorder.samples), 0)
        self.assertEqual(self.manager.recorder.workout_laps is not None, True)

    def test_reset_closes_the_current_session_without_opening_a_new_one(self):
        self._start_and_fill(samples=4)
        summary = self.manager.reset_session()
        self.assertIsNotNone(summary)
        self.assertFalse(self.manager.session_active)
        self.assertIsNone(self.manager.snapshot()["session_id"])


if __name__ == "__main__":
    unittest.main()
