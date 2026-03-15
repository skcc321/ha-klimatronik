"""Sensor platform for Klimatronik."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONCENTRATION_PARTS_PER_BILLION, CONCENTRATION_PARTS_PER_MILLION, PERCENTAGE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .entity import KlimatronikEntity


ValueFn = Callable[[dict[str, Any]], Any]

DEFAULT_TEMP_PROBE_MAP = {
    "inflow_inlet": "jt3",
    "inflow_outlet": "jt2",
    "outflow_inlet": "inside",
    "outflow_outlet": "jt4",
}


def _temp_for_probe(decoded: dict[str, Any], probe: str) -> Any:
    if probe == "inside":
        return decoded.get("temp_inside_c")
    if probe == "jt2":
        return decoded.get("temp_jt2_c") if decoded.get("temp_jt2_c") is not None else decoded.get("temp_outside_c")
    if probe == "jt3":
        return decoded.get("temp_jt3_c") if decoded.get("temp_jt3_c") is not None else _temp_for_probe(decoded, "jt2")
    if probe == "jt4":
        return decoded.get("temp_jt4_c")
    if probe == "jt5":
        return decoded.get("temp_jt5_c")
    return None


def _mapped_temp(data: dict[str, Any], slot: str) -> Any:
    decoded = data.get("decoded", {})
    semantic_key = f"temp_{slot}_c"
    if decoded.get(semantic_key) is not None:
        return decoded.get(semantic_key)
    probe = DEFAULT_TEMP_PROBE_MAP[slot]
    return _temp_for_probe(decoded, probe)


@dataclass(frozen=True, slots=True, kw_only=True)
class KlimatronikSensorDescription(SensorEntityDescription):
    """Klimatronik sensor description."""

    value_fn: ValueFn


SENSORS: tuple[KlimatronikSensorDescription, ...] = (
    KlimatronikSensorDescription(
        key="mode",
        name="Mode",
        icon="mdi:tune-variant",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.get("mode"),
    ),
    KlimatronikSensorDescription(
        key="temp_inside_c",
        name="Temperature Inside",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: data.get("decoded", {}).get("temp_inside_c"),
    ),
    KlimatronikSensorDescription(
        key="temp_outside_c",
        name="Temperature Outside",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _mapped_temp(data, "inflow_inlet"),
    ),
    KlimatronikSensorDescription(
        key="humidity_inside_pct",
        name="Humidity Inside",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.HUMIDITY,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: data.get("decoded", {}).get("humidity_inside_pct"),
    ),
    KlimatronikSensorDescription(
        key="co2",
        name="CO2",
        native_unit_of_measurement=CONCENTRATION_PARTS_PER_MILLION,
        device_class=SensorDeviceClass.CO2,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: data.get("decoded", {}).get("mq1.sgp30.eCO2"),
    ),
    KlimatronikSensorDescription(
        key="tvoc",
        name="TVOC",
        icon="mdi:molecule",
        native_unit_of_measurement=CONCENTRATION_PARTS_PER_BILLION,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: data.get("decoded", {}).get("mq1.sgp30.TVOC"),
    ),
    KlimatronikSensorDescription(
        key="fan_inflow_rpm",
        name="Fan Inflow RPM",
        icon="mdi:fan",
        native_unit_of_measurement="rpm",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.get("decoded", {}).get("ff1.rpm"),
    ),
    KlimatronikSensorDescription(
        key="fan_outflow_rpm",
        name="Fan Outflow RPM",
        icon="mdi:fan",
        native_unit_of_measurement="rpm",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.get("decoded", {}).get("ff2.rpm"),
    ),
    KlimatronikSensorDescription(
        key="temp_inflow_inlet_c",
        name="Inflow Inlet Temperature",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: _mapped_temp(data, "inflow_inlet"),
    ),
    KlimatronikSensorDescription(
        key="temp_inflow_outlet_c",
        name="Inflow Outlet Temperature",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: _mapped_temp(data, "inflow_outlet"),
    ),
    KlimatronikSensorDescription(
        key="temp_outflow_inlet_c",
        name="Outflow Inlet Temperature",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: _mapped_temp(data, "outflow_inlet"),
    ),
    KlimatronikSensorDescription(
        key="temp_outflow_outlet_c",
        name="Outflow Outlet Temperature",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: _mapped_temp(data, "outflow_outlet"),
    ),
    KlimatronikSensorDescription(
        key="efficiency_coeff_pct",
        name="Efficiency Coeff",
        icon="mdi:percent-circle-outline",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: data.get("decoded", {}).get("fcoeffi"),
    ),
    KlimatronikSensorDescription(
        key="fan_inflow_pwm",
        name="Fan Inflow PWM",
        icon="mdi:fan-chevron-up",
        native_unit_of_measurement="pwm",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.get("decoded", {}).get("ff1.pwm"),
    ),
    KlimatronikSensorDescription(
        key="fan_outflow_pwm",
        name="Fan Outflow PWM",
        icon="mdi:fan-chevron-down",
        native_unit_of_measurement="pwm",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.get("decoded", {}).get("ff2.pwm"),
    ),
    KlimatronikSensorDescription(
        key="light",
        name="Ambient Light",
        icon="mdi:brightness-6",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.get("decoded", {}).get("light")
        if data.get("decoded", {}).get("light") is not None
        else (
            data.get("decoded", {}).get("il1.ltr329")
            if data.get("decoded", {}).get("il1.ltr329") is not None
            else 0
        ),
    ),
    KlimatronikSensorDescription(
        key="turbo_duration_s",
        name="Turbo Duration",
        icon="mdi:timer-outline",
        native_unit_of_measurement="s",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.get("decoded", {}).get("turbo_duration_s")
        if data.get("decoded", {}).get("turbo_duration_s") is not None
        else (
            data.get("decoded", {}).get("hduration")
            if data.get("decoded", {}).get("hduration") is not None
            else data.get("turbo_duration")
        ),
    ),
    KlimatronikSensorDescription(
        key="heater_state",
        name="Heater State",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.get("decoded", {}).get("heater_state"),
    ),
    KlimatronikSensorDescription(
        key="defroster_state",
        name="Defroster State",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.get("decoded", {}).get("defroster_state"),
    ),
    KlimatronikSensorDescription(
        key="alarm_state",
        name="Alarm State",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.get("decoded", {}).get("alarm_state"),
    ),
    KlimatronikSensorDescription(
        key="servo_state",
        name="Servo State",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.get("decoded", {}).get("servo_state"),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensor entities."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        KlimatronikSensor(coordinator, entry.entry_id, description)
        for description in SENSORS
    )


class KlimatronikSensor(KlimatronikEntity, SensorEntity):
    """Klimatronik telemetry sensor."""

    entity_description: KlimatronikSensorDescription

    def __init__(
        self,
        coordinator,
        entry_id: str,
        description: KlimatronikSensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry_id}_{description.key}"

    @property
    def native_value(self) -> Any:
        data = self.coordinator.data or {}
        return self.entity_description.value_fn(data)

    @property
    def icon(self) -> str | None:
        key = self.entity_description.key
        value = self.native_value
        if key == "alarm_state":
            return "mdi:alert-circle" if value == "on" else "mdi:check-circle-outline"
        if key == "heater_state":
            return "mdi:radiator" if value == "on" else "mdi:radiator-disabled"
        if key == "defroster_state":
            return "mdi:snowflake-melt" if value == "on" else "mdi:snowflake-off"
        if key == "servo_state":
            return "mdi:swap-horizontal-bold" if value == "on" else "mdi:swap-horizontal"
        return self.entity_description.icon

    @property
    def available(self) -> bool:
        return super().available and bool(self.coordinator.data)
