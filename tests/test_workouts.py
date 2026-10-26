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

    def test_default_library_has_notes_for_every_workout(self):
        library = default_workouts()
        self.assertTrue(library)
        self.assertTrue(all(item["notes"].strip() for item in library))


if __name__ == "__main__":
    unittest.main()
