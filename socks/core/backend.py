# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 stepspower
"""Backends that read the car and send commands: BLE, Fleet API (cloud) and a Router.

All protocol work goes through ``tesla-fleet-api`` (imported lazily, it is heavy):

* BLE:   ``tesla_fleet_api.tesla.bluetooth.TeslaBluetooth`` -> ``.vehicles.create(vin, key=...)``
         returns ``tesla_fleet_api.tesla.vehicle.bluetooth.VehicleBluetooth``.
* Cloud: ``tesla_fleet_api.tesla.fleet.TeslaFleetApi`` -> ``.vehicles.createSigned(vin)``
         returns ``tesla_fleet_api.tesla.vehicle.signed.VehicleSigned`` (signed commands).

Tesla reports distances in miles: they are converted to km here, at the boundary. Payload
parsers are pure functions over dicts and tolerate missing keys (-> None, never a crash).

Note: ``tesla_fleet_api.exceptions.TeslaFleetError`` derives from ``BaseException``, not
``Exception``; every library call is therefore wrapped by :func:`_guard`.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from socks.core.ble import device_rssi
from socks.core.commands import COMMANDS, Command, CommandArg, DepartureArg, validate_arg
from socks.core.keys import KeyFileError, is_valid_vin, load_private_key, vin_model
from socks.core.model import ChargingState, ShiftState, Source, VehicleState
from socks.history.units import KM_PER_MI

log = logging.getLogger(__name__)

BLE_CONNECT_TIMEOUT = 20.0
COMMAND_TIMEOUT = 15.0
WAKE_TIMEOUT = 30.0
PAIR_TIMEOUT = 60.0
PAIR_ROLES = ("driver", "owner")

TokenProvider = Callable[[], Awaitable[str | None]]


class CommandError(Exception):
    """A read or command failed. The message is human-readable and free of secrets.

    ``maybe_executed`` is True when a *command* was handed to the car but its outcome is
    unknown (lost ack). Such a command must not be retried on another transport.
    """

    def __init__(
        self, message: str, *, maybe_executed: bool = False, kind: str | None = None
    ) -> None:
        super().__init__(message)
        self.maybe_executed = maybe_executed
        self.kind = kind  # class name of the underlying library error, if any


class NotAvailable(CommandError):
    """The backend cannot reach the car at all (not configured, out of range, offline...)."""


ASLEEP = "Asleep"  # CommandError.kind for "car is asleep and the read was not allowed to wake it"


class Backend(Protocol):
    name: Source

    async def available(self) -> bool: ...

    async def read_state(self, wake: bool = False, include_location: bool = True) -> VehicleState:
        """Read the car. ``include_location=False`` skips the GPS fix.

        ``wake=False`` (polls, background reads) must never wake a sleeping car;
        ``wake=True`` (user pressed Refresh) may wake it where that is free (BLE).
        """
        ...

    async def send(self, cmd: Command, arg: CommandArg = None) -> str | None:
        """Send a command (raises CommandError). Returns None, or a short note such as
        "already set" when the car reported it was already in the requested state."""
        ...


def _utcnow() -> datetime:
    return datetime.now(UTC)


# ======================================================================= payload parsing


def _camel(name: str) -> str:
    head, *rest = name.split("_")
    return head + "".join(p[:1].upper() + p[1:] for p in rest)


def _get(d: Any, name: str) -> Any:
    """``d[name]`` trying snake_case then camelCase (protobuf JSON); None if absent."""
    if not isinstance(d, dict):
        return None
    if name in d:
        return d[name]
    return d.get(_camel(name))


def _section(payload: dict[str, Any], name: str) -> dict[str, Any]:
    value = _get(payload, name)
    return value if isinstance(value, dict) else {}


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _int(value: Any) -> int | None:
    n = _num(value)
    return None if n is None else int(round(n))


def _bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    return None


def _miles(value: Any, ndigits: int = 1) -> float | None:
    n = _num(value)
    return None if n is None else round(n * KM_PER_MI, ndigits)


def _variant(value: Any) -> str | None:
    """Name of a protobuf oneof-of-Void (``{"Stopped": {}}``) or a plain REST string."""
    if isinstance(value, str):
        return value or None
    if isinstance(value, dict) and value:
        return next(iter(value))
    return None


def _charging(value: Any) -> ChargingState:
    name = _variant(value)
    try:
        return ChargingState(name)
    except ValueError:
        return ChargingState.UNKNOWN


def _shift(value: Any) -> ShiftState:
    name = _variant(value)
    try:
        return ShiftState(name)
    except ValueError:
        return ShiftState.UNKNOWN


def _plugged_in(cable: Any, charging: ChargingState) -> bool | None:
    name = _variant(cable)
    if name is not None:
        return name not in ("SNA", "<invalid>", "None", "none")
    if charging is ChargingState.DISCONNECTED:
        return False
    if charging is ChargingState.UNKNOWN:
        return None
    return True


def _epoch(value: Any) -> datetime | None:
    """Epoch seconds or milliseconds (tesla-control prints ms) -> aware UTC datetime."""
    n = _num(value)
    if not n or n <= 0:
        return None
    if n > 1e11:
        n /= 1000.0
    try:
        return datetime.fromtimestamp(n, UTC)
    except (OverflowError, OSError, ValueError):
        return None


def _scheduled_start(charge: dict[str, Any]) -> datetime | None:
    if _bool(_get(charge, "scheduled_charging_pending")) is False:
        return None
    return _epoch(_get(charge, "scheduled_charging_start_time"))


def _schedule_mode(charge: dict[str, Any]) -> str | None:
    """ "Off" | "StartAt" | "DepartBy" from the protobuf enum name or the REST string."""
    mode = _variant(_get(charge, "scheduled_charging_mode"))
    if mode is None:
        return None
    mode = mode.removeprefix("ScheduledChargingMode")
    return mode if mode in ("Off", "StartAt", "DepartBy") else None


def _scheduled_departure(charge: dict[str, Any]) -> datetime | None:
    """BLE: protobuf Timestamp (RFC 3339 string); REST: epoch seconds."""
    mode = _schedule_mode(charge)
    if mode is not None and mode != "DepartBy":
        return None
    value = _get(charge, "scheduled_departure_time")
    if isinstance(value, str) and "T" in value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC) if parsed.timestamp() > 0 else None
    return _epoch(value)


def _flag_or_block(charge: dict[str, Any], flag: str, block: str) -> bool | None:
    """REST has an explicit bool; BLE marks "on" by the presence of a *_times block."""
    value = _bool(_get(charge, flag))
    if value is not None:
        return value
    return True if _variant(_get(charge, block)) is not None else None


def _departure_options(charge: dict[str, Any]) -> dict[str, Any]:
    """Current preconditioning/off-peak settings as ``set_scheduled_departure`` kwargs, so
    editing the departure time does not silently switch them off (the library sends an
    absent block as "off")."""
    pre = _variant(_get(charge, "preconditioning_times"))
    off = _variant(_get(charge, "off_peak_charging_times"))
    pre_on = pre is not None or _bool(_get(charge, "preconditioning_enabled")) is True
    off_on = off is not None or _bool(_get(charge, "off_peak_charging_enabled")) is True
    return {
        "preconditioning_enabled": pre_on,
        "preconditioning_weekdays_only": pre == "weekdays",
        "off_peak_charging_enabled": off_on,
        "off_peak_charging_weekdays_only": off == "weekdays",
        "end_off_peak_time": _int(_get(charge, "off_peak_hours_end_time")) or 0,
    }


def _minutes_to_limit(charge: dict[str, Any]) -> int | None:
    for name in ("minutes_to_charge_limit", "minutes_to_full_charge"):
        value = _int(_get(charge, name))
        if value is not None:
            return value
    hours = _num(_get(charge, "time_to_full_charge"))  # REST: hours, float
    return None if hours is None else int(round(hours * 60))


def _named(flags: Iterator[tuple[str, Any]]) -> tuple[str, ...]:
    return tuple(name for name, value in flags if value)


# Left = driver side: assumes a left-hand-drive car (Tesla's APIs say driver/passenger).
_BLE_DOORS = (
    ("front_left", "door_open_driver_front"),
    ("front_right", "door_open_passenger_front"),
    ("rear_left", "door_open_driver_rear"),
    ("rear_right", "door_open_passenger_rear"),
    ("frunk", "door_open_trunk_front"),
    ("trunk", "door_open_trunk_rear"),
)
_BLE_WINDOWS = (
    ("front_left", "window_open_driver_front"),
    ("front_right", "window_open_passenger_front"),
    ("rear_left", "window_open_driver_rear"),
    ("rear_right", "window_open_passenger_rear"),
)
_VCSEC_DOORS = (
    ("front_left", "frontDriverDoor"),
    ("front_right", "frontPassengerDoor"),
    ("rear_left", "rearDriverDoor"),
    ("rear_right", "rearPassengerDoor"),
    ("frunk", "frontTrunk"),
    ("trunk", "rearTrunk"),
)
_REST_DOORS = (
    ("front_left", "df"),
    ("front_right", "pf"),
    ("rear_left", "dr"),
    ("rear_right", "pr"),
    ("frunk", "ft"),
    ("trunk", "rt"),
)
_REST_WINDOWS = (
    ("front_left", "fd_window"),
    ("front_right", "fp_window"),
    ("rear_left", "rd_window"),
    ("rear_right", "rp_window"),
)
_VCSEC_OPEN = {
    "CLOSURESTATE_OPEN",
    "CLOSURESTATE_AJAR",
    "CLOSURESTATE_OPENING",
    "CLOSURESTATE_CLOSING",
    "CLOSURESTATE_FAILED_UNLATCH",
}


def _vcsec_locked(vcsec: dict[str, Any]) -> bool | None:
    lock = _get(vcsec, "vehicleLockState")
    if lock in ("VEHICLELOCKSTATE_LOCKED", "VEHICLELOCKSTATE_INTERNAL_LOCKED"):
        return True
    if lock in ("VEHICLELOCKSTATE_UNLOCKED", "VEHICLELOCKSTATE_SELECTIVE_UNLOCKED"):
        return False
    return None


def _charge_fields(charge: dict[str, Any]) -> dict[str, Any]:
    """Fields common to the BLE (protobuf JSON) and REST charge_state shapes."""
    charging = _charging(_get(charge, "charging_state")) if charge else ChargingState.UNKNOWN
    return {
        "soc": _int(_get(charge, "battery_level")),
        "usable_soc": _int(_get(charge, "usable_battery_level")),
        "charge_limit": _int(_get(charge, "charge_limit_soc")),
        "charging": charging,
        "charger_power_kw": _num(_get(charge, "charger_power")),
        "charger_voltage": _int(_get(charge, "charger_voltage")),
        "charger_current_a": _int(_get(charge, "charger_actual_current")),
        "charge_energy_added_kwh": _num(_get(charge, "charge_energy_added")),
        "minutes_to_limit": _minutes_to_limit(charge),
        "rated_range_km": _miles(_get(charge, "battery_range")),
        "ideal_range_km": _miles(_get(charge, "ideal_battery_range")),
        "plugged_in": _plugged_in(_get(charge, "conn_charge_cable"), charging),
        "charge_port_open": _bool(_get(charge, "charge_port_door_open")),
        "scheduled_charging_start": _scheduled_start(charge),
        "charge_current_request_a": _int(_get(charge, "charge_current_request")),
        "charge_current_request_max_a": _int(_get(charge, "charge_current_request_max")),
        "scheduled_charging_mode": _schedule_mode(charge),
        "scheduled_departure": _scheduled_departure(charge),
        "preconditioning_enabled": _flag_or_block(
            charge, "preconditioning_enabled", "preconditioning_times"
        ),
        "off_peak_charging_enabled": _flag_or_block(
            charge, "off_peak_charging_enabled", "off_peak_charging_times"
        ),
    }


def _location_fields(d: dict[str, Any], *, prefer_native: bool = False) -> dict[str, Any]:
    """latitude/longitude/heading/gps_as_of from a BLE location_state or REST drive_state.

    BLE: native_latitude/native_longitude win when present. (0, 0) means "no fix" -> None.
    """
    lat = lon = None
    if prefer_native:
        lat, lon = _num(_get(d, "native_latitude")), _num(_get(d, "native_longitude"))
    if lat is None or lon is None:
        lat, lon = _num(_get(d, "latitude")), _num(_get(d, "longitude"))
    if lat is None or lon is None or (lat == 0 and lon == 0):
        lat = lon = None
    elif not (-90 <= lat <= 90 and -180 <= lon <= 180):
        lat = lon = None
    return {
        "latitude": lat,
        "longitude": lon,
        "heading": _int(_get(d, "heading")) if lat is not None else None,
        "gps_as_of": _epoch(_get(d, "gps_as_of")) if lat is not None else None,
    }


def parse_ble_payload(vin: str, payload: dict[str, Any], ts: datetime) -> VehicleState:
    """Map the BLE payload to a VehicleState.

    ``payload`` holds protobuf JSON dicts (snake_case from ``MessageToDict`` or camelCase from
    ``tesla-control``): ``charge_state``, ``drive_state``, ``climate_state``,
    ``closures_state`` (CarServer) and ``vehicle_status`` (VCSEC). Any may be missing.
    """
    charge = _section(payload, "charge_state")
    drive = _section(payload, "drive_state")
    climate = _section(payload, "climate_state")
    closures = _section(payload, "closures_state")
    vcsec = _section(payload, "vehicle_status")
    location = _section(payload, "location_state")

    odo = _num(_get(drive, "odometer_in_hundredths_of_a_mile"))
    speed = _num(_get(drive, "speed_float"))
    if speed is None:
        speed = _num(_get(drive, "speed"))

    if closures:
        doors = _named((n, _bool(_get(closures, k))) for n, k in _BLE_DOORS)
        locked = _bool(_get(closures, "locked"))
    else:
        cs = _section(vcsec, "closureStatuses")
        doors = _named((n, _get(cs, k) in _VCSEC_OPEN) for n, k in _VCSEC_DOORS)
        locked = None
    if locked is None:
        locked = _vcsec_locked(vcsec)
    sentry = _variant(_get(closures, "sentry_mode_state"))

    return VehicleState(
        ts=ts,
        source=Source.BLE,
        vin=vin,
        **_charge_fields(charge),
        odometer_km=None if odo is None else round(odo / 100.0 * KM_PER_MI, 2),
        shift=_shift(_get(drive, "shift_state")),
        speed_kmh=None if speed is None else round(speed * KM_PER_MI, 1),
        power_kw=_num(_get(drive, "power")),
        inside_temp_c=_num(_get(climate, "inside_temp_celsius")),
        outside_temp_c=_num(_get(climate, "outside_temp_celsius")),
        climate_on=_bool(_get(climate, "is_climate_on")),
        target_temp_c=_num(_get(climate, "driver_temp_setting")),
        locked=locked,
        doors_open=doors,
        windows_open=_named((n, _bool(_get(closures, k))) for n, k in _BLE_WINDOWS),
        sentry_on=None if sentry is None else sentry != "Off",
        car_name=None,
        firmware=None,
        **_location_fields(location, prefer_native=True),
        raw=payload,
    )


def parse_cloud_payload(vin: str, payload: dict[str, Any], ts: datetime) -> VehicleState:
    """Map a Fleet API ``vehicle_data`` REST response (``{"response": {...}}`` or bare)."""
    data = payload.get("response", payload) if isinstance(payload, dict) else {}
    if not isinstance(data, dict):
        data = {}
    charge = _section(data, "charge_state")
    drive = _section(data, "drive_state")
    climate = _section(data, "climate_state")
    vstate = _section(data, "vehicle_state")

    version = _get(vstate, "car_version")
    firmware = version.split()[0] if isinstance(version, str) and version.strip() else None
    name = _get(vstate, "vehicle_name") or _get(data, "display_name")

    return VehicleState(
        ts=ts,
        source=Source.CLOUD,
        vin=vin,
        **_charge_fields(charge),
        odometer_km=_miles(_get(vstate, "odometer"), 2),
        shift=_shift(_get(drive, "shift_state")),
        speed_kmh=_miles(_get(drive, "speed")),
        power_kw=_num(_get(drive, "power")),
        inside_temp_c=_num(_get(climate, "inside_temp")),
        outside_temp_c=_num(_get(climate, "outside_temp")),
        climate_on=_bool(_get(climate, "is_climate_on")),
        target_temp_c=_num(_get(climate, "driver_temp_setting")),
        locked=_bool(_get(vstate, "locked")),
        doors_open=_named((n, _get(vstate, k)) for n, k in _REST_DOORS),
        windows_open=_named((n, _get(vstate, k)) for n, k in _REST_WINDOWS),
        sentry_on=_bool(_get(vstate, "sentry_mode")),
        car_name=name if isinstance(name, str) and name else None,
        firmware=firmware,
        **_location_fields(drive),
        raw=payload,
    )


# ======================================================================= shared helpers


def _describe(exc: BaseException) -> str:
    """Short, secret-free description of a library exception (never its ``data``)."""
    msg = getattr(exc, "message", None) or str(exc) or type(exc).__name__
    return f"{type(exc).__name__}: {str(msg)[:200]}"


def _named_state(state: VehicleState, override: str | None) -> VehicleState:
    """car_name = user override, else what the car reports (cloud), else the VIN's model."""
    name = override or state.car_name or vin_model(state.vin)
    return state if name == state.car_name else replace(state, car_name=name)


