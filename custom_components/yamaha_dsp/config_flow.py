import logging

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.data_entry_flow import section
from homeassistant.helpers import selector
from homeassistant.helpers.selector import (
    BooleanSelector,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelectorConfig,
)

from custom_components.yamaha_dsp.const import (
    CONF_HOST,
    CONF_PORT,
    DOMAIN,
    OPTION_ADVANCED,
    OPTION_DEFAULT_SPEAKER_SOURCES,
    OPTION_MTX_CHANNELS,
    OPTION_MTX_LIMIT_TO_0_DB,
    OPTION_MTX_METERS,
    OPTION_ROUTE_CONFIGURATION,
    OPTION_ROUTER_CONFIGURATION,
    OPTION_SOURCE_CONFIGURATION,
    OPTION_SPEAKER_CONFIGURATION,
)
from custom_components.yamaha_dsp.mtx import MTX_CHANNELS
from custom_components.yamaha_dsp.yamaha.device import YamahaDspDevice

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Required(CONF_PORT, default=49280): int,
    }
)

logger = logging.getLogger(__name__)


class YamahaDspConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    # noinspection PyTypeChecker
    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                # Try to connect to the device
                device = YamahaDspDevice(user_input["host"], user_input["port"])
                await device.connect()

                # Make a title for the entry
                product_information = await device.query_product_information()
                title = f"Yamaha {product_information.product_name}"
                logger.info(product_information)

                # Make a unique ID for the entry, prevent adding the same device twice
                unique_id = product_information.serial_number
                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_configured()

                # Disconnect, we'll connect again later, this was just for validation
                await device.disconnect()
            except (BrokenPipeError, ConnectionError, OSError):  # all technically OSError
                errors["base"] = "cannot_connect"
            else:
                return self.async_create_entry(title=title, data=user_input)

        return self.async_show_form(step_id="user", data_schema=STEP_USER_DATA_SCHEMA, errors=errors)

    @staticmethod
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return YamahaDspOptionsFlow(config_entry)


JSON_OPTIONS = [
    OPTION_DEFAULT_SPEAKER_SOURCES,
    OPTION_SPEAKER_CONFIGURATION,
    OPTION_SOURCE_CONFIGURATION,
    OPTION_ROUTE_CONFIGURATION,
    OPTION_ROUTER_CONFIGURATION,
]


def _is_mtx(config_entry: ConfigEntry) -> bool:
    # The entry title is "Yamaha <product name>", see YamahaDspConfigFlow
    return config_entry.title.upper().startswith("YAMAHA MTX")


class YamahaDspOptionsFlow(OptionsFlow):
    def __init__(self, config_entry: ConfigEntry) -> None:
        self._config_entry = config_entry

    def _json_fields(self) -> dict:
        options = self._config_entry.options
        multiline = TextSelectorConfig(multiline=True, multiple=True)

        return {
            vol.Optional(
                OPTION_DEFAULT_SPEAKER_SOURCES, default=options.get(OPTION_DEFAULT_SPEAKER_SOURCES) or []
            ): selector.TextSelector(TextSelectorConfig(multiple=True)),
            vol.Optional(
                OPTION_SPEAKER_CONFIGURATION, default=options.get(OPTION_SPEAKER_CONFIGURATION) or []
            ): selector.TextSelector(multiline),
            vol.Optional(
                OPTION_SOURCE_CONFIGURATION, default=options.get(OPTION_SOURCE_CONFIGURATION) or []
            ): selector.TextSelector(multiline),
            vol.Optional(
                OPTION_ROUTE_CONFIGURATION, default=options.get(OPTION_ROUTE_CONFIGURATION) or []
            ): selector.TextSelector(multiline),
            vol.Optional(
                OPTION_ROUTER_CONFIGURATION, default=options.get(OPTION_ROUTER_CONFIGURATION) or []
            ): selector.TextSelector(multiline),
        }

    def _mtx_schema(self) -> vol.Schema:
        options = self._config_entry.options
        channel_options = [SelectOptionDict(value=c.key, label=c.label) for c in MTX_CHANNELS.values()]

        return vol.Schema(
            {
                vol.Optional(OPTION_MTX_CHANNELS, default=options.get(OPTION_MTX_CHANNELS) or []): SelectSelector(
                    SelectSelectorConfig(options=channel_options, multiple=True, mode=SelectSelectorMode.DROPDOWN)
                ),
                vol.Optional(
                    OPTION_MTX_LIMIT_TO_0_DB, default=options.get(OPTION_MTX_LIMIT_TO_0_DB, True)
                ): BooleanSelector(),
                vol.Optional(OPTION_MTX_METERS, default=options.get(OPTION_MTX_METERS, False)): BooleanSelector(),
                vol.Required(OPTION_ADVANCED): section(vol.Schema(self._json_fields()), {"collapsed": True}),
            }
        )

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}
        is_mtx = _is_mtx(self._config_entry)

        if user_input is not None:
            # Flatten the collapsed "advanced" section so that options keep the same shape
            data = dict(user_input)
            data.update(data.pop(OPTION_ADVANCED, None) or {})

            try:
                # Imported here to avoid a circular import at module load time
                from custom_components.yamaha_dsp import create_dsp_configuration

                create_dsp_configuration(data)
            except (ValueError, KeyError, TypeError) as e:
                logger.warning(f"Invalid DSP configuration: {e}")
                errors["base"] = "invalid_configuration"
            else:
                return self.async_create_entry(title="", data=data)

        schema = self._mtx_schema() if is_mtx else vol.Schema(self._json_fields())

        return self.async_show_form(
            step_id="init" if not is_mtx else "mtx",
            data_schema=schema,
            errors=errors,
        )

    async def async_step_mtx(self, user_input: dict[str, Any] | None = None):
        return await self.async_step_init(user_input)
