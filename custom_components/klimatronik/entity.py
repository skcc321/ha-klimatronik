"""Shared entity helpers."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import KlimatronikCoordinator


class KlimatronikEntity(CoordinatorEntity[KlimatronikCoordinator]):
    """Base entity for Klimatronik devices."""

    _attr_has_entity_name = True

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.host)},
            name=self.coordinator.display_name,
            manufacturer="Klimatronik",
            model="LAN Ventilation Unit",
            configuration_url=f"http://{self.coordinator.host}",
        )