def _wrappable(exc: BaseException) -> bool:
    """Ordinary errors plus tesla-fleet-api's (``TeslaFleetError`` is a BaseException);
    never CancelledError, KeyboardInterrupt or SystemExit."""
    if isinstance(exc, Exception):
        return True
    return any(c.__module__.startswith("tesla_fleet_api") for c in type(exc).__mro__)


# Refusals that only mean "the car is already in the requested state" (Tesla reason
# strings, normalised to lower_snake_case). Some only count for specific commands.
_NOOP_REASONS = frozenset(
    {
        "already_set",
        "already_on",
        "already_off",
        "already_locked",
        "already_unlocked",
        "already_open",
        "already_closed",
        "already_standard",
        "already_max_range",
    }
)
_NOOP_FOR: dict[str, frozenset[Command]] = {
    "not_charging": frozenset({Command.CHARGE_STOP}),
    "is_charging": frozenset({Command.CHARGE_START}),
    "charging": frozenset({Command.CHARGE_START}),
    "is_unlocked": frozenset({Command.UNLOCK}),
    "is_locked": frozenset({Command.LOCK}),
}
REFUSED = "Refused"  # CommandError.kind for a definitive car-side refusal (never failed over)


def _normalise_reason(reason: str) -> str:
    return reason.strip().lower().replace(" ", "_").replace("-", "_")


