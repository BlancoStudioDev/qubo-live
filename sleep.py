"""Sleep tracking and local analysis from heart-rate samples.

The data lives in ``data/sleep_sessions`` and is intentionally separate from
the Strava/trainer sessions.  No TCX file is generated and nothing is uploaded
to Strava: this is a local sleep diary built from the heart-rate strap.
"""

from __future__ import annotations

import csv
import json
import math
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


def _median(values: list[float]) -> float:
    return float(statistics.median(values)) if values else 0.0


def _std(values: list[float]) -> float:
    return float(statistics.pstdev(values)) if len(values) > 1 else 0.0


def _rmssd(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    differences = [current - previous for previous, current in zip(values, values[1:])]
    return math.sqrt(_mean([difference * difference for difference in differences]))


_STAGE_LABELS = {
    "deep": "Profondo",
    "light": "Leggero",
    "rem": "REM",
    "awake": "Veglia",
    "gap": "Nessun dato",
}


def _stage_breakdown(
    stages: list[dict[str, object]],
) -> tuple[dict[str, float], dict[str, float]]:
    totals = {key: 0.0 for key in ("deep", "light", "rem", "awake")}
    for item in stages:
        stage = str(item.get("stage"))
        if stage in totals:
            totals[stage] += float(item.get("duration_s", 0.0))
    covered = sum(totals.values())
    if covered <= 0:
        return (
            {key: 0.0 for key in totals},
            {key: 0.0 for key in totals},
        )
    percentages = {
        key: round(value / covered * 100.0, 1) for key, value in totals.items()
    }
    minutes = {key: round(value / 60.0, 1) for key, value in totals.items()}
    return percentages, minutes


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
        self._last_elapsed = -1.0

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
        rr_ms: float | None = None,
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
        sample: dict[str, object] = {
            "timestamp": moment,
            "elapsed_s": round(elapsed, 1),
            "heart_rate_bpm": round(heart_rate, 1),
        }
        if rr_ms is not None and float(rr_ms) > 0:
            sample["rr_interval_ms"] = round(float(rr_ms), 1)
        self.samples.append(sample)

    def stats(self) -> dict[str, float]:
        duration = max(self.elapsed_s, 0.0)
        values = [float(item["heart_rate_bpm"]) for item in self.samples]
        rr_values = [
            float(item["rr_interval_ms"])
            for item in self.samples
            if float(item.get("rr_interval_ms") or 0) > 0
        ]
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
                "rr_samples": 0,
                "avg_rr_ms": 0.0,
                "rr_std_ms": 0.0,
                "rr_rmssd_ms": 0.0,
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
            "rr_samples": len(rr_values),
            "avg_rr_ms": round(_mean(rr_values), 1),
            "rr_std_ms": round(_std(rr_values), 1),
            "rr_rmssd_ms": round(_rmssd(rr_values), 1),
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

    def stages(self, epoch_s: int = 300) -> list[dict[str, object]]:
        """Estimate sleep stages from HR and RR variability.

        This is a local heuristic, not a clinical staging.  ``epoch_s`` is the
        width of each block; five minutes keeps the timeline readable without
        pretending to resolve individual sleep cycles.
        """
        if not self.samples:
            return []
        duration = max(self.elapsed_s, 0.0)
        if duration <= 0:
            return []
        buckets: dict[int, list[dict[str, object]]] = {}
        for sample in self.samples:
            bucket = int(float(sample["elapsed_s"]) // epoch_s)
            buckets.setdefault(bucket, []).append(sample)
        all_hr = [float(item["heart_rate_bpm"]) for item in self.samples]
        all_rr = [
            float(item["rr_interval_ms"])
            for item in self.samples
            if float(item.get("rr_interval_ms") or 0) > 0
        ]
        base_hr = _median(all_hr)
        base_rr = _median(all_rr)
        base_rr_cv = (_std(all_rr) / base_rr) if base_rr > 0 else 0.0
        last_bucket = int(math.ceil(duration / epoch_s)) - 1
        raw: list[dict[str, object]] = []
        for bucket in range(last_bucket + 1):
            start_s = bucket * epoch_s
            end_s = min(duration, (bucket + 1) * epoch_s)
            epoch_duration = max(0.0, end_s - start_s)
            group = buckets.get(bucket, [])
            if not group or epoch_duration <= 0:
                raw.append(
                    {
                        "stage": "gap",
                        "label": "Nessun dato",
                        "start_s": round(start_s, 1),
                        "end_s": round(end_s, 1),
                        "duration_s": round(epoch_duration, 1),
                        "heart_rate_bpm": 0.0,
                        "rr_interval_ms": 0.0,
                        "rr_cv": 0.0,
                        "coverage_pct": 0.0,
                    }
                )
                continue
            hrs = [float(item["heart_rate_bpm"]) for item in group]
            rrs = [
                float(item["rr_interval_ms"])
                for item in group
                if float(item.get("rr_interval_ms") or 0) > 0
            ]
            avg_hr = _mean(hrs)
            avg_rr = _mean(rrs)
            rr_std = _std(rrs)
            rr_cv = (rr_std / avg_rr) if avg_rr > 0 else 0.0
            hr_ratio = avg_hr / base_hr if base_hr > 0 else 1.0
            rr_ratio = avg_rr / base_rr if base_rr > 0 else 1.0
            rr_cv_ratio = rr_cv / base_rr_cv if base_rr_cv > 0 else 1.0
            if hr_ratio > 1.12 or (hr_ratio > 1.05 and rr_cv_ratio > 1.25):
                stage = "awake"
            elif base_rr > 0 and hr_ratio < 0.96 and rr_ratio >= 1.0 and rr_cv_ratio >= 0.9:
                stage = "deep"
            elif base_rr > 0 and hr_ratio >= 0.98 and rr_cv_ratio < 0.9:
                stage = "rem"
            elif hr_ratio < 0.95:
                stage = "deep"
            else:
                stage = "light"
            raw.append(
                {
                    "stage": stage,
                    "label": _STAGE_LABELS[stage],
                    "start_s": round(start_s, 1),
                    "end_s": round(end_s, 1),
                    "duration_s": round(epoch_duration, 1),
                    "heart_rate_bpm": round(avg_hr, 1),
                    "rr_interval_ms": round(avg_rr, 1),
                    "rr_cv": round(rr_cv, 3),
                    "coverage_pct": round(
                        min(100.0, len(hrs) / max(1.0, epoch_duration) * 100.0),
                        1,
                    ),
                }
            )
        # Smooth the labels with a three-epoch majority: real sleep changes are
        # slow, while HR artefacts are usually short spikes.
        smoothed: list[dict[str, object]] = []
        for index, item in enumerate(raw):
            if item["stage"] == "gap":
                smoothed.append(item)
                continue
            candidates = [
                raw[position]["stage"]
                for position in (index - 1, index, index + 1)
                if 0 <= position < len(raw) and raw[position]["stage"] != "gap"
            ]
            chosen = (
                max(set(candidates), key=candidates.count)
                if candidates
                else item["stage"]
            )
            adjusted = dict(item)
            adjusted["stage"] = chosen
            adjusted["label"] = _STAGE_LABELS.get(str(chosen), str(chosen))
            smoothed.append(adjusted)
        return smoothed

    def summary(self, *, include_series: bool = False) -> dict[str, object]:
        stage_list = self.stages()
        stage_pct, stage_minutes = _stage_breakdown(stage_list)
        data = {
            "id": self.session_id,
            "started_at": self.started_at.isoformat(timespec="seconds"),
            "ended_at": datetime.fromtimestamp(
                self.start_epoch + self.elapsed_s
            ).isoformat(timespec="seconds"),
            "source": "hr",
            **self.stats(),
            "stage_pct": stage_pct,
            "stage_minutes": stage_minutes,
        }
        if include_series:
            data["series"] = self.series()
            data["stages"] = stage_list
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
            writer.writerow(
                ["timestamp", "elapsed_s", "heart_rate_bpm", "rr_interval_ms"]
            )
            for sample in self.samples:
                writer.writerow(
                    [
                        sample["timestamp"],
                        sample["elapsed_s"],
                        sample["heart_rate_bpm"],
                        sample.get("rr_interval_ms", ""),
                    ]
                )
        json_path.write_text(
            json.dumps(summary, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        self.finalized_path = csv_path
        public = dict(summary)
        public.pop("series", None)
        public.pop("stages", None)
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
        data.pop("stages", None)
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


class SleepAutoDetector:
    """Heuristic state machine for automatic sleep/wake detection.

    It only decides when to open or close a night.  The actual stage estimate
    remains in SleepRecorder.  A manual start/stop always wins.
    """

    def __init__(
        self,
        data_dir: Path,
        *,
        window_start: int = 21,
        window_end: int = 9,
        onset_minutes: int = 30,
        wake_minutes: int = 5,
        min_sleep_minutes: int = 120,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.window_start = max(0, min(23, int(window_start)))
        self.window_end = max(0, min(23, int(window_end)))
        self.onset_minutes = max(5, int(onset_minutes))
        self.wake_minutes = max(2, int(wake_minutes))
        self.min_sleep_minutes = max(30, int(min_sleep_minutes))
        self._baseline_cache_at = 0.0
        self._baseline_cache: float | None = None

    def _in_window(self, moment: datetime) -> bool:
        hour = moment.hour
        if self.window_start <= self.window_end:
            return self.window_start <= hour < self.window_end
        return hour >= self.window_start or hour < self.window_end

    @staticmethod
    def _window_samples(
        hr_window: list[dict[str, object]], minutes: int, now: datetime
    ) -> list[dict[str, object]]:
        cutoff = now.timestamp() - minutes * 60.0
        return [
            item for item in hr_window if float(item.get("t") or 0) >= cutoff
        ]

    @staticmethod
    def _continuous(
        samples: list[dict[str, object]], gap_s: float = 90.0
    ) -> list[dict[str, object]]:
        if not samples:
            return []
        start = 0
        previous_t: float | None = None
        for index, sample in enumerate(samples):
            current = float(sample.get("t") or 0)
            if previous_t is not None and current - previous_t > gap_s:
                start = index
            previous_t = current
        return samples[start:]

    @staticmethod
    def _low_hr_streak(
        samples: list[dict[str, object]],
        threshold: float,
        gap_s: float = 90.0,
    ) -> list[dict[str, object]]:
        if not samples:
            return []
        start = 0
        previous_t: float | None = None
        for index, sample in enumerate(samples):
            moment = float(sample.get("t") or 0)
            heart_rate = float(sample.get("h") or 0)
            if (
                heart_rate <= 0
                or heart_rate > threshold + 8.0
                or (previous_t is not None and moment - previous_t > gap_s)
            ):
                start = index + 1
            previous_t = moment
        return samples[start:]

    def baseline_hr(self, *, max_age_s: float = 300.0) -> float | None:
        now = time.time()
        if now - self._baseline_cache_at <= max_age_s:
            return self._baseline_cache
        previous = [
            item
            for item in list_sleep_sessions(self.data_dir, limit=8)
            if float(item.get("resting_heart_rate_bpm") or 0) > 0
        ][:7]
        self._baseline_cache = (
            _median([float(item["resting_heart_rate_bpm"]) for item in previous])
            if previous
            else None
        )
        self._baseline_cache_at = now
        return self._baseline_cache

    def evaluate(
        self,
        hr_window: list[dict[str, object]],
        *,
        sleep_active: bool,
        session_stats: dict[str, float] | None = None,
        now: datetime | None = None,
    ) -> dict[str, object] | None:
        moment = now or datetime.now()
        if sleep_active:
            # Auto-stop must work at any hour: waking after the night window
            # should still close the session instead of leaving it open all day.
            stats = session_stats or {}
            duration = float(stats.get("duration_s") or 0.0)
            if duration < self.min_sleep_minutes * 60.0:
                return None
            if duration > 12 * 3600.0:
                return {"action": "stop", "reason": "durata massima"}
            recent = self._continuous(
                self._window_samples(hr_window, self.wake_minutes, moment)
            )
            values = [
                float(item["h"])
                for item in recent
                if float(item.get("h") or 0) > 0
            ]
            if len(values) < max(3, int(self.wake_minutes * 60 * 0.5)):
                return None
            average = float(stats.get("avg_heart_rate_bpm") or _mean(values))
            if _median(values) > average + 12:
                return {"action": "stop", "reason": "battito in risalita"}
            return None
        if not self._in_window(moment):
            return None
        recent = self._continuous(
            self._window_samples(hr_window, self.onset_minutes, moment)
        )
        baseline = self.baseline_hr()
        threshold = (baseline + 5.0) if baseline is not None else 60.0
        streak = self._low_hr_streak(recent, threshold)
        values = [
            float(item["h"])
            for item in streak
            if float(item.get("h") or 0) > 0
        ]
        expected = max(3, int(self.onset_minutes * 60 * 0.6))
        if len(values) < expected:
            return None
        if _median(values) > threshold:
            return None
        if _std(values) > 6.0:
            return None
        started_at = moment
        if streak:
            started_at = datetime.fromtimestamp(
                float(streak[0].get("t") or moment.timestamp())
            )
        return {
            "action": "start",
            "started_at": started_at,
            "threshold": round(threshold, 1),
            "baseline": baseline,
        }
