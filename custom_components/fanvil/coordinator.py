"""Coordinate one poll for all Fanvil entities."""

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .action_url import valid_ipv4
from .api import FanvilApiError, FanvilAuthError, FanvilClient, FanvilError
from .const import (
    AUTH_FAILURES_BEFORE_REAUTH,
    CONF_POLL_INTERVAL,
    DEFAULT_POLL_INTERVAL,
    DOMAIN,
    DOOR_LOG_AFTER_RELAY,
    EVENT_FANVIL,
    SIGNAL_DOOR_LOG,
    SIGNAL_EVENT,
    SIP_CONFIG_EVERY,
    relay_key,
)
from .door_log import DoorLogTracker

_LOGGER = logging.getLogger(__name__)


class FanvilCoordinator(DataUpdateCoordinator[dict[str, str]]):
    """Keep the panel state in sync without blocking Home Assistant."""

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, client: FanvilClient
    ) -> None:
        self.client = client
        self._settings: dict[str, str] = {}
        self._polls = 0
        self._auth_failures = 0
        # Action URL: event -> ours/foreign/rejected; None - switched off.
        self.action_url_status: dict[str, str] | None = None
        self.action_url_base: str | None = None
        self.last_event: dict[str, str] | None = None
        # None: the panel has no door log API (checked at setup).
        self.door_log: DoorLogTracker | None = None
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(
                seconds=entry.options.get(
                    CONF_POLL_INTERVAL,
                    entry.data.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL),
                )
            ),
            always_update=False,
        )

    async def _async_update_data(self) -> dict[str, str]:
        try:
            if not self.client.supported_fields:
                data = await self.client.async_initialize()
            else:
                data = await self.client.async_update()
            if self._polls % SIP_CONFIG_EVERY == 0:
                try:
                    self._settings = await self.client.async_read_settings()
                except FanvilApiError as err:
                    # Older firmware without api/config: keep the status entities.
                    _LOGGER.debug("Settings are not available: %s", err)
            self._polls += 1
            self._auth_failures = 0
            return {**data, **self._settings}
        except FanvilAuthError as err:
            # While booting the panel briefly shows the login page even for valid
            # credentials: ask for new ones only if it persists.
            self._auth_failures += 1
            if self._auth_failures >= AUTH_FAILURES_BEFORE_REAUTH:
                raise ConfigEntryAuthFailed("Fanvil authentication failed") from err
            raise UpdateFailed(str(err)) from err
        except FanvilError as err:
            raise UpdateFailed(str(err)) from err

    async def async_write_settings(self, values: dict[str, str]) -> None:
        """Write panel settings and show them at once (no full poll)."""
        rejected = await self.client.async_write_config(values)
        if rejected:
            raise HomeAssistantError(f"The panel rejected {', '.join(rejected)}")
        self._settings = {**self._settings, **values}
        self.async_set_updated_data({**self.data, **values})

    @callback
    def set_action_url_status(
        self, status: dict[str, str] | None, base_url: str | None
    ) -> None:
        self.action_url_status = status
        self.action_url_base = base_url
        self.async_update_listeners()

    @callback
    def async_handle_event(self, data: dict[str, str]) -> None:
        """Apply an Action URL event: state at once, then a confirming poll."""
        event = data["event"]
        self.last_event = data
        device = dr.async_get(self.hass).async_get_device(
            identifiers={(DOMAIN, self.config_entry.unique_id)}
        )
        self.hass.bus.async_fire(
            EVENT_FANVIL,
            {"device_id": device.id if device else None, **data},
        )
        async_dispatcher_send(
            self.hass, SIGNAL_EVENT.format(self.config_entry.entry_id), data
        )
        if event == "ip_changed" and self._async_follow_ip(data.get("ip", "")):
            return
        if event.startswith("relay_") and self.data:
            relay = int(event.split("_")[1])
            state = "open" if event.endswith("_open") else "close"
            self.async_set_updated_data({**self.data, relay_key(relay): state})
            if state == "open" and self.door_log:
                # The panel writes the log entry right after opening.
                async_call_later(
                    self.hass, DOOR_LOG_AFTER_RELAY, self._async_door_log_later
                )
            return
        self.hass.async_create_task(self.async_request_refresh())

    async def _async_door_log_later(self, _now) -> None:
        if self.door_log:
            await self.door_log.async_check()

    def enable_door_log(self) -> None:
        self.door_log = DoorLogTracker(self.client, self._async_door_log_entry)

    @callback
    def _async_door_log_entry(self, entry: dict | None) -> None:
        """A new door log entry: bus event, event entity, sensor.

        None: the newest existing entry was located (sensor only).
        """
        signal = SIGNAL_DOOR_LOG.format(self.config_entry.entry_id)
        if entry is None:
            async_dispatcher_send(self.hass, signal, None)
            return
        device = dr.async_get(self.hass).async_get_device(
            identifiers={(DOMAIN, self.config_entry.unique_id)}
        )
        data = {k: v for k, v in entry.items() if k != "time"}
        if entry.get("time"):
            data["time"] = entry["time"].isoformat()
        self.hass.bus.async_fire(
            EVENT_FANVIL,
            {
                "device_id": device.id if device else None,
                "event": f"door_{entry['result']}",
                **data,
            },
        )
        async_dispatcher_send(self.hass, signal, entry)

    @callback
    def _async_follow_ip(self, new_ip: str) -> bool:
        """The panel got a new address (DHCP): update the entry and reload."""
        entry = self.config_entry
        host = entry.data[CONF_HOST]
        if not valid_ipv4(new_ip) or not valid_ipv4(host) or new_ip == host:
            return False
        _LOGGER.info("Fanvil %s: address changed %s -> %s", entry.title, host, new_ip)
        self.hass.config_entries.async_update_entry(
            entry, data={**entry.data, CONF_HOST: new_ip}
        )
        self.hass.config_entries.async_schedule_reload(entry.entry_id)
        return True
