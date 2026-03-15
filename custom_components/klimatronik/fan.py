"""Fan platform for Klimatronik."""

from __future__ import annotations

from typing import Any

from homeassistant.components.fan import (
    FanEntity,
    FanEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import UpdateFailed

from .const import DOMAIN
from .entity import KlimatronikEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up fan entity."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([KlimatronikFan(coordinator, entry.entry_id)])


class KlimatronikFan(KlimatronikEntity, FanEntity):
    """Single Klimatronik fan device."""

    _attr_supported_features = (
        FanEntityFeature.TURN_ON
        | FanEntityFeature.TURN_OFF
        | FanEntityFeature.SET_SPEED
        | FanEntityFeature.PRESET_MODE
    )
    _attr_preset_modes = ["auto", "manual", "turbo", "quiet"]

    def __init__(self, coordinator, entry_id: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry_id}_fan"
        self._attr_name = "Ventilation"
        self._attr_icon = "mdi:fan"

    @property
    def is_on(self) -> bool:
        return self._mode != "off"

    @property
    def percentage(self) -> int | None:
        if not self.coordinator.data:
            return self.coordinator.default_intensity
        value = self.coordinator.data.get("intensity")
        if value is None:
            return self.coordinator.default_intensity
        return int(value)

    @property
    def preset_mode(self) -> str | None:
        mode = self._mode
        if mode == "off":
            return None
        if mode in self._attr_preset_modes:
            return mode
        return "auto"

    @property
    def _mode(self) -> str:
        if not self.coordinator.data:
            return "off"
        return str(self.coordinator.data.get("mode", "off"))

    async def async_turn_off(self, **kwargs: Any) -> None:
        _ = kwargs
        await self.coordinator.async_set_off()

    async def async_turn_on(
        self,
        percentage: int | None = None,
        preset_mode: str | None = None,
        **kwargs: Any,
    ) -> None:
        _ = kwargs
        if preset_mode:
            await self.async_set_preset_mode(preset_mode)
            return
        await self.coordinator.async_set_auto(percentage or self.coordinator.default_intensity)

    async def async_set_percentage(self, percentage: int) -> None:
        await self.coordinator.async_set_auto(percentage)

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        if preset_mode == "auto":
            await self.coordinator.async_set_auto(self.percentage or self.coordinator.default_intensity)
            return
        if preset_mode == "manual":
            await self.coordinator.async_set_manual()
            return
        if preset_mode == "turbo":
            await self.coordinator.async_set_turbo()
            return
        if preset_mode == "quiet":
            await self.coordinator.async_set_quiet()
            return
        raise UpdateFailed(f"Unsupported preset_mode: {preset_mode}")
