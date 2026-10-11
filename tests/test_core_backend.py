# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 stepspower
"""Tests for socks.core.backend: payload mapping, BLE/cloud backends on fake vehicles, Router.

No Bluetooth, no network: the tesla-fleet-api vehicle objects are replaced by fakes that
return real ``tesla_protocol`` protobuf messages (BLE) or REST-shaped dicts (cloud).
"""

from __future__ import annotations

# Run from a source checkout without installing: make `socks` and `tests.fakes` importable.
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from tesla_fleet_api.exceptions import (
    BluetoothCommandFailed,
    BluetoothTransportError,
    BluetoothUnconfirmedCommand,
    VehicleOffline,
)
from tesla_protocol.command import vcsec_pb2, vehicle_pb2

from socks.core import keys
from socks.core.backend import (
    ASLEEP,
    BleBackend,
    CloudBackend,
    CommandError,
    NotAvailable,
    Router,
    build_router,
    fake_backend,
    parse_ble_payload,
    parse_cloud_payload,
)
from socks.core.commands import Command
from socks.core.config import Settings
from socks.core.model import ChargingState, ShiftState, Source
from socks.history.units import KM_PER_MI
from tests.fakes import FakeBackend

VIN = "5YJ3E7EB0KF000001"
TS = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)

# Shape printed by Tesla's `tesla-control state charge` / `state drive` (protobuf JSON).
TESLA_CONTROL_PAYLOAD: dict[str, Any] = {
    "chargeState": {
        "chargingState": {"Stopped": {}},
        "chargeLimitSoc": 75,
        "batteryRange": 270.9,
        "batteryLevel": 70,
        "chargerPower": 1,
        "chargerVoltage": 229,
        "chargePortDoorOpen": True,
        "connChargeCable": {"IEC": {}},
        "scheduledChargingStartTime": "1791752400000",
        "scheduledChargingPending": True,
    },
    "driveState": {
        "odometerInHundredthsOfAMile": 288906,
        "shiftState": {"P": {}},
        "power": -1,
    },
}

# Fleet API vehicle_data REST shape.
CLOUD_PAYLOAD: dict[str, Any] = {
    "response": {
        "vin": VIN,
        "display_name": "step's 3",
        "state": "online",
        "charge_state": {
            "battery_level": 64,
            "usable_battery_level": 63,
            "charge_limit_soc": 80,
            "charging_state": "Charging",
            "charger_power": 11,
            "charger_voltage": 228,
            "charger_actual_current": 16,
            "charge_energy_added": 4.25,
            "minutes_to_full_charge": 95,
            "battery_range": 200.0,
            "ideal_battery_range": 210.0,
            "charge_port_door_open": True,
            "conn_charge_cable": "IEC",
            "scheduled_charging_start_time": None,
            "scheduled_charging_pending": False,
        },
        "drive_state": {"shift_state": None, "speed": None, "power": 0},
        "climate_state": {
            "inside_temp": 19.5,
            "outside_temp": 8.0,
            "is_climate_on": False,
            "driver_temp_setting": 21.0,
        },
        "vehicle_state": {
            "odometer": 1795.1,
            "locked": False,
            "df": 0,
            "pf": 1,
            "dr": 0,
            "pr": 0,
            "ft": 0,
            "rt": 1,
            "fd_window": 0,
            "fp_window": 0,
            "rd_window": 1,
            "rp_window": 0,
            "sentry_mode": True,
            "car_version": "2026.32.5 abcdef123456",
            "vehicle_name": "step's 3",
        },
    }
}


# ======================================================================= parsers


def test_parse_tesla_control_reference_payload() -> None:
    s = parse_ble_payload(VIN, TESLA_CONTROL_PAYLOAD, TS)
    assert s.source is Source.BLE and s.vin == VIN and s.ts == TS
    assert s.soc == 70
    assert s.charge_limit == 75
    assert s.charging is ChargingState.STOPPED
    assert s.rated_range_km == pytest.approx(270.9 * KM_PER_MI, abs=0.05)
    assert s.charger_power_kw == 1.0
    assert s.charger_voltage == 229
    assert s.charge_port_open is True
    assert s.plugged_in is True
    assert s.scheduled_charging_start == datetime(2026, 10, 11, 21, 0, tzinfo=UTC)
    assert s.odometer_km == pytest.approx(2889.06 * KM_PER_MI, abs=0.01)
    assert s.shift is ShiftState.P
    assert s.power_kw == -1.0
    assert s.speed_kmh is None
    assert s.inside_temp_c is None and s.locked is None and s.doors_open == ()
    assert s.raw is TESLA_CONTROL_PAYLOAD


