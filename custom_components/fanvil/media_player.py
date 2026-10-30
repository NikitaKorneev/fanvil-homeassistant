"""Panel speaker: announcements and text-to-speech at the door."""

from __future__ import annotations

from typing import Any

from homeassistant.components import media_source
from homeassistant.components.media_player import (
    BrowseMedia,
    MediaPlayerDeviceClass,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
    MediaType,
    async_process_play_media_url,
)
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_call_later

from . import FanvilConfigEntry, async_base_url, audio
from .api import FanvilError
from .coordinator import FanvilCoordinator
from .entity import FanvilEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FanvilConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Every Fanvil device with the HTTP API has api/audio_file."""
    async_add_entities([FanvilSpeaker(entry.runtime_data)])


class FanvilSpeaker(FanvilEntity, MediaPlayerEntity):
    """Plays audio on the panel; the panel downloads a WAV from Home Assistant."""

    _attr_translation_key = "speaker"
    _attr_device_class = MediaPlayerDeviceClass.SPEAKER
    _attr_media_content_type = MediaType.MUSIC
    _attr_supported_features = (
        MediaPlayerEntityFeature.PLAY_MEDIA
        | MediaPlayerEntityFeature.BROWSE_MEDIA
        | MediaPlayerEntityFeature.MEDIA_ANNOUNCE
        | MediaPlayerEntityFeature.STOP
    )

    def __init__(self, coordinator: FanvilCoordinator) -> None:
        super().__init__(coordinator, "speaker")
        self._attr_state = MediaPlayerState.IDLE
        self._cancel_idle: CALLBACK_TYPE | None = None

    async def async_play_media(
        self, media_type: MediaType | str, media_id: str, **kwargs: Any
    ) -> None:
        if media_source.is_media_source_id(media_id):
            item = await media_source.async_resolve_media(
                self.hass, media_id, self.entity_id
            )
            media_id = item.url
        source = async_process_play_media_url(self.hass, media_id)
        data = await audio.async_convert(self.hass, source)
        path, clip = audio.async_add_clip(self.hass, data)
        base_url = await async_base_url(self.hass, self.coordinator.config_entry)
        try:
            await self.coordinator.client.async_play_audio(f"{base_url}{path}")
        except FanvilError as err:
            raise HomeAssistantError(
                f"The panel did not play the audio: {err}"
            ) from err
        self._set_playing(clip.duration)

    async def async_media_stop(self) -> None:
        try:
            await self.coordinator.client.async_stop_audio()
        except FanvilError as err:
            raise HomeAssistantError(f"The panel did not stop: {err}") from err
        self._set_idle()

    async def async_browse_media(
        self,
        media_content_type: MediaType | str | None = None,
        media_content_id: str | None = None,
    ) -> BrowseMedia:
        return await media_source.async_browse_media(
            self.hass,
            media_content_id,
            content_filter=lambda item: item.media_content_type.startswith("audio/"),
        )

    @callback
    def _set_playing(self, duration: float) -> None:
        """The panel does not report playback: estimate it from the clip length."""
        if self._cancel_idle:
            self._cancel_idle()
        self._attr_state = MediaPlayerState.PLAYING
        self.async_write_ha_state()
        self._cancel_idle = async_call_later(self.hass, duration + 1, self._async_done)

    @callback
    def _async_done(self, _now) -> None:
        self._cancel_idle = None
        self._set_idle()

    @callback
    def _set_idle(self) -> None:
        if self._cancel_idle:
            self._cancel_idle()
            self._cancel_idle = None
        self._attr_state = MediaPlayerState.IDLE
        self.async_write_ha_state()

    async def async_will_remove_from_hass(self) -> None:
        if self._cancel_idle:
            self._cancel_idle()
        await super().async_will_remove_from_hass()
