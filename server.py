#!/usr/bin/env python3
"""Local web dashboard for the Elite Qubo (BLE name QD).

Run:
    .venv/bin/python server.py --simulate
    .venv/bin/python server.py

The BLE backend reads the standard Cycling Power / CSC / FTMS profiles and
exposes FTMS resistance and target-power (ERG) control.  It falls back to
showing raw vendor notifications when the sensor uses a proprietary protocol.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import math
import re
import sys
import time
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

SCRIPT_DIR = Path(__file__).resolve().parent
DATA_DIR = SCRIPT_DIR / "data" / "trainer_sessions"
SIM_DATA_DIR = SCRIPT_DIR / "data" / "sim_sessions"
CONFIG_FILE = SCRIPT_DIR / "data" / "trainer_config.json"
WORKOUTS_FILE = SCRIPT_DIR / "data" / "workouts.json"
INDEX_HTML = SCRIPT_DIR / "static" / "index.html"
MINI_HTML = SCRIPT_DIR / "static" / "mini.html"
PLAN_MD = SCRIPT_DIR / "PIANO_ALLENAMENTO.md"

sys.path.insert(0, str(SCRIPT_DIR))

from ble_client import TrainerClient, scan_devices  # noqa: E402
from session import (  # noqa: E402
    Metrics,
    SessionRecorder,
    apply_calibration,
    list_sessions,
    tcx_from_csv,
)
from plan import render_plan_page  # noqa: E402
from workouts import (  # noqa: E402
    load_workouts,
    normalize_workout,
    save_workouts,
    workout_summary,
)


class Manager:
    # Per-field source priority.  On this Qubo the CPS stream (0x2A63)
    # contains unrecoverable byte-shift spikes (multiples of 256, up to
    # 2816 W) and long stretches near zero, so it is only a fallback.  The
    # FTMS Indoor Bike field is the power the brake actually applies and is
    # the only source used while it is alive.  Speed is native FTMS; cadence
    # comes from the crank revolution counters of CPS/CSC because the FTMS
    # cadence field of this firmware is not trustworthy.
    FIELD_PRIORITY = {
        "power_w": ("2ad2", "2a63", "sim"),
        "speed_kmh": ("2ad2", "2a63", "2a5b", "sim"),
        "cadence_rpm": ("2a63", "2a5b", "sim"),
    }

    # Anything outside these ranges cannot come from a Qubo Digital/Power
    # Mag (max around 1070 W) and is a decode artefact, not a measurement.
    PLAUSIBLE_RANGES = {
        "power_w": (0.0, 1200.0),
        "speed_kmh": (0.0, 120.0),
        "cadence_rpm": (0.0, 220.0),
    }

    # Below these raw values the calibration offset ramps in instead of
    # applying at full strength.  A coasting trainer can report 1-4 km/h and
    # 1-5 W of noise; without the ramp that would display as 30+ W and
    # several phantom km/h.
    CALIBRATION_FULL_AT = {
        "power_w": 20.0,
        "speed_kmh": 10.0,
    }

    def __init__(
        self,
        *,
        simulate: bool = False,
        scan_time: float = 6.0,
    ) -> None:
        self.data_dir = SIM_DATA_DIR if simulate else DATA_DIR
        self.simulate = simulate
        self.session_source = "sim" if simulate else "trainer"
        self.scan_time = scan_time

        config = self._load_config()
        self.config: dict[str, object] = {
            "wheel_circumference_mm": int(config.get("wheel_circumference_mm", 94)),
            "ftp_w": int(config.get("ftp_w", 179)),
            "debug": bool(config.get("debug", True)),
            "weight_kg": float(config.get("weight_kg", 82.0)),
            # The Qubo Digital estimates power from roller speed and load and
            # typically reads 10-50 W low; +30 W is the midpoint of that
            # range.  The speed offset is the flat-road equivalent of that
            # correction at 25 km/h (about 10%) and is configurable.
            "power_offset_w": float(config.get("power_offset_w", 30.0)),
            "speed_offset_kmh": float(config.get("speed_offset_kmh", 2.5)),
        }

        self.status = "idle"
        self.status_detail = ""
        self.mode = "simulate" if simulate else "ble"
        self.device: dict[str, object] | None = None
        self.battery: int | None = None
        self.error: str | None = None
        self.latest = Metrics()
        self.latest_at = 0.0
        self.sources: dict[str, str] = {}
        self.field_at: dict[str, float] = {}
        # Last value received for each metric, per BLE source.  Used by the
        # Debug BLE panel to compare FTMS, CPS and CSC on the same stroke.
        self.source_values: dict[str, dict[str, dict[str, object]]] = {}
        self.rejected: deque[dict[str, object]] = deque(maxlen=12)
        self.history: deque[dict[str, object]] = deque(maxlen=240)
        self.raw: deque[dict[str, object]] = deque(maxlen=60)
        self.recorder = self._new_recorder()
        self.session_active = False
        self.session_paused = False
        self.last_session: dict[str, object] | None = None
        self.control: dict[str, object] = self._empty_control()

        # Structured workout library and the currently followed workout.
        # The active workout lives in memory: a server restart clears it, but
        # the library itself is persisted in data/workouts.json.
        self.workouts: list[dict[str, object]] = load_workouts(WORKOUTS_FILE)
        self.active_workout: dict[str, object] | None = None
        self._workout_status = "idle"
        self._workout_started_at = 0.0
        self._workout_paused_at: float | None = None
        self._workout_paused_total = 0.0

        self.client: TrainerClient | None = None
        self._sim_task: asyncio.Task | None = None
        self._tick_task: asyncio.Task | None = None
        self._scan_lock = asyncio.Lock()
        self._control_lock = asyncio.Lock()
        self._cached_devices: list[dict[str, object]] = []

    @staticmethod
    def _empty_control() -> dict[str, object]:
        return {
            "supported": False,
            "connected": False,
            "mode": "off",
            "resistance": None,
            "actual_resistance": None,
            "target_power": None,
            "range": None,
            "power_range": None,
            "erg_supported": False,
            "simulation_supported": False,
            "simulation": False,
            "features": {},
        }

    # -- config ------------------------------------------------------------
    def _load_config(self) -> dict[str, object]:
        try:
            return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def save_config(self) -> None:
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(
            json.dumps(self.config, indent=2), encoding="utf-8"
        )

    def calibration(self) -> dict[str, float]:
        return {
            "power_offset_w": round(float(self.config["power_offset_w"]), 1),
            "speed_offset_kmh": round(float(self.config["speed_offset_kmh"]), 1),
        }

    def _new_recorder(self) -> SessionRecorder:
        return SessionRecorder(
            self.data_dir,
            source=self.session_source,
            calibration=self.calibration(),
            weight_kg=float(self.config["weight_kg"]),
        )

    def _finalize_current_session(self) -> dict[str, object] | None:
        """Write the open session to disk, if it has enough data."""
        if not self.session_active and not self.recorder.samples:
            return None
        summary = self.recorder.finalize()
        if summary:
            self.last_session = summary
        return summary

    def _reset_workout_state(self) -> None:
        self.active_workout = None
        self._workout_status = "idle"
        self._workout_started_at = 0.0
        self._workout_paused_at = None
        self._workout_paused_total = 0.0

    def _close_session(self) -> dict[str, object] | None:
        """Finalize the current session and leave the recorder idle."""
        summary = self._finalize_current_session()
        self._reset_workout_state()
        self.recorder = self._new_recorder()
        self.history.clear()
        self.session_active = False
        self.session_paused = False
        return summary

    def _begin_session(self) -> None:
        """Finalize anything open and start a fresh recording session."""
        self._finalize_current_session()
        self._reset_workout_state()
        self.recorder = self._new_recorder()
        self.history.clear()
        self.session_active = True
        self.session_paused = False

    # -- lifecycle ---------------------------------------------------------
    def start_tick(self) -> None:
        if self._tick_task is None:
            self._tick_task = asyncio.create_task(self._tick_loop(), name="tick")

    def start_simulation(self) -> None:
        self.mode = "simulate"
        self.status = "simulating"
        self.status_detail = "demo"
        self.sources = {
            "power_w": "sim",
            "speed_kmh": "sim",
            "cadence_rpm": "sim",
        }
        self.field_at.clear()
        self.control = {
            "supported": True,
            "connected": True,
            "mode": "resistance",
            "resistance": 0.0,
            "actual_resistance": 0.0,
            "target_power": None,
            "range": {"min": 0.0, "max": 20.0, "step": 0.1},
            "power_range": {"min": 0.0, "max": 4000.0, "step": 1.0},
            "erg_supported": True,
            "features": {},
        }
        self.device = {
            "name": "Elite Qubo (demo)",
            "address": "SIMULATED",
            "rssi": -40,
            "profiles": ["Cycling Power", "CSC", "FTMS"],
        }
        self.battery = 82
        self._sim_task = asyncio.create_task(self._simulate(), name="simulate")

    async def _simulate(self) -> None:
        started = time.monotonic()
        while True:
            t = time.monotonic() - started
            ramp = min(1.0, t / 4.0)
            effort = 0.5 + 0.28 * math.sin(t / 47.0) + 0.12 * math.sin(t / 9.0 + 1.2)
            speed = (14.0 + 21.0 * effort) * ramp
            speed += 0.6 * math.sin(t * 1.7)
            if t < 5:
                speed *= max(0.0, (t - 1.0) / 4.0)
            cadence = 0.0 if speed < 2 else 66.0 + 28.0 * effort + 2.0 * math.sin(t / 1.3)
            power = (
                0.0
                if speed < 2
                else 0.17 * speed**2 + 32.0 + 14.0 * math.sin(t / 5.5) + 6.0 * math.sin(t * 2.1)
            )
            self._apply_metrics(
                "sim",
                {
                    "power_w": max(0.0, power),
                    "speed_kmh": max(0.0, speed),
                    "cadence_rpm": max(0.0, cadence),
                },
            )
            await asyncio.sleep(0.4)

    async def shutdown(self) -> None:
        if self._sim_task:
            self._sim_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._sim_task
        if self._tick_task:
            self._tick_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._tick_task
        if self.client:
            await self.client.disconnect()
        self.recorder.finalize()

    # -- data --------------------------------------------------------------
    def _apply_metrics(self, source: str, values: dict[str, float]) -> None:
        # The Qubo broadcasts the same ride on Cycling Power (2a63), CSC
        # (2a5b) and FTMS (2ad2).  Merge per field instead of choosing one
        # profile.  Power must come from FTMS: the instantaneous power of
        # 2a63 on this firmware is unreliable (recorded sessions show
        # unreal spikes around 2800 W and stretches near zero), while the
        # FTMS Indoor Bike field is the brake-load power the trainer
        # actually computes.  Speed is best on FTMS too; cadence stays on
        # the crank revolution data (CPS/CSC).
        now = time.monotonic()
        if "resistance_level" in values:
            # FTMS reports the brake position in 0.1 units (2AD6 scale).
            self.control["actual_resistance"] = round(
                float(values["resistance_level"]), 1
            )
        applied = False
        for key, value in values.items():
            if key not in self.PLAUSIBLE_RANGES or not hasattr(self.latest, key):
                continue

            numeric = float(value)
            self.source_values.setdefault(key, {})[source] = {
                "value": round(numeric, 1),
                "valid": self._plausible(key, numeric),
            }
            if not self._plausible(key, numeric):
                self.rejected.append(
                    {
                        "t": time.strftime("%H:%M:%S"),
                        "source": source,
                        "field": key,
                        "value": round(numeric, 1),
                    }
                )
                log.debug("Scartato %s=%.1f da %s (fuori range)", key, numeric, source)
                continue

            priority = self.FIELD_PRIORITY.get(key)
            if priority is None or source not in priority:
                continue

            current = self.sources.get(key)
            current_at = self.field_at.get(key, 0.0)
            current_rank = priority.index(current) if current in priority else -1
            source_rank = priority.index(source)
            if (
                current
                and current != source
                and now - current_at < 3.0
                and current_rank < source_rank
            ):
                continue

            setattr(self.latest, key, float(value))
            self.sources[key] = source
            self.field_at[key] = now
            applied = True
        if applied:
            self.latest_at = now

    @classmethod
    def _plausible(cls, key: str, value: float) -> bool:
        minimum, maximum = cls.PLAUSIBLE_RANGES[key]
        return minimum <= value <= maximum

    def _on_raw(self, uuid: str, data: bytes) -> None:
        self.raw.append(
            {
                "t": time.strftime("%H:%M:%S"),
                "uuid": uuid,
                "hex": data.hex(" "),
                "len": len(data),
            }
        )

    def _on_status(self, status: str, detail: str) -> None:
        if status == "connected":
            self.status = "connected"
            self.status_detail = detail
            self.error = None
            if self.client is not None:
                info = dict(self.device or {})
                for key, value in self.client.device_info.items():
                    if value:
                        info[key] = value
                self.device = info
                self.battery = self.client.battery
                control = self.client.control_info
                self.control = {
                    "supported": bool(control.get("supported")),
                    "connected": True,
                    "mode": (
                        "resistance"
                        if control.get("resistance") is not None
                        else "erg" if control.get("power") is not None else "off"
                    ),
                    "resistance": None,
                    "actual_resistance": None,
                    "target_power": None,
                    "range": control.get("resistance"),
                    "power_range": control.get("power"),
                    "erg_supported": bool(control.get("target_power")),
                    "simulation_supported": bool(control.get("target_simulation")),
                    "simulation": False,
                    "features": control.get("features") or {},
                }
        elif status in {"connecting", "reconnecting"}:
            self.status = status
            self.status_detail = detail
            self.error = None
            self.control = self._empty_control()
        elif status == "error":
            self.status = "error"
            self.status_detail = detail
            self.error = detail
            self.control = self._empty_control()

    def effective_metrics(self) -> Metrics:
        now = time.monotonic()

        def fresh(key: str, max_age: float) -> float:
            at = self.field_at.get(key)
            if at is None or now - at > max_age:
                return 0.0
            return float(getattr(self.latest, key))

        return Metrics(
            power_w=apply_calibration(
                fresh("power_w", 5.0),
                float(self.config["power_offset_w"]),
                self.PLAUSIBLE_RANGES["power_w"][1],
                self.CALIBRATION_FULL_AT["power_w"],
            ),
            speed_kmh=apply_calibration(
                fresh("speed_kmh", 3.0),
                float(self.config["speed_offset_kmh"]),
                self.PLAUSIBLE_RANGES["speed_kmh"][1],
                self.CALIBRATION_FULL_AT["speed_kmh"],
            ),
            cadence_rpm=fresh("cadence_rpm", 3.0),
        )

    def _record_sample(self, metrics: Metrics) -> None:
        """Append a sample only while the session is running."""
        if self.session_active and not self.session_paused:
            self.recorder.add(metrics)

    async def _tick_loop(self) -> None:
        while True:
            await asyncio.sleep(1.0)
            if self.status not in {"connected", "simulating"}:
                continue
            metrics = self.effective_metrics()
            self._record_sample(metrics)
            point: dict[str, object] = {
                "t": int(time.time()),
                "p": round(metrics.power_w, 1),
                "s": round(metrics.speed_kmh, 1),
                "c": round(metrics.cadence_rpm, 1),
            }
            workout = self.workout_snapshot()
            if workout.get("active") and workout.get("status") == "running":
                point["wt"] = workout["elapsed_s"]
                point["target"] = workout["target_power"]
            self.history.append(point)

    def snapshot(self) -> dict[str, object]:
        return {
            "status": self.status,
            "detail": self.status_detail,
            "mode": self.mode,
            "device": self.device,
            "battery": self.battery,
            "metrics": self.effective_metrics().as_dict(),
            "stats": self.recorder.stats(),
            "history": list(self.history),
            "raw": list(self.raw)[-12:],
            "config": self.config,
            "calibration": self.calibration(),
            "error": self.error,
            "last_session": self.last_session,
            "session_active": self.session_active,
            "session_paused": self.session_paused,
            "session_id": self.recorder.session_id if self.session_active else None,
            "source": self.sources.get("power_w"),
            "sources": dict(self.sources),
            "source_values": {
                key: dict(sources) for key, sources in self.source_values.items()
            },
            "rejected": list(self.rejected),
            "control": self.control,
            "workout": self.workout_snapshot(),
            "server_time": time.time(),
        }

    # -- commands ----------------------------------------------------------
    async def scan(self) -> list[dict[str, object]]:
        async with self._scan_lock:
            devices = await scan_devices(self.scan_time)
            self._cached_devices = [device.to_dict() for device in devices]
            return self._cached_devices

    async def autoconnect(self) -> None:
        try:
            self.status = "scanning"
            self.status_detail = "ricerca del sensore Elite..."
            devices = await self.scan()
        except Exception as exc:  # noqa: BLE001 - surfaced in the UI
            self.status = "error"
            self.error = str(exc)
            return
        candidates = [d for d in devices if int(d.get("score", 0)) > 0]
        if not candidates:
            self.status = "idle"
            self.status_detail = "nessun sensore trovato"
            return
        await self.connect(str(candidates[0]["address"]))

    async def connect(self, address: str) -> None:
        if self.simulate:
            return
        if self.client:
            await self.client.disconnect()

        self._close_session()
        self.latest = Metrics()
        self.latest_at = 0.0
        self.sources.clear()
        self.field_at.clear()
        self.source_values.clear()
        self.rejected.clear()
        self.control = self._empty_control()
        self.raw.clear()
        self.error = None

        cached = next(
            (d for d in self._cached_devices if d["address"] == address), None
        )
        self.device = cached or {"address": address, "name": "(sconosciuto)"}
        self.status = "connecting"
        self.status_detail = "apertura canale BLE"

        self.client = TrainerClient(
            on_metrics=self._apply_metrics,
            on_status=self._on_status,
            on_raw=self._on_raw,
            wheel_circumference_m=int(self.config["wheel_circumference_mm"]) / 1000.0,
            debug_vendor=bool(self.config["debug"]),
        )
        await self.client.connect(address)

    async def disconnect(self) -> None:
        if self._sim_task:
            self._sim_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._sim_task
            self._sim_task = None
        if self.client:
            await self.client.disconnect()
            self.client = None
        self.status = "idle"
        self.status_detail = "disconnesso"
        self.sources.clear()
        self.field_at.clear()
        self.source_values.clear()
        self.rejected.clear()
        self.latest = Metrics()
        self.latest_at = 0.0
        self.control = self._empty_control()
        self._close_session()

    def reset_session(self) -> dict[str, object] | None:
        """Save the current session and leave the recorder stopped."""
        return self._close_session()

    def update_config(
        self,
        wheel_circumference_mm: int | None,
        ftp_w: int | None,
        debug: bool | None,
        power_offset_w: float | None = None,
        speed_offset_kmh: float | None = None,
        weight_kg: float | None = None,
    ) -> None:
        if wheel_circumference_mm is not None:
            self.config["wheel_circumference_mm"] = max(
                40, min(2500, wheel_circumference_mm)
            )
            if self.client:
                self.client.wheel_circumference_m = (
                    int(self.config["wheel_circumference_mm"]) / 1000.0
                )
        if ftp_w is not None:
            self.config["ftp_w"] = max(50, min(600, ftp_w))
        if debug is not None:
            self.config["debug"] = debug
        if power_offset_w is not None:
            self.config["power_offset_w"] = max(
                -100.0, min(300.0, float(power_offset_w))
            )
        if speed_offset_kmh is not None:
            self.config["speed_offset_kmh"] = max(
                -20.0, min(20.0, float(speed_offset_kmh))
            )
        if weight_kg is not None:
            self.config["weight_kg"] = max(30.0, min(200.0, float(weight_kg)))
            self.recorder.weight_kg = float(self.config["weight_kg"])
        if self.recorder is not None:
            self.recorder.calibration = self.calibration()
        self.save_config()

    # -- structured workouts ------------------------------------------------
    def workout_library(self) -> list[dict[str, object]]:
        return [workout_summary(workout) for workout in self.workouts]

    def _workout_elapsed(self) -> float:
        if not self.active_workout or self._workout_started_at <= 0:
            return 0.0
        end = (
            self._workout_paused_at
            if self._workout_status == "paused"
            else time.time()
        )
        if end is None:
            end = time.time()
        return max(
            0.0, end - self._workout_started_at - self._workout_paused_total
        )

    def workout_snapshot(self) -> dict[str, object]:
        if not self.active_workout:
            return {
                "active": False,
                "status": "idle",
                "elapsed_s": 0.0,
                "remaining_s": 0.0,
                "total_s": 0,
                "progress": 0.0,
                "target_power": None,
                "target_pct_ftp": None,
                "intervals": [],
                "current": None,
                "next": None,
            }

        workout = self.active_workout
        intervals = list(workout.get("intervals") or [])
        total_s = sum(int(item["duration_s"]) for item in intervals)
        if self._workout_status == "running" and total_s > 0:
            if self._workout_elapsed() >= total_s:
                self._workout_status = "completed"
                self._workout_paused_at = None

        elapsed = min(float(total_s), self._workout_elapsed())
        status = self._workout_status
        if status == "idle":
            status = "running"
        if status == "completed":
            elapsed = float(total_s)

        ftp = max(1, int(self.config.get("ftp_w", 179)))
        cursor = 0.0
        current_index = len(intervals) - 1
        current_elapsed = float(intervals[-1]["duration_s"])
        current_remaining = 0.0
        found = False
        enriched: list[dict[str, object]] = []
        for index, item in enumerate(intervals):
            duration = float(item["duration_s"])
            start = cursor
            end = cursor + duration
            power = int(item["power_w"])
            enriched.append(
                {
                    **item,
                    "index": index,
                    "start_s": round(start, 1),
                    "end_s": round(end, 1),
                    "duration_s": int(item["duration_s"]),
                    "power_w": power,
                    "pct_ftp": round(power / ftp, 3),
                }
            )
            if not found and (elapsed < end or index == len(intervals) - 1):
                current_index = index
                current_elapsed = max(0.0, min(duration, elapsed - start))
                current_remaining = max(0.0, end - elapsed)
                found = True
            cursor = end

        current = dict(enriched[current_index])
        current["elapsed_s"] = round(current_elapsed, 1)
        current["remaining_s"] = round(current_remaining, 1)

        next_interval: dict[str, object] | None = None
        if status != "completed" and current_index + 1 < len(enriched):
            next_interval = dict(enriched[current_index + 1])
            next_interval["starts_in_s"] = round(current_remaining, 1)

        target_power = (
            None if status == "completed" else int(current["power_w"])
        )
        return {
            "active": True,
            "status": status,
            "id": workout.get("id"),
            "name": workout.get("name"),
            "description": workout.get("description"),
            "builtin": bool(workout.get("builtin")),
            "started_at": self._workout_started_at,
            "elapsed_s": round(elapsed, 1),
            "remaining_s": round(max(0.0, total_s - elapsed), 1),
            "total_s": total_s,
            "progress": round(elapsed / total_s, 4) if total_s else 0.0,
            "target_power": target_power,
            "target_pct_ftp": (
                round(target_power / ftp, 3)
                if target_power is not None
                else None
            ),
            "current": current,
            "next": next_interval,
            "intervals": enriched,
            "ftp_w": ftp,
        }

    def save_workout(self, payload: dict[str, object]) -> dict[str, object]:
        requested_id = str(payload.get("id") or "").strip()
        existing_index = None
        if requested_id:
            existing_index = next(
                (
                    index
                    for index, workout in enumerate(self.workouts)
                    if workout.get("id") == requested_id
                ),
                None,
            )

        if existing_index is not None:
            existing = self.workouts[existing_index]
            data = dict(payload)
            if data.get("notes") is None:
                data["notes"] = existing.get("notes", "")
            normalized = normalize_workout(
                data,
                workout_id=str(existing.get("id")),
                builtin=bool(existing.get("builtin", False)),
                created_at=str(existing.get("created_at") or "") or None,
            )
            self.workouts[existing_index] = normalized
        else:
            normalized = normalize_workout(payload, builtin=False)
            used = {str(workout.get("id")) for workout in self.workouts}
            base = str(normalized["id"])
            candidate = base
            suffix = 2
            while candidate in used:
                candidate = f"{base}-{suffix}"
                suffix += 1
            normalized["id"] = candidate
            self.workouts.append(normalized)

        save_workouts(WORKOUTS_FILE, self.workouts)
        return normalized

    def delete_workout(self, workout_id: str) -> None:
        before = len(self.workouts)
        self.workouts = [
            workout
            for workout in self.workouts
            if workout.get("id") != workout_id
        ]
        if len(self.workouts) == before:
            raise ValueError("allenamento non trovato")
        if (
            self.active_workout
            and self.active_workout.get("id") == workout_id
        ):
            self.stop_workout()
        save_workouts(WORKOUTS_FILE, self.workouts)

    def start_session(self) -> dict[str, object]:
        """Start a new free recording session without a workout."""
        self._begin_session()
        return self.snapshot()

    def stop_session(self) -> dict[str, object]:
        """Save the current session and leave the recorder stopped."""
        summary = self._close_session()
        state = self.snapshot()
        state["stopped_session"] = summary
        return state

    def start_workout(self, workout_id: str) -> dict[str, object]:
        workout = next(
            (
                item
                for item in self.workouts
                if item.get("id") == workout_id
            ),
            None,
        )
        if workout is None:
            raise ValueError("allenamento non trovato")

        # A structured workout is its own session.  Finalize whatever was
        # being recorded, then start clean so the summary matches the plan.
        self._begin_session()
        self.active_workout = json.loads(json.dumps(workout))
        self.recorder.workout_laps = [
            {
                "label": str(item.get("label") or f"Intervallo {index}"),
                "duration_s": int(item["duration_s"]),
            }
            for index, item in enumerate(
                self.active_workout.get("intervals", []), start=1
            )
        ]
        self._workout_started_at = time.time()
        self._workout_paused_at = None
        self._workout_paused_total = 0.0
        self._workout_status = "running"
        return self.snapshot()

    def pause_workout(self) -> dict[str, object]:
        if not self.active_workout:
            raise ValueError("nessun allenamento attivo")
        if self._workout_status == "running":
            self._workout_paused_at = time.time()
            self._workout_status = "paused"
            self.session_paused = True
        return self.snapshot()

    def resume_workout(self) -> dict[str, object]:
        if not self.active_workout:
            raise ValueError("nessun allenamento attivo")
        if self._workout_status == "paused" and self._workout_paused_at:
            self._workout_paused_total += time.time() - self._workout_paused_at
            self._workout_paused_at = None
            self._workout_status = "running"
            self.session_paused = False
            self.recorder.resume_recording()
        return self.snapshot()

    def stop_workout(self) -> dict[str, object]:
        """Conclude the workout and save its session immediately."""
        return self.stop_session()

    async def set_resistance(self, level: float) -> dict[str, object]:
        level = round(float(level), 1)
        if self.simulate:
            self.control.update(
                connected=True,
                supported=True,
                mode="resistance",
                resistance=level,
                actual_resistance=level,
                target_power=None,
            )
            return self.snapshot()

        if not self.client or not self.client.control_info.get("supported"):
            raise RuntimeError("controllo non disponibile: rullo non connesso")
        info = self.client.control_info.get("resistance")
        if not isinstance(info, dict):
            raise RuntimeError("resistenza non supportata dal rullo")
        minimum = float(info["min"])
        maximum = float(info["max"])
        if not (minimum <= level <= maximum):
            raise ValueError(
                f"resistenza fuori intervallo: {minimum:.1f}-{maximum:.1f}"
            )

        async with self._control_lock:
            ok, message = await self.client.set_resistance(level)
        if not ok:
            raise RuntimeError(message)

        self.control.update(
            {"mode": "resistance", "resistance": level, "target_power": None}
        )
        return self.snapshot()

    async def set_simulation(
        self,
        enabled: bool,
        grade_pct: float = 0.0,
        rolling_resistance: float = 0.005,
        aerodynamic_drag: float = 0.35,
    ) -> dict[str, object]:
        if self.simulate:
            self.control.update(
                connected=True,
                supported=True,
                mode="simulation" if enabled else "resistance",
                simulation=bool(enabled),
                target_power=None,
            )
            return self.snapshot()

        if not self.client or not self.client.control_info.get("supported"):
            raise RuntimeError("controllo non disponibile: rullo non connesso")

        if enabled:
            if not self.client.control_info.get("target_simulation"):
                raise RuntimeError("simulazione strada non supportata dal rullo")
            async with self._control_lock:
                ok, message = await self.client.set_simulation(
                    grade_pct=grade_pct,
                    rolling_resistance=rolling_resistance,
                    aerodynamic_drag=aerodynamic_drag,
                )
            if not ok:
                raise RuntimeError(message)
            self.control.update(
                {
                    "mode": "simulation",
                    "simulation": True,
                    "target_power": None,
                }
            )
            return self.snapshot()

        level = self.control.get("actual_resistance")
        if level is None:
            level = self.control.get("resistance")
        level = float(level) if level is not None else 0.0
        async with self._control_lock:
            ok, message = await self.client.set_resistance(level)
        if not ok:
            raise RuntimeError(message)
        self.control.update(
            {
                "mode": "resistance",
                "simulation": False,
                "resistance": level,
                "target_power": None,
            }
        )
        return self.snapshot()

    async def set_target_power(self, watts: int) -> dict[str, object]:
        watts = int(round(watts))
        if self.simulate:
            self.control.update(
                connected=True,
                supported=True,
                mode="erg",
                target_power=watts,
            )
            return self.snapshot()

        if not self.client or not self.client.control_info.get("supported"):
            raise RuntimeError("controllo non disponibile: rullo non connesso")
        info = self.client.control_info.get("power")
        if not isinstance(info, dict):
            raise RuntimeError("target power non supportato dal rullo")
        minimum = int(info["min"])
        maximum = int(info["max"])
        if not (minimum <= watts <= maximum):
            raise ValueError(f"potenza fuori intervallo: {minimum}-{maximum} W")

        async with self._control_lock:
            ok, message = await self.client.set_target_power(watts)
        if not ok:
            raise RuntimeError(message)

        self.control.update({"mode": "erg", "target_power": watts})
        return self.snapshot()

    def sessions(self) -> list[dict[str, object]]:
        return list_sessions(self.data_dir)


# ---------------------------------------------------------------------------
# API models
# ---------------------------------------------------------------------------
class ConnectRequest(BaseModel):
    address: str = Field(min_length=1)


class ConfigRequest(BaseModel):
    wheel_circumference_mm: int | None = None
    ftp_w: int | None = None
    debug: bool | None = None
    power_offset_w: float | None = Field(default=None, ge=-100, le=300)
    speed_offset_kmh: float | None = Field(default=None, ge=-20, le=20)
    weight_kg: float | None = Field(default=None, ge=30, le=200)


class ResistanceRequest(BaseModel):
    level: float = Field(ge=0, le=20)


class TargetPowerRequest(BaseModel):
    watts: int = Field(ge=0, le=4000)


class SimulationRequest(BaseModel):
    enabled: bool
    grade_pct: float = Field(default=0.0, ge=-10.0, le=20.0)
    rolling_resistance: float = Field(default=0.005, ge=0.001, le=0.02)
    aerodynamic_drag: float = Field(default=0.35, ge=0.1, le=0.8)


class WorkoutIntervalRequest(BaseModel):
    label: str = ""
    duration_s: int = Field(ge=5, le=10800)
    power_w: int = Field(ge=0, le=4000)


class WorkoutRequest(BaseModel):
    id: str | None = None
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=280)
    notes: str | None = Field(default=None, max_length=6000)
    intervals: list[WorkoutIntervalRequest]


class WorkoutStartRequest(BaseModel):
    id: str = Field(min_length=1)


def create_app(args: argparse.Namespace) -> FastAPI:
    manager = Manager(simulate=args.simulate, scan_time=args.scan_time)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        manager.start_tick()
        if args.simulate:
            manager.start_simulation()
        elif args.address:
            asyncio.create_task(manager.connect(args.address))
        elif args.auto:
            asyncio.create_task(manager.autoconnect())
        yield
        await manager.shutdown()

    app = FastAPI(title="Qubo Live", lifespan=lifespan)
    app.state.manager = manager

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(INDEX_HTML)

    @app.get("/mini")
    def mini() -> FileResponse:
        return FileResponse(MINI_HTML)

    @app.get("/piano")
    def piano() -> Response:
        try:
            markdown = PLAN_MD.read_text(encoding="utf-8")
        except OSError as exc:
            raise HTTPException(status_code=404, detail="piano non trovato") from exc
        return Response(
            content=render_plan_page(markdown),
            media_type="text/html; charset=utf-8",
        )

    @app.get("/piano.md")
    def piano_md() -> FileResponse:
        return FileResponse(
            PLAN_MD,
            media_type="text/markdown; charset=utf-8",
            filename="PIANO_ALLENAMENTO.md",
        )

    @app.get("/api/state")
    def state() -> dict[str, object]:
        return manager.snapshot()

    @app.get("/api/devices")
    async def devices() -> dict[str, object]:
        try:
            found = await manager.scan()
        except Exception as exc:  # noqa: BLE001 - message for the UI
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return {"devices": found, "error": None}

    @app.post("/api/connect")
    async def connect(payload: ConnectRequest) -> dict[str, object]:
        await manager.connect(payload.address)
        return manager.snapshot()

    @app.post("/api/disconnect")
    async def disconnect() -> dict[str, object]:
        await manager.disconnect()
        return manager.snapshot()

    @app.post("/api/resistance")
    async def resistance(payload: ResistanceRequest) -> dict[str, object]:
        try:
            return await manager.set_resistance(payload.level)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/erg")
    async def erg(payload: TargetPowerRequest) -> dict[str, object]:
        try:
            return await manager.set_target_power(payload.watts)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/simulation")
    async def simulation(payload: SimulationRequest) -> dict[str, object]:
        try:
            return await manager.set_simulation(
                payload.enabled,
                payload.grade_pct,
                payload.rolling_resistance,
                payload.aerodynamic_drag,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/workouts")
    def workouts() -> dict[str, object]:
        return {"workouts": manager.workout_library()}

    @app.post("/api/workouts")
    def save_workout(payload: WorkoutRequest) -> dict[str, object]:
        try:
            workout = manager.save_workout(payload.model_dump())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "workout": workout_summary(workout),
            "workouts": manager.workout_library(),
        }

    @app.delete("/api/workouts/{workout_id}")
    def delete_workout(workout_id: str) -> dict[str, object]:
        try:
            manager.delete_workout(workout_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"workouts": manager.workout_library()}

    @app.post("/api/workout/start")
    def start_workout(payload: WorkoutStartRequest) -> dict[str, object]:
        try:
            return manager.start_workout(payload.id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/workout/pause")
    def pause_workout() -> dict[str, object]:
        try:
            return manager.pause_workout()
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/workout/resume")
    def resume_workout() -> dict[str, object]:
        try:
            return manager.resume_workout()
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/workout/stop")
    def stop_workout() -> dict[str, object]:
        return manager.stop_workout()

    @app.post("/api/session/start")
    def start_session() -> dict[str, object]:
        return manager.start_session()

    @app.post("/api/session/stop")
    def stop_session() -> dict[str, object]:
        return manager.stop_session()

    @app.post("/api/session/reset")
    def reset() -> dict[str, object]:
        summary = manager.reset_session()
        return {"last_session": summary, "state": manager.snapshot()}

    @app.get("/api/export.csv")
    def export_csv() -> Response:
        if not manager.session_active or not manager.recorder.samples:
            raise HTTPException(status_code=409, detail="nessuna sessione attiva")
        filename = f"qubo-{manager.recorder.session_id}.csv"
        return Response(
            content=manager.recorder.to_csv(),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @app.get("/api/export.tcx")
    def export_tcx() -> Response:
        if not manager.session_active or not manager.recorder.samples:
            raise HTTPException(status_code=409, detail="nessuna sessione attiva")
        filename = f"qubo-{manager.recorder.session_id}.tcx"
        return Response(
            content=manager.recorder.to_tcx(),
            media_type="application/vnd.garmin.tcx+xml",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @app.get("/api/sessions")
    def sessions() -> dict[str, object]:
        return {"sessions": manager.sessions()}

    @app.get("/api/sessions/{name}/tcx")
    def session_tcx(name: str) -> Response:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
            raise HTTPException(status_code=400, detail="nome non valido")
        tcx_path = (manager.data_dir / name).with_suffix(".tcx").resolve()
        if (
            tcx_path.parent == manager.data_dir.resolve()
            and tcx_path.is_file()
        ):
            return FileResponse(
                tcx_path,
                filename=f"qubo-{name}.tcx",
                media_type="application/vnd.garmin.tcx+xml",
            )
        csv_path = (manager.data_dir / name).with_suffix(".csv").resolve()
        if (
            csv_path.parent != manager.data_dir.resolve()
            or not csv_path.is_file()
        ):
            raise HTTPException(status_code=404, detail="sessione non trovata")
        try:
            content = tcx_from_csv(csv_path)
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        filename = f"qubo-{name}.tcx"
        return Response(
            content=content,
            media_type="application/vnd.garmin.tcx+xml",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @app.get("/api/sessions/{name}")
    def session_file(name: str) -> FileResponse:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
            raise HTTPException(status_code=400, detail="nome non valido")
        path = (manager.data_dir / name).resolve()
        if path.parent != manager.data_dir.resolve() or not path.is_file():
            raise HTTPException(status_code=404, detail="file non trovato")
        if path.suffix not in {".csv", ".json", ".tcx"}:
            raise HTTPException(status_code=400, detail="formato non supportato")
        return FileResponse(path, filename=path.name)

    @app.post("/api/config")
    def config(payload: ConfigRequest) -> dict[str, object]:
        manager.update_config(
            payload.wheel_circumference_mm,
            payload.ftp_w,
            payload.debug,
            payload.power_offset_w,
            payload.speed_offset_kmh,
            payload.weight_kg,
        )
        return manager.snapshot()

    @app.websocket("/ws")
    async def websocket(ws: WebSocket) -> None:
        await ws.accept()
        try:
            while True:
                await ws.send_json(manager.snapshot())
                await asyncio.sleep(0.5)
        except (WebSocketDisconnect, RuntimeError):
            return

    return app


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Dashboard locale per Elite Qubo (QD)"
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--simulate",
        action="store_true",
        help="dati finti per provare la dashboard senza sensore",
    )
    parser.add_argument(
        "--address",
        help="indirizzo BLE (o nome) a cui connettersi all'avvio",
    )
    parser.add_argument(
        "--auto",
        action="store_true",
        help="scansiona e connettiti al miglior candidato all'avvio",
    )
    parser.add_argument("--scan-time", type=float, default=6.0)
    parser.add_argument("--log-level", default="info")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    app = create_app(args)
    print()
    print("  Qubo Live  ->  http://%s:%s" % (args.host, args.port))
    if args.simulate:
        print("  modalita' DEMO: dati simulati, nessun Bluetooth richiesto")
    else:
        print("  assicurati che il sensore sia sveglio e non connesso ad altre app")
        print("  (Zwift, My E-Training, ecc.) e concedi il permesso Bluetooth al terminale")
    print()
    import uvicorn

    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        log_level=args.log_level,
        access_log=False,
    )


if __name__ == "__main__":
    main()
