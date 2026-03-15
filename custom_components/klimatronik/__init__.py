"""Klimatronik Home Assistant integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED, EVENT_HOMEASSISTANT_STOP
from homeassistant.core import CoreState, HomeAssistant
from homeassistant.helpers import config_validation as cv

from .const import DOMAIN, PLATFORMS
from .coordinator import KlimatronikCoordinator

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up integration via UI only."""
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN].setdefault("logger", logging.getLogger(__package__))
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up from config entry."""
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN].setdefault("logger", logging.getLogger(__package__))

    coordinator = KlimatronikCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    hass.data[DOMAIN][entry.entry_id] = coordinator
    async def _async_stop_background_session(_event) -> None:
        # Home Assistant can stop without unloading config entries first. Shut
        # down the read loop on the stop event so background tasks do not
        # survive into the final writes shutdown stage.
        await coordinator.async_shutdown()

    async def _start_background_session(_event) -> None:
        await coordinator.async_enable_background_session()

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    entry.async_on_unload(hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _async_stop_background_session))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Keep HA startup on the normal first-refresh path. The long-lived owner
    # session is enabled only after startup so the overview page does not block
    # waiting on background socket lifecycle.
    if hass.state is CoreState.running:
        hass.async_create_task(coordinator.async_enable_background_session())
    else:
        entry.async_on_unload(hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, _start_background_session))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload entry."""
    coordinator = hass.data[DOMAIN].get(entry.entry_id)
    if coordinator is not None:
        await coordinator.async_shutdown()
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unloaded


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload entry after options update so runtime settings take effect."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """No-op migration hook for future schema updates."""
    _ = hass
    _ = entry
    return True
