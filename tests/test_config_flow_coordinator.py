"""Focused tests for config flow and coordinator behavior."""

from __future__ import annotations

import asyncio
import importlib.util
import logging
from pathlib import Path
import sys
import time
import types
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = ROOT / "custom_components" / "klimatronik"


def _ensure_package(name: str, path: Path | None = None) -> None:
    module = sys.modules.get(name)
    if module is None:
        module = types.ModuleType(name)
        if path is not None:
            module.__path__ = [str(path)]
        sys.modules[name] = module


def _install_homeassistant_stubs() -> None:
    if "homeassistant" in sys.modules:
        return

    homeassistant = types.ModuleType("homeassistant")
    sys.modules["homeassistant"] = homeassistant

    const_module = types.ModuleType("homeassistant.const")
    const_module.CONF_HOST = "host"
    const_module.CONF_NAME = "name"

    class Platform:
        FAN = "fan"
        SENSOR = "sensor"
        SELECT = "select"

    const_module.Platform = Platform
    sys.modules["homeassistant.const"] = const_module

    core_module = types.ModuleType("homeassistant.core")

    def callback(func):
        return func

    class HomeAssistant:
        pass

    core_module.callback = callback
    core_module.HomeAssistant = HomeAssistant
    sys.modules["homeassistant.core"] = core_module

    config_entries_module = types.ModuleType("homeassistant.config_entries")

    class ConfigFlow:
        def __init_subclass__(cls, **kwargs):
            return super().__init_subclass__()

        async def async_set_unique_id(self, unique_id: str) -> None:
            self._unique_id = unique_id

        def _abort_if_unique_id_configured(self) -> None:
            return None

        def async_show_menu(self, *, step_id: str, menu_options: list[str]) -> dict:
            return {
                "type": "menu",
                "step_id": step_id,
                "menu_options": menu_options,
            }

        def async_show_form(
            self, *, step_id: str, data_schema, errors, description_placeholders=None
        ) -> dict:
            return {
                "type": "form",
                "step_id": step_id,
                "data_schema": data_schema,
                "errors": errors,
                "description_placeholders": description_placeholders,
            }

        def async_create_entry(self, *, title: str, data: dict) -> dict:
            return {"type": "create_entry", "title": title, "data": data}

    class OptionsFlow:
        def async_show_form(self, *, step_id: str, data_schema, errors=None) -> dict:
            return {
                "type": "form",
                "step_id": step_id,
                "data_schema": data_schema,
                "errors": errors or {},
            }

        def async_create_entry(self, *, title: str, data: dict) -> dict:
            return {"type": "create_entry", "title": title, "data": data}

    config_entries_module.ConfigFlow = ConfigFlow
    config_entries_module.OptionsFlow = OptionsFlow
    config_entries_module.ConfigEntry = SimpleNamespace
    sys.modules["homeassistant.config_entries"] = config_entries_module

    helpers_module = types.ModuleType("homeassistant.helpers")
    sys.modules["homeassistant.helpers"] = helpers_module

    update_coordinator_module = types.ModuleType(
        "homeassistant.helpers.update_coordinator"
    )

    class UpdateFailed(Exception):
        pass

    class DataUpdateCoordinator:
        def __class_getitem__(cls, _item):
            return cls

        def __init__(self, hass, logger, name, update_interval):
            self.hass = hass
            self.logger = logger
            self.name = name
            self.update_interval = update_interval
            self.data = None

        def async_update_listeners(self) -> None:
            return None

        def async_set_updated_data(self, data) -> None:
            self.data = data

    update_coordinator_module.DataUpdateCoordinator = DataUpdateCoordinator
    update_coordinator_module.UpdateFailed = UpdateFailed
    sys.modules["homeassistant.helpers.update_coordinator"] = update_coordinator_module


