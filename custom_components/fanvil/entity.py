"""Shared device information for Fanvil entities."""

from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import device_unique_id
from .const import DOMAIN, FIRMWARE, HARDWARE, MAC, MODEL, SERIAL
from .coordinator import FanvilCoordinator


class FanvilEntity(CoordinatorEntity[FanvilCoordinator]):
    """An entity belonging to one physical panel."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: FanvilCoordinator, key: str) -> None:
        super().__init__(coordinator)
        info = coordinator.data
        unique_id = device_unique_id(info)
        self._key = key
        self._attr_unique_id = f"{unique_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, unique_id)},
            manufacturer="Fanvil",
            model=info[MODEL],
            name=f"Fanvil {info[MODEL]}",
            sw_version=info.get(FIRMWARE) or None,
            hw_version=info.get(HARDWARE) or None,
            serial_number=info.get(SERIAL) or None,
            configuration_url=str(coordinator.client.base_url),
        )
        if info.get(MAC):
            self._attr_device_info["connections"] = {
                (CONNECTION_NETWORK_MAC, info[MAC])
            }
