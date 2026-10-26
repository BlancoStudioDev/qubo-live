"""BLE client for the Elite Qubo (BLE name QD) / Misuro B+.

The sensor exposes speed, cadence and estimated power over Bluetooth Smart.
The Qubo also exposes an FTMS Control Point, used here to set resistance and
target power (ERG).  Depending on the firmware it may use the standard Cycling
Speed & Cadence (0x1816), Cycling Power (0x1818) or FTMS (0x1826) profiles, or
a manufacturer-specific service.  Standard profiles are decoded here; any
vendor-specific notification is forwarded as raw hex so it can be inspected
and reverse engineered later.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import struct
import time
from dataclasses import dataclass, field
from typing import Callable, Iterable

from bleak import BleakClient, BleakScanner
from bleak.backends.characteristic import BleakGATTCharacteristic
from bleak.backends.scanner import AdvertisementData

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# UUIDs
# ---------------------------------------------------------------------------
CSC_SERVICE = "00001816-0000-1000-8000-00805f9b34fb"
CSC_MEASUREMENT = "00002a5b-0000-1000-8000-00805f9b34fb"
CPS_SERVICE = "00001818-0000-1000-8000-00805f9b34fb"
CPS_MEASUREMENT = "00002a63-0000-1000-8000-00805f9b34fb"
FTMS_SERVICE = "00001826-0000-1000-8000-00805f9b34fb"
FTMS_INDOOR_BIKE = "00002ad2-0000-1000-8000-00805f9b34fb"
FTMS_FEATURE = "00002acc-0000-1000-8000-00805f9b34fb"
FTMS_CONTROL_POINT = "00002ad9-0000-1000-8000-00805f9b34fb"
FTMS_RESISTANCE_RANGE = "00002ad6-0000-1000-8000-00805f9b34fb"
FTMS_POWER_RANGE = "00002ad8-0000-1000-8000-00805f9b34fb"

# FTMS Control Point result codes (response opcode 0x80).
FTMS_RESULTS = {
    0x01: "ok",
    0x02: "operazione non supportata",
    0x03: "parametro non valido",
    0x04: "operazione fallita",
    0x05: "controllo non consentito",
}

DEVICE_INFO_SERVICE = "0000180a-0000-1000-8000-00805f9b34fb"
BATTERY_SERVICE = "0000180f-0000-1000-8000-00805f9b34fb"
DFU_SERVICE = "00001530-1212-efde-1523-785feabcd123"

MANUFACTURER_NAME = "00002a29-0000-1000-8000-00805f9b34fb"
MODEL_NUMBER = "00002a24-0000-1000-8000-00805f9b34fb"
SERIAL_NUMBER = "00002a25-0000-1000-8000-00805f9b34fb"
FIRMWARE_REVISION = "00002a26-0000-1000-8000-00805f9b34fb"
HARDWARE_REVISION = "00002a27-0000-1000-8000-00805f9b34fb"
BATTERY_LEVEL = "00002a19-0000-1000-8000-00805f9b34fb"

PROFILE_NAMES = {
    "1816": "CSC (velocita/cadenza)",
    "1818": "Cycling Power",
    "1826": "FTMS",
    "180a": "Device Info",
    "180f": "Battery",
}

KNOWN_STANDARD_SERVICES = {"1800", "1801", "180a", "180f", "1816", "1818", "1826"}


def short_uuid(uuid: str) -> str:
    """Return the 16-bit short form for standard Bluetooth UUIDs."""
    value = str(uuid).lower()
    if len(value) == 4:
        return value
    if value.startswith("0000") and value.endswith("-0000-1000-8000-00805f9b34fb"):
        return value[4:8]
    return value


def is_standard_service(uuid: str) -> bool:
    return short_uuid(uuid) in KNOWN_STANDARD_SERVICES


@dataclass
class DeviceInfo:
    address: str
    name: str
    rssi: int
    service_uuids: list[str] = field(default_factory=list)
    manufacturer_data: dict[int, str] = field(default_factory=dict)

    @property
    def profiles(self) -> list[str]:
        return [
            PROFILE_NAMES[short_uuid(u)]
            for u in self.service_uuids
            if short_uuid(u) in PROFILE_NAMES
        ]

    def score(self) -> int:
        score = 0
        name = self.name.lower()
        if any(key in name for key in ("elite", "qubo", "misuro")):
            score += 100
        shorts = {short_uuid(u) for u in self.service_uuids}
        if "1818" in shorts:
            score += 40
        if "1816" in shorts:
            score += 35
        if "1826" in shorts:
            score += 30
        if "180a" in shorts:
            score += 5
        return score

    def to_dict(self) -> dict:
        return {
            "address": self.address,
            "name": self.name or "(senza nome)",
            "rssi": self.rssi,
            "profiles": self.profiles,
            "service_uuids": [short_uuid(u) for u in self.service_uuids],
            "score": self.score(),
        }


async def scan_devices(timeout: float = 6.0) -> list[DeviceInfo]:
    """Scan for BLE peripherals. Raises on missing Bluetooth permission."""
    result = await asyncio.wait_for(
        BleakScanner.discover(timeout=timeout, return_adv=True),
        timeout=timeout + 5,
    )
    devices: list[DeviceInfo] = []
    for address, (_device, adv) in result.items():
        devices.append(_from_adv(address, adv))
    devices.sort(key=lambda d: (d.score(), d.rssi), reverse=True)
    return devices


def _from_adv(address: str, adv: AdvertisementData) -> DeviceInfo:
    manufacturer = {
        str(key): bytes(value).hex() for key, value in adv.manufacturer_data.items()
    }
    return DeviceInfo(
        address=address,
        name=adv.local_name or "",
        rssi=adv.rssi,
        service_uuids=list(adv.service_uuids or []),
        manufacturer_data=manufacturer,
    )


# ---------------------------------------------------------------------------
# Revolution based speed / cadence
# ---------------------------------------------------------------------------
class RevolutionTracker:
    """Turns cumulative revolution counters into a rate.

    ANT+ / BLE sensors send the cumulative number of wheel (or crank)
    revolutions together with the timestamp of the last revolution in units
    of 1/1024 s.  Both counters wrap, so deltas must be computed modulo.
    """

    def __init__(self) -> None:
        self._last: tuple[int, int] | None = None

    def update(
        self, revolutions: int, event_time: int, revolution_modulus: int
    ) -> tuple[int, float] | None:
        if self._last is None:
            self._last = (revolutions, event_time)
            return None

        prev_revs, prev_time = self._last
        if revolutions == prev_revs and event_time == prev_time:
            return None

        dt = ((event_time - prev_time) & 0xFFFF) / 1024.0
        if dt <= 0 or dt > 5.0:
            # First sample after a pause or a counter reset: re-baseline.
            self._last = (revolutions, event_time)
            return None

        delta_revs = (revolutions - prev_revs) % revolution_modulus
        self._last = (revolutions, event_time)

        if delta_revs == 0 or delta_revs > revolution_modulus / 2:
            return None
        return delta_revs, dt


def decode_csc(
    data: bytes,
    wheel: RevolutionTracker,
    crank: RevolutionTracker,
    wheel_circumference_m: float,
) -> dict[str, float]:
    """Cycling Speed and Cadence measurement (0x2A5B)."""
    if len(data) < 1:
        return {}
    flags = data[0]
    offset = 1
    values: dict[str, float] = {}

    if flags & 0x01:
        if len(data) < offset + 6:
            return values
        revs = struct.unpack_from("<I", data, offset)[0]
        event_time = struct.unpack_from("<H", data, offset + 4)[0]
        offset += 6
        delta = wheel.update(revs, event_time, 2**32)
        if delta:
            values["speed_kmh"] = delta[0] * wheel_circumference_m / delta[1] * 3.6

    if flags & 0x02:
        if len(data) < offset + 4:
            return values
        revs = struct.unpack_from("<H", data, offset)[0]
        event_time = struct.unpack_from("<H", data, offset + 2)[0]
        delta = crank.update(revs, event_time, 2**16)
        if delta:
            values["cadence_rpm"] = delta[0] / delta[1] * 60.0

    return values


def decode_cps(
    data: bytes,
    wheel: RevolutionTracker,
    crank: RevolutionTracker,
    wheel_circumference_m: float,
) -> dict[str, float]:
    """Cycling Power measurement (0x2A63)."""
    if len(data) < 4:
        return {}
    flags = struct.unpack_from("<H", data, 0)[0]
    values: dict[str, float] = {
        "power_w": float(struct.unpack_from("<h", data, 2)[0])
    }
    offset = 4

    if flags & 0x0001:  # pedal power balance
        offset += 1
    if flags & 0x0004:  # accumulated torque
        offset += 2
    if flags & 0x0010:  # wheel revolution data
        if len(data) < offset + 6:
            return values
        revs = struct.unpack_from("<I", data, offset)[0]
        event_time = struct.unpack_from("<H", data, offset + 4)[0]
        offset += 6
        delta = wheel.update(revs, event_time, 2**32)
        if delta:
            values["speed_kmh"] = delta[0] * wheel_circumference_m / delta[1] * 3.6
    if flags & 0x0020:  # crank revolution data
        if len(data) < offset + 4:
            return values
        revs = struct.unpack_from("<H", data, offset)[0]
        event_time = struct.unpack_from("<H", data, offset + 2)[0]
        delta = crank.update(revs, event_time, 2**16)
        if delta:
            values["cadence_rpm"] = delta[0] / delta[1] * 60.0

    return values


def ftms_spec_size(flags: int) -> int:
    """Length of a spec-compliant Indoor Bike Data frame for these flags."""
    size = 2
    if not flags & 0x0001:
        size += 2  # instantaneous speed
    if flags & 0x0002:
        size += 2  # average speed
    if flags & 0x0004:
        size += 2  # instantaneous cadence
    if flags & 0x0008:
        size += 2  # average cadence
    if flags & 0x0010:
        size += 3  # total distance (uint24)
    if flags & 0x0020:
        size += 2  # resistance level
    if flags & 0x0040:
        size += 2  # instantaneous power
    if flags & 0x0080:
        size += 2  # average power
    if flags & 0x0100:
        size += 2  # total energy
    if flags & 0x0200:
        size += 2  # energy per hour
    if flags & 0x0400:
        size += 1  # energy per minute
    if flags & 0x0800:
        size += 1  # heart rate
    if flags & 0x1000:
        size += 1  # metabolic equivalent
    if flags & 0x2000:
        size += 2  # elapsed time
    if flags & 0x4000:
        size += 2  # remaining time
    return size


def decode_ftms(data: bytes) -> dict[str, float]:
    """FTMS Indoor Bike Data (0x2AD2).

    The Qubo firmware (flags 0x0874) sets the heart-rate bit but appends a
    16-bit elapsed-time counter instead of the one-byte heart rate.  When the
    frame is exactly one byte longer than the specification, that trailing
    word is decoded as elapsed time.  All the fields we use for the dashboard
    come before it, so a firmware variation here can never shift the power.
    """
    if len(data) < 2:
        return {}
    flags = struct.unpack_from("<H", data, 0)[0]
    offset = 2
    values: dict[str, float] = {}
    elite_elapsed = bool(flags & 0x0800) and len(data) == ftms_spec_size(flags) + 1

    def take(count: int) -> bytes:
        nonlocal offset
        if offset + count > len(data):
            raise struct.error("truncated FTMS frame")
        chunk = data[offset : offset + count]
        offset += count
        return chunk

    def u8() -> int:
        return take(1)[0]

    def u16() -> int:
        return struct.unpack("<H", take(2))[0]

    def i16() -> int:
        return struct.unpack("<h", take(2))[0]

    def u24() -> int:
        raw = take(3)
        return raw[0] | (raw[1] << 8) | (raw[2] << 16)

    try:
        if not flags & 0x0001:  # instantaneous speed present
            values["speed_kmh"] = u16() / 100.0
        if flags & 0x0002:  # average speed
            values["avg_speed_kmh"] = u16() / 100.0
        if flags & 0x0004:  # instantaneous cadence (uint16, 0.5 rpm)
            raw = u16()
            values["cadence_rpm"] = raw / 2.0
            values["cadence_raw"] = float(raw)
        if flags & 0x0008:  # average cadence (uint16, 0.5 rpm)
            values["avg_cadence_rpm"] = u16() / 2.0
        if flags & 0x0010:  # total distance (uint24)
            values["distance_m"] = float(u24())
        if flags & 0x0020:  # resistance level (0.1 units, same scale as 2AD6)
            values["resistance_level"] = i16() / 10.0
        if flags & 0x0040:  # instantaneous power
            values["power_w"] = float(i16())
        if flags & 0x0080:  # average power
            values["avg_power_w"] = float(i16())
        if flags & 0x0100:  # total energy (kcal)
            values["total_energy_kcal"] = float(u16())
        if flags & 0x0200:  # energy per hour (kcal)
            values["energy_per_hour_kcal"] = float(u16())
        if flags & 0x0400:  # energy per minute (kcal)
            values["energy_per_minute_kcal"] = float(u8())
        if flags & 0x0800:
            if elite_elapsed:
                values["elapsed_s"] = float(u16())
            else:
                values["heart_rate_bpm"] = float(u8())
        if flags & 0x1000:  # metabolic equivalent (0.1)
            values["metabolic_equivalent"] = u8() / 10.0
        if flags & 0x2000:  # elapsed time
            values["elapsed_s"] = float(u16())
        if flags & 0x4000:  # remaining time
            values["remaining_s"] = float(u16())
    except struct.error:
        # Keep the fields decoded before the truncated one.
        log.debug("Truncated FTMS frame %s", data.hex())
    return values


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------
StatusCallback = Callable[[str, str], None]
MetricsCallback = Callable[[str, dict[str, float]], None]
RawCallback = Callable[[str, bytes], None]


def _empty_control_info() -> dict[str, object]:
    return {
        "supported": False,
        "control_point": False,
        "target_resistance": False,
        "target_power": False,
        "target_simulation": False,
        "write_response": True,
        "resistance": None,
        "power": None,
        "features": {},
    }


class TrainerClient:
    def __init__(
        self,
        on_metrics: MetricsCallback,
        on_status: StatusCallback | None = None,
        on_raw: RawCallback | None = None,
        wheel_circumference_m: float = 0.094,
        debug_vendor: bool = True,
    ) -> None:
        self.on_metrics = on_metrics
        self.on_status = on_status or (lambda _status, _detail: None)
        self.on_raw = on_raw or (lambda _uuid, _data: None)
        self.wheel_circumference_m = wheel_circumference_m
        self.debug_vendor = debug_vendor

        self.client: BleakClient | None = None
        self.device_info: dict[str, object] = {}
        self.battery: int | None = None
        self.subscribed: list[str] = []

        # FTMS control point (0x2AD9): resistance / target power commands.
        self.control_point: BleakGATTCharacteristic | None = None
        self.control_info: dict[str, object] = _empty_control_info()
        self._control_queue: asyncio.Queue[bytes] = asyncio.Queue()
        self._has_control = False

        self._wheel_csc = RevolutionTracker()
        self._crank_csc = RevolutionTracker()
        self._wheel_cps = RevolutionTracker()
        self._crank_cps = RevolutionTracker()
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._disconnected = asyncio.Event()
        self._address: str | None = None

    # -- lifecycle ---------------------------------------------------------
    async def connect(self, address: str) -> None:
        await self.disconnect()
        self._address = address
        self._stop = asyncio.Event()
        self._task = asyncio.create_task(self._run(address), name="ble-trainer")

    async def disconnect(self) -> None:
        self._stop.set()
        self._disconnected.set()
        client = self.client
        if client is not None:
            with contextlib.suppress(Exception):
                await client.disconnect()
        task = self._task
        self._task = None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self.client = None

    async def _run(self, address: str) -> None:
        attempt = 0
        while not self._stop.is_set():
            try:
                self.on_status("connecting", f"connessione a {address}")
                self._disconnected.clear()
                async with BleakClient(
                    address,
                    timeout=20.0,
                    disconnected_callback=self._on_disconnect,
                ) as client:
                    self.client = client
                    attempt = 0
                    await self._setup(client)
                    self.on_status("connected", "in ascolto")
                    await self._disconnected.wait()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - surfaced to the UI
                log.warning("BLE connection failed: %s", exc)
                self.on_status("error", _friendly_error(exc))
            finally:
                self.client = None
                self.control_point = None
                self.control_info = _empty_control_info()
                self._has_control = False
                self._wheel_csc = RevolutionTracker()
                self._crank_csc = RevolutionTracker()
                self._wheel_cps = RevolutionTracker()
                self._crank_cps = RevolutionTracker()

            if self._stop.is_set():
                break
            attempt += 1
            delay = min(2 * attempt, 10)
            self.on_status("reconnecting", f"nuovo tentativo tra {delay}s")
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._stop.wait(), timeout=delay)

    def _on_disconnect(self, _client: BleakClient) -> None:
        self._disconnected.set()

    # -- discovery / subscriptions ----------------------------------------
    async def _setup(self, client: BleakClient) -> None:
        services = client.services
        characteristics = list(services.characteristics.values())

        self.device_info = await self._read_device_info(client)
        self.battery = await self._read_battery(client)
        self.subscribed = []
        self.control_info = _empty_control_info()
        self.control_point = None
        self._has_control = False

        for char in characteristics:
            props = {prop.lower() for prop in char.properties}
            if not ({"notify", "indicate"} & props):
                continue
            uuid = short_uuid(char.uuid)
            is_measurement = uuid in {"2a5b", "2a63", "2ad2"}
            is_vendor = (
                not is_standard_service(char.service_uuid)
                and char.service_uuid.lower() != DFU_SERVICE
            )
            if not (is_measurement or (self.debug_vendor and is_vendor)):
                continue
            try:
                await client.start_notify(char, self._handler(char))
                self.subscribed.append(uuid)
            except Exception as exc:  # noqa: BLE001 - optional debug streams
                log.debug("Cannot subscribe to %s: %s", uuid, exc)

        await self._setup_control(client, characteristics)

        log.info(
            "Connected to %s (%s), streams: %s",
            self.device_info.get("name") or self.device_info.get("model") or "?",
            self.device_info.get("address", "?"),
            ", ".join(self.subscribed) or "nessuno",
        )

    async def _setup_control(
        self, client: BleakClient, characteristics: list[BleakGATTCharacteristic]
    ) -> None:
        """Discover the FTMS control point and the supported command ranges."""
        control = next(
            (c for c in characteristics if short_uuid(c.uuid) == "2ad9"), None
        )
        if control is None:
            return

        props = {prop.lower() for prop in control.properties}
        self.control_info["control_point"] = True
        self.control_info["write_response"] = "write" in props
        if not ({"write", "write-without-response"} & props):
            return
        if not ({"indicate", "notify"} & props):
            return
        try:
            await client.start_notify(control, self._on_control)
        except Exception as exc:  # noqa: BLE001 - optional control point
            log.debug("Cannot subscribe to FTMS control point: %s", exc)
            return

        self.control_point = control
        self.subscribed.append("2ad9")

        feature = await _read(client, FTMS_FEATURE)
        if feature and len(feature) >= 8:
            fitness, target = struct.unpack_from("<II", feature, 0)
            self.control_info["features"] = {"fitness": fitness, "target": target}
            self.control_info["target_resistance"] = bool(target & (1 << 2))
            self.control_info["target_power"] = bool(target & (1 << 3))
            self.control_info["target_simulation"] = bool(target & (1 << 13))

        resistance = await _read(client, FTMS_RESISTANCE_RANGE)
        if resistance and len(resistance) >= 6:
            minimum, maximum, increment = struct.unpack_from("<hhH", resistance, 0)
            self.control_info["resistance"] = {
                "min": minimum / 10.0,
                "max": maximum / 10.0,
                "step": increment / 10.0,
            }

        power = await _read(client, FTMS_POWER_RANGE)
        if power and len(power) >= 6:
            minimum, maximum, increment = struct.unpack_from("<hhH", power, 0)
            self.control_info["power"] = {
                "min": float(minimum),
                "max": float(maximum),
                "step": float(increment),
            }

        # If the feature characteristic is absent, trust the range
        # characteristic.  If it is present, honour its target bits.
        features = self.control_info["features"]
        target_bits_known = bool(features)
        can_resistance = self.control_info["resistance"] is not None and (
            not target_bits_known or bool(self.control_info["target_resistance"])
        )
        can_power = self.control_info["power"] is not None and (
            not target_bits_known or bool(self.control_info["target_power"])
        )
        self.control_info["supported"] = bool(can_resistance or can_power)

        log.info(
            "FTMS control: point=%s resistance=%s power=%s",
            "yes" if self.control_point else "no",
            self.control_info["resistance"],
            self.control_info["power"],
        )

    def _on_control(self, _sender, data: bytearray) -> None:
        raw = bytes(data)
        self.on_raw("2ad9", raw)
        self._control_queue.put_nowait(raw)

    async def _send_control_raw(
        self, payload: bytes, opcode: int, timeout: float = 5.0
    ) -> tuple[bool, str]:
        """Write one FTMS Control Point command and wait for its indication."""
        client = self.client
        if client is None or self.control_point is None:
            return False, "controllo non disponibile"

        while not self._control_queue.empty():
            with contextlib.suppress(asyncio.QueueEmpty):
                self._control_queue.get_nowait()

        try:
            await client.write_gatt_char(
                self.control_point,
                payload,
                response=bool(self.control_info.get("write_response", True)),
            )
        except Exception as exc:  # noqa: BLE001 - surfaced to the caller
            return False, _friendly_error(exc)

        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False, "nessuna risposta dal rullo"
            try:
                response = await asyncio.wait_for(
                    self._control_queue.get(), timeout=remaining
                )
            except asyncio.TimeoutError:
                return False, "nessuna risposta dal rullo"
            if len(response) < 3 or response[0] != 0x80 or response[1] != opcode:
                continue
            result = response[2]
            if result == 0x01:
                return True, "ok"
            if result == 0x05:
                self._has_control = False
            return False, FTMS_RESULTS.get(result, f"errore 0x{result:02x}")

    async def request_control(self) -> tuple[bool, str]:
        if self._has_control:
            return True, "ok"
        ok, message = await self._send_control_raw(bytes([0x00]), 0x00)
        if ok:
            self._has_control = True
        return ok, message

    async def start_resume(self) -> tuple[bool, str]:
        """Start/resume the FTMS machine.

        On the Qubo Digital the target resistance is acknowledged even when
        the machine is stopped, but the brake only moves after Start/Resume.
        The firmware answers 0x04 ("operation failed") when it is already
        running, so treat that as a successful start.
        """
        ok, message = await self._send_control_raw(bytes([0x07]), 0x07)
        if ok:
            return True, "ok"
        if message == "operazione fallita":
            return True, "già avviato"
        return False, message

    async def set_resistance(self, level: float) -> tuple[bool, str]:
        """Set the FTMS target resistance level (unit: 0.1, range 0-20)."""
        info = self.control_info.get("resistance")
        if not isinstance(info, dict):
            return False, "resistenza non supportata"
        minimum = float(info["min"])
        maximum = float(info["max"])
        level = max(minimum, min(maximum, float(level)))
        ok, message = await self.request_control()
        if not ok:
            return ok, message
        ok, message = await self.start_resume()
        if not ok:
            return ok, message
        raw_value = int(round(level * 10))
        return await self._send_control_raw(
            struct.pack("<BH", 0x04, raw_value), 0x04
        )

    async def set_simulation(
        self,
        wind_speed_m_s: float = 0.0,
        grade_pct: float = 0.0,
        rolling_resistance: float = 0.005,
        aerodynamic_drag: float = 0.35,
    ) -> tuple[bool, str]:
        """Set FTMS indoor bike simulation parameters (flat road by default).

        Payload: opcode 0x11, wind speed (0.001 m/s), grade (0.01 %),
        rolling resistance (0.0001), aerodynamic drag (0.01 kg/m).
        """
        if not self.control_info.get("target_simulation"):
            return False, "simulazione non supportata"
        wind = max(-32768, min(32767, int(round(wind_speed_m_s * 1000))))
        grade = max(-32768, min(32767, int(round(grade_pct * 100))))
        crr = max(0, min(255, int(round(rolling_resistance * 10000))))
        cda = max(0, min(255, int(round(aerodynamic_drag * 100))))
        ok, message = await self.request_control()
        if not ok:
            return ok, message
        ok, message = await self.start_resume()
        if not ok:
            return ok, message
        return await self._send_control_raw(
            struct.pack("<BhhBB", 0x11, wind, grade, crr, cda), 0x11
        )

    async def set_target_power(self, watts: int) -> tuple[bool, str]:
        """Set the FTMS target power in ERG mode (unit: 1 W)."""
        info = self.control_info.get("power")
        if not isinstance(info, dict):
            return False, "target power non supportato"
        minimum = int(info["min"])
        maximum = int(info["max"])
        value = max(minimum, min(maximum, int(round(watts))))
        ok, message = await self.request_control()
        if not ok:
            return ok, message
        ok, message = await self.start_resume()
        if not ok:
            return ok, message
        return await self._send_control_raw(
            struct.pack("<Bh", 0x05, value), 0x05
        )

    def _handler(self, char: BleakGATTCharacteristic):
        uuid = short_uuid(char.uuid)

        def callback(_sender, data: bytearray) -> None:
            raw = bytes(data)
            self.on_raw(uuid, raw)
            try:
                values = self._decode(uuid, raw)
            except (struct.error, IndexError) as exc:
                log.debug("Malformed %s frame %s: %s", uuid, raw.hex(), exc)
                return
            if values:
                self.on_metrics(uuid, values)

        return callback

    def _decode(self, uuid: str, data: bytes) -> dict[str, float]:
        if uuid == "2a5b":
            return decode_csc(
                data, self._wheel_csc, self._crank_csc, self.wheel_circumference_m
            )
        if uuid == "2a63":
            return decode_cps(
                data, self._wheel_cps, self._crank_cps, self.wheel_circumference_m
            )
        if uuid == "2ad2":
            return decode_ftms(data)
        return {}

    async def _read_device_info(self, client: BleakClient) -> dict[str, object]:
        info: dict[str, object] = {"address": self._address}
        fields = {
            "manufacturer": MANUFACTURER_NAME,
            "model": MODEL_NUMBER,
            "serial": SERIAL_NUMBER,
            "firmware": FIRMWARE_REVISION,
            "hardware": HARDWARE_REVISION,
        }
        for label, uuid in fields.items():
            text = await _read_text(client, uuid)
            if text:
                info[label] = text
        return info

    async def _read_battery(self, client: BleakClient) -> int | None:
        data = await _read(client, BATTERY_LEVEL)
        if data:
            return int(data[0])
        return None


async def _read(client: BleakClient, uuid: str) -> bytes | None:
    try:
        return bytes(await client.read_gatt_char(uuid))
    except Exception:  # noqa: BLE001 - optional characteristic
        return None


async def _read_text(client: BleakClient, uuid: str) -> str | None:
    data = await _read(client, uuid)
    if not data:
        return None
    text = data.decode("utf-8", errors="replace").strip("\x00 ")
    return text or None


def _friendly_error(exc: Exception) -> str:
    message = str(exc)
    lowered = message.lower()
    if "not authorized" in lowered or "denied" in lowered:
        return (
            "Bluetooth non autorizzato: concedi il permesso al terminale in "
            "Impostazioni di Sistema > Privacy e Sicurezza > Bluetooth."
        )
    if "not found" in lowered or "no device" in lowered:
        return "Dispositivo non trovato: verifica che il sensore sia sveglio e vicino."
    if "busy" in lowered or "in progress" in lowered:
        return "Connessione gia' in corso: chiudi Zwift/My E-Training e riprova."
    return message or exc.__class__.__name__


def merge_metrics(*parts: dict[str, float]) -> dict[str, float]:
    merged: dict[str, float] = {}
    for part in parts:
        merged.update(part)
    return merged


def now_ms() -> int:
    return int(time.time() * 1000)


def format_services(service_uuids: Iterable[str]) -> str:
    return ", ".join(short_uuid(u) for u in service_uuids) or "-"
