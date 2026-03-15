"""Select platform for Klimatronik."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .entity import KlimatronikEntity


MODES = ["off", "auto", "manual", "turbo", "quiet"]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up mode select."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([KlimatronikModeSelect(coordinator, entry.entry_id)])


class KlimatronikModeSelect(KlimatronikEntity, SelectEntity):
    """Mode selector."""

    _attr_options = MODES

    def __init__(self, coordinator, entry_id: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry_id}_mode_command"
        self._attr_name = "Mode"
        self._attr_icon = "mdi:tune-variant"

    @property
    def current_option(self) -> str:
        if not self.coordinator.data:
            return "off"
        mode = str(self.coordinator.data.get("mode", "off"))
        if mode in MODES:
            return mode
        return "off"

    async def async_select_option(self, option: str) -> None:
        await self.coordinator.async_set_mode(option)
