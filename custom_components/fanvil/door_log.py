"""Door opening log: who opened the door, how, and failed attempts.

The panel keeps the log itself (api/device_log). It is read by position, 1 is
the oldest entry. The log upload to a server ("Report Door Logs") is often used
by Fanvil cloud management, so the integration only reads the log and never
changes that setting.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from .api import FanvilClient, FanvilError

_LOGGER = logging.getLogger(__name__)

# Values that are not a number (one name: works on Python 3.13 and 3.14 alike).
BAD_NUMBER = (TypeError, ValueError, OverflowError, OSError)

# Read at most this many new entries per request.
BATCH = 20

# openType (HTTP API Manual V0.13.0, 3.8.1)
METHODS = {
    "0": "pin",
    "1": "remote_pin",
    "2": "card",
    "3": "active_uri",
    "4": "tr069",
    "5": "web",
    "6": "indoor_station",
    "7": "timeout",
    "8": "face",
    "9": "input",
    "10": "sms",
    "11": "call",
    "12": "function_key",
    "13": "api",
    "14": "schedule",
}

# reason
REASONS = {
    "1": "wrong_pin",
    "2": "wrong_card",
    "3": "face_error",
    "4": "id_card_error",
    "5": "time_period",
    "6": "temperature",
}


def parse_entry(row: dict[str, Any]) -> dict[str, Any]:
    """Normalize one log entry. PIN codes are never passed on."""
    method = str(row.get("openType", ""))
    success = str(row.get("openResult", "")) == "0"
    entry: dict[str, Any] = {
        "id": str(row.get("id", "")),
        "result": "opened" if success else "denied",
        "method": METHODS.get(method, f"other_{method}" if method else "unknown"),
    }
    try:
        relay = int(row.get("relay", 0))
    except BAD_NUMBER:
        relay = 0
    if relay > 0:
        entry["relay"] = relay
    if not success:
        reason = str(row.get("reason", ""))
        entry["reason"] = REASONS.get(reason, f"other_{reason}")
    user = str(row.get("userName", "")).strip()
    if user and user != "*":
        entry["user"] = user
    try:
        extra = json.loads(row.get("extraJson") or "{}")
    except ValueError:
        extra = {}
    if isinstance(extra, dict):
        if card := extra.get("cardNumber"):
            entry["card"] = str(card)
        if sip := extra.get("sipNumber"):
            entry["sip_number"] = str(sip)
    try:
        entry["time"] = datetime.fromtimestamp(int(row.get("openTime", "")), UTC)
    except BAD_NUMBER:
        pass
    return entry


class DoorLogTracker:
    """Follow the end of the log and report new entries."""

    def __init__(
        self,
        client: FanvilClient,
        on_entry: Callable[[dict[str, Any] | None], None],
    ) -> None:
        """on_entry gets each new entry, and None once the end is located."""
        self._client = client
        self._on_entry = on_entry
        self._lock = asyncio.Lock()
        # Position and id of the newest known entry; None until located.
        self._position: int | None = None
        self._last_id: str | None = None
        self.last_entry: dict[str, Any] | None = None

    async def async_check(self) -> None:
        """Read new entries; the first call only finds the end of the log."""
        async with self._lock:
            try:
                await self._async_check()
            except FanvilError as err:
                _LOGGER.debug("Door log not read: %s", err)

    async def _async_check(self) -> None:
        if self._position is None:
            await self._async_locate()
            return
        while True:
            first = max(self._position, 1)
            rows = await self._client.async_read_door_log(first, first + BATCH)
            if self._position:
                if not rows or str(rows[0].get("id")) != self._last_id:
                    # The log was cleared or rotated: start again from its end.
                    await self._async_locate()
                    return
                rows = rows[1:]
            for row in rows:
                self._position += 1
                self._remember(row)
                self._on_entry(self.last_entry)
            if len(rows) < BATCH:
                return

    async def _async_locate(self) -> None:
        """Find the number of entries: doubling, then binary search."""
        high = 1
        while await self._client.async_read_door_log(high, high):
            high *= 2
        low = high // 2  # the last position known to exist (0: empty log)
        while high - low > 1:
            middle = (low + high) // 2
            if await self._client.async_read_door_log(middle, middle):
                low = middle
            else:
                high = middle
        self._position = low
        self._last_id = None
        if low:
            rows = await self._client.async_read_door_log(low, low)
            if rows:
                self._remember(rows[0])
        self._on_entry(None)

    def _remember(self, row: dict[str, Any]) -> None:
        self._last_id = str(row.get("id"))
        self.last_entry = parse_entry(row)
