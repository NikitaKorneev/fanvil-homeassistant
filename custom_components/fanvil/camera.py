"""Built-in camera: RTSP stream and API snapshot."""

from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import FanvilConfigEntry
from .api import FanvilError
from .const import STREAMS
from .coordinator import FanvilCoordinator
from .entity import FanvilEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FanvilConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Supported door phones always have a camera: main and sub stream."""
    coordinator = entry.runtime_data
    async_add_entities([FanvilCamera(coordinator, 0), FanvilCamera(coordinator, 1)])


class FanvilCamera(FanvilEntity, Camera):
    """RTSP rtsp://host/h264/stream.live0 (main) or stream.live1 (sub).

    The sub stream has a lower resolution and bitrate: for dashboards and
    mobile clients. Both snapshots come from cgi-bin/snapShot.
    """

    _attr_supported_features = CameraEntityFeature.STREAM

    def __init__(self, coordinator: FanvilCoordinator, stream: int) -> None:
        # The main stream keeps the original unique ID "camera".
        super().__init__(coordinator, "camera" if stream == 0 else "camera_sub")
        Camera.__init__(self)
        self._stream = stream
        # "Camera main/sub stream": the device page sorts entities by name,
        # so both streams stay together at the top of the list.
        self._attr_translation_key = STREAMS[stream]

    @property
    def suggested_object_id(self) -> str:
        """camera.<device>_main_stream and camera.<device>_sub_stream."""
        return STREAMS[self._stream]

    async def stream_source(self) -> str:
        return self.coordinator.client.rtsp_url(self._stream)

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        try:
            return await self.coordinator.client.async_snapshot()
        except FanvilError:
            # The stream integration can still render a frame from RTSP.
            return None