def _install_voluptuous_stub() -> None:
    if "voluptuous" in sys.modules:
        return

    voluptuous = types.ModuleType("voluptuous")

    class _Marker:
        def __init__(self, key, default=None):
            self.key = key
            self.default = default

        def __hash__(self) -> int:
            return hash((type(self), self.key, self.default))

        def __eq__(self, other: object) -> bool:
            return (
                isinstance(other, type(self))
                and self.key == other.key
                and self.default == other.default
            )

    class Required(_Marker):
        pass

    class Optional(_Marker):
        pass

    class In:
        def __init__(self, options):
            self.options = tuple(options)

    class Coerce:
        def __init__(self, target_type):
            self.target_type = target_type

    class Range:
        def __init__(self, *, min=None, max=None):
            self.min = min
            self.max = max

    class All:
        def __init__(self, *validators):
            self.validators = validators

    class Schema:
        def __init__(self, schema):
            self.schema = schema

    voluptuous.Required = Required
    voluptuous.Optional = Optional
    voluptuous.In = In
    voluptuous.Coerce = Coerce
    voluptuous.Range = Range
    voluptuous.All = All
    voluptuous.Schema = Schema
    sys.modules["voluptuous"] = voluptuous


def _load_module(module_name: str, file_name: str):
    spec = importlib.util.spec_from_file_location(module_name, PACKAGE_ROOT / file_name)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


_ensure_package("custom_components", ROOT / "custom_components")
_ensure_package("custom_components.klimatronik", PACKAGE_ROOT)
_install_homeassistant_stubs()
_install_voluptuous_stub()

CONST_MODULE = _load_module("custom_components.klimatronik.const", "const.py")
API_MODULE = _load_module("custom_components.klimatronik.api", "api.py")
COORDINATOR_MODULE = _load_module(
    "custom_components.klimatronik.coordinator", "coordinator.py"
)
CONFIG_FLOW_MODULE = _load_module(
    "custom_components.klimatronik.config_flow", "config_flow.py"
)

DOMAIN = CONST_MODULE.DOMAIN
CONF_DEFAULT_INTENSITY = CONST_MODULE.CONF_DEFAULT_INTENSITY
CONF_MANUAL_INFLOW = CONST_MODULE.CONF_MANUAL_INFLOW
CONF_MANUAL_OUTFLOW = CONST_MODULE.CONF_MANUAL_OUTFLOW
CONF_QUIET_WEEKDAY_START = CONST_MODULE.CONF_QUIET_WEEKDAY_START
CONF_QUIET_WEEKDAY_END = CONST_MODULE.CONF_QUIET_WEEKDAY_END
CONF_QUIET_WEEKEND_START = CONST_MODULE.CONF_QUIET_WEEKEND_START
CONF_QUIET_WEEKEND_END = CONST_MODULE.CONF_QUIET_WEEKEND_END
CONF_TURBO_DURATION = CONST_MODULE.CONF_TURBO_DURATION
CONF_TURBO_RPM = CONST_MODULE.CONF_TURBO_RPM
DEFAULT_INTENSITY = CONST_MODULE.DEFAULT_INTENSITY
DEFAULT_MANUAL_INFLOW = CONST_MODULE.DEFAULT_MANUAL_INFLOW
DEFAULT_MANUAL_OUTFLOW = CONST_MODULE.DEFAULT_MANUAL_OUTFLOW
DEFAULT_QUIET_WEEKDAY_START = CONST_MODULE.DEFAULT_QUIET_WEEKDAY_START
DEFAULT_QUIET_WEEKDAY_END = CONST_MODULE.DEFAULT_QUIET_WEEKDAY_END
DEFAULT_QUIET_WEEKEND_START = CONST_MODULE.DEFAULT_QUIET_WEEKEND_START
DEFAULT_QUIET_WEEKEND_END = CONST_MODULE.DEFAULT_QUIET_WEEKEND_END
DEFAULT_TURBO_DURATION = CONST_MODULE.DEFAULT_TURBO_DURATION
DEFAULT_TURBO_RPM = CONST_MODULE.DEFAULT_TURBO_RPM

