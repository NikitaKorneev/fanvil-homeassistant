"""Built-in camera: RTSP stream and API snapshot."""

from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import FanvilConfigEntry
from .api import FanvilError
from .coordinator import FanvilCoordinator
from .entity import FanvilEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FanvilConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Supported door phones always have a camera."""
    async_add_entities([FanvilCamera(entry.runtime_data)])


class FanvilCamera(FanvilEntity, Camera):
    """Main stream rtsp://host/h264/stream.live0, snapshot via cgi-bin/snapShot."""

    _attr_name = None
    _attr_supported_features = CameraEntityFeature.STREAM

    def __init__(self, coordinator: FanvilCoordinator) -> None:
        super().__init__(coordinator, "camera")
        Camera.__init__(self)

    async def stream_source(self) -> str:
        return self.coordinator.client.rtsp_url(0)

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        try:
            return await self.coordinator.client.async_snapshot()
        except FanvilError:
            # The stream integration can still render a frame from RTSP.
            return None
