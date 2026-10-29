"""Diagnostics download: panel data and Action URL status, secrets redacted."""

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant

from . import FanvilConfigEntry
from .action_url import summary
from .const import CONF_WEBHOOK_ID, MAC, SERIAL

TO_REDACT = {CONF_PASSWORD, CONF_USERNAME, CONF_WEBHOOK_ID, MAC, SERIAL, "mac"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: FanvilConfigEntry
) -> dict[str, Any]:
    coordinator = entry.runtime_data
    status = coordinator.action_url_status
    return {
        "entry": {
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": dict(entry.options),
            "unique_id": "**REDACTED**",
        },
        "data": async_redact_data(dict(coordinator.data or {}), TO_REDACT),
        "supported_fields": list(coordinator.client.supported_fields),
        "action_url": {
            "enabled": status is not None,
            "home_assistant_url": coordinator.action_url_base,
            "status": summary(status) if status else None,
            "last_event": async_redact_data(coordinator.last_event or {}, TO_REDACT),
        },
    }