KlimatronikStateSample = API_MODULE.KlimatronikStateSample
KlimatronikConnectionError = API_MODULE.KlimatronikConnectionError
KlimatronikCoordinator = COORDINATOR_MODULE.KlimatronikCoordinator
UpdateFailed = COORDINATOR_MODULE.UpdateFailed
KlimatronikConfigFlow = CONFIG_FLOW_MODULE.KlimatronikConfigFlow
KlimatronikOptionsFlow = CONFIG_FLOW_MODULE.KlimatronikOptionsFlow


def _entry(**overrides):
    data = {
        "host": "192.168.1.20",
        "name": "Unit",
        CONF_DEFAULT_INTENSITY: DEFAULT_INTENSITY,
        CONF_MANUAL_INFLOW: DEFAULT_MANUAL_INFLOW,
        CONF_MANUAL_OUTFLOW: DEFAULT_MANUAL_OUTFLOW,
        CONF_TURBO_DURATION: DEFAULT_TURBO_DURATION,
        CONF_TURBO_RPM: DEFAULT_TURBO_RPM,
        CONF_QUIET_WEEKDAY_START: DEFAULT_QUIET_WEEKDAY_START,
        CONF_QUIET_WEEKDAY_END: DEFAULT_QUIET_WEEKDAY_END,
        CONF_QUIET_WEEKEND_START: DEFAULT_QUIET_WEEKEND_START,
        CONF_QUIET_WEEKEND_END: DEFAULT_QUIET_WEEKEND_END,
    }
    data.update(overrides.pop("data", {}))
    entry = SimpleNamespace(
        entry_id="entry-1",
        data=data,
        options=overrides.pop("options", {}),
    )
    for key, value in overrides.items():
        setattr(entry, key, value)
    return entry


def _hass(entries: list | None = None):
    updates: list[dict] = []

    def async_update_entry(entry, *, data=None, options=None):
        updates.append({"entry": entry, "data": data, "options": options})
        if data is not None:
            entry.data = data
        if options is not None:
            entry.options = options

    return SimpleNamespace(
        data={DOMAIN: {"logger": logging.getLogger("test")}},
        config_entries=SimpleNamespace(
            async_entries=lambda _domain: list(entries or []),
            async_update_entry=async_update_entry,
        ),
        async_create_task=lambda coro: asyncio.create_task(coro),
        updates=updates,
    )


class ConfigFlowBehaviorTests(unittest.IsolatedAsyncioTestCase):
    async def test_manual_step_reports_invalid_time(self) -> None:
        flow = KlimatronikConfigFlow()
        with patch.object(
            CONFIG_FLOW_MODULE.socket, "gethostbyname", return_value="127.0.0.1"
        ):
            result = await flow.async_step_manual(
                {
                    "host": "demo.local",
                    "name": "Demo",
                    CONF_QUIET_WEEKDAY_START: "25:00",
                }
            )

        self.assertEqual(result["type"], "form")
        self.assertEqual(result["errors"][CONF_QUIET_WEEKDAY_START], "invalid_time")

    async def test_pick_discovered_creates_entry_with_trimmed_host(self) -> None:
        flow = KlimatronikConfigFlow()
        flow._discovered_hosts = ["192.168.1.50"]

        result = await flow.async_step_pick_discovered(
            {
                "host": " 192.168.1.50 ",
                "name": "Hallway",
                CONF_DEFAULT_INTENSITY: 44,
            }
        )

        self.assertEqual(result["type"], "create_entry")
        self.assertEqual(result["title"], "Hallway")
        self.assertEqual(result["data"]["host"], "192.168.1.50")

    def test_discover_subnet_prefixes_prioritizes_entries_and_skips_non_lan(
        self,
    ) -> None:
        flow = KlimatronikConfigFlow()
        flow.hass = _hass(
            entries=[
                _entry(data={"host": "192.168.10.25"}),
                _entry(data={"host": "127.0.0.1"}),
            ]
        )
        flow._detect_local_subnet_prefixes = Mock(
            return_value=["169.254.1", "192.168.10", "10.0.0"]
        )

        prefixes = flow._discover_subnet_prefixes()

        self.assertEqual(prefixes, ["192.168.10", "10.0.0"])

    async def test_options_quiet_step_updates_entry_and_returns_options(self) -> None:
        entry = _entry(options={CONF_DEFAULT_INTENSITY: 40})
        hass = _hass()
        flow = KlimatronikOptionsFlow(entry)
        flow.hass = hass

        result = await flow.async_step_quiet(
            {
                CONF_QUIET_WEEKDAY_START: "21:30",
                CONF_QUIET_WEEKDAY_END: "06:15",
                CONF_QUIET_WEEKEND_START: "23:45",
                CONF_QUIET_WEEKEND_END: "08:00",
            }
        )

        self.assertEqual(result["type"], "create_entry")
        self.assertEqual(result["data"][CONF_DEFAULT_INTENSITY], 40)
        self.assertEqual(hass.updates[0]["data"]["host"], "192.168.1.20")
        self.assertEqual(hass.updates[0]["data"]["name"], "Unit")


class CoordinatorBehaviorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.entry = _entry()
        self.hass = _hass()
        self.coordinator = KlimatronikCoordinator(self.hass, self.entry)

    async def test_async_set_mode_dispatches_auto_and_rejects_unknown_mode(
        self,
    ) -> None:
        self.coordinator.async_set_auto = AsyncMock()

        await self.coordinator.async_set_mode("auto", intensity=55)

        self.coordinator.async_set_auto.assert_awaited_once_with(55)
        with self.assertRaises(UpdateFailed):
            await self.coordinator.async_set_mode("boost")

    async def test_async_update_setting_updates_options_and_cached_data(self) -> None:
        self.coordinator.data = {"mode": "auto"}
        self.coordinator.async_update_listeners = Mock()

        await self.coordinator.async_update_setting(CONF_QUIET_WEEKDAY_START, " 21:15 ")

        self.assertEqual(self.coordinator.quiet_weekday_start, "21:15")
        self.assertEqual(
            self.hass.updates[0]["options"][CONF_QUIET_WEEKDAY_START], "21:15"
        )
        self.assertEqual(self.coordinator.data[CONF_QUIET_WEEKDAY_START], "21:15")
        self.coordinator.async_update_listeners.assert_called_once_with()

    async def test_async_update_data_returns_cached_data_when_stream_is_fresh(
        self,
    ) -> None:
        self.coordinator.data = {"mode": "auto", "available": False}
        self.coordinator._consecutive_failures = 1
        self.coordinator._stream_last_sample_monotonic = time.monotonic()
        self.coordinator._poll_state = AsyncMock()

        result = await self.coordinator._async_update_data()

        self.assertEqual(result["mode"], "auto")
        self.assertTrue(result["available"])
        self.assertEqual(self.coordinator._consecutive_failures, 0)
        self.coordinator._poll_state.assert_not_called()

    async def test_async_update_data_uses_cached_data_for_first_poll_failure(
        self,
    ) -> None:
        self.coordinator.data = {"mode": "manual", "available": True}
        self.coordinator._poll_state = AsyncMock(
            side_effect=KlimatronikConnectionError("offline")
        )

        result = await self.coordinator._async_update_data()

        self.assertEqual(result["mode"], "manual")
        self.assertTrue(result["available"])
        self.assertEqual(self.coordinator._consecutive_failures, 1)

    def test_build_state_payload_preserves_sticky_values_and_last_mode(self) -> None:
        first = KlimatronikStateSample(
            timestamp="2024-01-01T00:00:00+00:00",
            frame_type=1,
            raw={"iintensity": 35},
            decoded={"app_mode": "auto", "temp_inside_c": 21.5},
        )
        second = KlimatronikStateSample(
            timestamp="2024-01-01T00:00:01+00:00",
            frame_type=1,
            raw={},
            decoded={"temp_inside_c": None},
        )

        self.coordinator._build_state_payload(first)
        payload = self.coordinator._build_state_payload(second)

        self.assertEqual(payload["mode"], "auto")
        self.assertEqual(payload["intensity"], 35)
        self.assertEqual(payload["decoded"]["temp_inside_c"], 21.5)


if __name__ == "__main__":
    unittest.main()