def test_parse_empty_payloads_never_crash() -> None:
    for parse in (parse_ble_payload, parse_cloud_payload):
        s = parse(VIN, {}, TS)
        assert s.soc is None and s.odometer_km is None
        assert s.charging is ChargingState.UNKNOWN and s.shift is ShiftState.UNKNOWN
        assert s.plugged_in is None
    weird = {"charge_state": {"battery_level": "n/a", "charging_state": 7}, "drive_state": []}
    s = parse_ble_payload(VIN, weird, TS)
    assert s.soc is None and s.charging is ChargingState.UNKNOWN
    assert parse_cloud_payload(VIN, {"response": None}, TS).soc is None


def test_parse_cloud_payload() -> None:
    s = parse_cloud_payload(VIN, CLOUD_PAYLOAD, TS)
    assert s.source is Source.CLOUD
    assert (s.soc, s.usable_soc, s.charge_limit) == (64, 63, 80)
    assert s.charging is ChargingState.CHARGING
    assert (s.charger_power_kw, s.charger_voltage, s.charger_current_a) == (11.0, 228, 16)
    assert s.charge_energy_added_kwh == 4.25
    assert s.minutes_to_limit == 95
    assert s.rated_range_km == pytest.approx(321.9, abs=0.05)
    assert s.ideal_range_km == pytest.approx(210 * KM_PER_MI, abs=0.05)
    assert s.plugged_in is True
    assert s.scheduled_charging_start is None
    assert s.odometer_km == pytest.approx(1795.1 * KM_PER_MI, abs=0.01)
    assert s.shift is ShiftState.UNKNOWN and s.speed_kmh is None
    assert (s.inside_temp_c, s.outside_temp_c, s.climate_on, s.target_temp_c) == (
        19.5,
        8.0,
        False,
        21.0,
    )
    assert s.locked is False
    assert s.doors_open == ("front_right", "trunk")
    assert s.windows_open == ("rear_left",)
    assert s.sentry_on is True
    assert s.firmware == "2026.32.5" and s.car_name == "step's 3"


def test_parse_cloud_unplugged_and_seconds_epoch() -> None:
    payload = {
        "charge_state": {
            "charging_state": "Disconnected",
            "conn_charge_cable": "<invalid>",
            "scheduled_charging_start_time": 1791752400,
            "scheduled_charging_pending": True,
            "time_to_full_charge": 1.5,
        },
        "drive_state": {"shift_state": "D", "speed": 50},
    }
    s = parse_cloud_payload(VIN, payload, TS)
    assert s.plugged_in is False
    assert s.charging is ChargingState.DISCONNECTED
    assert s.scheduled_charging_start == datetime(2026, 10, 11, 21, 0, tzinfo=UTC)
    assert s.minutes_to_limit == 90
    assert s.shift is ShiftState.D
    assert s.speed_kmh == pytest.approx(80.5, abs=0.05)


# ======================================================================= fake vehicles


def _protobuf_state() -> dict[str, Any]:
    cs = vehicle_pb2.ChargeState(
        battery_level=55,
        usable_battery_level=54,
        charge_limit_soc=80,
        battery_range=180.0,
        charger_power=0,
        charge_port_door_open=False,
        minutes_to_charge_limit=0,
    )
    cs.charging_state.Disconnected.SetInParent()
    cs.conn_charge_cable.SNA.SetInParent()
    ds = vehicle_pb2.DriveState(
        odometer_in_hundredths_of_a_mile=288906, speed_float=31.07, power=12
    )
    ds.shift_state.D.SetInParent()
    cl = vehicle_pb2.ClimateState(
        inside_temp_celsius=21.5,
        outside_temp_celsius=9.0,
        is_climate_on=True,
        driver_temp_setting=20.0,
    )
    clo = vehicle_pb2.ClosuresState(locked=True, door_open_trunk_rear=True)
    clo.sentry_mode_state.Armed.SetInParent()
    vs = vcsec_pb2.VehicleStatus(
        vehicleLockState=vcsec_pb2.VEHICLELOCKSTATE_LOCKED,
        vehicleSleepStatus=vcsec_pb2.VEHICLE_SLEEP_STATUS_AWAKE,
    )
    loc = vehicle_pb2.LocationState(latitude=52.1, longitude=5.1, heading=270, gps_as_of=1791720000)
    return {
        "charge": cs,
        "drive": ds,
        "climate": cl,
        "closures": clo,
        "status": vs,
        "location": loc,
    }


