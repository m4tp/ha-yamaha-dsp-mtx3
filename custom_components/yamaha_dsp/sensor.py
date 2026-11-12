"""Level meter sensors for MTX channels (input channels, stereo inputs, zones and output channels)."""

import time

from collections.abc import Callable
from datetime import timedelta

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.event import async_track_time_interval

from custom_components.yamaha_dsp import RuntimeData, SourceConfiguration, YamahaDspDevice
from custom_components.yamaha_dsp.yamaha.response import METER_OVER, meter_value_to_dbfs

# The device streams meters every second while they are requested, see _request_meters in __init__.py
STALE_AFTER = 15  # seconds without data before the sensor becomes unavailable
STALE_CHECK_INTERVAL = timedelta(seconds=10)


async def async_setup_entry(_hass: HomeAssistant, entry, async_add_entities):
    runtime_data: RuntimeData = entry.runtime_data

    async_add_entities(
        [
            MeterSensorEntity(source, runtime_data.device, runtime_data.device_info)
            for source in runtime_data.dsp_configuration.sources
            if source.meter is not None
        ]
    )


class MeterSensorEntity(SensorEntity):
    _attr_should_poll = False
    _attr_native_unit_of_measurement = "dBFS"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:waveform"
    _attr_suggested_display_precision = 0
    # Clipping flips often while a channel is hot, there's no point in storing it
    _unrecorded_attributes = frozenset({"clipping"})

    def __init__(self, config: SourceConfiguration, device: YamahaDspDevice, device_info: DeviceInfo):
        self._config = config
        self._device = device
        self._device_info = device_info
        self._meter_address, self._position = config.meter

        self._value: int | None = None
        self._clipping = False
        self._last_update = 0.0
        self._unsubscribers: list[Callable[[], None]] = []

    @property
    def name(self) -> str:
        return f"{self._config.name} level"

    @property
    def unique_id(self) -> str:
        serial = next(iter(self._device_info["identifiers"]))[1]
        return f"{serial}_mtx_{self._config.channel_key}_level"

    @property
    def device_info(self) -> DeviceInfo:
        return self._device_info

    @property
    def available(self) -> bool:
        return (
            self._device.is_connected()
            and self._value is not None
            and time.monotonic() - self._last_update < STALE_AFTER
        )

    @property
    def native_value(self) -> int | None:
        return self._value

    @property
    def extra_state_attributes(self) -> dict:
        return {"clipping": self._clipping}

    async def async_added_to_hass(self) -> None:
        self._unsubscribers.append(self._device.add_meter_listener(self._meter_address, self._on_meter))
        self._unsubscribers.append(self._device.add_connection_listener(self._on_connection_change))
        self._unsubscribers.append(async_track_time_interval(self.hass, self._check_stale, STALE_CHECK_INTERVAL))

    async def async_will_remove_from_hass(self) -> None:
        for unsubscribe in self._unsubscribers:
            unsubscribe()
        self._unsubscribers.clear()

    @callback
    def _on_meter(self, values: list[int]) -> None:
        if self._position >= len(values):
            return

        value = meter_value_to_dbfs(values[self._position])
        clipping = values[self._position] == METER_OVER
        was_available = self.available
        self._last_update = time.monotonic()

        # Only write when something changed, to keep the recorder load down
        if value != self._value or clipping != self._clipping or not was_available:
            self._value = value
            self._clipping = clipping
            self.async_write_ha_state()

    @callback
    def _on_connection_change(self, connected: bool) -> None:
        if not connected and self._value is not None:
            self.async_write_ha_state()

    @callback
    def _check_stale(self, _now) -> None:
        if self._value is not None and not self.available:
            self.async_write_ha_state()
