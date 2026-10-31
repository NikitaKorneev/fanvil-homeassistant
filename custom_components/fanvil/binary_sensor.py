"""Fanvil SIP registration, relay and input states; motion, noise, tamper."""

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import EntityCategory
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_call_later

from . import FanvilConfigEntry, action_url_enabled
from .action_url import DETECTION_EVENTS
from .const import (
    DETECTION_OFF_DELAY,
    PORTS,
    SIGNAL_EVENT,
    input_key,
    registration_key,
    relay_key,
)
from .coordinator import FanvilCoordinator
from .entity import FanvilEntity

DETECTION_CLASSES = {
    "motion": BinarySensorDeviceClass.MOTION,
    "noise": BinarySensorDeviceClass.SOUND,
    "tamper": BinarySensorDeviceClass.TAMPER,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FanvilConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create binary sensors supported by this firmware."""
    coordinator = entry.runtime_data
    entities: list[BinarySensorEntity] = []
    for kind, key_fn, numbers in (
        ("sip_registered", registration_key, (1, 2)),
        ("relay_active", relay_key, PORTS),
        ("input_open", input_key, PORTS),
    ):
        for number in numbers:
            key = key_fn(number)
            value = coordinator.data.get(key, "").lower()
            if (kind == "sip_registered" and value.isdecimal()) or (
                kind != "sip_registered" and value in ("open", "close")
            ):
                entities.append(FanvilBinarySensor(coordinator, key, kind, number))
    if action_url_enabled(entry):
        # Known only from Action URL events.
        entities.extend(
            FanvilDetectionSensor(coordinator, kind) for kind in DETECTION_EVENTS
        )
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
            # Worth watching: without SIP the call button does not ring.
            # Lines without an account are off by default.
            self._attr_entity_registry_enabled_default = (
                coordinator.data.get(f"sip.line.{number}.RegEnabled") == "1"
            )

    @property
    def is_on(self) -> bool | None:
        value = self.coordinator.data.get(self._key, "").lower()
        if self._kind == "sip_registered":
            return value == "200" if value.isdecimal() else None
        return {"open": True, "close": False}.get(value)


class FanvilDetectionSensor(FanvilEntity, BinarySensorEntity):
    """On when the panel reports motion, noise or tampering.

    The panel sends only the start, so the sensor turns off after
    DETECTION_OFF_DELAY seconds without a new report.
    """

    def __init__(self, coordinator: FanvilCoordinator, kind: str) -> None:
        super().__init__(coordinator, kind)
        self._kind = kind
        self._attr_translation_key = kind
        self._attr_device_class = DETECTION_CLASSES[kind]
        self._attr_is_on = False
        self._cancel_off: CALLBACK_TYPE | None = None

    @property
    def available(self) -> bool:
        # Reports come from the panel itself: do not hide them when a poll fails.
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
        self.async_on_remove(self._cancel)

    @callback
    def _async_event(self, data: dict[str, str]) -> None:
        if data["event"] != self._kind:
            return
        self._cancel()
        self._attr_is_on = True
        self.async_write_ha_state()
        self._cancel_off = async_call_later(
            self.hass, DETECTION_OFF_DELAY, self._async_off
        )

    @callback
    def _async_off(self, _now) -> None:
        self._cancel_off = None
        self._attr_is_on = False
        self.async_write_ha_state()

    @callback
    def _cancel(self) -> None:
        if self._cancel_off:
            self._cancel_off()
            self._cancel_off = None
