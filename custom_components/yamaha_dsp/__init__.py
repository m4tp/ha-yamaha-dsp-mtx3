import asyncio
import json
import logging
import re

from dataclasses import dataclass
from enum import Enum

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryError, ConfigEntryNotReady
from homeassistant.helpers.device_registry import DeviceInfo

from custom_components.yamaha_dsp.const import (
    DOMAIN,
    MAX_NORMALIZED_VOLUME,
    OPTION_MTX_CHANNELS,
    OPTION_MTX_LIMIT_TO_0_DB,
    OPTION_MTX_METERS,
)
from custom_components.yamaha_dsp.mtx import MTX_CHANNELS, NORMALIZED_0_DB
from custom_components.yamaha_dsp.yamaha.command import Address, create_parameter
from custom_components.yamaha_dsp.yamaha.device import ProductInformation, YamahaDspDevice


@dataclass
class SpeakerConfiguration:
    name: str
    index_source: Address
    index_volume: Address
    index_mute: Address
    available_inputs: list[str]
    max_volume: int = MAX_NORMALIZED_VOLUME


@dataclass
class SourceConfiguration:
    name: str
    index_volume: Address
    index_mute: Address
    max_volume: int = MAX_NORMALIZED_VOLUME
    invert_mute: bool = False
    # Stable key for entities created from the MTX channel picker (None for JSON-configured sources)
    channel_key: str | None = None
    # Level meter as (meter address, position) when a level sensor should be created
    meter: tuple[str, int] | None = None


@dataclass
class RouteConfiguration:
    name: str
    index_mute: Address


@dataclass
class RouterSourceConfiguration:
    label: str
    value: int


@dataclass
class RouterConfiguration:
    name: str
    index_source: Address
    sources: list[RouterSourceConfiguration]


@dataclass
class DspConfiguration:
    speakers: [SpeakerConfiguration]
    sources: [SourceConfiguration]
    routes: [RouteConfiguration]
    routers: [RouterConfiguration]

    def __init__(self):
        self.speakers = []
        self.sources = []
        self.routes = []
        self.routers = []


class EntityType(Enum):
    SPEAKER = "speaker"
    SOURCE = "source"
    ROUTE = "route"
    ROUTER = "router"
    PRESET = "preset"


PLATFORMS: list[Platform] = [Platform.MEDIA_PLAYER, Platform.SWITCH, Platform.SELECT, Platform.SENSOR]
UNIQUE_ID_REGEXP = re.compile(r"[\s+]")

logger = logging.getLogger(__name__)


def create_unique_id(name: str, entity_type: EntityType) -> str:
    id = f"{name.lower()}_{entity_type.value}"
    id = re.sub(UNIQUE_ID_REGEXP, "_", id)

    return id


def _parse_address(parsed: dict, key: str) -> Address:
    address = parsed[key]
    # Validate early so that a typo fails the config entry instead of every update
    create_parameter(address)
    return address


def _parse_max_volume(parsed: dict) -> int:
    max_volume = int(parsed.get("max_volume", MAX_NORMALIZED_VOLUME))
    if not 1 <= max_volume <= MAX_NORMALIZED_VOLUME:
        raise ValueError(f"max_volume must be between 1 and {MAX_NORMALIZED_VOLUME} in '{parsed['name']}'")
    return max_volume


