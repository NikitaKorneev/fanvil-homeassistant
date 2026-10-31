"""Explicit Fanvil relay and restart actions."""

import asyncio

from homeassistant.components.button import ButtonDeviceClass, ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import FanvilConfigEntry
from .api import FanvilError
from .const import COMMAND_REFRESH_DELAY, PORTS, PULSE_REFRESH_DELAY, relay_key
from .coordinator import FanvilCoordinator
from .entity import FanvilEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FanvilConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        [
            FanvilButton(coordinator, relay)
            for relay in PORTS
            if coordinator.data.get(relay_key(relay), "").lower() in ("open", "close")
        ]
        + [FanvilButton(coordinator, None)]
    )


class FanvilButton(FanvilEntity, ButtonEntity):
    """A single non-retried command; normal polling observes the result."""

    def __init__(self, coordinator: FanvilCoordinator, relay: int | None) -> None:
        super().__init__(coordinator, f"pulse_relay_{relay}" if relay else "restart")
        self._relay = relay
        if relay:
            self._attr_translation_key = "pulse_relay"
            self._attr_translation_placeholders = {"number": str(relay)}
        else:
            self._attr_translation_key = "restart"
            self._attr_device_class = ButtonDeviceClass.RESTART
            self._attr_entity_category = EntityCategory.CONFIG

    async def async_press(self) -> None:
        try:
            if self._relay:
                await self.coordinator.client.async_pulse_relay(self._relay)
            else:
                await self.coordinator.client.async_reboot()
        except FanvilError as err:
            raise HomeAssistantError(str(err)) from err
        if self._relay:
            await asyncio.sleep(COMMAND_REFRESH_DELAY)
            await self.coordinator.async_refresh()
            # The panel closes the relay by itself (door open time): show it.
            self.hass.async_create_background_task(
                self._async_refresh_later(), f"fanvil_relay_{self._relay}_closed"
            )

    async def _async_refresh_later(self) -> None:
        await asyncio.sleep(PULSE_REFRESH_DELAY)
        await self.coordinator.async_refresh()
