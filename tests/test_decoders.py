"""Decoder regression tests built from real Qubo frames.

Run with:
    .venv/bin/python -m unittest discover -s tests -v
"""

from __future__ import annotations

import struct
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ble_client import (  # noqa: E402
    RevolutionTracker,
    decode_cps,
    decode_csc,
    decode_ftms,
)


class FtmsTests(unittest.TestCase):
    def test_idle_frame_from_real_device(self) -> None:
        # Captured at 21:03:50, trainer awake but not pedalled.
        frame = bytes.fromhex("74 08 00 00 00 00 47 01 00 00 00 00 00 00 00")
        values = decode_ftms(frame)
        self.assertEqual(values["speed_kmh"], 0.0)
        self.assertEqual(values["cadence_rpm"], 0.0)
        self.assertEqual(values["distance_m"], 327.0)
        self.assertEqual(values["resistance_level"], 0.0)
        self.assertEqual(values["power_w"], 0.0)

    def test_elite_trailing_elapsed_time_does_not_shift_power(self) -> None:
        # 15-byte frame: the firmware sets the HR bit but appends a uint16
        # elapsed-seconds counter.  Power must stay at offset 11.
        frame = bytes.fromhex("74 08 00 00 00 00 00 13 00 00 00 00 00 0f 0c")
        values = decode_ftms(frame)
        self.assertEqual(values["distance_m"], 4864.0)
        self.assertEqual(values["power_w"], 0.0)
        self.assertEqual(values["elapsed_s"], 0x0C0F)
        self.assertNotIn("heart_rate_bpm", values)

    def test_active_frame_fields(self) -> None:
        frame = (
            struct.pack("<H", 0x0874)
            + struct.pack("<H", 2230)      # 22.30 km/h
            + struct.pack("<H", 132)       # 66.0 rpm
            + bytes([0xE8, 0x03, 0x00])    # 1000 m
            + struct.pack("<h", 50)        # resistance 5.0
            + struct.pack("<h", 150)       # 150 W
            + struct.pack("<H", 3600)      # elapsed 3600 s
        )
        values = decode_ftms(frame)
        self.assertEqual(values["speed_kmh"], 22.3)
        self.assertEqual(values["cadence_rpm"], 66.0)
        self.assertEqual(values["cadence_raw"], 132.0)
        self.assertEqual(values["distance_m"], 1000.0)
        self.assertEqual(values["resistance_level"], 5.0)
        self.assertEqual(values["power_w"], 150.0)
        self.assertEqual(values["elapsed_s"], 3600.0)

    def test_spec_compliant_heart_rate_is_one_byte(self) -> None:
        frame = (
            struct.pack("<H", 0x0844)      # speed + cadence + power + HR
            + struct.pack("<H", 2230)
            + struct.pack("<H", 132)
            + struct.pack("<h", 150)
            + bytes([100])
        )
        values = decode_ftms(frame)
        self.assertEqual(values["heart_rate_bpm"], 100.0)
        self.assertNotIn("elapsed_s", values)

    def test_truncated_frame_keeps_earlier_fields(self) -> None:
        frame = struct.pack("<H", 0x0874) + struct.pack("<H", 2230)
        values = decode_ftms(frame)
        self.assertEqual(values["speed_kmh"], 22.3)
        self.assertNotIn("power_w", values)


class CpsTests(unittest.TestCase):
    def test_power_is_read_from_offset_two(self) -> None:
        frame = (
            struct.pack("<H", 0x0030)
            + struct.pack("<h", 200)
            + struct.pack("<I", 1000)
            + struct.pack("<H", 1000)
            + struct.pack("<H", 50)
            + struct.pack("<H", 500)
        )
        values = decode_cps(frame, RevolutionTracker(), RevolutionTracker(), 0.094)
        self.assertEqual(values["power_w"], 200.0)

    def test_short_frame_is_ignored(self) -> None:
        values = decode_cps(b"\x30\x00", RevolutionTracker(), RevolutionTracker(), 0.094)
        self.assertEqual(values, {})


class CscTests(unittest.TestCase):
    def test_speed_and_cadence_from_revolution_deltas(self) -> None:
        wheel = RevolutionTracker()
        crank = RevolutionTracker()
        first = bytes([0x03]) + struct.pack("<I", 0) + struct.pack("<H", 0)
        first += struct.pack("<H", 0) + struct.pack("<H", 0)
        second = bytes([0x03]) + struct.pack("<I", 1) + struct.pack("<H", 1024)
        second += struct.pack("<H", 2) + struct.pack("<H", 1024)

        self.assertEqual(decode_csc(first, wheel, crank, 2.096), {})
        values = decode_csc(second, wheel, crank, 2.096)
        self.assertAlmostEqual(values["speed_kmh"], 7.5456, places=3)
        self.assertAlmostEqual(values["cadence_rpm"], 120.0, places=3)


if __name__ == "__main__":
    unittest.main()