class FakeBleVehicle:
    """Stands in for tesla_fleet_api's VehicleBluetooth."""

    def __init__(self, msgs: dict[str, Any] | None = None) -> None:
        self.msgs = msgs or _protobuf_state()
        self.device: object | None = None
        self.calls: list[str] = []
        self.find_error: BaseException | None = None
        self.command_error: BaseException | None = None
        self.fail_reads: set[str] = set()

    def get_device(self) -> object | None:
        return self.device

    async def find_vehicle(self) -> object:
        self.calls.append("find")
        if self.find_error is not None:
            raise self.find_error
        self.device = object()
        return self.device

    async def connect_if_needed(self) -> None:
        self.calls.append("connect")

    async def disconnect(self) -> bool:
        self.calls.append("disconnect")
        return True

    async def vehicle_state(self) -> Any:
        return self.msgs["status"]

    async def wake_up(self, wait: bool = False, timeout: float = 30) -> dict[str, Any]:
        self.calls.append("wake_up")
        return {"response": {"result": True, "reason": ""}}

    async def _read(self, name: str) -> Any:
        if name in self.fail_reads:
            raise BluetoothCommandFailed()
        return self.msgs[name]

    async def charge_state(self) -> Any:
        return await self._read("charge")

    async def drive_state(self) -> Any:
        return await self._read("drive")

    async def climate_state(self) -> Any:
        return await self._read("climate")

    async def closures_state(self) -> Any:
        return await self._read("closures")

    async def location_state(self) -> Any:
        return await self._read("location")

    def __getattr__(self, name: str) -> Any:
        # Any command method (door_lock, set_charge_limit, ...): record and answer OK.
        async def command(*args: Any, **kwargs: Any) -> dict[str, Any]:
            self.calls.append(f"{name}{args}" + (f"{kwargs}" if kwargs else ""))
            if self.command_error is not None:
                raise self.command_error
            return {"response": {"result": True, "reason": ""}}

        return command


@pytest.fixture
def key_path(tmp_path: Path) -> Path:
    path = tmp_path / "socks" / "private_key.pem"
    keys.ensure_key(path)
    return path


def _ble(key_path: Path, vehicle: FakeBleVehicle) -> BleBackend:
    return BleBackend(VIN, key_path, vehicle_factory=lambda: vehicle, clock=lambda: TS)


async def test_ble_read_state_from_protobuf(key_path: Path) -> None:
    vehicle = FakeBleVehicle()
    ble = _ble(key_path, vehicle)
    assert await ble.available()
    s = await ble.read_state()
    assert vehicle.calls[:2] == ["find", "connect"]
    assert s.source is Source.BLE and s.ts == TS
    assert (s.soc, s.usable_soc, s.charge_limit) == (55, 54, 80)
    assert s.charging is ChargingState.DISCONNECTED and s.plugged_in is False
    assert s.charger_power_kw == 0.0 and s.minutes_to_limit == 0  # zeros survive
    assert s.rated_range_km == pytest.approx(180 * KM_PER_MI, abs=0.05)
    assert s.odometer_km == pytest.approx(2889.06 * KM_PER_MI, abs=0.01)
    assert s.shift is ShiftState.D and s.speed_kmh == pytest.approx(50.0, abs=0.05)
    assert s.power_kw == 12.0
    assert (s.inside_temp_c, s.outside_temp_c, s.climate_on, s.target_temp_c) == (
        21.5,
        9.0,
        True,
        20.0,
    )
    assert s.locked is True and s.doors_open == ("trunk",) and s.sentry_on is True
    assert set(s.raw) == {
        "vehicle_status",
        "charge_state",
        "drive_state",
        "climate_state",
        "closures_state",
        "location_state",
    }
    assert (s.latitude, s.longitude, s.heading) == (pytest.approx(52.1), pytest.approx(5.1), 270)
    assert s.gps_as_of == datetime.fromtimestamp(1791720000, UTC)
    await ble.close()
    assert "disconnect" in vehicle.calls


async def test_ble_wakes_sleeping_car_and_survives_optional_failures(key_path: Path) -> None:
    msgs = _protobuf_state()
    msgs["status"] = vcsec_pb2.VehicleStatus(
        vehicleLockState=vcsec_pb2.VEHICLELOCKSTATE_UNLOCKED,
        vehicleSleepStatus=vcsec_pb2.VEHICLE_SLEEP_STATUS_ASLEEP,
    )
    msgs["status"].closureStatuses.frontDriverDoor = vcsec_pb2.CLOSURESTATE_OPEN
    vehicle = FakeBleVehicle(msgs)
    vehicle.fail_reads = {"closures", "climate"}
    s = await _ble(key_path, vehicle).read_state(wake=True)
    assert "wake_up" in vehicle.calls
    assert s.soc == 55
    assert s.inside_temp_c is None
    # closures came from VCSEC instead (unlocked state 0 must not be lost)
    assert s.locked is False and s.doors_open == ("front_left",)


