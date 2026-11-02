"""Sleep tracking and local analysis from heart-rate samples.

The data lives in ``data/sleep_sessions`` and is intentionally separate from
the Strava/trainer sessions.  No TCX file is generated and nothing is uploaded
to Strava: this is a local sleep diary built from the heart-rate strap.
"""

from __future__ import annotations

import csv
import json
import statistics
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from session import Metrics


def _bedtime_minutes(started_at: object) -> float:
    moment = datetime.fromisoformat(str(started_at))
    return ((moment.hour * 60 + moment.minute) - 18 * 60) % (24 * 60)


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


class SleepRecorder:
    """Collect one night of heart-rate samples and save a local summary."""

    _reserved_ids: set[str] = set()

    def __init__(
        self,
        data_dir: Path,
        *,
        started_at: datetime | None = None,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.started_at = started_at or datetime.now().replace(microsecond=0)
        self.start_epoch = self.started_at.timestamp()
        self.session_id = self._unique_id()
        self.samples: list[dict[str, object]] = []
        self.finalized_path: Path | None = None
        self._last_elapsed = 0.0

    def _unique_id(self) -> str:
        base = self.started_at.strftime("%Y-%m-%d_%H-%M-%S")
        candidate = base
        suffix = 2
        while candidate in self._reserved_ids or any(
            (self.data_dir / f"{candidate}{extension}").exists()
            for extension in (".json", ".csv")
        ):
            candidate = f"{base}-{suffix}"
            suffix += 1
        self._reserved_ids.add(candidate)
        return candidate

    @property
    def elapsed_s(self) -> float:
        wall = time.time() - self.start_epoch
        return round(max(self._last_elapsed, wall), 1)

    def add(
        self,
        metrics: Metrics,
        *,
        elapsed_s: float | None = None,
        timestamp: str | None = None,
    ) -> None:
        """Store one HR sample.  Invalid/zero readings are gaps, not samples."""
        heart_rate = float(metrics.heart_rate_bpm)
        if heart_rate <= 0:
            return
        elapsed = (
            time.time() - self.start_epoch
            if elapsed_s is None
            else float(elapsed_s)
        )
        if elapsed <= self._last_elapsed:
            return
        self._last_elapsed = elapsed
        moment = timestamp or datetime.fromtimestamp(
            self.start_epoch + elapsed
        ).isoformat(timespec="seconds")
        self.samples.append(
            {
                "timestamp": moment,
                "elapsed_s": round(elapsed, 1),
                "heart_rate_bpm": round(heart_rate, 1),
            }
        )

    def stats(self) -> dict[str, float]:
        duration = max(self.elapsed_s, 0.0)
        values = [float(item["heart_rate_bpm"]) for item in self.samples]
        if not values:
            return {
                "duration_s": duration,
                "samples": 0,
                "avg_heart_rate_bpm": 0.0,
                "min_heart_rate_bpm": 0.0,
                "max_heart_rate_bpm": 0.0,
                "resting_heart_rate_bpm": 0.0,
                "hr_std_bpm": 0.0,
                "coverage_pct": 0.0,
            }
        ordered = sorted(values)
        low_count = max(1, int(round(len(ordered) * 0.1)))
        avg = _mean(values)
        return {
            "duration_s": duration,
            "samples": len(values),
            "avg_heart_rate_bpm": round(avg, 1),
            "min_heart_rate_bpm": round(min(values), 1),
            "max_heart_rate_bpm": round(max(values), 1),
            "resting_heart_rate_bpm": round(_mean(ordered[:low_count]), 1),
            "hr_std_bpm": (
                round(statistics.pstdev(values), 1) if len(values) > 1 else 0.0
            ),
            "coverage_pct": round(
                min(100.0, len(values) / max(1.0, duration) * 100.0), 1
            ),
        }

    def series(self, step_s: int = 30) -> list[dict[str, object]]:
        """Downsample the night for a readable chart (default one point/30s)."""
        if not self.samples:
            return []
        buckets: dict[int, list[float]] = {}
        for item in self.samples:
            bucket = int(float(item["elapsed_s"]) // step_s)
            buckets.setdefault(bucket, []).append(float(item["heart_rate_bpm"]))
        return [
            {"t": bucket * step_s, "hr": round(_mean(values), 1)}
            for bucket, values in sorted(buckets.items())
        ]

    def summary(self, *, include_series: bool = False) -> dict[str, object]:
        data = {
            "id": self.session_id,
            "started_at": self.started_at.isoformat(timespec="seconds"),
            "ended_at": datetime.fromtimestamp(
                self.start_epoch + self.elapsed_s
            ).isoformat(timespec="seconds"),
            "source": "hr",
            **self.stats(),
        }
        if include_series:
            data["series"] = self.series()
        return data

    def _baseline(self) -> dict[str, float] | None:
        previous = [
            item
            for item in list_sleep_sessions(self.data_dir, limit=8)
            if item.get("id") != self.session_id
        ][:7]
        previous = [item for item in previous if float(item.get("resting_heart_rate_bpm") or 0) > 0]
        if not previous:
            return None
        return {
            "resting_heart_rate_bpm": round(
                _mean([float(item["resting_heart_rate_bpm"]) for item in previous]),
                1,
            ),
            "bedtime_minutes": round(
                _mean([_bedtime_minutes(item["started_at"]) for item in previous]), 1
            ),
            "nights": len(previous),
        }

    def _score(
        self, stats: dict[str, float], baseline: dict[str, float] | None, bedtime: float
    ) -> tuple[int, str]:
        duration_h = float(stats["duration_s"]) / 3600.0
        if 7.0 <= duration_h <= 9.0:
            duration_score = 40
        elif 6.0 <= duration_h < 7.0 or 9.0 < duration_h <= 10.0:
            duration_score = 28
        elif 5.0 <= duration_h < 6.0 or 10.0 < duration_h <= 11.0:
            duration_score = 15
        else:
            duration_score = 5

        resting = float(stats["resting_heart_rate_bpm"])
        if baseline and baseline["resting_heart_rate_bpm"] > 0:
            delta = resting - float(baseline["resting_heart_rate_bpm"])
            if delta <= -3:
                resting_score = 25
            elif delta <= 3:
                resting_score = 20
            elif delta <= 8:
                resting_score = 12
            else:
                resting_score = 5
        else:
            resting_score = 18

        std = float(stats["hr_std_bpm"])
        if std <= 4:
            stability_score = 20
        elif std <= 8:
            stability_score = 15
        elif std <= 12:
            stability_score = 8
        else:
            stability_score = 3

        if baseline:
            diff = abs(bedtime - float(baseline["bedtime_minutes"]))
            if diff <= 30:
                consistency_score = 10
            elif diff <= 60:
                consistency_score = 7
            elif diff <= 90:
                consistency_score = 4
            else:
                consistency_score = 2
        else:
            consistency_score = 7

        coverage = float(stats["coverage_pct"])
        coverage_score = 5 if coverage >= 90 else 3 if coverage >= 70 else 1
        total = (
            duration_score
            + resting_score
            + stability_score
            + consistency_score
            + coverage_score
        )
        if total >= 85:
            label = "ottimo"
        elif total >= 70:
            label = "buono"
        elif total >= 55:
            label = "discreto"
        else:
            label = "da migliorare"
        return total, label

    def _tips(
        self,
        stats: dict[str, float],
        baseline: dict[str, float] | None,
        bedtime: float,
        quality: int | None,
        score: int,
    ) -> list[str]:
        tips: list[str] = []
        duration_h = float(stats["duration_s"]) / 3600.0
        if duration_h < 7:
            tips.append("Durata sotto 7 ore: prova a spostare l'addormentamento di 20-30 minuti prima.")
        elif duration_h > 10:
            tips.append("Hai dormito più di 10 ore: verifica se il sonno è frammentato o se stai recuperando debito.")
        if baseline:
            diff = abs(bedtime - float(baseline["bedtime_minutes"]))
            if diff > 60:
                tips.append("Orario irregolare: mantieni una finestra sonno stabile anche nel weekend.")
            delta_hr = float(stats["resting_heart_rate_bpm"]) - float(
                baseline["resting_heart_rate_bpm"]
            )
            if delta_hr > 5:
                tips.append("Battito a riposo sopra la tua media: controlla cena tardi, alcol, stanza calda o allenamento serale.")
        if float(stats["coverage_pct"]) < 80:
            tips.append("Pochi campioni: controlla la fascia prima di dormire e lascia il Mac collegato.")
        if quality is not None and quality <= 2 and score >= 65:
            tips.append("La percezione è peggiore dell'indicatore: dai priorità a recupero e routine serale.")
        if not tips:
            tips.append("Notte regolare: mantieni gli stessi orari e la stessa routine.")
        tips.append("Questa è una stima basata sul battito, non una diagnosi del sonno.")
        return tips[:5]

    def finalize(
        self,
        *,
        quality: int | None = None,
        notes: str | None = None,
    ) -> dict[str, object] | None:
        if self.finalized_path is not None:
            return None
        stats = self.stats()
        if len(self.samples) < 10 or float(stats["duration_s"]) < 60:
            return None
        bedtime = _bedtime_minutes(self.started_at.isoformat(timespec="seconds"))
        baseline = self._baseline()
        score, label = self._score(stats, baseline, bedtime)
        summary = self.summary(include_series=True)
        summary.update(
            {
                "bedtime_minutes": bedtime,
                "baseline": baseline,
                "score": score,
                "score_label": label,
                "tips": self._tips(stats, baseline, bedtime, quality, score),
                "quality": quality,
                "notes": (notes or "").strip()[:2000],
                "csv": f"{self.session_id}.csv",
            }
        )
        csv_path = self.data_dir / f"{self.session_id}.csv"
        json_path = self.data_dir / f"{self.session_id}.json"
        with csv_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle, lineterminator="\n")
            writer.writerow(["timestamp", "elapsed_s", "heart_rate_bpm"])
            for sample in self.samples:
                writer.writerow(
                    [
                        sample["timestamp"],
                        sample["elapsed_s"],
                        sample["heart_rate_bpm"],
                    ]
                )
        json_path.write_text(
            json.dumps(summary, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        self.finalized_path = csv_path
        public = dict(summary)
        public.pop("series", None)
        return public


def list_sleep_sessions(data_dir: Path, limit: int = 60) -> list[dict[str, Any]]:
    directory = Path(data_dir)
    if not directory.exists():
        return []
    sessions: list[dict[str, Any]] = []
    for path in directory.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        data.pop("series", None)
        sessions.append(data)
    sessions.sort(key=lambda item: str(item.get("started_at") or item.get("id")), reverse=True)
    return sessions[: max(0, int(limit))]


def load_sleep_session(data_dir: Path, session_id: str) -> dict[str, Any]:
    safe = Path(session_id).name
    if safe != session_id or not safe:
        raise ValueError("nome sessione non valido")
    path = Path(data_dir) / f"{safe}.json"
    if not path.is_file():
        raise FileNotFoundError(session_id)
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("sessione non valida")
    return data


def sleep_csv_path(data_dir: Path, session_id: str) -> Path:
    safe = Path(session_id).name
    if safe != session_id or not safe:
        raise ValueError("nome sessione non valido")
    return Path(data_dir) / f"{safe}.csv"