def _check_result(result: Any, cmd: Command | None = None) -> str | None:
    """Raise CommandError(kind=REFUSED) if a command reply says ``result: false``.

    A refusal that just means "already in that state" is success: returns "already set".
    """
    resp = result.get("response", result) if isinstance(result, dict) else None
    if not (isinstance(resp, dict) and resp.get("result") is False):
        return None
    reason = str(resp.get("reason") or "no reason given")[:200]
    norm = _normalise_reason(reason)
    if norm in _NOOP_REASONS or (cmd is not None and cmd in _NOOP_FOR.get(norm, ())):
        return "already set"
    raise CommandError(f"car refused the command: {reason}", kind=REFUSED)


def _scalar(arg: CommandArg) -> float | int | None:
    if isinstance(arg, DepartureArg):
        raise CommandError("departure options only apply to set_scheduled_departure")
    return arg


async def _current_charge(vehicle: Any, cloud: bool) -> dict[str, Any]:
    if cloud:
        data = await vehicle.vehicle_data(endpoints=["charge_state"])
        resp = data.get("response", data) if isinstance(data, dict) else {}
        return _section(resp if isinstance(resp, dict) else {}, "charge_state")
    return _to_dict(await vehicle.charge_state())


async def _dispatch(
    vehicle: Any, cmd: Command, arg: CommandArg, wake_timeout: float, *, cloud: bool
) -> Any:
    """Call the tesla-fleet-api method for ``cmd`` (identical on BLE and signed cloud)."""
    match cmd:
        case Command.SET_CHARGE_AMPS:
            return await vehicle.set_charging_amps(int(_scalar(arg) or 0))
        case Command.SET_SCHEDULED_CHARGING:
            # minutes after midnight, vehicle-local time; None disables.
            minutes = _scalar(arg)
            return await vehicle.set_scheduled_charging(minutes is not None, int(minutes or 0))
        case Command.SET_SCHEDULED_DEPARTURE:
            # Preserve preconditioning/off-peak unless given explicitly (costs one extra
            # read on the cloud).
            options = _departure_options(await _current_charge(vehicle, cloud))
            minutes = arg.minutes if isinstance(arg, DepartureArg) else arg
            if isinstance(arg, DepartureArg):
                if arg.preconditioning is not None:
                    options["preconditioning_enabled"] = arg.preconditioning
                    options["preconditioning_weekdays_only"] = False
                if arg.off_peak_charging is not None:
                    options["off_peak_charging_enabled"] = arg.off_peak_charging
                    options["off_peak_charging_weekdays_only"] = False
                    if arg.off_peak_charging and not options["end_off_peak_time"]:
                        # No off-peak window known: let it run until departure.
                        options["end_off_peak_time"] = int(minutes or 0)
            return await vehicle.set_scheduled_departure(
                enable=minutes is not None, departure_time=int(minutes or 0), **options
            )
        case Command.LOCK:
            return await vehicle.door_lock()
        case Command.UNLOCK:
            return await vehicle.door_unlock()
        case Command.CLIMATE_ON:
            return await vehicle.auto_conditioning_start()
        case Command.CLIMATE_OFF:
            return await vehicle.auto_conditioning_stop()
        case Command.SET_TEMP:
            temp = float(_scalar(arg) or 0)  # validate_arg guarantees a value
            return await vehicle.set_temps(temp, temp)
        case Command.CHARGE_START:
            return await vehicle.charge_start()
        case Command.CHARGE_STOP:
            return await vehicle.charge_stop()
        case Command.SET_CHARGE_LIMIT:
            return await vehicle.set_charge_limit(int(_scalar(arg) or 0))
        case Command.CHARGE_PORT_OPEN:
            return await vehicle.charge_port_door_open()
        case Command.CHARGE_PORT_CLOSE:
            return await vehicle.charge_port_door_close()
        case Command.FLASH_LIGHTS:
            return await vehicle.flash_lights()
        case Command.HONK:
            return await vehicle.honk_horn()
        case Command.FRUNK:
            return await vehicle.actuate_trunk("front")
        case Command.TRUNK:
            return await vehicle.actuate_trunk("rear")
        case Command.SENTRY_ON:
            return await vehicle.set_sentry_mode(True)
        case Command.SENTRY_OFF:
            return await vehicle.set_sentry_mode(False)
        case Command.WAKE:
            return await vehicle.wake_up(wait=True, timeout=wake_timeout)
    raise CommandError(f"unsupported command {cmd.value}")  # pragma: no cover


