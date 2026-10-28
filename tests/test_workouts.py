import unittest

from workouts import MAX_NOTES, default_workouts, normalize_workout, workout_summary


def workout_payload(**overrides):
    payload = {
        "name": "VO2max test",
        "description": "Test",
        "notes": "OBIETTIVO: test.\nCADENZA: 95-105 rpm.",
        "intervals": [
            {"label": "Riscaldamento", "duration_s": 600, "power_w": 117},
            {"label": "VO2max", "duration_s": 180, "power_w": 200},
        ],
    }
    payload.update(overrides)
    return payload


class WorkoutNotesTests(unittest.TestCase):
    def test_notes_are_kept_and_trimmed(self):
        workout = normalize_workout(workout_payload(notes="  nota  "))
        self.assertEqual(workout["notes"], "nota")

    def test_notes_are_optional(self):
        workout = normalize_workout(workout_payload(notes=None))
        self.assertEqual(workout["notes"], "")

    def test_notes_too_long_are_rejected(self):
        with self.assertRaises(ValueError):
            normalize_workout(workout_payload(notes="x" * (MAX_NOTES + 1)))

    def test_summary_keeps_notes(self):
        workout = normalize_workout(workout_payload())
        self.assertEqual(workout_summary(workout)["notes"], workout["notes"])

    def test_cadence_range_is_kept(self):
        payload = workout_payload()
        payload["intervals"][1]["cadence_min"] = 65
        payload["intervals"][1]["cadence_max"] = 75
        workout = normalize_workout(payload)
        self.assertEqual(workout["intervals"][1]["cadence_min"], 65)
        self.assertEqual(workout["intervals"][1]["cadence_max"], 75)

    def test_cadence_is_filled_from_known_interval_labels(self):
        workout = normalize_workout(workout_payload())
        self.assertEqual(workout["intervals"][0]["cadence_min"], 85)
        self.assertEqual(workout["intervals"][0]["cadence_max"], 95)
        self.assertEqual(workout["intervals"][1]["cadence_min"], 95)
        self.assertEqual(workout["intervals"][1]["cadence_max"], 105)

    def test_invalid_cadence_range_is_rejected(self):
        payload = workout_payload()
        payload["intervals"][1]["cadence_min"] = 100
        payload["intervals"][1]["cadence_max"] = 80
        with self.assertRaises(ValueError):
            normalize_workout(payload)

    def test_default_library_has_notes_for_every_workout(self):
        library = default_workouts()
        self.assertTrue(library)
        self.assertTrue(all(item["notes"].strip() for item in library))
        self.assertTrue(
            all(
                "cadence_min" in interval and "cadence_max" in interval
                for workout in library
                for interval in workout["intervals"]
            )
        )

    def test_tcx_notes_say_laps_are_only_summaries(self):
        notes = {item["id"]: item["notes"] for item in default_workouts()}
        for identifier in ("vo2max-5x3", "vo2max-4x3", "soglia-3x8"):
            self.assertIn("flusso completo", notes[identifier])
        self.assertNotIn("su Strava vedi la media", notes["vo2max-4x3"])


if __name__ == "__main__":
    unittest.main()
