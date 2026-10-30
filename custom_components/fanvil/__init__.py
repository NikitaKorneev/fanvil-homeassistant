"""Fanvil local HTTP integration."""

import logging
import socket
from datetime import timedelta

from aiohttp import web
from homeassistant.components import webhook
from homeassistant.components.network import async_get_source_ip
from homeassistant.components.network import util as network_util
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_SSL,
    CONF_USERNAME,
    Platform,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import slugify

from . import action_url, audio
from .api import FanvilApiError, FanvilClient, FanvilError, device_unique_id
from .const import (
    ACTION_URL_RESYNC,
    CONF_ACTION_URL,
    CONF_AUTH_TYPE,
    CONF_HA_URL,
    CONF_WEBHOOK_ID,
    DEFAULT_ACTION_URL,
    DOMAIN,
    DOOR_LOG_INTERVAL,
)
from .coordinator import FanvilCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = (
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.SWITCH,
    Platform.CAMERA,
    Platform.EVENT,
    Platform.MEDIA_PLAYER,
    Platform.NUMBER,
)
type FanvilConfigEntry = ConfigEntry[FanvilCoordinator]


def _client(hass: HomeAssistant, entry: ConfigEntry) -> FanvilClient:
    return FanvilClient(
        async_get_clientsession(hass, family=socket.AF_INET),
        entry.data[CONF_HOST],
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
        port=entry.data[CONF_PORT],
        use_ssl=entry.data[CONF_SSL],
        auth_type=entry.data[CONF_AUTH_TYPE],
    )


def action_url_enabled(entry: ConfigEntry) -> bool:
    return entry.options.get(CONF_ACTION_URL, DEFAULT_ACTION_URL)


async def async_base_url(hass: HomeAssistant, entry: ConfigEntry) -> str:
    """Home Assistant address as seen from the panel.

    The internal URL is often an mDNS name (homeassistant.local) a panel cannot
    resolve, so by default use the IP Home Assistant uses to reach the panel.
    """
    if url := entry.options.get(CONF_HA_URL, "").strip():
        return url.rstrip("/")
    host = entry.data[CONF_HOST]
    try:
        target = await hass.async_add_executor_job(socket.gethostbyname, host)
    except OSError:
        target = None
    # The address of the route to the panel; Home Assistant's network settings
    # may list only some adapters, so they are the fallback.
    source_ip = (target and network_util.async_get_source_ip(target)) or (
        await async_get_source_ip(hass, host)
    )
    api = hass.config.api
    scheme = "https" if api and api.use_ssl else "http"
    port = api.port if api else 8123
    return f"{scheme}://{source_ip}:{port}"


