"""Fanvil relay held open (relayCtrl, mode long) until switched off."""

import asyncio
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import FanvilConfigEntry
from .api import FanvilError
from .const import COMMAND_REFRESH_DELAY, relay_key
from .coordinator import FanvilCoordinator
from .entity import FanvilEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FanvilConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """One switch per relay the panel reports a state for."""
    coordinator = entry.runtime_data
    async_add_entities(
        FanvilRelaySwitch(coordinator, relay)
        for relay in (1, 2)
        if coordinator.data.get(relay_key(relay), "").lower() in ("open", "close")
    )


class FanvilRelaySwitch(FanvilEntity, SwitchEntity):
    """On - the relay stays open, off - closed. A pulse is a separate button."""

    _attr_translation_key = "relay"

    def __init__(self, coordinator: FanvilCoordinator, relay: int) -> None:
        super().__init__(coordinator, f"relay_{relay}")
        self._relay = relay
        self._attr_translation_placeholders = {"number": str(relay)}

    @property
    def is_on(self) -> bool | None:
        value = self.coordinator.data.get(relay_key(self._relay), "").lower()
        return {"open": True, "close": False}.get(value)

    async def _async_command(self, action: str) -> None:
        try:
            await self.coordinator.client.async_relay(self._relay, action, "long")
        except FanvilError as err:
            raise HomeAssistantError(str(err)) from err
        await asyncio.sleep(COMMAND_REFRESH_DELAY)
        await self.coordinator.async_refresh()

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._async_command("on")

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_command("off")
