"""Session accumulation and CSV export."""

from __future__ import annotations

import csv
import io
import json
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass
class Metrics:
    power_w: float = 0.0
    speed_kmh: float = 0.0
    cadence_rpm: float = 0.0

    def as_dict(self) -> dict[str, float]:
        return {
            "power_w": round(self.power_w, 1),
            "speed_kmh": round(self.speed_kmh, 1),
            "cadence_rpm": round(self.cadence_rpm, 1),
        }


def apply_calibration(
    value: float,
    offset: float,
    maximum: float | None = None,
    full_at: float = 0.0,
) -> float:
    """Apply an additive calibration without inventing values at rest.

    A raw zero means the trainer is idle, so it must stay zero.  Positive
    values get the configured offset and are clamped to the plausibility
    limit of the field.  When ``full_at`` is set, the offset ramps in from
    zero so tiny coast-down values do not turn into phantom watts or km/h.
    """
    if value <= 0:
        return 0.0
    factor = 1.0
    if full_at > 0:
        factor = min(1.0, value / full_at)
    corrected = max(0.0, value + offset * factor)
    if maximum is not None:
        corrected = min(maximum, corrected)
    return corrected


def _as_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _tcx_time(value: object) -> str:
    if isinstance(value, (int, float)):
        moment = datetime.fromtimestamp(float(value))
    elif isinstance(value, datetime):
        moment = value
    else:
        moment = datetime.fromisoformat(str(value))
    if moment.tzinfo is not None:
        moment = moment.astimezone().replace(tzinfo=None)
    # Strava treats timestamps in GPS-less TCX files as local wall-clock
    # times.  Writing the session's local timestamps as-is keeps the clock
    # time the rider saw, instead of shifting it by the profile timezone.
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def build_tcx(
    samples: list[dict[str, object]],
    *,
    started_at: object | None = None,
    sport: str = "Biking",
    laps: list[dict[str, object]] | None = None,
) -> str:
    """Build a TCX with power, cadence, speed and distance for Strava.

    The complete sample stream is always written, from the first to the last
    recorded sample.  ``laps`` only groups those points into workout intervals
    so Strava can show per-interval summaries; it never replaces the stream.
    """
    if not samples:
        raise ValueError("nessun campione disponibile per il TCX")

    if started_at is None:
        started_at = samples[0].get("timestamp")
    start_text = _tcx_time(started_at)
    start_dt = datetime.fromisoformat(start_text.removesuffix("Z"))

    points: list[dict[str, object]] = []

    def add_point(
        moment: str,
        elapsed: float,
        distance_m: float,
        speed_mps: float,
        power_w: float,
        cadence_rpm: float,
    ) -> None:
        point = {
            "time": moment,
            "elapsed": max(0.0, elapsed),
            "distance_m": max(0.0, distance_m),
            "speed_mps": max(0.0, speed_mps),
            "power_w": max(0.0, power_w),
            "cadence_rpm": max(0.0, cadence_rpm),
        }
        # CSVs record the first sample in the same second as the session
        # start.  Keep one point per timestamp, preferring the later sample.
        if points and points[-1]["time"] == moment:
            points[-1] = point
        else:
            points.append(point)

    # Start at the exact session time with zero load, then replay every
    # recorded sample.  This makes the first and last timestamps line up with
    # the duration shown in the dashboard.
    add_point(start_text, 0.0, 0.0, 0.0, 0.0, 0.0)
    for sample in samples:
        elapsed = _as_float(sample.get("elapsed_s"))
        distance_m = _as_float(sample.get("distance_km")) * 1000.0
        speed_mps = _as_float(sample.get("speed_kmh")) / 3.6
        power_w = _as_float(sample.get("power_w"))
        cadence_rpm = _as_float(sample.get("cadence_rpm"))
        timestamp = sample.get("timestamp")
        if timestamp:
            moment = _tcx_time(timestamp)
        else:
            moment = _tcx_time(start_dt.timestamp() + elapsed)
        add_point(
            moment,
            elapsed,
            distance_m,
            speed_mps,
            power_w,
            cadence_rpm,
        )

    total_time = max(float(point["elapsed"]) for point in points)

    specs: list[dict[str, object]] = []
    for lap in laps or []:
        duration = _as_float(lap.get("duration_s"))
        if duration > 0:
            specs.append(
                {
                    "label": str(lap.get("label") or "Intervallo"),
                    "duration_s": duration,
                }
            )
    if not specs:
        specs = [{"label": "Sessione", "duration_s": total_time}]
    else:
        planned = sum(float(spec["duration_s"]) for spec in specs)
        if planned < total_time:
            specs.append({"label": "Extra", "duration_s": total_time - planned})

    # Split points into laps.  A point exactly on a boundary belongs to the
    # next lap.
    lap_points: list[list[dict[str, object]]] = [[] for _ in specs]
    lap_index = 0
    boundary = float(specs[0]["duration_s"])
    for point in points:
        while (
            lap_index < len(specs) - 1
            and float(point["elapsed"]) >= boundary
        ):
            lap_index += 1
            boundary += float(specs[lap_index]["duration_s"])
        lap_points[lap_index].append(point)

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<TrainingCenterDatabase xmlns="http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
        'xsi:schemaLocation="http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2 '
        'http://www.garmin.com/xmlschemas/TrainingCenterDatabasev2.xsd">',
        "  <Activities>",
        f'    <Activity Sport="{sport}">',
        f"      <Id>{start_text}</Id>",
    ]

    for spec, points_in_lap in zip(specs, lap_points):
        if not points_in_lap:
            continue
        lap_start = str(points_in_lap[0]["time"])
        lap_distance = max(
            0.0,
            float(points_in_lap[-1]["distance_m"])
            - float(points_in_lap[0]["distance_m"]),
        )
        lap_max_speed = max(float(point["speed_mps"]) for point in points_in_lap)
        lap_work = 0.0
        for previous, current_point in zip(points_in_lap, points_in_lap[1:]):
            delta = max(
                0.0,
                float(current_point["elapsed"]) - float(previous["elapsed"]),
            )
            lap_work += float(current_point["power_w"]) * delta
        lap_calories = max(0, int(round(lap_work / 1000.0)))
        lap_cadences = [
            float(point["cadence_rpm"])
            for point in points_in_lap
            if float(point["cadence_rpm"]) > 0
        ]
        lap_cadence = (
            round(sum(lap_cadences) / len(lap_cadences))
            if lap_cadences
            else 0
        )
        lines.extend(
            [
                f'      <Lap StartTime="{lap_start}">',
                f"        <TotalTimeSeconds>{float(spec['duration_s']):.1f}</TotalTimeSeconds>",
                f"        <DistanceMeters>{lap_distance:.1f}</DistanceMeters>",
                f"        <MaximumSpeed>{lap_max_speed:.4f}</MaximumSpeed>",
                f"        <Calories>{lap_calories}</Calories>",
                f"        <Cadence>{lap_cadence}</Cadence>",
                "        <Intensity>Active</Intensity>",
                "        <TriggerMethod>Manual</TriggerMethod>",
                "        <Track>",
            ]
        )
        for point in points_in_lap:
            cadence = max(0, min(255, int(round(float(point["cadence_rpm"])))))
            power = max(0, int(round(float(point["power_w"]))))
            lines.extend(
                [
                    "          <Trackpoint>",
                    f"            <Time>{point['time']}</Time>",
                    f"            <DistanceMeters>{float(point['distance_m']):.1f}</DistanceMeters>",
                    f"            <Cadence>{cadence}</Cadence>",
                    "            <Extensions>",
                    '              <TPX xmlns="http://www.garmin.com/xmlschemas/ActivityExtension/v2">',
                    f"                <Speed>{float(point['speed_mps']):.4f}</Speed>",
                    f"                <Watts>{power}</Watts>",
                    "              </TPX>",
                    "            </Extensions>",
                    "          </Trackpoint>",
                ]
            )
        lines.extend(["        </Track>", "      </Lap>"])

    lines.extend(
        [
            "    </Activity>",
            "  </Activities>",
            "</TrainingCenterDatabase>",
        ]
    )
    return "\n".join(lines)


