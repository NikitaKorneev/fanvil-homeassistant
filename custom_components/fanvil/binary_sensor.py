"""Fanvil SIP registration, relay and physical input states."""

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import FanvilConfigEntry
from .const import input_key, registration_key, relay_key
from .coordinator import FanvilCoordinator
from .entity import FanvilEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FanvilConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create binary sensors supported by this firmware."""
    coordinator = entry.runtime_data
    entities = []
    for kind, key_fn in (
        ("sip_registered", registration_key),
        ("relay_active", relay_key),
        ("input_open", input_key),
    ):
        for number in (1, 2):
            key = key_fn(number)
            value = coordinator.data.get(key, "").lower()
            if (kind == "sip_registered" and value.isdecimal()) or (
                kind != "sip_registered" and value in ("open", "close")
            ):
                entities.append(FanvilBinarySensor(coordinator, key, kind, number))
    async_add_entities(entities)


class FanvilBinarySensor(FanvilEntity, BinarySensorEntity):
    """Report a relay/input state, never infer physical door position."""

    def __init__(
        self, coordinator: FanvilCoordinator, key: str, kind: str, number: int
    ) -> None:
        super().__init__(coordinator, key)
        self._kind = kind
        self._attr_translation_key = kind
        self._attr_translation_placeholders = {"number": str(number)}
        if kind == "sip_registered":
            self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def is_on(self) -> bool | None:
        value = self.coordinator.data.get(self._key, "").lower()
        if self._kind == "sip_registered":
            return value == "200" if value.isdecimal() else None
        return {"open": True, "close": False}.get(value)
