"""Asynchronous Fanvil HTTP API client, independent of Home Assistant."""

import asyncio
import json
import logging
import re
from base64 import b64encode
from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager
from enum import StrEnum
from typing import Any

from aiohttp import ClientError, ClientSession, ClientTimeout, DigestAuthMiddleware
from yarl import URL

from .const import (
    CONFIG_WRITE_MAX,
    IDENTITY_FIELDS,
    MAC,
    MODEL,
    READ_ATTEMPTS,
    READ_RETRY_DELAY,
    REQUEST_GAP,
    SERIAL,
    SIP_CONFIG_FIELDS,
    STATUS_FIELDS,
    TESTED_MODELS,
)

_LOGGER = logging.getLogger(__name__)


class FanvilError(Exception):
    """Base client error. Never includes credentials or raw response bodies."""


class FanvilConnectionError(FanvilError):
    """The device cannot be reached."""


class FanvilAuthError(FanvilError):
    """Authentication failed, including devices returning an HTML login page."""


class FanvilProtocolError(FanvilError):
    """The response does not follow the documented API."""


class FanvilBusyError(FanvilProtocolError):
    """An HTML stub instead of JSON (seen right after relay commands on 2.12.51)."""


class FanvilApiError(FanvilError):
    """The device rejected a request."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(f"Fanvil API error {code}")


class FanvilUnsupportedModel(FanvilError):
    """The current version supports only the tested device profile."""


class AuthType(StrEnum):
    """Authentication modes found in Fanvil documentation."""

    BASIC = "basic"
    DIGEST = "digest"


def normalize_host(host: str) -> str:
    """Accept a host name or IPv4 address, never a URL or embedded credentials."""
    host = host.strip()
    if (
        not host
        or len(host) > 253
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]*", host)
    ):
        raise ValueError("Enter a host name or IPv4 address")
    return host


def device_unique_id(info: dict[str, str]) -> str:
    """Use the API MAC address, or serial number, independently of the IP."""
    mac = re.sub(r"[:-]", "", info.get(MAC, "")).lower()
    if re.fullmatch(r"[0-9a-f]{12}", mac) and mac != "0" * 12:
        return f"mac_{mac}"
    if serial := info.get(SERIAL, "").strip():
        return f"sn_{serial}"
    raise FanvilProtocolError("The API did not return a MAC address or serial number")


class FanvilClient:
    """Talk to a single panel using a caller-owned aiohttp session."""

    def __init__(
        self,
        session: ClientSession,
        host: str,
        username: str,
        password: str,
        *,
        port: int = 80,
        use_ssl: bool = False,
        auth_type: str = AuthType.BASIC,
        timeout: float = 8,
    ) -> None:
        self._session = session
        self.base_url = URL.build(
            scheme="https" if use_ssl else "http",
            host=normalize_host(host),
            port=port,
        )
        self._auth_type = AuthType(auth_type)
        self._basic_header = "Basic " + b64encode(
            f"{username}:{password}".encode()
        ).decode("ascii")
        self._username = username
        self._password = password
        self._digest = DigestAuthMiddleware(login=username, password=password)
        self._timeout = ClientTimeout(total=timeout)
        self._lock = asyncio.Lock()
        self._last_request = 0.0
        self.info: dict[str, str] = {}
        self.supported_fields: tuple[str, ...] = ()

    def _auth_kwargs(self) -> dict[str, Any]:
        # The panel drops kept-alive connections (e.g. after a config write):
        # one connection per request.
        headers = {"Connection": "close"}
        if self._auth_type == AuthType.DIGEST:
            return {"middlewares": (self._digest,), "headers": headers}
        return {"headers": {**headers, "Authorization": self._basic_header}}

    def rtsp_url(self, stream: int = 0) -> str:
        """RTSP URL of the built-in camera (0 - main, 1 - sub stream).

        The panel requires the web credentials; here the API credentials are used.
        """
        return str(
            URL.build(
                scheme="rtsp",
                user=self._username,
                password=self._password,
                host=self.base_url.host,
                path=f"/h264/stream.live{stream}",
            )
        )

    @asynccontextmanager
    async def _slot(self) -> AsyncIterator[None]:
        """One request at a time, with a pause after the previous one.

        Right after answering, i60K 2.12.51 drops or rejects (code 301) the next
        request for ~0.3 s.
        """
        async with self._lock:
            loop = asyncio.get_running_loop()
            if (wait := self._last_request + REQUEST_GAP - loop.time()) > 0:
                await asyncio.sleep(wait)
            try:
                yield
            finally:
                self._last_request = loop.time()

    async def async_snapshot(self) -> bytes:
        """Return a JPEG frame from the built-in camera."""
        try:
            async with self._slot():
                async with self._session.get(
                    self.base_url / "cgi-bin/snapShot",
                    params={"channel": "0"},
                    timeout=self._timeout,
                    allow_redirects=False,
                    **self._auth_kwargs(),
                ) as response:
                    if response.status in (401, 403):
                        raise FanvilAuthError("The panel rejected the credentials")
                    if response.status != 200:
                        raise FanvilApiError(str(response.status))
                    body = await response.read()
        except (TimeoutError, ClientError) as err:
            raise FanvilConnectionError("Cannot communicate with the panel") from err
        if not body.startswith(b"\xff\xd8"):
            raise FanvilProtocolError("The snapshot is not a JPEG image")
        return body

    async def _request(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Send one request. Never retry commands after a timeout."""
        kwargs = self._auth_kwargs()
        try:
            async with self._slot():
                async with self._session.post(
                    self.base_url / path,
                    json=payload,
                    timeout=self._timeout,
                    allow_redirects=False,
                    **kwargs,
                ) as response:
                    if response.status in (401, 403):
                        raise FanvilAuthError("The panel rejected the credentials")
                    if 300 <= response.status < 400:
                        raise FanvilAuthError("The API redirected to another page")
                    if response.status != 200:
                        raise FanvilApiError(str(response.status))
                    # This firmware returns text/html even for valid JSON.
                    body = await response.text()
        except (TimeoutError, ClientError) as err:
            raise FanvilConnectionError("Cannot communicate with the panel") from err
        try:
            result = json.loads(body)
        except (ValueError, UnicodeError) as err:
            lower = body.lower()
            if "<html" in lower and ("login" in lower or 'type="password"' in lower):
                raise FanvilAuthError("The API returned the login page") from err
            if "<html" in lower:
                raise FanvilBusyError("The API returned an HTML page") from err
            raise FanvilProtocolError("The API returned invalid JSON") from err
        if not isinstance(result, dict):
            raise FanvilProtocolError("The API response is not an object")
        code = str(result.get("code", ""))
        if code in ("401", "403"):
            raise FanvilAuthError("The panel rejected the credentials")
        if code != "200":
            raise FanvilApiError(code if code.isdecimal() else "unknown")
        return result

    async def _read_request(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Reads and idempotent writes only (never commands): retry a stub answer
        or a dropped connection."""
        for attempt in range(READ_ATTEMPTS):
            try:
                return await self._request(path, payload)
            except FanvilBusyError:
                if attempt == READ_ATTEMPTS - 1:
                    raise
            except FanvilConnectionError:
                # Once: a panel that is really offline must not stall the poll.
                if attempt:
                    raise
            await asyncio.sleep(READ_RETRY_DELAY)
        raise AssertionError  # unreachable

    async def async_read(self, fields: Iterable[str]) -> dict[str, str]:
        """Read selected system/status fields."""
        requested = tuple(fields)
        result = await self._read_request(
            "api/device_info",
            {
                "type": {"item": "device_info", "cmd": "get"},
                "info": dict.fromkeys(requested, ""),
            },
        )
        info = result.get("info")
        if not isinstance(info, dict):
            raise FanvilProtocolError("The API response has no info object")
        # Empty strings can be meaningful for call status. Do not discard them.
        return {
            key: str(value).strip()
            for key, value in info.items()
            if key in requested and isinstance(value, (str, int, float))
        }

    async def _read_optional(self, fields: tuple[str, ...]) -> dict[str, str]:
        """Discover optional fields without hiding network or authentication errors."""
        try:
            return await self.async_read(fields)
        except FanvilApiError as err:
            if err.code not in ("404", "406"):
                raise
        values: dict[str, str] = {}
        for key in fields:
            try:
                values.update(await self.async_read((key,)))
            except FanvilApiError as err:
                if err.code not in ("404", "406"):
                    raise
        return values

    async def async_initialize(self) -> dict[str, str]:
        """Verify the model and discover the fields available on this firmware."""
        identity = await self.async_read((MODEL, MAC, SERIAL))
        if not identity.get(MODEL):
            raise FanvilUnsupportedModel("The API did not return the device model")
        if identity[MODEL].casefold() not in TESTED_MODELS:
            _LOGGER.info(
                "Fanvil %s is not tested yet; basic functions should work",
                identity[MODEL],
            )
        device_unique_id(identity)
        optional = tuple(key for key in IDENTITY_FIELDS if key not in identity)
        info = {**identity, **await self._read_optional(optional + STATUS_FIELDS)}
        self.supported_fields = tuple(info)
        self.info = info
        return info

    async def async_update(self) -> dict[str, str]:
        """Refresh the fields discovered at setup."""
        values = await self.async_read(self.supported_fields)
        if not values or values.get(MODEL) != self.info.get(MODEL):
            raise FanvilProtocolError("The device identity is missing or changed")
        if device_unique_id(values) != device_unique_id(self.info):
            raise FanvilProtocolError("A different device is using this address")
        self.info = values
        return values

    async def async_read_config(self, keys: Iterable[str]) -> dict[str, str]:
        """Read configuration keys (api/config get). Unknown keys come back empty."""
        requested = tuple(keys)
        result = await self._read_request(
            "api/config",
            {"type": {"cmd": "get"}, "config": dict.fromkeys(requested, "")},
        )
        data = result.get("data")
        if not isinstance(data, dict):
            raise FanvilProtocolError("The API response has no data object")
        return {
            key: str(value).strip()
            for key, value in data.items()
            if key in requested and isinstance(value, (str, int, float))
        }

    async def async_write_config(self, values: dict[str, str]) -> list[str]:
        """Write configuration keys (api/config set); return the rejected keys.

        The firmware lists the keys it did not accept in "data". It also fails
        (code 301) on large bodies sent by aiohttp, so write in small batches.
        A config write is idempotent, so a failed batch may be retried.
        """
        rejected: list[str] = []
        batch: dict[str, str] = {}
        batches: list[dict[str, str]] = []
        for key, value in values.items():
            if batch and len(json.dumps({**batch, key: value})) > CONFIG_WRITE_MAX:
                batches.append(batch)
                batch = {}
            batch[key] = value
        if batch:
            batches.append(batch)
        for part in batches:
            result = await self._read_request(
                "api/config", {"type": {"cmd": "set"}, "config": part}
            )
            data = result.get("data")
            if isinstance(data, dict):
                rejected.extend(key for key in data if key in part)
        return rejected

    async def async_read_sip_config(self) -> dict[str, str]:
        """Read SIP account settings (number, name, server). Never the password."""
        return await self.async_read_config(SIP_CONFIG_FIELDS)

    async def async_relay(self, relay: int, action: str, mode: str) -> None:
        """Control one relay: on/off, once (panel timeout) or long (stay)."""
        if relay not in (1, 2) or action not in ("on", "off"):
            raise ValueError("Invalid relay command")
        if mode not in ("once", "long"):
            raise ValueError("Invalid relay mode")
        await self._request(
            "api/device",
            {
                "type": {"cmd": "relayCtrl"},
                "dooropen": {"relay": str(relay), "action": action, "mode": mode},
            },
        )

    async def async_pulse_relay(self, relay: int) -> None:
        """Activate one relay using the duration already configured on the panel."""
        await self.async_relay(relay, "on", "once")

    async def async_reboot(self) -> None:
        """Reboot only when explicitly requested by an entity action."""
        await self._request("api/device", {"type": {"cmd": "reboot"}})

    async def async_read_door_log(self, first: int, last: int) -> list[dict[str, Any]]:
        """Door opening log entries by position (1 is the oldest).

        Positions past the end return an empty list. Pictures are dropped:
        they may be large and are not used.
        """
        if first < 1 or last < first:
            raise ValueError("Invalid log range")
        index = str(first) if first == last else f"{first}-{last}"
        result = await self._read_request(
            "api/device_log",
            {
                "type": {"cmd": "get", "condition": "index"},
                "deviceLog": {"index": index},
            },
        )
        rows = result.get("data") or []
        if not isinstance(rows, list):
            raise FanvilProtocolError("The door log is not a list")
        return [
            {
                key: value
                for key, value in row.items()
                if key not in ("pictureData", "snapshotData", "faceData")
            }
            for row in rows
            if isinstance(row, dict)
        ]

    async def async_play_audio(self, url: str, times: int = 1) -> None:
        """Download a WAV file from url and play it on the panel speaker.

        times: 1..N, 0 - loop until stopped. Firmware 2.12.51 fetches WAV but
        ignores MP3 URLs.
        """
        if times < 0:
            raise ValueError("Invalid play count")
        await self._request(
            "api/audio_file",
            {
                "type": {"cmd": "play"},
                "audioPlay": {"path": "url", "name": url, "times": str(times)},
            },
        )

    async def async_stop_audio(self) -> None:
        """Stop a file started by async_play_audio."""
        await self._request(
            "api/audio_file",
            {"type": {"cmd": "stop"}, "audioPlay": {"path": "url"}},
        )