def tcx_from_csv(path: Path) -> str:
    """Build a TCX from a saved session CSV."""
    with Path(path).open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise ValueError("CSV senza campioni")
    return build_tcx(rows, started_at=rows[0].get("timestamp"))


class SessionRecorder:
    """Integrates live metrics into distance and averages, and stores samples."""

    _reserved_ids: set[str] = set()

    def __init__(
        self,
        data_dir: Path,
        source: str = "trainer",
        calibration: dict[str, float] | None = None,
        weight_kg: float = 82.0,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.source = source
        self.calibration = dict(calibration or {})
        self.weight_kg = max(30.0, min(200.0, float(weight_kg)))
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.reset()

    def reset(self) -> None:
        self.started_at = time.time()
        base_id = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        session_id = base_id
        suffix = 2
        while session_id in SessionRecorder._reserved_ids or any(
            (self.data_dir / f"{session_id}{extension}").exists()
            for extension in (".csv", ".json", ".tcx")
        ):
            session_id = f"{base_id}-{suffix}"
            suffix += 1
        SessionRecorder._reserved_ids.add(session_id)
        self.session_id = session_id
        self.elapsed_s = 0.0
        self.distance_m = 0.0
        self.max_power_w = 0.0
        self.max_speed_kmh = 0.0
        self.max_cadence_rpm = 0.0
        self.samples: list[dict[str, object]] = []
        self.workout_laps: list[dict[str, object]] | None = None
        self.finalized_path: Path | None = None
        self._power_integral = 0.0
        self._speed_integral = 0.0
        self._cadence_integral = 0.0
        self._last_monotonic = time.monotonic()

    def add(self, metrics: Metrics) -> None:
        now = time.monotonic()
        dt = now - self._last_monotonic
        self._last_monotonic = now
        if dt <= 0:
            return
        # The tick runs at 1 Hz; clamp long gaps (sleep, debugger) so a pause
        # does not turn into kilometres.
        dt = min(dt, 2.0)

        self.elapsed_s += dt
        self.distance_m += metrics.speed_kmh / 3.6 * dt
        self._power_integral += metrics.power_w * dt
        self._speed_integral += metrics.speed_kmh * dt
        self._cadence_integral += metrics.cadence_rpm * dt

        self.max_power_w = max(self.max_power_w, metrics.power_w)
        self.max_speed_kmh = max(self.max_speed_kmh, metrics.speed_kmh)
        self.max_cadence_rpm = max(self.max_cadence_rpm, metrics.cadence_rpm)

        self.samples.append(
            {
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "elapsed_s": round(self.elapsed_s, 1),
                "power_w": round(metrics.power_w, 1),
                "speed_kmh": round(metrics.speed_kmh, 1),
                "cadence_rpm": round(metrics.cadence_rpm, 1),
                "distance_km": round(self.distance_m / 1000.0, 4),
            }
        )

    def resume_recording(self) -> None:
        """Restart the elapsed clock without counting a paused gap."""
        self._last_monotonic = time.monotonic()

    def stats(self) -> dict[str, float]:
        duration = self.elapsed_s
        avg_power = self._power_integral / duration if duration > 0 else 0.0
        avg_speed = self._speed_integral / duration if duration > 0 else 0.0
        avg_cadence = self._cadence_integral / duration if duration > 0 else 0.0
        avg_wkg = avg_power / self.weight_kg if self.weight_kg > 0 else 0.0
        max_wkg = self.max_power_w / self.weight_kg if self.weight_kg > 0 else 0.0
        # Mechanical work in kJ is a good approximation of kcal at ~24%
        # human efficiency.  The UI and the Strava note both use this value.
        calories_kcal = self._power_integral / 1000.0
        return {
            "duration_s": round(duration, 0),
            "distance_km": round(self.distance_m / 1000.0, 2),
            "avg_power_w": round(avg_power, 1),
            "max_power_w": round(self.max_power_w, 1),
            "avg_speed_kmh": round(avg_speed, 1),
            "max_speed_kmh": round(self.max_speed_kmh, 1),
            "avg_cadence_rpm": round(avg_cadence, 1),
            "max_cadence_rpm": round(self.max_cadence_rpm, 1),
            "weight_kg": round(self.weight_kg, 1),
            "calories_kcal": round(calories_kcal, 1),
            "avg_wkg": round(avg_wkg, 2),
            "max_wkg": round(max_wkg, 2),
            "samples": len(self.samples),
        }

    def to_tcx(self) -> str:
        return build_tcx(
            self.samples,
            started_at=self.started_at,
            laps=self.workout_laps,
        )

    def to_csv(self) -> str:
        buffer = io.StringIO()
        writer = csv.writer(buffer, lineterminator="\n")
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
        for sample in self.samples:
            writer.writerow(
                [
                    sample["timestamp"],
                    sample["elapsed_s"],
                    sample["power_w"],
                    sample["speed_kmh"],
                    sample["cadence_rpm"],
                    sample["distance_km"],
                ]
            )
        return buffer.getvalue()

    def finalize(self) -> dict[str, object] | None:
        """Write CSV + JSON summary. Short sessions are discarded."""
        if self.finalized_path is not None or len(self.samples) < 3:
            return None
        if self.elapsed_s < 5:
            return None
        # Do not keep sessions where nobody pedalled.
        if self.distance_m < 10 and self.max_power_w < 5:
            return None

        stats = self.stats()
        summary: dict[str, object] = {
            "id": self.session_id,
            "started_at": datetime.fromtimestamp(self.started_at).isoformat(
                timespec="seconds"
            ),
            "source": self.source,
            **stats,
        }
        if self.calibration:
            summary["calibration"] = self.calibration

        csv_path = self.data_dir / f"{self.session_id}.csv"
        json_path = self.data_dir / f"{self.session_id}.json"
        tcx_path = self.data_dir / f"{self.session_id}.tcx"
        csv_path.write_text(self.to_csv(), encoding="utf-8")
        json_path.write_text(
            json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        tcx_path.write_text(self.to_tcx(), encoding="utf-8")
        self.finalized_path = csv_path
        return summary


def list_sessions(data_dir: Path, limit: int = 20) -> list[dict[str, object]]:
    directory = Path(data_dir)
    if not directory.exists():
        return []
    sessions: list[dict[str, object]] = []
    for path in sorted(
        directory.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True
    )[:limit]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        data["csv"] = f"{path.stem}.csv"
        data["tcx"] = f"{path.stem}.tcx"
        sessions.append(data)
    return sessions
