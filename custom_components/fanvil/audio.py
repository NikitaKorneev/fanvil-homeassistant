"""Audio for the panel speaker: convert to WAV and serve it to the panel.

The panel downloads the file itself (api/audio_file, path "url"). Firmware
2.12.51 fetches WAV but ignores MP3, while text-to-speech usually produces MP3,
so every clip is converted with ffmpeg to 16 kHz mono PCM. The clip is served
under an unguessable one-off address for a few minutes; the panel cannot send
Home Assistant credentials.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import struct
import time
from dataclasses import dataclass

from aiohttp import web
from homeassistant.components.ffmpeg import get_ffmpeg_manager
from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError

from .const import AUDIO_MAX_SECONDS, AUDIO_TTL, DOMAIN

_LOGGER = logging.getLogger(__name__)

SAMPLE_RATE = 16000
CONVERT_TIMEOUT = 60
DATA_CLIPS = f"{DOMAIN}_audio_clips"
URL_PATH = "/api/fanvil/audio/{token}.wav"


@dataclass(slots=True)
class Clip:
    data: bytes
    expires: float

    @property
    def duration(self) -> float:
        return max(len(self.data) - 44, 0) / (SAMPLE_RATE * 2)


@callback
def async_setup(hass: HomeAssistant) -> None:
    """Register the download view once."""
    if DATA_CLIPS in hass.data:
        return
    hass.data[DATA_CLIPS] = {}
    hass.http.register_view(FanvilAudioView(hass.data[DATA_CLIPS]))


def fix_wav_sizes(data: bytes) -> bytes:
    """ffmpeg cannot seek back in a pipe: write the RIFF and data sizes."""
    if len(data) < 44 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise HomeAssistantError("ffmpeg did not produce a WAV file")
    buffer = bytearray(data)
    struct.pack_into("<I", buffer, 4, len(buffer) - 8)
    position = 12
    while position + 8 <= len(buffer):
        chunk = bytes(buffer[position : position + 4])
        if chunk == b"data":
            struct.pack_into("<I", buffer, position + 4, len(buffer) - position - 8)
            return bytes(buffer)
        (size,) = struct.unpack_from("<I", buffer, position + 4)
        position += 8 + size + (size & 1)
    raise HomeAssistantError("ffmpeg produced a WAV file without data")


async def async_convert(hass: HomeAssistant, source: str) -> bytes:
    """Convert any audio URL or file ffmpeg can read to 16 kHz mono WAV."""
    process = await asyncio.create_subprocess_exec(
        get_ffmpeg_manager(hass).binary,
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-t",
        str(AUDIO_MAX_SECONDS),
        "-i",
        source,
        "-map_metadata",
        "-1",
        "-fflags",
        "+bitexact",
        "-ac",
        "1",
        "-ar",
        str(SAMPLE_RATE),
        "-c:a",
        "pcm_s16le",
        "-f",
        "wav",
        "pipe:1",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        output, errors = await asyncio.wait_for(process.communicate(), CONVERT_TIMEOUT)
    except TimeoutError as err:
        process.kill()
        await process.wait()
        raise HomeAssistantError("Audio conversion took too long") from err
    if process.returncode:
        message = errors.decode(errors="replace").strip().splitlines()
        raise HomeAssistantError(
            f"Audio conversion failed: {message[-1] if message else 'ffmpeg error'}"
        )
    return fix_wav_sizes(output)


@callback
def async_add_clip(hass: HomeAssistant, data: bytes) -> tuple[str, Clip]:
    """Store a clip; return its path (relative to the base URL) and the clip."""
    clips: dict[str, Clip] = hass.data[DATA_CLIPS]
    now = time.monotonic()
    for token in [token for token, clip in clips.items() if clip.expires < now]:
        del clips[token]
    token = secrets.token_urlsafe(24)
    clip = clips[token] = Clip(data, now + AUDIO_TTL)
    return URL_PATH.format(token=token), clip


class FanvilAudioView(HomeAssistantView):
    """The panel downloads a converted clip; the token is the only key."""

    url = URL_PATH
    name = "api:fanvil:audio"
    requires_auth = False

    def __init__(self, clips: dict[str, Clip]) -> None:
        self._clips = clips

    async def get(self, request: web.Request, token: str) -> web.Response:
        clip = self._clips.get(token)
        if clip is None or clip.expires < time.monotonic():
            return web.Response(status=404)
        _LOGGER.debug("Audio clip downloaded by %s", request.remote)
        return web.Response(body=clip.data, content_type="audio/wav")