def create_dsp_configuration(options) -> DspConfiguration:
    dsp_configuration = DspConfiguration()

    for speaker_configuration in options.get("speaker_configuration") or []:
        parsed = json.loads(speaker_configuration)
        dsp_configuration.speakers.append(
            SpeakerConfiguration(
                parsed["name"],
                _parse_address(parsed, "index_source"),
                _parse_address(parsed, "index_volume"),
                _parse_address(parsed, "index_mute"),
                parsed.get("sources") or options["default_speaker_sources"],
                _parse_max_volume(parsed),
            )
        )

    for source_configuration in options.get("source_configuration") or []:
        parsed = json.loads(source_configuration)
        dsp_configuration.sources.append(
            SourceConfiguration(
                parsed["name"],
                _parse_address(parsed, "index_volume"),
                _parse_address(parsed, "index_mute"),
                _parse_max_volume(parsed),
                bool(parsed.get("invert_mute", False)),
            )
        )

    # MTX channels picked in the options flow
    limit_to_0_db = options.get(OPTION_MTX_LIMIT_TO_0_DB, True)
    meters_enabled = options.get(OPTION_MTX_METERS, False)
    for key in options.get(OPTION_MTX_CHANNELS) or []:
        channel = MTX_CHANNELS.get(key)
        if channel is None:
            logger.warning(f"Ignoring unknown MTX channel '{key}'")
            continue

        dsp_configuration.sources.append(
            SourceConfiguration(
                channel.label,
                channel.index_volume,
                channel.index_mute,
                NORMALIZED_0_DB if limit_to_0_db else MAX_NORMALIZED_VOLUME,
                channel.mute_inverted,
                channel.key,
                channel.meter if meters_enabled else None,
            )
        )

    for route_configuration in options.get("route_configuration") or []:
        parsed = json.loads(route_configuration)
        dsp_configuration.routes.append(
            RouteConfiguration(
                parsed["name"],
                _parse_address(parsed, "index_mute"),
            )
        )

    for router_configuration in options.get("router_configuration") or []:
        parsed = json.loads(router_configuration)
        sources: list[RouterSourceConfiguration] = []
        seen_labels: set[str] = set()

        for source in parsed["sources"]:
            label = source["label"]
            value = int(source["value"])
            if label in seen_labels:
                raise ValueError(f"Duplicate router source label '{label}' in '{parsed['name']}'")
            seen_labels.add(label)
            sources.append(RouterSourceConfiguration(label=label, value=value))

        if not sources:
            raise ValueError(f"Router '{parsed['name']}' must define at least one source")

        dsp_configuration.routers.append(
            RouterConfiguration(
                parsed["name"],
                _parse_address(parsed, "index_source"),
                sources,
            )
        )

    return dsp_configuration


@dataclass
class RuntimeData:
    device: YamahaDspDevice
    product_information: ProductInformation
    device_info: DeviceInfo
    dsp_configuration: DspConfiguration
    supports_presets: bool = False


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    # Create device
    if "host" in entry.data and "port" in entry.data:
        device = YamahaDspDevice(entry.data["host"], entry.data["port"])
    else:
        raise KeyError("Config entry is missing required parameters")

    # Verify that we can connect, store device handle and product/device information
    try:
        await device.connect()
        product_information = await device.query_product_information()
        device_info = DeviceInfo(
            identifiers={(DOMAIN, product_information.serial_number)},
            manufacturer="Yamaha",
            model=product_information.product_name,
            model_id=product_information.device_id,
            serial_number=product_information.serial_number,
            sw_version=product_information.firmware_version,
        )

        # Build the DSP configuration based on the configuration given by the option flow
        dsp_configuration = create_dsp_configuration(entry.options)

        # MTX3/MTX5-D presets are recalled with ssrecall (the MRX7-D uses snapshots instead)
        supports_presets = product_information.product_name.upper().startswith("MTX")

        entry.runtime_data = RuntimeData(device, product_information, device_info, dsp_configuration, supports_presets)
    except ConnectionError as e:
        raise ConfigEntryNotReady("Unable to connect") from e
    except json.JSONDecodeError as e:
        raise ConfigEntryError() from e
    except ValueError as e:
        raise ConfigEntryError() from e

    # Register a listener for option updates
    entry.async_on_unload(entry.add_update_listener(entry_update_listener))

    logger.info(f"Initializing entry with runtime data: {entry.runtime_data}")
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Level meters are streamed by the device for 10 seconds after each request
    meters = sorted({source.meter[0] for source in dsp_configuration.sources if source.meter is not None})
    if meters:
        entry.async_create_background_task(
            hass, _request_meters(entry.runtime_data.device, meters), "yamaha_dsp meter requests"
        )

    return True


METER_REQUEST_PERIOD = 5  # seconds, must stay well below the 10 second transmission window
METER_INTERVAL_MS = 1000


async def _request_meters(device: YamahaDspDevice, meters: list[str]) -> None:
    while True:
        for meter in meters:
            try:
                await device.request_meter(meter, METER_INTERVAL_MS)
            except Exception as e:  # connection problems are retried on the next round
                logger.debug(f"Meter request for {meter} failed: {e}")
        await asyncio.sleep(METER_REQUEST_PERIOD)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    # Disconnect from the device when unloading, otherwise we'll get a
    # "Task was destroyed but it is pending" error
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        runtime_data: RuntimeData = entry.runtime_data
        await runtime_data.device.disconnect()

    return unload_ok


async def entry_update_listener(hass: HomeAssistant, config_entry: ConfigEntry):
    # Reload the entry when options have been changed
    await hass.config_entries.async_reload(config_entry.entry_id)
