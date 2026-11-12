import shlex

from dataclasses import dataclass


@dataclass
class Response:
    raw_response: str
    parsed_response: list[str]


@dataclass
class ValueResponse(Response):
    value: str

    def get_int_value(self) -> int:
        return int(self.value)

    def get_bool_value(self) -> bool:
        return bool(self.get_int_value())


@dataclass
class OkResponse(ValueResponse):
    pass


@dataclass
class NotifyResponse(ValueResponse):
    pass


@dataclass
class ErrorResponse(Response):
    command_name: str
    error_code: str


def parse_response(response: str) -> Response:
    raw_response = response.strip()
    parsed_response = [part.strip('"') for part in raw_response.split(" ")]

    match parsed_response[0]:
        case "OK" | "OKm":
            value = parsed_response[-1]
            return OkResponse(raw_response, parsed_response, value)
        case "NOTIFY":
            value = parsed_response[-1]
            return NotifyResponse(raw_response, parsed_response, value)
        case "ERROR":
            command_name = parsed_response[1]
            error_code = parsed_response[2]
            return ErrorResponse(raw_response, parsed_response, command_name, error_code)
        case _:
            raise ValueError("Unknown response type")


class ResponseError(Exception):
    pass


@dataclass
class PresetInfo:
    index: int
    number: str
    attribute: str
    title: str

    @property
    def is_user_preset(self) -> bool:
        return self.attribute == "user"


def parse_preset_info(raw_response: str) -> PresetInfo:
    # OK ssinfo 10 "10" user "Preset 10" ""
    # Titles may contain spaces, so the response must be split respecting quotes
    parts = shlex.split(raw_response.strip())

    if len(parts) < 6 or parts[0] not in ("OK", "OKm") or parts[1] != "ssinfo":
        raise ValueError(f"Unexpected ssinfo response: {raw_response}")

    return PresetInfo(int(parts[2]), parts[3], parts[4], parts[5])


def parse_current_preset(raw_response: str) -> int:
    # OK sscurrent 10 unmodified
    parts = raw_response.strip().split()

    if len(parts) < 3 or parts[0] not in ("OK", "OKm") or parts[1] != "sscurrent":
        raise ValueError(f"Unexpected sscurrent response: {raw_response}")

    return int(parts[2])
