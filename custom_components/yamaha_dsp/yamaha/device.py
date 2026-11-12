import asyncio
import logging

from dataclasses import dataclass

from pytelnetdevice import TelnetDevice

from custom_components.yamaha_dsp.yamaha.command import ParameterValueType
from custom_components.yamaha_dsp.yamaha.response import (
    NotifyResponse,
    OkResponse,
    PresetInfo,
    Response,
    ResponseError,
    parse_current_preset,
    parse_preset_info,
    parse_response,
)


@dataclass
class ProductInformation:
    protocol_version: str
    parameter_set_version: str
    firmware_version: str
    product_name: str
    serial_number: str
    device_id: str
    device_name: str


logger = logging.getLogger(__name__)


class YamahaDspDevice(TelnetDevice):
    def __init__(self, host: str, port: int, timeout: int = 5):
        super().__init__(host, port)
        self._timeout = timeout
        self._response_listener_task: asyncio.Task | None = None
        self._last_command_response: Response | Exception | None = None
        self._command_response_received = asyncio.Event()

    async def after_connect(self):
        # Start the response listener
        self._response_listener_task = asyncio.create_task(self._response_listener())

        # A handshake must be performed before the device will accept commands
        await self._perform_handshake()

    async def before_disconnect(self):
        # Cancel the response listener task
        self._response_listener_task.cancel()

    async def _response_listener(self):
        while True:
            raw_resp = await self._read_until("\n")

            # Exit the loop if we read nothing, the other layers will
            # handle the underlying error
            if raw_resp is None:
                return

            try:
                resp = parse_response(raw_resp)

                if isinstance(resp, NotifyResponse):
                    await self._handle_notify_response(resp)
                else:
                    self._last_command_response = resp
                    self._command_response_received.set()
            except ValueError as e:
                logger.error(f"Response listener received a response it couldn't handle: {e}")
                self._last_command_response = e
                self._command_response_received.set()

    @staticmethod
    async def _handle_notify_response(response: NotifyResponse):
        logger.debug(f"Got NOTIFY response: {response.raw_response}")

    async def _send_command(self, command):
        self._writer.write(f"{command}\n".encode())
        await self._writer.drain()

    async def _wait_for_response(self):
        await self._command_response_received.wait()

    async def _run_command(self, command: str) -> OkResponse:
        try:
            async with self._semaphore:
                logger.debug(f"Sending command: {command}")
                # Send the command
                await asyncio.wait_for(self._send_command(command), self._timeout)

                # Wait for the response
                await asyncio.wait_for(self._wait_for_response(), self._timeout)
                resp = self._last_command_response
                self._command_response_received.clear()

                if isinstance(resp, OkResponse):
                    logger.debug(f"Received response: {resp.raw_response}")
                    return resp
                elif isinstance(resp, Exception):
                    raise ResponseError(resp) from resp
                else:
                    raise ResponseError(resp)
        except TimeoutError:
            raise RuntimeError("Command timed out")
        except (ConnectionResetError, BrokenPipeError):
            await self.disconnect()
            raise RuntimeError("Connection was reset")
        finally:
            if not self._connected:
                logger.error("Connection seems to be broken, will attempt to reconnect")
                await self.reconnect()

    async def _perform_handshake(self):
        await self._run_command("devstatus runmode")

    async def query_product_information(self) -> ProductInformation:
        protocol_ver_resp = await self._run_command("devinfo protocolver")
        parameter_set_resp = await self._run_command("devinfo paramsetver")
        firmware_version_resp = await self._run_command("devinfo version")
        product_name_resp = await self._run_command("devinfo productname")
        serial_number_query = await self._run_command("devinfo serialno")
        device_id_query = await self._run_command("devinfo deviceid")
        device_name_query = await self._run_command("devinfo devicename")

        return ProductInformation(
            protocol_ver_resp.value,
            parameter_set_resp.value,
            firmware_version_resp.value,
            product_name_resp.value,
            serial_number_query.value,
            device_id_query.value,
            device_name_query.value,
        )

    async def _query_parameter(
        self, value_type: ParameterValueType, option1: str, option2: str = "0", option3: str = "0"
    ) -> OkResponse:
        command = "get" if value_type is ParameterValueType.RAW else "getn"

        return await self._run_command(f"{command} {option1} {option2} {option3}")

    async def query_parameter_raw(self, option1: str, option2: str = "0", option3: str = "0") -> OkResponse:
        return await self._query_parameter(ParameterValueType.RAW, option1, option2, option3)

    async def query_parameter_normalized(self, option1: str, option2: str = "0", option3: str = "0") -> OkResponse:
        return await self._query_parameter(ParameterValueType.NORMALIZED, option1, option2, option3)

    async def _set_parameter(
        self, value_type: ParameterValueType, option1: str, option2: str, option3: str, value: str
    ) -> OkResponse:
        command = "set" if value_type is ParameterValueType.RAW else "setn"

        return await self._run_command(f"{command} {option1} {option2} {option3} {value}")

    async def set_parameter_raw(self, option1: str, option2: str, option3: str, value: str) -> OkResponse:
        return await self._set_parameter(ParameterValueType.RAW, option1, option2, option3, value)

    async def set_parameter_normalized(self, option1: str, option2: str, option3: str, value: str) -> OkResponse:
        return await self._set_parameter(ParameterValueType.NORMALIZED, option1, option2, option3, value)

    async def query_preset_count(self) -> int:
        resp = await self._run_command("ssnum")
        return resp.get_int_value()

    async def query_preset_info(self, index: int) -> PresetInfo:
        resp = await self._run_command(f"ssinfo {index}")
        return parse_preset_info(resp.raw_response)

    async def query_user_presets(self) -> list[PresetInfo]:
        count = await self.query_preset_count()
        presets = []

        for index in range(count):
            try:
                info = await self.query_preset_info(index)
            except (ResponseError, ValueError) as e:
                logger.debug(f"Skipping preset {index}: {e}")
                continue

            if info.is_user_preset:
                presets.append(info)

        return presets

    async def query_current_preset(self) -> int:
        resp = await self._run_command("sscurrent")
        return parse_current_preset(resp.raw_response)

    async def recall_preset(self, index: int) -> OkResponse:
        return await self._run_command(f"ssrecall {index}")