async def test_ble_not_found_is_not_available(key_path: Path) -> None:
    vehicle = FakeBleVehicle()
    vehicle.find_error = ValueError("Device S123 not found")
    with pytest.raises(NotAvailable, match="not found nearby"):
        await _ble(key_path, vehicle).read_state()
    vehicle.find_error = BluetoothTransportError()
    with pytest.raises(NotAvailable, match="cannot connect"):
        await _ble(key_path, vehicle).read_state()


async def test_ble_unconfigured(tmp_path: Path) -> None:
    ble = BleBackend("", tmp_path / "none.pem")
    assert not await ble.available()
    with pytest.raises(NotAvailable):
        await ble.read_state()


async def test_ble_charge_read_failure_is_command_error(key_path: Path) -> None:
    vehicle = FakeBleVehicle()
    vehicle.fail_reads = {"charge"}
    with pytest.raises(CommandError) as exc:
        await _ble(key_path, vehicle).read_state()
    assert not isinstance(exc.value, NotAvailable)


async def test_ble_commands_map_to_library(key_path: Path) -> None:
    vehicle = FakeBleVehicle()
    ble = _ble(key_path, vehicle)
    await ble.send(Command.SET_CHARGE_LIMIT, 85.0)
    await ble.send(Command.SET_TEMP, 21)
    await ble.send(Command.FRUNK)
    await ble.send(Command.SENTRY_OFF)
    await ble.send(Command.FLASH_LIGHTS)
    assert "set_charge_limit(85,)" in vehicle.calls
    assert "set_temps(21.0, 21.0)" in vehicle.calls
    assert "actuate_trunk('front',)" in vehicle.calls
    assert "set_sentry_mode(False,)" in vehicle.calls
    assert "flash_lights()" in vehicle.calls
    with pytest.raises(CommandError, match="50..100"):
        await ble.send(Command.SET_CHARGE_LIMIT, 20)


async def test_ble_command_outcomes(key_path: Path) -> None:
    vehicle = FakeBleVehicle()
    ble = _ble(key_path, vehicle)
    vehicle.command_error = BluetoothUnconfirmedCommand()
    with pytest.raises(CommandError) as exc:
        await ble.send(Command.LOCK)
    assert exc.value.maybe_executed
    vehicle.command_error = BluetoothCommandFailed()
    with pytest.raises(CommandError) as exc:
        await ble.send(Command.LOCK)
    assert not exc.value.maybe_executed

    class Refusing(FakeBleVehicle):
        async def door_lock(self) -> dict[str, Any]:
            return {"response": {"result": False, "reason": "already locked"}}

    with pytest.raises(CommandError, match="already locked"):
        await _ble(key_path, Refusing()).send(Command.LOCK)


# ======================================================================= cloud


class FakeCloudVehicle(FakeBleVehicle):
    def __init__(self) -> None:
        super().__init__()
        self.offline_once = False
        self.data: Any = CLOUD_PAYLOAD

    async def vehicle_data(self, endpoints: list[str]) -> Any:
        self.calls.append("vehicle_data")
        if self.offline_once:
            raise VehicleOffline()
        return self.data

    def __getattr__(self, name: str) -> Any:
        inner = super().__getattr__(name)

        async def command(*args: Any, **kwargs: Any) -> dict[str, Any]:
            if self.offline_once:
                self.offline_once = False
                raise VehicleOffline()
            return await inner(*args, **kwargs)

        return command


async def _token() -> str | None:
    return "not-a-real-token"


def _cloud(key_path: Path, vehicle: FakeCloudVehicle) -> CloudBackend:
    return CloudBackend(VIN, key_path, _token, vehicle_factory=lambda: vehicle, clock=lambda: TS)


async def test_cloud_read_and_asleep(key_path: Path) -> None:
    vehicle = FakeCloudVehicle()
    cloud = _cloud(key_path, vehicle)
    s = await cloud.read_state()
    assert s.source is Source.CLOUD and s.soc == 64
    vehicle.offline_once = True
    with pytest.raises(NotAvailable):
        await cloud.read_state()
    assert "wake_up" not in vehicle.calls  # reads never pay for a wake
    vehicle.data = "garbage"
    with pytest.raises(CommandError):
        await cloud.read_state()


async def test_cloud_command_wakes_offline_car_once(key_path: Path) -> None:
    vehicle = FakeCloudVehicle()
    vehicle.offline_once = True
    await _cloud(key_path, vehicle).send(Command.LOCK)
    assert "wake_up" in vehicle.calls
    assert vehicle.calls[-1] == "door_lock()"


