"""Catalog of the MTX3 channels that can be added from the options flow without writing JSON.

Addresses come from section 7 of the remote control protocol specification (see docs/).
Input channels, stereo inputs and DCA groups have been verified on a real MTX3.
"""

from dataclasses import dataclass

# Normalized fader values: 804 = 0 dB, 1000 = +10 dB
NORMALIZED_0_DB = 804


@dataclass(frozen=True)
class MtxChannel:
    key: str
    label: str
    index_volume: str
    index_mute: str
    # True when the mute address is a "mute on" parameter (1 = muted) instead of a channel "ON" (1 = on)
    mute_inverted: bool = False
    # Level meter as (meter address, position), see section 8 of the specification
    meter: tuple[str, int] | None = None


# MTX3 level meters (section 8.1): post-ON input channels, zone outputs and output channels
METER_INPUT_CHANNELS = "MTX:mtr_512/20005"
METER_ZONE_OUT = "MTX:mtr_512/20015"
METER_OUTPUT_CHANNELS = "MTX:mtr_512/20020"


def _address(unique_id: int, element: int, x: int, y: int = 0) -> str:
    return f"mem_512/{unique_id}/{element}/{x}/{y}/0/0"


def _build_catalog() -> dict[str, MtxChannel]:
    channels: list[MtxChannel] = []

    for n in range(8):
        channels.append(
            MtxChannel(
                f"input_{n + 1}",
                f"CH{n + 1}",
                _address(60000, 0, n),
                _address(60001, 0, n),
                meter=(METER_INPUT_CHANNELS, n),
            )
        )

    for n, label in enumerate(["STIN1L", "STIN1R", "STIN2L", "STIN2R", "STIN3L", "STIN3R"], start=8):
        channels.append(
            MtxChannel(
                f"stereo_{label.lower()}",
                label,
                _address(60000, 0, n),
                _address(60001, 0, n),
                meter=(METER_INPUT_CHANNELS, n),
            )
        )

    for n, letter in enumerate("ABCDEFGH"):
        channels.append(
            MtxChannel(
                f"dca_{letter.lower()}",
                f"DCA {letter}",
                _address(60000, 2, 0, n),
                _address(60001, 2, 0, n),
                mute_inverted=True,
            )
        )

    for n in range(8):
        channels.append(
            MtxChannel(
                f"zone_{n + 1}",
                f"ZONE{n + 1}",
                _address(60002, 0, n),
                _address(60003, 0, n),
                meter=(METER_ZONE_OUT, n),
            )
        )

    for n in range(8):
        channels.append(
            MtxChannel(
                f"output_{n + 1}",
                f"OUT{n + 1}",
                _address(20017, 0, n),
                _address(20017, 1, n),
                meter=(METER_OUTPUT_CHANNELS, n),
            )
        )

    return {channel.key: channel for channel in channels}


MTX_CHANNELS: dict[str, MtxChannel] = _build_catalog()
