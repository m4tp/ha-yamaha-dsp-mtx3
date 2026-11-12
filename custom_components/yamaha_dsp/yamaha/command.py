import re

from enum import Enum


class ParameterValueType(Enum):
    RAW = 0
    NORMALIZED = 1


# A parameter is addressed either by an index in the MRX7-D "Remote Control Setup List"
# (e.g. 47) or, on the MTX3/MTX5-D, by its full memory address as listed in section 7
# of the protocol specification (e.g. "mem_512/60000/0/0/0/0/0").
Address = int | str

MEMORY_ADDRESS_REGEXP = re.compile(r"^mem_\d+(/\d+){6}$")


def create_index_parameter(idx: int) -> str:
    return f"MTX:Index_{idx}"


def create_parameter(address: Address) -> str:
    if isinstance(address, bool):
        raise ValueError(f"Invalid parameter address: {address!r}")

    if isinstance(address, int):
        return create_index_parameter(address)

    if not isinstance(address, str):
        raise ValueError(f"Invalid parameter address: {address!r}")

    normalized = address.strip()
    if normalized.startswith("MTX:"):
        normalized = normalized[len("MTX:") :]

    if normalized.isdigit():
        return create_index_parameter(int(normalized))

    if normalized.startswith("Index_") and normalized[len("Index_") :].isdigit():
        return create_index_parameter(int(normalized[len("Index_") :]))

    if MEMORY_ADDRESS_REGEXP.match(normalized):
        return f"MTX:{normalized}"

    raise ValueError(f"Invalid parameter address: {address!r}")