# ======================================================================= router


async def test_router_prefers_ble_and_falls_back_to_cloud() -> None:
    ble = FakeBackend(source=Source.BLE)
    cloud = FakeBackend(source=Source.CLOUD)
    router = Router(ble, cloud)
    assert (await router.read_state()).source is Source.BLE
    assert router.last_source is Source.BLE and router.name is Source.BLE
    ble.fail_reads = True
    assert (await router.read_state()).source is Source.CLOUD
    assert router.last_source is Source.CLOUD


async def test_router_remembers_cloud_while_ble_recently_failed() -> None:
    now = [1000.0]
    ble = FakeBackend(source=Source.BLE, fail_reads=True)
    cloud = FakeBackend(source=Source.CLOUD)
    router = Router(ble, cloud, retry_preferred_after=120, monotonic=lambda: now[0])
    await router.read_state()
    assert ble.reads == 0 and cloud.reads == 1
    ble.fail_reads = False
    now[0] += 60
    await router.read_state()  # BLE failed 60 s ago: go straight to cloud
    assert ble.reads == 0 and cloud.reads == 2
    now[0] += 120
    assert (await router.read_state()).source is Source.BLE  # BLE gets another chance
    assert router.last_source is Source.BLE


async def test_router_prefer_cloud() -> None:
    router = Router(FakeBackend(source=Source.BLE), FakeBackend(source=Source.CLOUD), Source.CLOUD)
    assert (await router.read_state()).source is Source.CLOUD


async def test_router_not_available_reasons() -> None:
    router = Router(
        FakeBackend(source=Source.BLE, available_flag=False),
        FakeBackend(source=Source.CLOUD, fail_reads=True),
    )
    with pytest.raises(NotAvailable) as exc:
        await router.read_state()
    assert "Bluetooth: not set up" in str(exc.value)
    assert "cloud" in str(exc.value)
    assert router.last_error == str(exc.value)
    with pytest.raises(NotAvailable, match="no backend"):
        await Router(None, None).read_state()


class MaybeExecuted(FakeBackend):
    async def send(self, cmd: Command, arg: float | int | None = None) -> None:
        raise CommandError("lost ack", maybe_executed=True)


async def test_router_never_replays_maybe_executed_command() -> None:
    cloud = FakeBackend(source=Source.CLOUD)
    router = Router(MaybeExecuted(source=Source.BLE), cloud)
    with pytest.raises(CommandError, match="lost ack"):
        await router.send(Command.UNLOCK)
    assert cloud.sent == []
    # a provably undelivered command does fall over
    router = Router(FakeBackend(source=Source.BLE, fail_commands=True), cloud)
    await router.send(Command.LOCK)
    assert cloud.sent == [(Command.LOCK, None)]
    with pytest.raises(CommandError):
        await router.send(Command.SET_TEMP, 99)


async def test_router_close_closes_backends() -> None:
    ble, cloud = FakeBackend(), FakeBackend(source=Source.CLOUD)
    await Router(ble, cloud).close()
    assert ble.closed and cloud.closed


def test_build_router(tmp_path: Path) -> None:
    settings = Settings(vin=VIN, key_path=tmp_path / "k.pem", cloud_enabled=True, prefer="cloud")
    r = build_router(settings)
    assert isinstance(r.ble, BleBackend) and r.cloud is None  # no token provider
    r = build_router(settings, _token)
    assert isinstance(r.cloud, CloudBackend) and r.prefer is Source.CLOUD
    r = build_router(Settings(vin=VIN, key_path=tmp_path / "k.pem"), _token)
    assert r.cloud is None  # cloud disabled


def test_fake_backend_factory() -> None:
    assert isinstance(fake_backend(), FakeBackend)


def test_library_classes_exist() -> None:
    """The real classes BleBackend/CloudBackend build on (import only, no I/O)."""
    from tesla_fleet_api.tesla.bluetooth import TeslaBluetooth
    from tesla_fleet_api.tesla.fleet import TeslaFleetApi
    from tesla_fleet_api.tesla.vehicle.bluetooth import VehicleBluetooth
    from tesla_fleet_api.tesla.vehicle.signed import VehicleSigned

    for cls in (VehicleBluetooth, VehicleSigned):
        for method in (
            "door_lock",
            "door_unlock",
            "auto_conditioning_start",
            "auto_conditioning_stop",
            "set_temps",
            "charge_start",
            "charge_stop",
            "set_charge_limit",
            "charge_port_door_open",
            "charge_port_door_close",
            "flash_lights",
            "honk_horn",
            "actuate_trunk",
            "set_sentry_mode",
            "wake_up",
        ):
            assert callable(getattr(cls, method)), (cls, method)
    for method in ("charge_state", "drive_state", "climate_state", "closures_state"):
        assert callable(getattr(VehicleBluetooth, method))
    assert hasattr(TeslaBluetooth, "vehicles") or callable(TeslaBluetooth)
    assert callable(TeslaFleetApi)


