# Yamaha DSP for Home Assistant (MTX3 / MTX5-D)

[![Linting](https://github.com/m4tp/ha-yamaha-dsp-mtx3/actions/workflows/ruff.yaml/badge.svg)](https://github.com/m4tp/ha-yamaha-dsp-mtx3/actions/workflows/ruff.yaml)
[![Tests](https://github.com/m4tp/ha-yamaha-dsp-mtx3/actions/workflows/unittest.yaml/badge.svg)](https://github.com/m4tp/ha-yamaha-dsp-mtx3/actions/workflows/unittest.yaml)

Home Assistant integration for Yamaha DSP processors. This is a fork of
[NitorCreations/ha-yamaha-dsp](https://github.com/NitorCreations/ha-yamaha-dsp) that adds support for the
**Yamaha MTX3 and MTX5-D** matrix processors. It stays compatible with the **MRX7-D** and with the JSON
configuration of the original integration (domain `yamaha_dsp`).

## Features

* **MTX channels from the options menu, no JSON needed**: input channels CH1–8, stereo inputs STIN1L–STIN3R,
  DCA groups A–H, zones ZONE1–8 and output channels OUT1–8.
* **Volume and mute** of each channel as a media player entity.
* **DCA groups** with their fader and the group mute.
* **Preset select**: shows the current preset and recalls the selected one.
* **Limit volume to 0 dB**: 100% in Home Assistant is 0 dB instead of +10 dB.
* **Push updates**: changes made from MTX-MRX Editor, Wireless DCP, DCP panels or preset recalls show up in
  Home Assistant within a fraction of a second.
* **Level meter sensors** in dBFS for input channels, stereo inputs, zones and output channels.
* **The matrix can be switched off**: entities become unavailable while it is off (also when Home Assistant
  starts first) and read every parameter again as soon as it is back.
* **DSP refresh button**: reads every parameter right away, or retries the connection immediately when the
  matrix is unreachable.

## Installation

### HACS (recommended)

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=m4tp&repository=ha-yamaha-dsp-mtx3&category=integration)

Or add it by hand: HACS → ⋮ → *Custom repositories* → `https://github.com/m4tp/ha-yamaha-dsp-mtx3`, category
*Integration*. Install **Yamaha DSP** and restart Home Assistant.

### Manual

Copy `custom_components/yamaha_dsp` from this repository into the `custom_components` folder of your Home
Assistant configuration and restart Home Assistant.

## When the matrix is switched off

The matrix does not need to be on when Home Assistant starts. While it is off, its entities are unavailable and
the integration tries to connect after 1, 2, 5 and 10 seconds, then every 30 seconds. As soon as the matrix
answers, every entity reads all its parameters again: there is no need to wait for the 5 minute poll.

The **DSP refresh** button (`button.dsp_refresh`) is always available:

* matrix reachable: every entity reads all its parameters again right away;
* matrix unreachable: the next connection attempt starts immediately instead of waiting up to 30 seconds.

Add it to a dashboard, e.g. next to the switch that powers the matrix on:

```yaml
type: button
entity: button.dsp_refresh
name: Reconnect matrix
tap_action:
  action: perform-action
  perform_action: button.press
  target:
    entity_id: button.dsp_refresh
```

Or press it from an automation, a few seconds after the matrix is powered on:

```yaml
actions:
  - action: button.press
    target:
      entity_id: button.dsp_refresh
```

## Network requirements

Home Assistant must reach the matrix on **TCP port 49280**. Check firewalls and VLAN rules between the two.
There is nothing to enable in MTX-MRX Editor for this.

To test the connection from the Home Assistant host (or another machine on the same network):

```sh
nc -vz <ip> 49280
```

## MTX configuration

1. *Settings → Devices & services → Add integration → Yamaha DSP*, then enter the IP address and the port
   (`49280`).
2. Open the integration and click **Configure**:
   * **Channels**: pick the channels to control. Each one becomes a media player with volume and mute.
   * **Limit volume to 0 dB** (on by default): 100% in Home Assistant is 0 dB instead of +10 dB.
   * **Level meter sensors**: adds a dBFS sensor for each selected channel that has a meter (DCA groups have
     none).

Entities are named after the channel (`CH1`, `DCA A`, `ZONE3`, …). Rename them from the Home Assistant UI.

**DCA groups** control the DCA fader and the mute group with the same letter (DCA A → mute group A). Assign
the channels to the mute group in MTX-MRX Editor (DCA tab → INPUT CH MUTE), otherwise muting the DCA in Home
Assistant does nothing.

## Level meters and the recorder

Level sensors update about once per second and every change is a new state in the database. While a channel
clips, the state stays at 0 dBFS and the `clipping` attribute is `true` (this attribute is not recorded).

To keep the database small, pick one of these two options.

**Exclude the sensors from the recorder** (no history at all), in `configuration.yaml`:

```yaml
recorder:
  exclude:
    entity_globs:
      - sensor.ch*_level
      - sensor.stin*_level
      - sensor.zone*_level
      - sensor.out*_level
```

**Keep a short history** and purge it every night with an automation:

```yaml
automation:
  - alias: Purge MTX level meters
    triggers:
      - trigger: time
        at: "03:00:00"
    actions:
      - action: recorder.purge_entities
        data:
          keep_days: 1
          entity_globs:
            - sensor.ch*_level
            - sensor.stin*_level
            - sensor.zone*_level
            - sensor.out*_level
```

> [!WARNING]
> These patterns match the default entity IDs (`sensor.ch1_level`, `sensor.zone3_level`, …). Check the real
> entity IDs in *Settings → Entities* before using them: if you renamed the entities, or other integrations have
> entities that match, adjust the patterns. Do not use `sensor.*_level`, it also matches battery level sensors
> of other integrations.

## Advanced configuration (JSON)

The options dialog has an **Advanced (JSON)** section (on the MRX7-D it is the whole dialog) to configure
entities by parameter address:

* speakers (media player with source selection)
* sources (media player)
* routes (on/off switch)
* routers (select, one source per sink)

### MRX7-D

The MRX7-D addresses parameters by their number in the Remote Control Setup List:

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

Each router sink exposes the source options you configure, and exactly one of them is selected at a time.

### MTX3 / MTX5-D by memory address

The MTX series has no Remote Control Setup List: every `index_*` field takes the parameter's memory address
from section 7 of the protocol specification ([`docs/`](./docs)), as a string:

```json
{
  "name": "MIC_1",
  "index_volume": "mem_512/60000/0/0/0/0/0",
  "index_mute": "mem_512/60001/0/0/0/0/0",
  "max_volume": 804
}
```

MTX3 addresses (`n` = channel number - 1):

| Parameter                         | Fader (level)              | On / mute                                   |
|-----------------------------------|----------------------------|---------------------------------------------|
| Input channel CH1–8               | `mem_512/60000/0/n/0/0/0`  | `mem_512/60001/0/n/0/0/0` (ON, 1 = on)      |
| Stereo input STIN1L–3R (n = 8–13) | `mem_512/60000/0/n/0/0/0`  | `mem_512/60001/0/n/0/0/0` (ON, 1 = on)      |
| DCA group A–H (n = 0–7)           | `mem_512/60000/2/0/n/0/0`  | `mem_512/60001/2/0/n/0/0` (mute group, 1 = muted, use `"invert_mute": true`) |
| Zone 1–8 (matrix output master)   | `mem_512/60002/0/n/0/0/0`  | `mem_512/60003/0/n/0/0/0` (ON, 1 = on)      |
| Output channel OUT1–8             | `mem_512/20017/0/n/0/0/0`  | `mem_512/20017/1/n/0/0/0` (ON, 1 = on)      |

Verified on a real MTX3: fader and ON of CH1–4, fader of STIN2L, DCA faders. The other channels of the same
rows follow the same pattern. Zones, output channels and the mute groups come from the specification only.

The normalized fader range is 0–1000, where 804 is 0 dB and 1000 is +10 dB. The optional `max_volume` setting
caps the volume entity, so that 100% in Home Assistant is e.g. 0 dB (`804`) instead of +10 dB.

### Finding an address

Connect to the matrix and start a session:

```sh
nc <ip> 49280
devstatus runmode
```

Then move the control in MTX-MRX Editor or Wireless DCP. The matrix prints a line such as
`NOTIFY set MTX:mem_512/60000/0/0/0/0/0 0 0 -600 "-6.00"`: the `mem_...` part is the address.

## Troubleshooting

**Debug logs**: add this to `configuration.yaml` and restart:

```yaml
logger:
  default: info
  logs:
    custom_components.yamaha_dsp: debug
```

**Entities unavailable**: Home Assistant cannot reach the matrix. Check the IP address, port 49280, firewalls and
VLANs (see [Network requirements](#network-requirements)). The integration reconnects by itself (after 1, 2,
5, 10, then every 30 seconds) and refreshes all entities once the connection is back. Press **DSP refresh** to
retry right away.

**"Update of … is taking over 10 seconds"**: commands to the matrix are sent one at a time. Since push updates
keep entities current, polling only runs every 5 minutes as a safety net. If the warning persists, the matrix
is slow to answer or unreachable: check the debug logs.

## Development

Development works like any custom Home Assistant integration, see e.g.
https://github.com/NitorCreations/ha-extron?tab=readme-ov-file#development.

```sh
python3.13 -m venv .venv
.venv/bin/pip install . ruff
.venv/bin/ruff check . && .venv/bin/ruff format --check .
.venv/bin/python -m unittest discover -s tests
```

The [examples](./examples) directory contains a small program showing how the device library works.

## Credits and license

Based on [ha-yamaha-dsp](https://github.com/NitorCreations/ha-yamaha-dsp) by NitorCreations.

GNU General Public License version 3, see [LICENSE](./LICENSE).
