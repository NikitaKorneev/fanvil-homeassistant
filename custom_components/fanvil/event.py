"""Action URL events: calls and panel events (button, inputs, boot, alarms)."""

from homeassistant.components.event import EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import FanvilConfigEntry
from .action_url import CALL_EVENTS
from .const import SIGNAL_EVENT
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
    """Event entities exist always; they fire only with Action URL enabled."""
    coordinator = entry.runtime_data
    async_add_entities(
        [
            FanvilEvent(coordinator, "call", CALL_EVENTS),
            FanvilEvent(coordinator, "panel", PANEL_EVENTS),
        ]
    )


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