async def test_real_library_objects_construct_without_io(key_path: Path) -> None:
    """Build the real tesla-fleet-api vehicles (no scan, no connect, no HTTP request)."""
    from tesla_fleet_api.tesla.vehicle.bluetooth import VehicleBluetooth
    from tesla_fleet_api.tesla.vehicle.signed import VehicleSigned

    ble = BleBackend(VIN, key_path)
    vehicle = ble._make_vehicle()
    assert isinstance(vehicle, VehicleBluetooth)
    assert vehicle.raise_unconfirmed and vehicle.keepalive_interval is None
    assert vehicle.get_device() is None
    await ble.close()  # never connected: nothing to do

    cloud = CloudBackend(VIN, key_path, _token, region="eu")
    signed = cloud._make_vehicle()
    assert isinstance(signed, VehicleSigned)
    assert signed.private_key is not None
    await cloud.close()


async def test_ble_background_read_never_wakes(key_path: Path) -> None:
    msgs = _protobuf_state()
    msgs["status"] = vcsec_pb2.VehicleStatus(
        vehicleSleepStatus=vcsec_pb2.VEHICLE_SLEEP_STATUS_ASLEEP
    )
    vehicle = FakeBleVehicle(msgs)
    ble = _ble(key_path, vehicle)
    with pytest.raises(NotAvailable, match="asleep") as exc:
        await ble.read_state()
    assert exc.value.kind == ASLEEP
    assert "wake_up" not in vehicle.calls
    assert vehicle.wake_if_asleep is True  # restored for commands


async def test_ble_awake_read_disables_library_wake(key_path: Path) -> None:
    seen: list[bool] = []

    class Spy(FakeBleVehicle):
        async def charge_state(self) -> Any:
            seen.append(self.wake_if_asleep)
            return await super().charge_state()

    vehicle = Spy()
    await _ble(key_path, vehicle).read_state()
    await _ble(key_path, vehicle).read_state(wake=True)
    assert seen == [False, True]


class AsleepBackend(FakeBackend):
    async def read_state(self, wake: bool = False, include_location: bool = True) -> Any:
        if wake:
            return await super().read_state(wake=True)
        raise NotAvailable("Bluetooth: car is asleep", kind=ASLEEP)


async def test_router_asleep_is_final_and_wake_is_passed() -> None:
    cloud = FakeBackend(source=Source.CLOUD)
    router = Router(AsleepBackend(source=Source.BLE), cloud)
    with pytest.raises(NotAvailable, match="asleep"):
        await router.read_state()
    assert cloud.reads == 0  # no paid fallback for a sleeping car
    s = await router.read_state(wake=True)
    assert s.source is Source.BLE


