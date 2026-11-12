import asyncio
import logging

from collections.abc import Callable
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
    parse_meter_notification,
    parse_preset_info,
    parse_response,
)

ParameterListener = Callable[[str], None]
MeterListener = Callable[[list[int]], None]
EventListener = Callable[[str], None]


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

        # Push notifications. Listeners are plain callbacks invoked from the response listener task:
        # they must not await or run device commands themselves, only schedule work.
        self._parameter_listeners: dict[str, list[ParameterListener]] = {}
        self._meter_listeners: dict[str, list[MeterListener]] = {}
        self._event_listeners: list[EventListener] = []

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

    async def _handle_notify_response(self, response: NotifyResponse):
        parts = response.parsed_response
        kind = parts[1] if len(parts) > 1 else ""

        if kind == "mtr":
            try:
                address, values = parse_meter_notification(response.raw_response)
            except ValueError as e:
                logger.debug(f"Ignoring meter notification: {e}")
                return
            self._dispatch(self._meter_listeners.get(address, []), values)
            return

        logger.debug(f"Got NOTIFY response: {response.raw_response}")

        if kind in ("set", "setn") and len(parts) > 2:
            # NOTIFY set MTX:mem_512/60001/0/0/0/0/0 0 0 1 "ON" -> raw value is the 6th field
            raw_value = parts[5] if len(parts) > 5 else response.value
            self._dispatch(self._parameter_listeners.get(parts[2], []), raw_value)
        else:
            # sscurrent, ssrecall, scheduler events, ...: listeners decide what is relevant
            self._dispatch(self._event_listeners, kind)

    @staticmethod
    def _dispatch(listeners: list, payload) -> None:
        for listener in list(listeners):
            try:
                listener(payload)
            except Exception:  # a broken listener must never stop the response listener
                logger.exception("Error in notification listener")

    @staticmethod
    def _add_listener(registry: dict[str, list], key: str, listener) -> Callable[[], None]:
        registry.setdefault(key, []).append(listener)

        def remove() -> None:
            listeners = registry.get(key, [])
            if listener in listeners:
                listeners.remove(listener)

        return remove

    def add_parameter_listener(self, parameter: str, listener: ParameterListener) -> Callable[[], None]:
        """Calls listener(value) when another controller changes the parameter (e.g. "MTX:mem_512/60000/0/0/0/0/0")."""
        return self._add_listener(self._parameter_listeners, parameter, listener)

    def add_meter_listener(self, meter: str, listener: MeterListener) -> Callable[[], None]:
        """Calls listener(levels) for each meter notification of the meter (e.g. "MTX:mtr_512/20005")."""
        return self._add_listener(self._meter_listeners, meter, listener)

    def add_event_listener(self, listener: EventListener) -> Callable[[], None]:
        """Calls listener(kind) for notifications that are not parameter or meter changes (presets, ...)."""
        self._event_listeners.append(listener)

        def remove() -> None:
            if listener in self._event_listeners:
                self._event_listeners.remove(listener)

        return remove

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

    async def request_meter(self, meter: str, interval_ms: int = 1000) -> OkResponse:
        # The device sends meter data for 10 seconds after each request, so this has to be repeated
        return await self._run_command(f"mtrstart {meter}/meter {interval_ms}")
