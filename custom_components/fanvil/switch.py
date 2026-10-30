"""Fanvil relay held open (relayCtrl, mode long) until switched off.

The panel reports only whether a relay is open, not why. The switch shows the
hold it set itself, so a pulse, a card or a call opening the door does not
flip it; the "Relay N active" binary sensor shows the real relay state.
"""

import asyncio
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import STATE_ON
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

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


class FanvilRelaySwitch(FanvilEntity, SwitchEntity, RestoreEntity):
    """On - held open by this switch, off - not held. A pulse is a button."""

    _attr_translation_key = "relay"

    def __init__(self, coordinator: FanvilCoordinator, relay: int) -> None:
        super().__init__(coordinator, f"relay_{relay}")
        self._relay = relay
        self._attr_translation_placeholders = {"number": str(relay)}
        self._attr_is_on = False

    @property
    def _relay_state(self) -> str:
        return self.coordinator.data.get(relay_key(self._relay), "").lower()

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        # Still held after a restart only if the relay is still open.
        if (last := await self.async_get_last_state()) is not None:
            self._attr_is_on = last.state == STATE_ON and self._relay_state == "open"

    @callback
    def _handle_coordinator_update(self) -> None:
        # Closed by anything (the panel, a pulse, a reboot): no longer held.
        if self._relay_state == "close":
            self._attr_is_on = False
        super()._handle_coordinator_update()

    async def _async_command(self, action: str) -> None:
        try:
            await self.coordinator.client.async_relay(self._relay, action, "long")
        except FanvilError as err:
            raise HomeAssistantError(str(err)) from err
        self._attr_is_on = action == "on"
        self.async_write_ha_state()
        await asyncio.sleep(COMMAND_REFRESH_DELAY)
        await self.coordinator.async_refresh()

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._async_command("on")

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_command("off")