def test_fake_backend_only_from_checkout(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import socks

    pkg = tmp_path / "site" / "socks"
    pkg.mkdir(parents=True)
    (tmp_path / "site" / "tests").mkdir()
    (tmp_path / "site" / "tests" / "fakes.py").write_text("raise SystemExit('must not run')\n")
    monkeypatch.setattr(socks, "__file__", str(pkg / "__init__.py"))
    with pytest.raises(RuntimeError, match="source checkout"):
        fake_backend()


class PairingVehicle(FakeBleVehicle):
    def __init__(self, error: BaseException | None = None) -> None:
        super().__init__()
        self.pair_error = error
        self.pair_calls: list[tuple[int, int, float]] = []

    async def pair(self, role: int, form: int, timeout: float) -> None:
        self.pair_calls.append((role, form, timeout))
        if self.pair_error is not None:
            raise self.pair_error


async def test_ble_pair(key_path: Path) -> None:
    from tesla_fleet_api.exceptions import BluetoothTimeout, WhitelistOperationNoPermissionToAdd
    from tesla_protocol.command.keys_pb2 import Role
    from tesla_protocol.command.vcsec_pb2 import KeyFormFactor

    vehicle = PairingVehicle()
    assert await _ble(key_path, vehicle).pair() is True
    assert vehicle.pair_calls == [(Role.ROLE_DRIVER, KeyFormFactor.KEY_FORM_FACTOR_CLOUD_KEY, 60.0)]
    assert await _ble(key_path, vehicle).pair("owner", 5) is True
    assert vehicle.pair_calls[-1][0] == Role.ROLE_OWNER
    assert await _ble(key_path, PairingVehicle(BluetoothTimeout())).pair() is False
    with pytest.raises(CommandError) as exc:
        await _ble(key_path, PairingVehicle(WhitelistOperationNoPermissionToAdd())).pair()
    assert not isinstance(exc.value, NotAvailable)
    with pytest.raises(CommandError, match="role"):
        await _ble(key_path, vehicle).pair("service")
    unreachable = PairingVehicle()
    unreachable.find_error = ValueError("not found")
    with pytest.raises(NotAvailable):
        await _ble(key_path, unreachable).pair()


async def test_ble_location_optional_and_skippable(key_path: Path) -> None:
    vehicle = FakeBleVehicle()
    vehicle.fail_reads = {"location"}
    s = await _ble(key_path, vehicle).read_state()
    assert s.soc == 55 and s.latitude is None and s.gps_as_of is None
    vehicle = FakeBleVehicle()
    s = await _ble(key_path, vehicle).read_state(include_location=False)
    assert "location_state" not in s.raw and s.latitude is None


def test_location_parsing_rules() -> None:
    native = {
        "location_state": {
            "latitude": 1.0,
            "longitude": 2.0,
            "nativeLatitude": 52.2,
            "nativeLongitude": 5.2,
            "gpsAsOf": "1791720000",
        }
    }
    s = parse_ble_payload(VIN, native, TS)
    assert (s.latitude, s.longitude) == (52.2, 5.2) and s.gps_as_of is not None
    zero = parse_ble_payload(VIN, {"location_state": {"heading": 10}}, TS)
    assert zero.latitude is None and zero.heading is None
    bogus = parse_ble_payload(VIN, {"location_state": {"latitude": 99, "longitude": 5}}, TS)
    assert bogus.latitude is None
    cloud = dict(CLOUD_PAYLOAD["response"])
    cloud["drive_state"] = {
        **cloud["drive_state"],
        "latitude": 52.3,
        "longitude": 5.3,
        "heading": 180,
        "gps_as_of": 1791720000,
    }
    c = parse_cloud_payload(VIN, {"response": cloud}, TS)
    assert (c.latitude, c.longitude, c.heading) == (52.3, 5.3, 180)
    assert parse_cloud_payload(VIN, CLOUD_PAYLOAD, TS).latitude is None  # no scope


async def test_cloud_location_endpoint_only_when_asked(key_path: Path) -> None:
    seen: list[list[str]] = []

    class Spy(FakeCloudVehicle):
        async def vehicle_data(self, endpoints: list[str]) -> Any:
            seen.append(endpoints)
            return self.data

    vehicle = Spy()
    await _cloud(key_path, vehicle).read_state()
    await _cloud(key_path, vehicle).read_state(include_location=False)
    assert "location_data" in seen[0] and "location_data" not in seen[1]


def test_redaction_strips_state_location() -> None:
    from socks.core.model import state_to_dict
    from socks.fleet.redact import redact_location

    s = parse_cloud_payload(
        VIN,
        {"drive_state": {"latitude": 52.3, "longitude": 5.3, "heading": 1, "gps_as_of": 1}},
        TS,
    )
    d = redact_location(state_to_dict(s))
    for key in ("latitude", "longitude", "heading", "gps_as_of"):
        assert key not in d
    assert "latitude" not in str(d["raw"])


def test_parse_charge_current_and_schedule() -> None:
    cs = vehicle_pb2.ChargeState(
        charge_current_request=13,
        charge_current_request_max=16,
        scheduled_charging_mode=2,  # ScheduledChargingModeDepartBy
    )
    cs.scheduled_departure_time.seconds = 1791790200
    from socks.core.backend import _to_dict

    s = parse_ble_payload(VIN, {"charge_state": _to_dict(cs)}, TS)
    assert (s.charge_current_request_a, s.charge_current_request_max_a) == (13, 16)
    assert s.scheduled_charging_mode == "DepartBy"
    assert s.scheduled_departure == datetime(2026, 10, 12, 7, 30, tzinfo=UTC)
    rest = {
        "charge_state": {
            "charge_current_request": 10,
            "charge_current_request_max": 32,
            "scheduled_charging_mode": "StartAt",
            "scheduled_departure_time": 1791790200,
        }
    }
    c = parse_cloud_payload(VIN, rest, TS)
    assert c.charge_current_request_a == 10 and c.scheduled_charging_mode == "StartAt"
    assert c.scheduled_departure is None  # not in DepartBy mode
    rest["charge_state"]["scheduled_charging_mode"] = "DepartBy"
    c = parse_cloud_payload(VIN, rest, TS)
    assert c.scheduled_departure == datetime(2026, 10, 12, 7, 30, tzinfo=UTC)
    assert parse_cloud_payload(VIN, {}, TS).scheduled_charging_mode is None


async def test_ble_new_commands_dispatch(key_path: Path) -> None:
    msgs = _protobuf_state()
    msgs["charge"].preconditioning_times.weekdays.SetInParent()
    msgs["charge"].off_peak_hours_end_time = 360
    vehicle = FakeBleVehicle(msgs)
    ble = _ble(key_path, vehicle)
    await ble.send(Command.SET_CHARGE_AMPS, 16.0)
    await ble.send(Command.SET_SCHEDULED_CHARGING, 420)
    await ble.send(Command.SET_SCHEDULED_CHARGING, None)
    await ble.send(Command.SET_SCHEDULED_DEPARTURE, 450)
    assert "set_charging_amps(16,)" in vehicle.calls
    assert "set_scheduled_charging(True, 420)" in vehicle.calls
    assert "set_scheduled_charging(False, 0)" in vehicle.calls
    dep = [c for c in vehicle.calls if c.startswith("set_scheduled_departure")][0]
    for part in (
        "'enable': True",
        "'departure_time': 450",
        "'preconditioning_enabled': True",
        "'preconditioning_weekdays_only': True",
        "'off_peak_charging_enabled': False",
        "'end_off_peak_time': 360",
    ):
        assert part in dep, (part, dep)
    with pytest.raises(CommandError, match="5..32"):
        await ble.send(Command.SET_CHARGE_AMPS, 40)
    with pytest.raises(CommandError):
        await ble.send(Command.SET_SCHEDULED_DEPARTURE, 1440)


async def test_cloud_departure_preserves_options(key_path: Path) -> None:
    vehicle = FakeCloudVehicle()
    resp = dict(CLOUD_PAYLOAD["response"])
    resp["charge_state"] = {
        **resp["charge_state"],
        "preconditioning_enabled": True,
        "preconditioning_times": "all_week",
        "off_peak_charging_enabled": True,
        "off_peak_charging_times": "weekdays",
        "off_peak_hours_end_time": 400,
    }
    vehicle.data = {"response": resp}
    await _cloud(key_path, vehicle).send(Command.SET_SCHEDULED_DEPARTURE, None)
    dep = [c for c in vehicle.calls if c.startswith("set_scheduled_departure")][0]
    assert "'enable': False" in dep and "'preconditioning_weekdays_only': False" in dep
    assert "'off_peak_charging_weekdays_only': True" in dep and "400" in dep


async def test_router_cloud_only_commands(monkeypatch: pytest.MonkeyPatch) -> None:
    from dataclasses import replace

    from socks.core.commands import COMMANDS

    monkeypatch.setitem(COMMANDS, Command.HONK, replace(COMMANDS[Command.HONK], cloud_only=True))
    ble, cloud = FakeBackend(source=Source.BLE), FakeBackend(source=Source.CLOUD)
    await Router(ble, cloud).send(Command.HONK)
    assert ble.sent == [] and cloud.sent == [(Command.HONK, None)]
    with pytest.raises(NotAvailable, match="cloud-only"):
        await Router(ble, None).send(Command.HONK)


async def test_ble_departure_explicit_options(key_path: Path) -> None:
    from socks.core.commands import DepartureArg

    vehicle = FakeBleVehicle()  # car has no preconditioning/off-peak set
    await _ble(key_path, vehicle).send(
        Command.SET_SCHEDULED_DEPARTURE, DepartureArg(450, True, True)
    )
    dep = [c for c in vehicle.calls if c.startswith("set_scheduled_departure")][0]
    for part in (
        "'preconditioning_enabled': True",
        "'preconditioning_weekdays_only': False",
        "'off_peak_charging_enabled': True",
        "'off_peak_charging_weekdays_only': False",
        "'end_off_peak_time': 450",
    ):
        assert part in dep, (part, dep)
    with pytest.raises(CommandError):
        await _ble(key_path, vehicle).send(Command.SET_TEMP, DepartureArg(1))


def test_parse_departure_flags() -> None:
    s = parse_cloud_payload(
        VIN,
        {"charge_state": {"preconditioning_enabled": True, "off_peak_charging_enabled": False}},
        TS,
    )
    assert s.preconditioning_enabled is True and s.off_peak_charging_enabled is False
    b = parse_ble_payload(VIN, {"charge_state": {"preconditioning_times": {"all_week": {}}}}, TS)
    assert b.preconditioning_enabled is True and b.off_peak_charging_enabled is None