def _timeout_for(cmd: Command, command_timeout: float, wake_timeout: float) -> float:
    return wake_timeout + 5 if cmd is Command.WAKE else command_timeout


# ======================================================================= BLE


class BleBackend:
    """Talks to the car directly over Bluetooth LE (BlueZ via bleak; no cloud, no cost).

    The GATT link is opened lazily and kept until :meth:`close`; library keepalive is off so
    the car can still go to sleep. ``vehicle_factory`` exists for tests only.
    """

    name = Source.BLE

    def __init__(
        self,
        vin: str,
        key_path: Path,
        *,
        connect_timeout: float = BLE_CONNECT_TIMEOUT,
        command_timeout: float = COMMAND_TIMEOUT,
        wake_timeout: float = WAKE_TIMEOUT,
        vehicle_factory: Callable[[], Any] | None = None,
        car_name: str | None = None,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self.vin = vin
        self.car_name = car_name or None  # user override (Settings.car_name)
        self.key_path = key_path
        self.connect_timeout = connect_timeout
        self.command_timeout = command_timeout
        self.wake_timeout = wake_timeout
        self._factory = vehicle_factory or self._make_vehicle
        self._clock = clock
        self._vehicle: Any = None
        self._lock = asyncio.Lock()

    async def available(self) -> bool:
        """Cheap check (no radio): VIN and key are configured."""
        return is_valid_vin(self.vin) and self.key_path.is_file()

    def _make_vehicle(self) -> Any:
        from tesla_fleet_api.tesla.bluetooth import TeslaBluetooth

        key = load_private_key(self.key_path)
        return TeslaBluetooth().vehicles.create(
            self.vin,
            key=key,
            keepalive_interval=None,
            raise_unconfirmed=True,
            wake_if_asleep=True,
        )

    async def _connected(self) -> Any:
        if not await self.available():
            raise NotAvailable("Bluetooth: VIN or key not configured")
        try:
            if self._vehicle is None:
                self._vehicle = self._factory()
            vehicle = self._vehicle
            async with asyncio.timeout(self.connect_timeout):
                if vehicle.get_device() is None:
                    await vehicle.find_vehicle()
                await vehicle.connect_if_needed()
        except KeyFileError as e:
            raise NotAvailable(f"Bluetooth: {e}") from None
        except TimeoutError:
            raise NotAvailable(
                f"Bluetooth: car not reachable within {self.connect_timeout:g} s "
                "(out of range, or Bluetooth off?)"
            ) from None
        except BaseException as e:
            if not _wrappable(e):
                raise
            if isinstance(e, ValueError):  # find_vehicle: no advertisement seen
                raise NotAvailable("Bluetooth: car not found nearby") from None
            raise NotAvailable(f"Bluetooth: cannot connect ({_describe(e)})") from None
        return vehicle

    async def read_state(self, wake: bool = False, include_location: bool = True) -> VehicleState:
        """Read over BLE. Without ``wake`` a sleeping car is left asleep: NotAvailable with
        ``kind == ASLEEP`` (the VCSEC status read used to find out does not wake it).

        tesla-fleet-api only has a per-vehicle ``wake_if_asleep`` attribute (read at send
        time), so it is set for the duration of this read, under the backend lock.
        """
        async with self._lock:
            vehicle = await self._connected()
            vehicle.wake_if_asleep = wake
            try:
                return await self._read(vehicle, wake, include_location)
            finally:
                vehicle.wake_if_asleep = True  # commands may wake (the user asked for them)

    async def _read(self, vehicle: Any, wake: bool, include_location: bool) -> VehicleState:
        payload: dict[str, Any] = {}
        with _guard("Bluetooth read"):
            async with asyncio.timeout(self.command_timeout):
                status = await vehicle.vehicle_state()
            payload["vehicle_status"] = _to_dict(status, all_fields=True)
            if payload["vehicle_status"].get("vehicleSleepStatus") == (
                "VEHICLE_SLEEP_STATUS_ASLEEP"
            ):
                if not wake:
                    raise NotAvailable(
                        "Bluetooth: car is asleep (not woken for a background read)",
                        kind=ASLEEP,
                    )
                async with asyncio.timeout(self.wake_timeout + 5):
                    await vehicle.wake_up(wait=True, timeout=self.wake_timeout)
            async with asyncio.timeout(self.command_timeout):
                payload["charge_state"] = _to_dict(await vehicle.charge_state())
        optional = ["drive_state", "climate_state", "closures_state"]
        if include_location:
            optional.append("location_state")  # failure here never fails the read
        for name in optional:
            try:
                with _guard(f"Bluetooth {name}"):
                    async with asyncio.timeout(self.command_timeout):
                        payload[name] = _to_dict(await getattr(vehicle, name)())
            except CommandError as e:
                log.debug("optional BLE read failed: %s", e)
        state = _named_state(parse_ble_payload(self.vin, payload, self._clock()), self.car_name)
        rssi = device_rssi(vehicle.get_device())
        return state if rssi is None else replace(state, ble_rssi_dbm=rssi)

    async def pair(self, role: str = "driver", timeout: float = PAIR_TIMEOUT) -> bool:
        """Send the BLE add-key request for this backend's key (form factor cloud_key).

        Same as ``tesla-control -ble add-key-request public_key.pem <role> cloud_key``. The
        user must tap a key card on the centre console. Returns True once the car confirms
        (reply or whitelist probe), False if nothing confirmed within ``timeout`` (approval
        may still be pending: check the car's Locks screen). Raises CommandError if the car
        rejects the request, NotAvailable if the car cannot be reached. The request is
        written once and never re-sent (that would prompt again).
        """
        if role not in PAIR_ROLES:
            raise CommandError(f"role must be one of {', '.join(PAIR_ROLES)}")
        async with self._lock:
            vehicle = await self._connected()
            from tesla_protocol.command.keys_pb2 import Role
            from tesla_protocol.command.vcsec_pb2 import KeyFormFactor

            try:
                with _guard("Bluetooth pairing"):
                    try:
                        async with asyncio.timeout(timeout + 10):
                            await vehicle.pair(
                                role=Role.ROLE_OWNER if role == "owner" else Role.ROLE_DRIVER,
                                form=KeyFormFactor.KEY_FORM_FACTOR_CLOUD_KEY,
                                timeout=timeout,
                            )
                    except TimeoutError:
                        return False  # not confirmed in time; may still be pending
            except CommandError as e:
                if e.kind == "BluetoothTimeout":  # library: neither reply nor probe confirmed
                    return False
                raise
            return True

    async def send(self, cmd: Command, arg: CommandArg = None) -> str | None:
        try:
            arg = validate_arg(cmd, arg)
        except ValueError as e:
            raise CommandError(str(e)) from None
        async with self._lock:
            vehicle = await self._connected()
            with _guard(f"Bluetooth {cmd.value}", command=True):
                async with asyncio.timeout(
                    _timeout_for(cmd, self.command_timeout, self.wake_timeout)
                ):
                    result = await _dispatch(vehicle, cmd, arg, self.wake_timeout, cloud=False)
            return _check_result(result, cmd)

    async def close(self) -> None:
        vehicle, self._vehicle = self._vehicle, None
        if vehicle is None:
            return
        try:
            async with asyncio.timeout(5):
                await vehicle.disconnect()
        except BaseException as e:
            if not _wrappable(e):
                raise
            log.debug("BLE disconnect failed: %s", _describe(e))


def _to_dict(message: Any, *, all_fields: bool = False) -> dict[str, Any]:
    """Protobuf message -> dict (snake_case); plain dicts pass through (tests)."""
    if isinstance(message, dict):
        return message
    if message is None:
        return {}
    from google.protobuf.json_format import MessageToDict

    return MessageToDict(
        message,
        preserving_proto_field_name=True,
        always_print_fields_with_no_presence=all_fields,
    )


@contextlib.contextmanager
def _guard(what: str, *, command: bool = False) -> Iterator[None]:
    """Turn library/timeout errors into CommandError with a secret-free message.

    For commands, anything after the request may have left the laptop is flagged
    ``maybe_executed`` so the Router will not replay it on another transport.
    """
    try:
        yield
    except CommandError:
        raise
    except TimeoutError:
        raise CommandError(
            f"{what}: no answer in time" + (" (the car may still have done it)" if command else ""),
            maybe_executed=command,
        ) from None
    except BaseException as e:
        if not _wrappable(e):
            raise  # CancelledError, KeyboardInterrupt, SystemExit
        kind = type(e).__name__
        if kind in _NOT_DELIVERED:
            raise NotAvailable(f"{what}: {_describe(e)}", kind=kind) from None
        maybe = command and kind not in _PROVABLY_NOT_EXECUTED
        raise CommandError(f"{what}: {_describe(e)}", maybe_executed=maybe, kind=kind) from None


# Library errors meaning the request provably never reached the car.
_NOT_DELIVERED = frozenset(
    {
        "BluetoothTransportError",
        "VehicleOffline",
        "MissingToken",
        "InvalidToken",
        "KeyFileError",
        "OAuthError",  # socks.fleet: not logged in / refresh failed
    }
)
# Library errors meaning a command reached the car but provably did not take effect
# (or was rejected before signing): safe to try on another transport.
_PROVABLY_NOT_EXECUTED = frozenset({"BluetoothCommandFailed", "SigningDisabled", "ValueError"})


# ======================================================================= cloud


class CloudBackend:
    """Tesla Fleet API with signed commands. Every call costs money (see Settings tab).

    Reads never wake the car (a wake costs $0.02): an asleep car is reported NotAvailable.
    Commands that need the infotainment computer wake it first when the car is asleep.
    ``token_provider`` returns a fresh access token (owned by ``socks.fleet``).
    """

    name = Source.CLOUD

    def __init__(
        self,
        vin: str,
        key_path: Path,
        token_provider: TokenProvider,
        *,
        region: str = "eu",
        command_timeout: float = COMMAND_TIMEOUT,
        wake_timeout: float = WAKE_TIMEOUT,
        vehicle_factory: Callable[[], Any] | None = None,
        car_name: str | None = None,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self.vin = vin
        self.car_name = car_name or None  # user override (Settings.car_name)
        self.key_path = key_path
        self.region = region
        self.command_timeout = command_timeout
        self.wake_timeout = wake_timeout
        self._token = token_provider
        self._factory = vehicle_factory or self._make_vehicle
        self._clock = clock
        self._vehicle: Any = None
        self._session: Any = None

    async def available(self) -> bool:
        return is_valid_vin(self.vin) and self.key_path.is_file()

    def _make_vehicle(self) -> Any:
        import aiohttp
        from tesla_fleet_api.tesla.fleet import TeslaFleetApi

        key = load_private_key(self.key_path)
        self._session = aiohttp.ClientSession()
        api = TeslaFleetApi(self._session, access_token=self._token, region=self.region)  # type: ignore[arg-type]
        api.private_key = key
        return api.vehicles.createSigned(self.vin)

    def _get_vehicle(self) -> Any:
        if not is_valid_vin(self.vin):
            raise NotAvailable("cloud: VIN not configured")
        if self._vehicle is None:
            try:
                self._vehicle = self._factory()
            except KeyFileError as e:
                raise NotAvailable(f"cloud: {e}") from None
        return self._vehicle

    async def read_state(self, wake: bool = False, include_location: bool = True) -> VehicleState:
        """Cloud reads never wake the car (a wake costs money), whatever ``wake`` says."""
        del wake
        vehicle = self._get_vehicle()
        with _guard("cloud read"):
            async with asyncio.timeout(self.command_timeout):
                endpoints = [
                    "charge_state",
                    "climate_state",
                    "closures_state",
                    "drive_state",
                    "vehicle_state",
                ]
                if include_location:  # needs the vehicle_location scope; absent -> None
                    endpoints.append("location_data")
                data = await vehicle.vehicle_data(endpoints=endpoints)
        if not isinstance(data, dict):
            raise CommandError("cloud read: unexpected reply")
        return _named_state(parse_cloud_payload(self.vin, data, self._clock()), self.car_name)

    async def send(self, cmd: Command, arg: CommandArg = None) -> str | None:
        try:
            arg = validate_arg(cmd, arg)
        except ValueError as e:
            raise CommandError(str(e)) from None
        vehicle = self._get_vehicle()
        timeout = _timeout_for(cmd, self.command_timeout, self.wake_timeout)
        try:
            with _guard(f"cloud {cmd.value}", command=True):
                async with asyncio.timeout(timeout):
                    result = await _dispatch(vehicle, cmd, arg, self.wake_timeout, cloud=True)
        except NotAvailable as e:
            # VehicleOffline: the command never reached the car (cloud commands, VCSEC ones
            # included, need the car online), so wake it and retry once.
            if e.kind != "VehicleOffline" or cmd is Command.WAKE:
                raise
            with _guard("cloud wake"):
                async with asyncio.timeout(self.wake_timeout + 5):
                    await vehicle.wake_up(wait=True, timeout=self.wake_timeout)
            with _guard(f"cloud {cmd.value}", command=True):
                async with asyncio.timeout(timeout):
                    result = await _dispatch(vehicle, cmd, arg, self.wake_timeout, cloud=True)
        return _check_result(result, cmd)

    async def close(self) -> None:
        session, self._session = self._session, None
        self._vehicle = None
        if session is not None:
            await session.close()


# ======================================================================= router


class Router(Backend):
    """BLE first, cloud as fallback (or the other way round with ``prefer``).

    * Reads fall over on any failure.
    * Commands fall over only when the first transport provably did not deliver them
      (``maybe_executed`` False), so a command is never executed twice.
    * Remembers the last successful source (``last_source``, also ``name``). When the
      preferred backend failed less than ``retry_preferred_after`` seconds ago and the
      other one worked, the other one is tried first (avoids a 20 s BLE scan on every poll
      while away from the car); after that the preferred one gets another chance.
    * Raises NotAvailable with every backend's reason when none works.
    """

    def __init__(
        self,
        ble: Backend | None,
        cloud: Backend | None,
        prefer: Source = Source.BLE,
        *,
        retry_preferred_after: float = 120.0,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.ble = ble
        self.cloud = cloud
        self.prefer = prefer
        self.retry_preferred_after = retry_preferred_after
        self._monotonic = monotonic
        self.last_source: Source | None = None
        self.last_error: str | None = None
        self._failed_at: dict[Source, float] = {}

    @property  # type: ignore[override]
    def name(self) -> Source:
        return self.last_source or self.prefer

    def backends(self) -> list[Backend]:
        """Backends in the order the next operation will try them."""
        pair = [self.ble, self.cloud] if self.prefer is Source.BLE else [self.cloud, self.ble]
        order = [b for b in pair if b is not None]
        if len(order) == 2 and self.last_source is order[1].name:
            failed = self._failed_at.get(order[0].name)
            if failed is not None and self._monotonic() - failed < self.retry_preferred_after:
                order.reverse()
        return order

    async def available(self) -> bool:
        for backend in self.backends():
            if await backend.available():
                return True
        return False

    async def read_state(self, wake: bool = False, include_location: bool = True) -> VehicleState:
        """Read via the first working backend. A car reported asleep is final (no paid cloud
        fallback that could not read a sleeping car anyway)."""

        async def op(b: Backend) -> VehicleState:
            return await b.read_state(wake=wake, include_location=include_location)

        return await self._run(op, failover_on_maybe=True)

    async def send(self, cmd: Command, arg: CommandArg = None) -> str | None:
        try:
            arg = validate_arg(cmd, arg)
        except ValueError as e:
            raise CommandError(str(e)) from None

        async def op(b: Backend) -> str | None:
            return await b.send(cmd, arg)

        only = Source.CLOUD if COMMANDS[cmd].cloud_only else None
        return await self._run(op, failover_on_maybe=False, only=only)

    async def _run[T](
        self,
        op: Callable[[Backend], Awaitable[T]],
        *,
        failover_on_maybe: bool,
        only: Source | None = None,
    ) -> T:
        reasons: list[str] = []
        for backend in self.backends():
            if only is not None and backend.name is not only:
                reasons.append(f"{backend.name.value}: command is {only.value}-only")
                continue
            label = "Bluetooth" if backend.name is Source.BLE else "cloud"
            if not await backend.available():
                reasons.append(f"{label}: not set up (VIN or key missing)")
                continue
            try:
                result = await op(backend)
            except CommandError as e:
                self.last_error = str(e)
                if e.kind == ASLEEP:
                    self.last_source = backend.name  # reachable, just asleep
                    raise
                if e.kind == REFUSED:  # the car answered: definitive, never fail over
                    self.last_source = backend.name
                    raise
                self._failed_at[backend.name] = self._monotonic()
                if e.maybe_executed and not failover_on_maybe:
                    raise
                reasons.append(
                    str(e) if str(e).lower().startswith(label.lower()) else f"{label}: {e}"
                )
                continue
            self.last_source = backend.name
            self.last_error = None
            self._failed_at.pop(backend.name, None)
            return result
        if not reasons:
            reasons.append("no backend configured (set a VIN and key, or enable the cloud)")
        message = "car not reachable. " + "; ".join(reasons)
        self.last_error = message
        raise NotAvailable(message)

    async def close(self) -> None:
        for backend in (self.ble, self.cloud):
            if backend is not None:
                await backend.close()


# ======================================================================= factories


def build_router(settings: Any, token_provider: TokenProvider | None = None) -> Router:
    """Router from Settings. The cloud backend exists only if enabled *and* a token provider
    (from ``socks.fleet``) is given."""
    name = getattr(settings, "car_name", "") or None
    ble = BleBackend(settings.vin, Path(settings.key_path), car_name=name)
    cloud = None
    if settings.cloud_enabled and token_provider is not None:
        cloud = CloudBackend(
            settings.vin,
            Path(settings.key_path),
            token_provider,
            region=settings.region,
            car_name=name,
        )
    prefer = Source.CLOUD if settings.prefer == "cloud" else Source.BLE
    return Router(ble, cloud, prefer)


def fake_backend(**kwargs: Any) -> Backend:
    """``tests/fakes.py``'s FakeBackend for ``--fake`` runs."""
    return fakes_module().FakeBackend(**kwargs)


def fakes_module() -> Any:
    """``tests/fakes.py`` of the source checkout (``--fake`` only).

    Loaded only from the source checkout that contains the running ``socks`` package
    (``socks.__file__``'s parent.parent, which must hold pyproject.toml), never through
    ``sys.path``/the current directory (audit L2).
    """
    import importlib.util
    import sys

    import socks

    root = Path(socks.__file__).resolve().parent.parent
    path = root / "tests" / "fakes.py"
    if not (root / "pyproject.toml").is_file() or not path.is_file():
        raise RuntimeError(
            "--fake is only available when running from a SOCKS source checkout "
            f"(no tests/fakes.py next to {Path(socks.__file__).resolve().parent})"
        )
    for name in ("tests.fakes", "socks_checkout_fakes"):
        loaded = sys.modules.get(name)
        loaded_file = getattr(loaded, "__file__", None)
        if loaded is not None and loaded_file and Path(loaded_file).resolve() == path:
            return loaded  # same file already imported (e.g. by the test-suite)
    spec = importlib.util.spec_from_file_location("socks_checkout_fakes", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["socks_checkout_fakes"] = module
    spec.loader.exec_module(module)
    return module
