"""Action URL: the panel reports events to a Home Assistant webhook.

The panel sends an HTTP GET for each configured event. Each event has its own
key in the panel configuration (cti.*). The integration writes only empty keys
or keys that already point to its own webhook: a URL left by another system is
never overwritten, that event then stays on polling only.
"""

from __future__ import annotations

import ipaddress
import logging
from collections.abc import Iterable
from typing import Any

from .api import FanvilClient

_LOGGER = logging.getLogger(__name__)

# event -> panel configuration key (i60K 2.12.51; verified on the device).
EVENT_KEYS: dict[str, str] = {
    "incoming": "cti.IncomingCallUrl",
    "outgoing": "cti.OutgoingCallUrl",
    "answered": "cti.CallActiveUrl",
    "ended": "cti.CallStopUrl",
    "missed": "cti.NewMissedcallUrl",
    "relay_1_open": "cti.vimCti.Output1.Url",
    "relay_1_closed": "cti.vimCti.Output1.RstUrl",
    "relay_2_open": "cti.vimCti.Output2.Url",
    "relay_2_closed": "cti.vimCti.Output2.RstUrl",
    "input_1_triggered": "cti.vimCti.Input1.Url",
    "input_1_reset": "cti.vimCti.Input1.RstUrl",
    "input_2_triggered": "cti.vimCti.Input2.Url",
    "input_2_reset": "cti.vimCti.Input2.RstUrl",
    "sip_registered": "cti.RegOnUrl",
    "sip_unregistered": "cti.RegOffUrl",
    "sip_failed": "cti.RegFailedUrl",
    "boot": "cti.BootCompletedUrl",
    "ip_changed": "cti.IPChangeUrl",
    "key": "cti.KeyTriggerUrl",
    "tamper": "cti.vimCti.TamperUrl",
    "motion": "cti.vimCti.MotionDetectionUrl",
}
ENABLED_KEY = "cti.ActionUrlEnabled"

# Panel variables passed to Home Assistant ($name is replaced by the panel).
VARIABLES = (
    "mac",
    "ip",
    "line",
    "remote",
    "display_remote",
    "call_id",
    "duration",
)

CALL_EVENTS = ("incoming", "outgoing", "answered", "ended", "missed")
DEVICE_EVENTS = ("key", "boot", "tamper", "motion")

STATUS_OURS = "ours"
STATUS_FOREIGN = "foreign"
STATUS_REJECTED = "rejected"


def webhook_marker(webhook_id: str) -> str:
    """A part of the URL that identifies this integration's own values."""
    return f"/api/webhook/{webhook_id}"


def build_url(base_url: str, webhook_id: str, event: str) -> str:
    """The URL for one event; the panel substitutes the $variables."""
    params = "&".join(f"{name}=${name}" for name in VARIABLES)
    return f"{base_url.rstrip('/')}{webhook_marker(webhook_id)}?event={event}&{params}"


def parse_request(query: dict[str, str]) -> dict[str, str] | None:
    """Normalize a webhook request, None if it is not a known event."""
    event = query.get("event", "")
    if event not in EVENT_KEYS:
        return None
    data = {"event": event}
    for name in VARIABLES:
        value = query.get(name, "").strip()
        # Unset panel variables look like "0", "-1", "$name" or "SIP:@:".
        if value and value not in ("0", "-1", f"${name}"):
            data[name] = value
    if "mac" in data:
        data["mac"] = data["mac"].replace(":", "").replace("-", "").lower()
    return data


def valid_ipv4(value: str) -> bool:
    try:
        return isinstance(ipaddress.ip_address(value), ipaddress.IPv4Address)
    except ValueError:
        return False


async def async_sync(
    client: FanvilClient,
    base_url: str,
    webhook_id: str,
    enabled: bool,
    events: Iterable[str] = EVENT_KEYS,
) -> dict[str, str]:
    """Write (enabled) or clear (disabled) this integration's Action URLs.

    Returns the status for each event: ours, foreign (another system's URL,
    left untouched) or rejected (the firmware does not accept the key).
    """
    keys = {event: EVENT_KEYS[event] for event in events}
    current = await client.async_read_config((*keys.values(), ENABLED_KEY))
    marker = webhook_marker(webhook_id)
    updates: dict[str, str] = {}
    status: dict[str, str] = {}
    for event, key in keys.items():
        value = current.get(key, "")
        ours = marker in value
        if not enabled:
            if ours:
                updates[key] = ""
            continue
        if value and not ours:
            status[event] = STATUS_FOREIGN
            continue
        status[event] = STATUS_OURS
        wanted = build_url(base_url, webhook_id, event)
        if value != wanted:
            updates[key] = wanted
    if enabled and status and current.get(ENABLED_KEY) == "0":
        # Action URL is switched off globally on the panel: switch it on.
        updates[ENABLED_KEY] = "1"
    if updates:
        rejected = await client.async_write_config(updates)
        by_key = {key: event for event, key in keys.items()}
        for key in rejected:
            if key in by_key and enabled:
                status[by_key[key]] = STATUS_REJECTED
        _LOGGER.debug(
            "Action URL %s: %d keys written, rejected %s",
            "enabled" if enabled else "disabled",
            len(updates),
            sorted(rejected),
        )
    return status


def summary(status: dict[str, Any]) -> dict[str, list[str]]:
    """Group event statuses for diagnostics and attributes."""
    result: dict[str, list[str]] = {}
    for event, value in sorted(status.items()):
        result.setdefault(value, []).append(event)
    return result
