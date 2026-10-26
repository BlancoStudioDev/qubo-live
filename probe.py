#!/usr/bin/env python3
"""Probe for the Elite Qubo (BLE name QD) / Misuro B+.

Examples:
    .venv/bin/python scripts/trainer/probe.py --mode ble
    .venv/bin/python scripts/trainer/probe.py --mode ble --connect "Qubo"
    .venv/bin/python scripts/trainer/probe.py --mode ant --id 11029

ANT+ is not Bluetooth: on macOS it only works with a USB ANT+ stick
(Garmin/Dynastream). Without one, use the BLE mode: the same sensor is
dual-mode and exposes its data over Bluetooth Smart.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import threading
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from ble_client import (  # noqa: E402
    PROFILE_NAMES,
    TrainerClient,
    format_services,
    scan_devices,
    short_uuid,
)

KNOWN_MEASUREMENTS = {"2a5b", "2a63", "2ad2"}
SOURCE_LABELS = {"2a63": "CPS", "2ad2": "FTMS", "2a5b": "CSC"}


def run_ble(args: argparse.Namespace) -> int:
    try:
        devices = asyncio.run(scan_devices(args.time if args.json else 6.0))
    except Exception as exc:  # noqa: BLE001 - CLI
        print(f"Scansione BLE fallita: {exc}")
        print(
            "Se sei su macOS: Impostazioni di Sistema > Privacy e Sicurezza > "
            "Bluetooth e abilita il terminale che esegue lo script."
        )
        return 1

    if args.json:
        print(json.dumps([d.to_dict() for d in devices], indent=2))
        return 0

    if not devices:
        print("Nessun dispositivo BLE trovato.")
        return 1

    print(f"\nDispositivi BLE trovati ({len(devices)}):\n")
    for index, device in enumerate(devices):
        profiles = ", ".join(device.profiles) or "-"
        print(
            f"  [{index:2d}] {device.name or '(senza nome)':34s} "
            f"{device.rssi:4d} dBm  {device.address}"
        )
        print(f"       servizi: {format_services(device.service_uuids)}")
        if any(short_uuid(u) in PROFILE_NAMES for u in device.service_uuids):
            print(f"       profili: {profiles}")

    target = select_device(devices, args.connect)
    if target is None:
        print(
            "\nNessun candidato riconosciuto. Rilancia con "
            '--connect "nome o indirizzo" oppure --json per vedere tutto.'
        )
        return 1

    print(f"\nConnessione a {target.name or target.address} ({target.address})...")
    return asyncio.run(listen_ble(target.address, args))


def select_device(devices, query: str | None):
    if not query:
        candidates = [d for d in devices if d.score() > 0]
        return candidates[0] if candidates else None
    query = query.lower()
    for device in devices:
        if query in device.address.lower() or query in device.name.lower():
            return device
    return None


async def listen_ble(address: str, args: argparse.Namespace) -> int:
    connected = asyncio.Event()
    latest: dict[str, float] = {}
    maximum: dict[str, float] = {}
    raw_seen: dict[str, int] = {}
    last_print = 0.0

    def on_status(status: str, detail: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        print(f"  [{stamp}] {status}: {detail}")
        if status == "connected":
            connected.set()

    def on_metrics(source: str, values: dict[str, float]) -> None:
        nonlocal last_print
        latest.update(values)
        for key, value in values.items():
            maximum[key] = max(maximum.get(key, 0.0), value)
        now = time.monotonic()
        if now - last_print < 0.5:
            return
        last_print = now
        print(f"  [{SOURCE_LABELS.get(source, source)}] " + format_values(values))

    def on_raw(uuid: str, data: bytes) -> None:
        if uuid in KNOWN_MEASUREMENTS and not args.raw:
            return
        count = raw_seen.get(uuid, 0) + 1
        raw_seen[uuid] = count
        if count <= args.max_raw:
            print(f"  raw {uuid} [{len(data):2d}] {data.hex(' ')}")

    client = TrainerClient(
        on_metrics=on_metrics,
        on_status=on_status,
        on_raw=on_raw,
        wheel_circumference_m=args.wheel / 1000.0,
    )
    await client.connect(address)

    try:
        await asyncio.wait_for(connected.wait(), timeout=args.connect_timeout)
    except asyncio.TimeoutError:
        print("\nConnessione non riuscita entro il timeout.")
        await client.disconnect()
        return 1

    await asyncio.sleep(0.4)
    print("\nDispositivo:")
    for key, value in client.device_info.items():
        print(f"  {key:13s} {value}")
    if client.battery is not None:
        print(f"  {'battery':13s} {client.battery}%")
    print(f"  {'stream':13s} {', '.join(client.subscribed) or 'nessuno'}")

    if client.client is not None:
        print("\nServizi e caratteristiche:")
        for service in client.client.services:
            print(f"  {short_uuid(service.uuid)}  {service.description}")
            for char in service.characteristics:
                props = ",".join(char.properties)
                print(f"    {short_uuid(char.uuid)}  {props:28s} {char.description}")

    if not client.subscribed:
        print(
            "\nNessuna caratteristica di misura notificata: il sensore usa "
            "probabilmente un protocollo proprietario (vedi i frame raw sopra)."
        )

    print(f"\nAscolto per {args.listen:.0f}s — pedala!")
    print("  " + " " * 4 + "(Ctrl-C per terminare)\n")
    try:
        await asyncio.sleep(args.listen)
    except asyncio.CancelledError:
        pass
    finally:
        await client.disconnect()

    print("\nRiepilogo:")
    if latest:
        print("  ultimi valori: " + format_values(latest))
        print(
            "  massimi:       "
            + format_values({key: maximum.get(key, 0.0) for key in latest})
        )
    else:
        print("  nessun dato ricevuto.")
    if raw_seen:
        print("  frame grezzi:  " + ", ".join(f"{k}={v}" for k, v in raw_seen.items()))
    return 0


def format_values(values: dict[str, float]) -> str:
    parts = []
    if "power_w" in values:
        parts.append(f"potenza {values['power_w']:5.0f} W")
    if "speed_kmh" in values:
        parts.append(f"velocita {values['speed_kmh']:5.1f} km/h")
    if "cadence_rpm" in values:
        parts.append(f"cadenza {values['cadence_rpm']:5.0f} rpm")
    return "  ".join(parts) if parts else "(in attesa)"


# ---------------------------------------------------------------------------
# ANT+
# ---------------------------------------------------------------------------
def has_ant_stick() -> bool:
    try:
        import usb.core

        return any(
            device.idVendor == 0x0FCF
            for device in usb.core.find(find_all=True) or []
        )
    except Exception:  # noqa: BLE001 - pyusb or libusb missing
        return False


def run_ant(args: argparse.Namespace) -> int:
    try:
        from openant.devices import ANTPLUS_NETWORK_KEY
        from openant.devices.common import DeviceType
        from openant.devices.scanner import Scanner
        from openant.devices.utilities import auto_create_device
        from openant.easy.node import Node
    except ImportError:
        print(
            "openant non installato. Serve per parlare ANT+:\n"
            "  python -m pip install openant\n"
            "Su macOS serve anche libusb:  brew install libusb"
        )
        return 1

    try:
        node = Node()
    except Exception as exc:  # noqa: BLE001 - no stick connected
        print(f"Chiavetta ANT+ non disponibile: {exc}")
        print(
            "Su questo Mac non risulta collegata una chiavetta USB ANT+ "
            "(Garmin/Dynastream). Il protocollo ANT+ non e' disponibile via "
            "Bluetooth: usa --mode ble con lo stesso sensore."
        )
        return 1

    node.set_network_key(0x00, ANTPLUS_NETWORK_KEY)
    scanner = Scanner(node, device_id=args.id)
    found: dict[tuple[int, int, int], object] = {}
    profiles: list[object] = []
    state = {"updates": 0}

    def on_found(device: tuple[int, int, int]) -> None:
        device_id, device_type, transmission = device
        name = DeviceType(device_type).name
        print(
            f"  TROVATO  #{device_id}  tipo {device_type} ({name})  "
            f"transmission {transmission}"
        )
        found[device] = True
        if len(profiles) >= 3:
            return
        try:
            profile = auto_create_device(
                node, device_id, device_type, transmission
            )
        except ValueError:
            print(f"  profilo non supportato per il tipo {device_type}")
            return
        profile.on_device_data = (
            lambda _profile, page_name, data, profile=profile: print(
                f"  {profile} {page_name}: {data}"
            )
        )
        profiles.append(profile)

    def on_update(device: tuple[int, int, int], common) -> None:
        state["updates"] += 1
        print(
            f"  comuni   #{device[0]}  serial={common.serial_no} "
            f"manufacturer={common.manufacturer_id} model={common.model_no}"
        )

    scanner.on_found = on_found
    scanner.on_update = on_update

    print(
        f"Scansione ANT+ per device #{args.id} "
        f"(tipo 0 = tutti) per {args.listen:.0f}s...\n"
    )
    thread = threading.Thread(target=node.start, name="ant-node", daemon=True)
    thread.start()

    try:
        deadline = time.monotonic() + args.listen
        while time.monotonic() < deadline:
            time.sleep(0.25)
            if state["updates"] and not found:
                pass
    except KeyboardInterrupt:
        print("\nInterrotto.")
    finally:
        try:
            scanner.close_channel()
        except Exception:  # noqa: BLE001 - best effort cleanup
            pass
        for profile in profiles:
            try:
                profile.close_channel()
            except Exception:  # noqa: BLE001 - best effort cleanup
                pass
        node.stop()

    if not found:
        print(
            f"\nDevice ANT+ #{args.id} non trovato. Verifica che il sensore "
            "sia sveglio (pedala due giri) e vicino alla chiavetta."
        )
        return 1
    print(f"\nDevice ANT+ #{args.id} trovato e in ascolto.")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Trova e connetti il sensore Elite (ANT+ / BLE)"
    )
    parser.add_argument(
        "--mode",
        choices=["auto", "ble", "ant"],
        default="auto",
        help="auto: usa ANT+ se c'e' una chiavetta USB, altrimenti BLE",
    )
    parser.add_argument("--id", type=int, default=11029, help="ANT+ device number")
    parser.add_argument("--connect", help="nome o indirizzo BLE da connettere")
    parser.add_argument("--listen", type=float, default=20.0, help="secondi di ascolto")
    parser.add_argument("--time", type=float, default=6.0, help="durata scansione BLE")
    parser.add_argument("--connect-timeout", type=float, default=25.0)
    parser.add_argument("--wheel", type=int, default=94, help="circonferenza rullo mm")
    parser.add_argument("--raw", action="store_true", help="stampa anche i frame standard")
    parser.add_argument("--max-raw", type=int, default=10, help="frame raw da stampare")
    parser.add_argument("--json", action="store_true", help="output JSON della scansione")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.mode == "ant":
        return run_ant(args)
    if args.mode == "ble":
        return run_ble(args)
    if has_ant_stick():
        return run_ant(args)
    print("Nessuna chiavetta ANT+ rilevata: uso il Bluetooth del Mac.\n")
    return run_ble(args)


if __name__ == "__main__":
    raise SystemExit(main())
