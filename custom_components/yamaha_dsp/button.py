"""Refresh button: reads every parameter again, or retries the connection right away when the device is off."""

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo

from custom_components.yamaha_dsp import RuntimeData, YamahaDspDevice


async def async_setup_entry(_hass: HomeAssistant, entry, async_add_entities):
    runtime_data: RuntimeData = entry.runtime_data
    async_add_entities([RefreshButtonEntity(runtime_data.device, runtime_data.device_info)])


class RefreshButtonEntity(ButtonEntity):
    # Stays available while the device is unreachable: that's when a manual retry is useful
    _attr_should_poll = False
    _attr_icon = "mdi:refresh"

    def __init__(self, device: YamahaDspDevice, device_info: DeviceInfo):
        self._device = device
        self._device_info = device_info

    @property
    def name(self) -> str:
        return "DSP refresh"

    @property
    def unique_id(self) -> str:
        serial = next(iter(self._device_info["identifiers"]))[1]
        return f"{serial}_refresh"

    @property
    def device_info(self) -> DeviceInfo:
        return self._device_info

    async def async_press(self) -> None:
        self._device.refresh()
