"""Fanvil diagnostic and call sensors."""

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import FanvilConfigEntry
from .action_url import STATUS_FOREIGN, STATUS_OURS, STATUS_REJECTED, summary
from .const import (
    FIRMWARE,
    IP_ADDRESS,
    SIGNAL_DOOR_LOG,
    UPTIME,
    call_key,
    registration_key,
    sip_number_key,
)
from .coordinator import FanvilCoordinator
from .entity import FanvilEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FanvilConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create only sensors whose keys were returned by the panel."""
    coordinator = entry.runtime_data
    entities: list[FanvilSensor] = []
    for key, name in (
        (IP_ADDRESS, "ip_address"),
        (FIRMWARE, "firmware"),
        (UPTIME, "uptime"),
    ):
        if key in coordinator.data:
            entities.append(FanvilSensor(coordinator, key, name))
    for line in (1, 2):
        key = registration_key(line)
        if key in coordinator.data:
            entities.append(FanvilSensor(coordinator, key, "sip_registration", line))
    for line in (1, 2):
        key = sip_number_key(line)
        if key in coordinator.data:
            entities.append(FanvilSensor(coordinator, key, "sip_number", line))
    for line in (0, 1, 2):
        key = call_key(line)
        if key in coordinator.data:
            entities.append(FanvilSensor(coordinator, key, "call_state", line))
    entities.append(FanvilActionUrlSensor(coordinator))
    if coordinator.door_log is not None:
        entities.append(FanvilLastOpeningSensor(coordinator))
    async_add_entities(entities)


class FanvilSensor(FanvilEntity, SensorEntity):
    """Expose documented values without inventing undocumented idle states."""

    def __init__(
        self,
        coordinator: FanvilCoordinator,
        key: str,
        kind: str,
        line: int | None = None,
    ) -> None:
        super().__init__(coordinator, key)
        self._attr_translation_key = kind
        if line is not None:
            self._attr_translation_placeholders = {"line": str(line)}
        if kind != "call_state":
            self._attr_entity_category = EntityCategory.DIAGNOSTIC
        if kind == "uptime":
            self._attr_device_class = SensorDeviceClass.DURATION
            self._attr_native_unit_of_measurement = UnitOfTime.SECONDS
            self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> str | float | None:
        """Use unknown for missing/empty data, rather than assuming idle/closed."""
        value = self.coordinator.data.get(self._key)
        if not value:
            return None
        if self._key == UPTIME:
            try:
                return float(value.removesuffix("s"))
            except ValueError:
                return None
        return value

    @property
    def extra_state_attributes(self) -> dict[str, str] | None:
        """Show the SIP account next to its number (never the password)."""
        if self._attr_translation_key != "sip_number":
            return None
        prefix = self._key.removesuffix("PhoneNumber")
        data = self.coordinator.data
        return {
            "display_name": data.get(prefix + "DisplayName", ""),
            "server": data.get(prefix + "RegAddr", ""),
            "enabled": data.get(prefix + "RegEnabled") == "1",
        }


class FanvilActionUrlSensor(FanvilEntity, SensorEntity):
    """Whether the panel reports events to Home Assistant, and which ones."""

    _attr_translation_key = "action_url"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ["on", "partial", "off"]

    def __init__(self, coordinator: FanvilCoordinator) -> None:
        super().__init__(coordinator, "action_url")

    @property
    def native_value(self) -> str:
        status = self.coordinator.action_url_status
        if status is None:
            return "off"
        groups = summary(status)
        return "partial" if set(groups) - {STATUS_OURS} else "on"

    @property
    def extra_state_attributes(self) -> dict[str, object] | None:
        status = self.coordinator.action_url_status
        if status is None:
            return None
        groups = summary(status)
        return {
            "home_assistant_url": self.coordinator.action_url_base,
            "events": groups.get(STATUS_OURS, []),
            "used_by_other_system": groups.get(STATUS_FOREIGN, []),
            "not_supported": groups.get(STATUS_REJECTED, []),
        }


class FanvilLastOpeningSensor(FanvilEntity, SensorEntity):
    """Time of the last door log entry; method, user and result as attributes."""

    _attr_translation_key = "last_opening"
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, coordinator: FanvilCoordinator) -> None:
        super().__init__(coordinator, "last_opening")

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
    def _async_entry(self, _entry: dict) -> None:
        self.async_write_ha_state()

    @property
    def _entry(self) -> dict | None:
        door_log = self.coordinator.door_log
        return door_log.last_entry if door_log else None

    @property
    def native_value(self):
        return (self._entry or {}).get("time")

    @property
    def extra_state_attributes(self) -> dict[str, object] | None:
        if not (entry := self._entry):
            return None
        return {k: v for k, v in entry.items() if k not in ("time", "id")}
