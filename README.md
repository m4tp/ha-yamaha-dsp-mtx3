# ha-yamaha-dsp

[![Linting](https://github.com/NitorCreations/ha-yamaha-dsp/actions/workflows/ruff.yaml/badge.svg)](https://github.com/NitorCreations/ha-yamaha-dsp/actions/workflows/ruff.yaml)
[![Tests](https://github.com/NitorCreations/ha-yamaha-dsp/actions/workflows/unittest.yaml/badge.svg)](https://github.com/NitorCreations/ha-yamaha-dsp/actions/workflows/unittest.yaml)

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=NitorCreations&repository=ha-yamaha-dsp&category=integration)

Home Assistant integration for controlling Yamaha DSP processors such as the MRX7-D.

## Supported devices

* Yamaha MRX7-D
* Yamaha MTX3 / MTX5-D (parameters addressed by memory address, see below)

## Requirements

The DSP must have Telnet access enabled

## Features

Since the DSP is a complicated device that allows for very fine-grained customization, the 
integration tries to be fairly generic. This means that out of the box, no entities are created 
when you configure the integration - everything must be configured explicitly.

The following entity types can be configured:

* speakers (media player, with source selection)
* sources (media player)
* routes (on/off switches)
* routers (select, one source selection per sink)

Entities are configured using JSON, like this:

A speaker:
```json
{
  "name": "Kitchen",
  "index_source": 33,
  "index_volume": 47,
  "index_mute": 48
}
```

A source:
```json
{
  "name": "Lounge DJ mixer",
  "index_volume": 11,
  "index_mute": 12
}
```

A route:
```json
{
  "name": "Wireless mics to classroom",
  "index_mute": 2
}
```

A router sink:
```json
{
  "name": "Classroom sink",
  "index_source": 20019,
  "sources": [
    { "label": "NONE", "value": 0 },
    { "label": "Mic bus", "value": 3 },
    { "label": "YDIF IN 1", "value": 17 }
  ]
}
```

Router entities behave like a matrix selector per sink: each sink exposes the allowed source options that
you configure, and exactly one source can be selected at a time for each sink.

## MTX3 / MTX5-D

The MTX series has no Remote Control Setup List. Instead, every `index_*` field accepts the parameter's
memory address from section 7 of the protocol specification (`docs/`), as a string:

```json
{
  "name": "MIC_1",
  "index_volume": "mem_512/60000/0/0/0/0/0",
  "index_mute": "mem_512/60001/0/0/0/0/0",
  "max_volume": 804
}
```

Commonly used MTX3 addresses (`n` = channel number - 1):

| Parameter                        | Fader (level)            | On (unmuted = 1)         |
|----------------------------------|--------------------------|--------------------------|
| Input channel CH1-8              | `mem_512/60000/0/n/0/0/0` | `mem_512/60001/0/n/0/0/0` |
| Stereo input STIN1L-3R (n = 8-13)| `mem_512/60000/0/n/0/0/0` | `mem_512/60001/0/n/0/0/0` |
| Zone 1-8 (matrix output master)  | `mem_512/60002/0/n/0/0/0` | `mem_512/60003/0/n/0/0/0` |
| Output channel OUT1-8            | `mem_512/20017/0/n/0/0/0` | `mem_512/20017/1/n/0/0/0` |

The normalized fader range is 0-1000, where 804 is 0 dB and 1000 is +10 dB. The optional `max_volume`
setting caps the volume entity, so that 100% in Home Assistant maps to e.g. 0 dB (`804`) instead of +10 dB.

An easy way to find any other address is to connect with `nc <ip> 49280`, send `devstatus runmode`,
then move the control in MTX-MRX Editor or Wireless DCP: the device prints a `NOTIFY set MTX:mem_...` line
containing the address.

On MTX devices a **Preset** select entity is created automatically. It lists the stored presets and
recalls the selected one.

## Development

Development is done the same way as any custom Home Assistant integration. For a more detailed description, see 
e.g. https://github.com/NitorCreations/ha-extron?tab=readme-ov-file#development.

The [examples](./examples) directory contains a small sample program that illustrates how the library for 
communicating with the Yamaha DSP works.

## License

GNU GENERAL PUBLIC LICENSE version 3
