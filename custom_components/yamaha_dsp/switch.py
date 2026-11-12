import logging

from datetime import timedelta
from typing import Any

from homeassistant.components.switch import SwitchDeviceClass, SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo

from custom_components.yamaha_dsp import (
    EntityType,
    RouteConfiguration,
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

    # Add entities for each route
    for route_configuration in dsp_configuration.routes:
        async_add_entities([RouteSwitchEntity(route_configuration, device, device_info)])


class RouteSwitchEntity(PushUpdateMixin, SwitchEntity):
    def __init__(self, config: RouteConfiguration, device: YamahaDspDevice, device_info: DeviceInfo):
        self._config = config
        self._device = device
        self._device_info = device_info

        self._switch_state = False

        self._mute_param = create_parameter(self._config.index_mute)
        self._init_push()

    def _push_parameters(self) -> list[str]:
        return [self._mute_param]

    _attr_device_class = SwitchDeviceClass.SWITCH

    @property
    def name(self) -> str:
        return f"{self._config.name} route"

    @property
    def unique_id(self) -> str:
        return create_unique_id(self._config.name, EntityType.ROUTE)

    @property
    def icon(self) -> str:
        return "mdi:arrow-decision"

    @property
    def device_info(self) -> DeviceInfo:
        return self._device_info

    @property
    def is_on(self) -> bool:
        return self._switch_state

    async def async_update(self) -> None:
        param = await self._device.query_parameter_raw(self._mute_param)
        self._switch_state = param.get_bool_value()

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._device.set_parameter_raw(self._mute_param, "0", "0", "1")
        self._switch_state = True
        self._write_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._device.set_parameter_raw(self._mute_param, "0", "0", "0")
        self._switch_state = False
        self._write_state()