@callback
def _async_rename_main_camera(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """0.4 named the main camera after the device: camera.fanvil_i62.

    Give it the new camera.fanvil_i62_main_stream next to the sub stream, but
    only if the user has not chosen an entity ID of their own.
    """
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id(
        Platform.CAMERA, DOMAIN, f"{entry.unique_id}_camera"
    )
    if not entity_id or not (camera := registry.async_get(entity_id)):
        return
    device = dr.async_get(hass).async_get(camera.device_id or "")
    names = {device.name, device.name_by_user} if device else set()
    if entity_id not in {f"camera.{slugify(name)}" for name in names if name}:
        return
    new_entity_id = f"{entity_id}_main_stream"
    if registry.async_is_registered(new_entity_id):
        return
    _LOGGER.info("Fanvil: %s renamed to %s", entity_id, new_entity_id)
    registry.async_update_entity(entity_id, new_entity_id=new_entity_id)


async def async_setup_entry(hass: HomeAssistant, entry: FanvilConfigEntry) -> bool:
    """Set up a panel from its config entry."""
    coordinator = FanvilCoordinator(hass, entry, _client(hass, entry))
    await coordinator.async_config_entry_first_refresh()
    if device_unique_id(coordinator.data) != entry.unique_id:
        raise ConfigEntryError("A different panel is using the configured address")
    entry.runtime_data = coordinator
    _async_rename_main_camera(hass, entry)
    await _async_detect_door_log(coordinator)
    audio.async_setup(hass)

    if not entry.data.get(CONF_WEBHOOK_ID):
        hass.config_entries.async_update_entry(
            entry,
            data={**entry.data, CONF_WEBHOOK_ID: webhook.async_generate_id()},
        )
    webhook_id = entry.data[CONF_WEBHOOK_ID]

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    if action_url_enabled(entry):
        webhook.async_register(
            hass,
            DOMAIN,
            entry.title,
            webhook_id,
            _async_handle_webhook,
            local_only=True,
            allowed_methods=("GET", "POST"),
        )
        entry.async_on_unload(lambda: webhook.async_unregister(hass, webhook_id))

    async def _async_sync(_now=None) -> None:
        enabled = action_url_enabled(entry)
        try:
            base_url = await async_base_url(hass, entry)
            status = await action_url.async_sync(
                coordinator.client, base_url, webhook_id, enabled
            )
        except FanvilError as err:
            _LOGGER.warning(
                "Fanvil %s: Action URL not updated: %s (%r)",
                entry.title,
                err,
                err.__cause__,
            )
            return
        coordinator.set_action_url_status(
            status if enabled else None, base_url if enabled else None
        )
        if foreign := action_url.summary(status).get(action_url.STATUS_FOREIGN):
            _LOGGER.info(
                "Fanvil %s: events kept for another system (polling only): %s",
                entry.title,
                ", ".join(foreign),
            )

    await _async_sync()
    if (door_log := coordinator.door_log) is not None:
        # Locating the end of a long log takes a few requests: in the background.
        entry.async_create_background_task(
            hass, door_log.async_check(), "fanvil door log"
        )

        async def _async_check_door_log(_now=None) -> None:
            await door_log.async_check()

        entry.async_on_unload(
            async_track_time_interval(
                hass, _async_check_door_log, timedelta(seconds=DOOR_LOG_INTERVAL)
            )
        )
    if action_url_enabled(entry):
        # Provisioning or a factory reset may wipe the keys: check them again.
        entry.async_on_unload(
            async_track_time_interval(
                hass, _async_sync, timedelta(seconds=ACTION_URL_RESYNC)
            )
        )
    return True


async def _async_detect_door_log(coordinator: FanvilCoordinator) -> None:
    """Door phones keep a door log; other devices answer with an error."""
    try:
        await coordinator.client.async_read_door_log(1, 1)
    except FanvilApiError:
        return
    except FanvilError as err:
        # Unknown yet (busy panel): keep the entities, reads will be retried.
        _LOGGER.debug("Door log check failed: %s", err)
    coordinator.enable_door_log()


async def async_unload_entry(hass: HomeAssistant, entry: FanvilConfigEntry) -> bool:
    """Unload the platforms and their update listeners."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Best effort: remove this integration's Action URLs from the panel."""
    if not (webhook_id := entry.data.get(CONF_WEBHOOK_ID)):
        return
    try:
        await action_url.async_sync(_client(hass, entry), "", webhook_id, False)
    except FanvilError as err:
        _LOGGER.warning("Fanvil %s: Action URL not removed: %s", entry.title, err)


async def _async_handle_webhook(
    hass: HomeAssistant, webhook_id: str, request: web.Request
) -> web.Response:
    """Receive one Action URL request from a panel."""
    entry = next(
        (
            entry
            for entry in hass.config_entries.async_entries(DOMAIN)
            if entry.data.get(CONF_WEBHOOK_ID) == webhook_id
            and entry.state is ConfigEntryState.LOADED
        ),
        None,
    )
    if entry is None:
        return web.Response(status=404)
    data = action_url.parse_request(dict(request.query))
    if data is None:
        return web.Response(status=400)
    unique_id = entry.unique_id or ""
    if (
        data.get("mac")
        and unique_id.startswith("mac_")
        and data["mac"] != unique_id.removeprefix("mac_")
    ):
        _LOGGER.warning(
            "Fanvil %s: Action URL from another device (%s) ignored",
            entry.title,
            request.remote,
        )
        return web.Response(status=403)
    entry.runtime_data.async_handle_event(data)
    return web.Response(text="OK")
