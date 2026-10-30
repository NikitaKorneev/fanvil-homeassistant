"""Events: calls, panel events, the doorbell and door openings."""

import time
from typing import Any

from homeassistant.components.event import EventDeviceClass, EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import FanvilConfigEntry
from .action_url import CALL_EVENTS
from .const import DOORBELL_DEBOUNCE, SIGNAL_DOOR_LOG, SIGNAL_EVENT
from .coordinator import FanvilCoordinator
from .entity import FanvilEntity

PANEL_EVENTS = (
    "key",
    "input_1_triggered",
    "input_1_reset",
    "input_2_triggered",
    "input_2_reset",
    "boot",
    "tamper",
    "motion",
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FanvilConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Action URL events exist always; they fire only with Action URL enabled."""
    coordinator = entry.runtime_data
    entities: list[EventEntity] = [
        FanvilEvent(coordinator, "call", CALL_EVENTS),
        FanvilEvent(coordinator, "panel", PANEL_EVENTS),
        FanvilDoorbell(coordinator),
    ]
    if coordinator.door_log is not None:
        entities.append(FanvilDoorEvent(coordinator))
    async_add_entities(entities)


class FanvilEvent(FanvilEntity, EventEntity):
    """One Home Assistant event per Action URL request."""

    def __init__(
        self, coordinator: FanvilCoordinator, kind: str, types: tuple[str, ...]
    ) -> None:
        super().__init__(coordinator, f"event_{kind}")
        self._attr_translation_key = kind
        self._attr_event_types = list(types)

    @property
    def available(self) -> bool:
        # Events come from the panel itself: do not hide them when a poll fails.
        return True

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_EVENT.format(self.coordinator.config_entry.entry_id),
                self._async_event,
            )
        )

    @callback
    def _async_event(self, data: dict[str, str]) -> None:
        if data["event"] not in self._attr_event_types:
            return
        attributes = {k: v for k, v in data.items() if k not in ("event", "mac")}
        self._trigger_event(data["event"], attributes)
        self.async_write_ha_state()


class FanvilDoorbell(FanvilEvent):
    """The call button as a doorbell.

    A press arrives as a "key" event (cti.KeyTriggerUrl) and, when the button
    dials a number, also as an "outgoing" call: one ring per press.
    """

    _attr_device_class = EventDeviceClass.DOORBELL

    def __init__(self, coordinator: FanvilCoordinator) -> None:
        super().__init__(coordinator, "doorbell", ("ring",))
        self._last_ring = 0.0

    @callback
    def _async_event(self, data: dict[str, str]) -> None:
        if data["event"] not in ("key", "outgoing"):
            return
        now = time.monotonic()
        if now - self._last_ring < DOORBELL_DEBOUNCE:
            return
        self._last_ring = now
        attributes = {"source": data["event"]}
        if data["event"] == "outgoing" and data.get("remote"):
            attributes["called"] = data["remote"]
        self._trigger_event("ring", attributes)
        self.async_write_ha_state()


class FanvilDoorEvent(FanvilEntity, EventEntity):
    """Door log: opened (card, PIN, call, exit button...) or denied."""

    _attr_translation_key = "door"
    _attr_event_types = ["opened", "denied"]

    def __init__(self, coordinator: FanvilCoordinator) -> None:
        super().__init__(coordinator, "event_door")

    @property
    def available(self) -> bool:
        return True

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_DOOR_LOG.format(self.coordinator.config_entry.entry_id),
                self._async_entry,
            )
        )

    @callback
    def _async_entry(self, entry: dict[str, Any] | None) -> None:
        if entry is None:
            return
        attributes = {
            key: value.isoformat() if key == "time" else value
            for key, value in entry.items()
            if key not in ("result", "id")
        }
        self._trigger_event(entry["result"], attributes)
        self.async_write_ha_state()
