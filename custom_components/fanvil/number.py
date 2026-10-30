"""Panel volume: one slider for talk, ring and tone volume."""

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import FanvilConfigEntry
from .api import FanvilError
from .const import VOLUME_FIELDS, VOLUME_RING, VOLUME_TALK, VOLUME_TONE
from .coordinator import FanvilCoordinator
from .entity import FanvilEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FanvilConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Only if the panel returned its volume settings."""
    coordinator = entry.runtime_data
    if all(coordinator.data.get(key, "").isdecimal() for key in VOLUME_FIELDS):
        async_add_entities([FanvilVolume(coordinator)])


class FanvilVolume(FanvilEntity, NumberEntity):
    """0-9 written to talk, ring and tone volume.

    The talk volume cannot go below 1, so 0 means silent ring and tones with the
    quietest talk volume.
    """

    _attr_translation_key = "volume"
    _attr_native_min_value = 0
    _attr_native_max_value = 9
    _attr_native_step = 1
    _attr_mode = NumberMode.SLIDER

    def __init__(self, coordinator: FanvilCoordinator) -> None:
        super().__init__(coordinator, "volume")

    @property
    def native_value(self) -> int | None:
        data = self.coordinator.data
        try:
            talk, ring, tone = (int(data[key]) for key in VOLUME_FIELDS)
        except KeyError, ValueError:
            return None
        return 0 if ring == tone == 0 else talk

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        """The three panel values, in case they were set apart on the panel."""
        data = self.coordinator.data
        return {
            "talk": data.get(VOLUME_TALK, ""),
            "ring": data.get(VOLUME_RING, ""),
            "tones": data.get(VOLUME_TONE, ""),
        }

    async def async_set_native_value(self, value: float) -> None:
        level = min(max(round(value), 0), 9)
        try:
            await self.coordinator.async_write_settings(
                {
                    VOLUME_TALK: str(max(level, 1)),
                    VOLUME_RING: str(level),
                    VOLUME_TONE: str(level),
                }
            )
        except FanvilError as err:
            raise HomeAssistantError(f"Volume not set: {err}") from err
