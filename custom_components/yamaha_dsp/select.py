import logging

from datetime import timedelta

from bidict import bidict
from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo

from custom_components.yamaha_dsp import (
    EntityType,
    RouterConfiguration,
    RuntimeData,
    YamahaDspDevice,
    create_unique_id,
)
from custom_components.yamaha_dsp.push import PushUpdateMixin
from custom_components.yamaha_dsp.yamaha.command import create_parameter

logger = logging.getLogger(__name__)

# Push notifications keep entities up to date, polling is only a safety net
SCAN_INTERVAL = timedelta(minutes=5)


async def async_setup_entry(_hass: HomeAssistant, entry, async_add_entities):
    # Extract stored runtime data
    runtime_data: RuntimeData = entry.runtime_data
    device = runtime_data.device
    device_info = runtime_data.device_info
    dsp_configuration = runtime_data.dsp_configuration

    # Add entities for each router sink
    for router_configuration in dsp_configuration.routers:
        async_add_entities([RouterSelectEntity(router_configuration, device, device_info)])

    # MTX3/MTX5-D: one select entity for recalling presets
    if runtime_data.supports_presets:
        async_add_entities([PresetSelectEntity(device, device_info)])


class RouterSelectEntity(PushUpdateMixin, SelectEntity):
    def __init__(self, config: RouterConfiguration, device: YamahaDspDevice, device_info: DeviceInfo):
        self._config = config
        self._device = device
        self._device_info = device_info

        self._source_param = create_parameter(self._config.index_source)
        self._source_bidict = bidict({source.value: source.label for source in self._config.sources})
        self._current_option: str | None = None
        self._init_push()

    def _push_parameters(self) -> list[str]:
        return [self._source_param]

    @property
    def name(self) -> str:
        return f"{self._config.name} router"

    @property
    def unique_id(self) -> str:
        return create_unique_id(self._config.name, EntityType.ROUTER)

    @property
    def icon(self) -> str:
        return "mdi:router-network"

    @property
    def device_info(self) -> DeviceInfo:
        return self._device_info

    @property
    def options(self) -> list[str]:
        return list(self._source_bidict.values())

    @property
    def current_option(self) -> str | None:
        return self._current_option

    async def async_update(self) -> None:
        source_response = await self._device.query_parameter_raw(self._source_param)
        source_value = source_response.get_int_value()
        self._current_option = self._source_bidict.get(source_value)

        if self._current_option is None:
            logger.warning("Router %s returned unknown source value %s", self._config.name, source_value)

    async def async_select_option(self, option: str) -> None:
        source_value = self._source_bidict.inverse.get(option)
        if source_value is None:
            raise ValueError(f"Invalid option '{option}' for router '{self._config.name}'")

        await self._device.set_parameter_raw(self._source_param, "0", "0", str(source_value))
        self._current_option = option
        self._write_state()


class PresetSelectEntity(PushUpdateMixin, SelectEntity):
    """Shows the current preset and recalls a preset when an option is selected."""

    # The preset list rarely changes (only when re-synchronized from MTX-MRX Editor),
    # so it is refreshed only every N updates or when an unknown preset is active
    PRESET_LIST_REFRESH_INTERVAL = 20

    def __init__(self, device: YamahaDspDevice, device_info: DeviceInfo):
        self._device = device
        self._device_info = device_info

        self._presets: bidict[int, str] = bidict()
        self._current_option: str | None = None
        self._updates_since_refresh = self.PRESET_LIST_REFRESH_INTERVAL
        self._init_push()

    @property
    def name(self) -> str:
        return "Preset"

    @property
    def unique_id(self) -> str:
        serial = next(iter(self._device_info["identifiers"]))[1]
        return f"{serial}_{EntityType.PRESET.value}"

    @property
    def icon(self) -> str:
        return "mdi:playlist-play"

    @property
    def device_info(self) -> DeviceInfo:
        return self._device_info

    @property
    def options(self) -> list[str]:
        return list(self._presets.values())

    @property
    def current_option(self) -> str | None:
        return self._current_option

    async def _refresh_preset_list(self) -> None:
        presets = await self._device.query_user_presets()
        labels: bidict[int, str] = bidict()

        for preset in presets:
            label = f"{preset.number} {preset.title}".strip()
            # Preset titles are not guaranteed to be unique
            if label in labels.inverse:
                label = f"{label} ({preset.index})"
            labels[preset.index] = label

        self._presets = labels
        self._updates_since_refresh = 0

    async def async_update(self) -> None:
        if self._updates_since_refresh >= self.PRESET_LIST_REFRESH_INTERVAL:
            await self._refresh_preset_list()
        else:
            self._updates_since_refresh += 1

        current = await self._device.query_current_preset()
        if current not in self._presets and self._updates_since_refresh > 0:
            await self._refresh_preset_list()

        self._current_option = self._presets.get(current)

    async def async_select_option(self, option: str) -> None:
        index = self._presets.inverse.get(option)
        if index is None:
            raise ValueError(f"Invalid preset '{option}'")

        await self._device.recall_preset(index)
        self._current_option = option
        self._write_state()
