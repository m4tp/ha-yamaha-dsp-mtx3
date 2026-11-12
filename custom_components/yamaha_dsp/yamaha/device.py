import asyncio
import contextlib
import logging
import time

from collections.abc import Callable
from dataclasses import dataclass

from pytelnetdevice import TelnetDevice

from custom_components.yamaha_dsp.yamaha.command import ParameterValueType
from custom_components.yamaha_dsp.yamaha.response import (
    ErrorResponse,
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
ConnectionListener = Callable[[bool], None]


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


# Delays between reconnection attempts after the connection was lost, the last one repeats
RECONNECT_BACKOFF = (1, 2, 5, 10, 30)
# The device drops a session that sends nothing for this long, so half-open sockets don't block new connections
KEEPALIVE_MS = 60000
# Seconds without outgoing traffic before a heartbeat command is sent, well below KEEPALIVE_MS
HEARTBEAT_INTERVAL = 25
# A link that died without closing (cable pulled) only shows up as commands timing out
MAX_CONSECUTIVE_TIMEOUTS = 2
# Event sent to event listeners when all parameters should be read again, see refresh()
REFRESH_EVENT = "refresh"


def reconnect_delay(attempt: int) -> float:
    return RECONNECT_BACKOFF[min(attempt, len(RECONNECT_BACKOFF) - 1)]


def _normalize_option(option: str) -> str:
    # Firmware V2.02 and earlier echo index addresses zero-padded (MTX:Index_002)
    if option.startswith("MTX:Index_") and option[len("MTX:Index_") :].isdigit():
        return f"MTX:Index_{int(option[len('MTX:Index_') :])}"
    return option


def response_matches(command: str, response: Response) -> bool:
    """OK repeats the command name and its first option (the address), ERROR repeats only the command name."""
    parts = command.split()
    if len(response.parsed_response) < 2 or response.parsed_response[1] != parts[0]:
        return False
    if isinstance(response, ErrorResponse) or len(parts) < 2:
        return True
    return len(response.parsed_response) > 2 and _normalize_option(response.parsed_response[2]) == _normalize_option(
        parts[1]
    )


class YamahaDspDevice(TelnetDevice):
    def __init__(self, host: str, port: int, timeout: int = 5):
        super().__init__(host, port)
        self._timeout = timeout
        self._response_listener_task: asyncio.Task | None = None
        self._heartbeat_task: asyncio.Task | None = None
        self._reconnect_task: asyncio.Task | None = None
        # Command waiting for its response and the future that receives it
        self._pending: tuple[str, asyncio.Future] | None = None
        self._last_sent = 0.0
        self._consecutive_timeouts = 0
        # Reconnect automatically only once a connection has been established and until disconnect()
        self._auto_reconnect = False
        # Set to cut the current reconnection delay short, see refresh()
        self._retry_now = asyncio.Event()

        # Push notifications. Listeners are plain callbacks invoked from the response listener task:
        # they must not await or run device commands themselves, only schedule work.
        self._parameter_listeners: dict[str, list[ParameterListener]] = {}
        self._meter_listeners: dict[str, list[MeterListener]] = {}
        self._event_listeners: list[EventListener] = []
        self._connection_listeners: list[ConnectionListener] = []

    async def after_connect(self):
        # Start the response listener
        self._response_listener_task = asyncio.create_task(self._response_listener())

        # A handshake must be performed before the device will accept commands
        await self._perform_handshake()
        try:
            await self._run_command(f"scpmode keepalive {KEEPALIVE_MS}")
        except ResponseError as e:
            logger.warning("Device does not support keepalive: %s", e)

        self._heartbeat_task = asyncio.create_task(self._heartbeat())
        self._auto_reconnect = True
        logger.info("Connected to %s:%s", self._host, self._port)
        self._dispatch(self._connection_listeners, True)

    async def before_disconnect(self):
        self._auto_reconnect = False
        for task in (self._reconnect_task, self._heartbeat_task, self._response_listener_task):
            if task is not None and task is not asyncio.current_task():
                task.cancel()

    async def disconnect(self):
        await self.before_disconnect()
        self._connected = False
        self._fail_pending()
        await self._close_transport()

    async def _close_transport(self):
        if self._writer is None:
            return
        try:
            self._writer.close()
            await self._writer.wait_closed()
        except OSError:
            pass  # we're closing the connection anyway

    async def _read_line(self) -> str | None:
        try:
            return (await self._reader.readuntil(b"\n")).decode()
        except asyncio.IncompleteReadError:
            return None  # EOF

    async def _response_listener(self):
        try:
            while (raw_resp := await self._read_line()) is not None:
                try:
                    resp = parse_response(raw_resp)
                except (ValueError, IndexError) as e:
                    logger.warning("Response listener received a response it couldn't handle: %s (%r)", e, raw_resp)
                    continue

                if isinstance(resp, NotifyResponse):
                    await self._handle_notify_response(resp)
                else:
                    self._handle_command_response(resp)
            logger.warning("Connection to %s:%s closed by the device", self._host, self._port)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning("Connection to %s:%s lost: %s", self._host, self._port, e)
        self._connection_lost()

    def _handle_command_response(self, response: Response) -> None:
        if self._pending is None or not response_matches(self._pending[0], response):
            # E.g. the response to a command that already timed out
            logger.debug("Discarding unexpected response: %s", response.raw_response)
            return
        future = self._pending[1]
        if not future.done():
            future.set_result(response)

    def _fail_pending(self) -> None:
        if self._pending is not None and not self._pending[1].done():
            self._pending[1].set_exception(ConnectionError("Connection lost"))

    def _connection_lost(self) -> None:
        """Marks the connection as lost, fails the pending command and starts reconnecting (once)."""
        was_connected = self._connected
        self._connected = False
        self._consecutive_timeouts = 0
        self._fail_pending()
        if self._writer is not None:
            self._writer.close()
        if self._heartbeat_task is not None and self._heartbeat_task is not asyncio.current_task():
            self._heartbeat_task.cancel()

        if self._auto_reconnect and (self._reconnect_task is None or self._reconnect_task.done()):
            self._reconnect_task = asyncio.create_task(self._reconnect_loop())
        if was_connected:
            self._dispatch(self._connection_listeners, False)

    async def _reconnect_loop(self):
        attempt = 0
        while True:
            delay = reconnect_delay(attempt)
            logger.info("Reconnecting to %s:%s in %s s", self._host, self._port, delay)
            self._retry_now.clear()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._retry_now.wait(), delay)
            if self._response_listener_task is not None:
                self._response_listener_task.cancel()
            try:
                await self.connect()
                return
            except Exception as e:  # unreachable, refused, handshake timeout, ...
                logger.debug("Reconnection attempt %s failed: %s", attempt + 1, e)
                self._connected = False
                await self._close_transport()
            attempt += 1

    async def _heartbeat(self):
        while True:
            await asyncio.sleep(HEARTBEAT_INTERVAL - (time.monotonic() - self._last_sent))
            if time.monotonic() - self._last_sent < HEARTBEAT_INTERVAL:
                continue  # other commands kept the connection alive
            try:
                await self._run_command("devstatus runmode")
            except ResponseError:
                pass  # the device answered, so the connection works
            except Exception as e:
                logger.warning("Heartbeat to %s:%s failed: %s", self._host, self._port, e)
                self._connection_lost()
                return

    def start_reconnecting(self) -> None:
        """Keeps trying to connect in the background, e.g. when the device is off while Home Assistant starts."""
        self._auto_reconnect = True
        self._connection_lost()

    def refresh(self) -> None:
        """Reconnects right away when disconnected, otherwise asks the entities to read all parameters again."""
        if self._connected:
            self._dispatch(self._event_listeners, REFRESH_EVENT)
        else:
            self._retry_now.set()

    def add_connection_listener(self, listener: ConnectionListener) -> Callable[[], None]:
        """Calls listener(True) after every (re)connection and listener(False) when the connection is lost."""
        self._connection_listeners.append(listener)

        def remove() -> None:
            if listener in self._connection_listeners:
                self._connection_listeners.remove(listener)

        return remove

    def active_meters(self) -> list[str]:
        """Meters that have at least one listener, i.e. an enabled sensor added to Home Assistant."""
        return sorted(meter for meter, listeners in self._meter_listeners.items() if listeners)

    async def _handle_notify_response(self, response: NotifyResponse):
        parts = response.parsed_response
        kind = parts[1] if len(parts) > 1 else ""

        if kind == "mtr":
            try:
                address, values = parse_meter_notification(response.raw_response)
            except ValueError as e:
                logger.debug("Ignoring meter notification: %s", e)
                return
            self._dispatch(self._meter_listeners.get(address, []), values)
            return

        logger.debug("Got NOTIFY response: %s", response.raw_response)

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
        self._last_sent = time.monotonic()

    async def _run_command(self, command: str) -> OkResponse:
        if not self._connected:
            raise ConnectionError("Not connected")

        async with self._semaphore:
            if not self._connected:
                raise ConnectionError("Not connected")

            future = asyncio.get_running_loop().create_future()
            self._pending = (command, future)
            try:
                logger.debug("Sending command: %s", command)
                await asyncio.wait_for(self._send_command(command), self._timeout)
                resp = await asyncio.wait_for(future, self._timeout)
            except TimeoutError:
                self._consecutive_timeouts += 1
                if self._consecutive_timeouts >= MAX_CONSECUTIVE_TIMEOUTS:
                    logger.warning("%s commands in a row timed out", self._consecutive_timeouts)
                    self._connection_lost()
                raise RuntimeError("Command timed out") from None
            except ConnectionError:
                raise
            except OSError as e:  # broken pipe, connection reset, ...
                self._connection_lost()
                raise ConnectionError("Connection lost") from e
            finally:
                self._pending = None
            self._consecutive_timeouts = 0

        if isinstance(resp, OkResponse):
            logger.debug("Received response: %s", resp.raw_response)
            return resp
        raise ResponseError(resp)

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
                logger.debug("Skipping preset %s: %s", index, e)
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
