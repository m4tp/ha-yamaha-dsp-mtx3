import logging

from bidict import bidict
from homeassistant.components.media_player import (
    MediaPlayerDeviceClass,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo

from custom_components.yamaha_dsp import (
    EntityType,
    RuntimeData,
    SourceConfiguration,
    SpeakerConfiguration,
    YamahaDspDevice,
    create_unique_id,
)
from custom_components.yamaha_dsp.yamaha.command import Address, create_parameter

logger = logging.getLogger(__name__)


async def async_setup_entry(_hass: HomeAssistant, entry, async_add_entities):
    # Extract stored runtime data
    runtime_data: RuntimeData = entry.runtime_data
    device = runtime_data.device
    device_info = runtime_data.device_info
    dsp_configuration = runtime_data.dsp_configuration

    # Add entities for each speaker
    for speaker_configuration in dsp_configuration.speakers:
        async_add_entities([SpeakerEntity(speaker_configuration, device, device_info)])

    # Add entities for each source
    for source_configuration in dsp_configuration.sources:
        async_add_entities([SourceEntity(source_configuration, device, device_info)])


class YamahaDspMediaPlayerEntity(MediaPlayerEntity):
    def __init__(
        self,
        device: YamahaDspDevice,
        device_info: DeviceInfo,
        index_volume: Address,
        index_mute: Address,
        max_volume: int,
    ):
        self._device = device
        self._device_info = device_info

        self._state = MediaPlayerState.ON
        self._volume = 0
        self._muted = False

        self._max_volume = max_volume
        self._volume_param = create_parameter(index_volume)
        self._mute_param = create_parameter(index_mute)

    @property
    def device_info(self) -> DeviceInfo:
        return self._device_info

    @property
    def state(self) -> MediaPlayerState:
        return self._state

    @property
    def volume_level(self) -> float:
        if self._volume <= 0:
            return 0
        # Volume above the configured ceiling (e.g. set from MTX-MRX Editor) is shown as 100%
        return min(self._volume / self._max_volume, 1.0)

    @property
    def volume_step(self) -> float:
        return 0.05

    @property
    def is_volume_muted(self) -> bool:
        return self._muted

    async def async_update(self) -> None:
        volume_resp = await self._device.query_parameter_normalized(self._volume_param)
        self._volume = volume_resp.get_int_value()
        is_on_resp = await self._device.query_parameter_raw(self._mute_param)
        self._muted = not is_on_resp.get_bool_value()

    async def async_mute_volume(self, mute: bool) -> None:
        await self._device.set_parameter_raw(self._mute_param, "0", "0", "0" if mute else "1")

    async def async_set_volume_level(self, volume: float) -> None:
        await self._device.set_parameter_normalized(self._volume_param, "0", "0", str(round(volume * self._max_volume)))


class SpeakerEntity(YamahaDspMediaPlayerEntity):
    def __init__(self, config: SpeakerConfiguration, device: YamahaDspDevice, device_info: DeviceInfo):
        super().__init__(device, device_info, config.index_volume, config.index_mute, config.max_volume)
        self._config = config

        self._source = None
        self._source_bidict = bidict(
            {i + 1: self._config.available_inputs[i] for i in range(len(self._config.available_inputs))}
        )

        self._source_param = create_parameter(self._config.index_source)

    _attr_device_class = MediaPlayerDeviceClass.SPEAKER
    _attr_supported_features = (
        MediaPlayerEntityFeature.VOLUME_MUTE
        | MediaPlayerEntityFeature.VOLUME_SET
        | MediaPlayerEntityFeature.SELECT_SOURCE
    )

    @property
    def name(self) -> str:
        return f"{self._config.name} speakers"

    @property
    def unique_id(self) -> str:
        return create_unique_id(self._config.name, EntityType.SPEAKER)

    @property
    def source(self) -> str | None:
        return self._source

    @property
    def source_list(self) -> list[str]:
        return list(self._source_bidict.values())

    async def async_update(self) -> None:
        await super().async_update()

        source_resp = await self._device.query_parameter_raw(self._source_param)
        self._source = self._source_bidict.get(source_resp.get_int_value())

    async def async_select_source(self, source: str) -> None:
        source_idx = self._source_bidict.inverse.get(source)
        if source_idx is None:
            logger.error(f"Asked to switch to invalid source {source}")
        else:
            logger.debug(f"Switching source on {self.name} to {source} ({source_idx})")
            await self._device.set_parameter_raw(self._source_param, "0", "0", str(source_idx))


class SourceEntity(YamahaDspMediaPlayerEntity):
    def __init__(self, config: SourceConfiguration, device: YamahaDspDevice, device_info: DeviceInfo):
        super().__init__(device, device_info, config.index_volume, config.index_mute, config.max_volume)
        self._config = config

    _attr_device_class = MediaPlayerDeviceClass.SPEAKER
    _attr_supported_features = MediaPlayerEntityFeature.VOLUME_MUTE | MediaPlayerEntityFeature.VOLUME_SET

    @property
    def name(self) -> str:
        return f"{self._config.name} source"

    @property
    def unique_id(self) -> str:
        return create_unique_id(self._config.name, EntityType.SOURCE)

    @property
    def icon(self) -> str:
        return "mdi:volume-source"
