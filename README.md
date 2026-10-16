# MeRGBW Sunset Lamp for Home Assistant

A Home Assistant integration for the inexpensive Bluetooth "sunset projection
lamps" that are controlled with the **MeRGBW** phone app. The lamps advertise
themselves as `Sunset lights`.

- Bluetooth discovery: lamps show up on the Integrations page by themselves,
  also through ESPHome Bluetooth proxies.
- One device and one light entity per lamp, with RGB color and brightness.
- No bright flash on turn-on: the lamp is dimmed before it is turned off, so it
  comes back on nearly dark before the new color and brightness arrive.
- By default connects only when it has something to send and disconnects
  after 10 seconds idle. That frees the proxy's connection slot, and the lamp's
  single connection, so the phone app keeps working.
- Optionally keeps the connection open instead, so commands go through right
  away and the light shows the lamp's real power and brightness (see
  [Options](#options)).
- Errors raise `HomeAssistantError`, so script steps with
  `continue_on_error: true` carry on when a lamp is unplugged or out of range.
- The last state is restored after a restart.

## Installation

1. In HACS, open the menu, choose **Custom repositories**, and add
   `https://github.com/davidanthoff/ha-mergbw` with category **Integration**.
2. Install **MeRGBW Sunset Lamp** and restart Home Assistant.
3. Lamps in range of a Bluetooth adapter or proxy appear under
   **Settings → Devices & services** as discovered. You can also add one with
   **Add integration → MeRGBW Sunset Lamp**, and pick a discovered lamp or type
   its Bluetooth address.

Each lamp is named after the end of its address, for example
`Sunset lamp A1B2` with the entity `light.sunset_lamp_a1b2`. Rename them as you
like.

Home Assistant 2026.9 or newer is required.

## Options

Each lamp has one option, under **Settings → Devices & services → MeRGBW
Sunset Lamp → Configure**:

**Keep the connection open** (off by default). Connecting to these lamps can be
slow, because they advertise only every few seconds; through an ESPHome proxy a
connection can take 30 seconds or more. With this option:

- The connection is opened when Home Assistant starts and kept open, so
  commands are sent right away. If it drops, it is re-established in the
  background, right away and then after 5, 15, 30 and every 60 seconds. A
  warning is logged after three failed attempts in a row, and again when the
  lamp is reachable.
- The lamp is asked for its status after every command and every 30 seconds.
  The light's on/off state and brightness follow what the lamp reports, also
  when it is switched with its remote, and the state is no longer assumed, so
  dashboards show a toggle instead of separate on and off buttons.
- The lamp accepts only one connection, so its phone app cannot connect while
  Home Assistant holds it. The connection also occupies one of the proxy's
  connection slots (ESPHome proxies have three).

Changing the option reloads the lamp.

## How the light behaves

Without **Keep the connection open**, the integration does not read the lamp's
state back, so the entity shows the last state that was sent (`assumed_state`).
With it, power and brightness are read back; the color is never reported by
the lamp, so it is always the last one sent. The state is kept across restarts.

The lamp ignores color and brightness commands while it is off, and powers up
at the color and brightness it had when it was turned off. So:

- **Turning off** sets the brightness to 1 % first, then powers off.
- **Turning on from off** powers on (the lamp briefly shows its last color at
  1 %), then sends color, then brightness. Color goes first so the lamp is never
  bright in the old color.
- **Turning on while on** sends only what changed. If nothing changed, it sends
  power on again, in case the lamp was switched off with its remote.

Because of this, a lamp turned off from Home Assistant comes back at 1 % when it
is switched on with its own remote or the phone app.

- **Brightness** 1–255 is mapped to 1–100 %, rounding up, so a low brightness
  never turns into 0 %. 1 % is still visibly lit.
- **Color temperature** is converted to RGB by Home Assistant.
- **Transitions** and the lamp's built-in scenes are not supported.

## Action: `mergbw.send_raw`

For working out the protocol. Sends bytes to a lamp as they are, and can return
what the lamp sends back on its notify characteristic. If the lamp answers with
a status frame, the light entity's power and brightness are updated from it.

```yaml
action: mergbw.send_raw
data:
  config_entry_id: 0123456789abcdef0123456789abcdef
  frame: "55 00 ff 05"   # status request
  add_checksum: true     # append the checksum byte
  listen: 2              # seconds to collect notifications
response_variable: reply
```

With a response variable, the result looks like
`{"sent": "5500ff05a5", "notifications": ["..."]}`.

## Protocol

Everything below was learned from other projects (see Credits) and from
testing on a lamp.

- Advertised service UUID `00003519-0000-1000-8000-00805f9b34fb`, local name
  `Sunset lights`. MeRGBW LED strips share the service UUID but use a
  different protocol, so discovery also requires the name.
- GATT service `0000fff0-…`. Writes go to `0000fff3-…`, notifications come
  from `0000fff4-…`.
- The lamp accepts one connection at a time.

Frames sent to the lamp:

```
0x55  command  0xFF  length  payload…  checksum
```

`length` is the length of the whole frame (payload + 5). The checksum is the
sum of all preceding bytes with carries folded back in
(`while s > 0xFF: s = (s >> 8) + (s & 0xFF)`), then inverted (`~s & 0xFF`).
The lamp does not actually check it: frames with a wrong checksum are accepted.

| Command | Meaning | Payload | Example |
|---|---|---|---|
| `0x00` | status request | none | `5500ff05a5` |
| `0x01` | power | `01` on, `00` off | `5501ff0601a2` |
| `0x03` | color | R, G, B | `5503ff08ff8c28ea` |
| `0x05` | brightness | percent, 0–100 | `5505ff06643b` |
| `0x06` | scene (not used) | scene ID, `0x80`–`0x93` | `5506ff06811d` |

While the lamp is off it ignores color and brightness and does not acknowledge
them. Power and status requests work either way.

The lamp answers on the notify characteristic with frames that start with
`0x56`:

- **Acknowledgement** of a command it carried out: `56 <command> ff 06 00 xx`.
- **Status** in reply to `0x00`: `56 00 ff 0f <power> <brightness %> 00 … 00 xx`,
  for example `5600ff0f0164000000000000000045` for on at 100 %. The color is not
  reported.

The last byte of these replies is not the checksum described above; its
meaning is unknown.

## Development

Tests use
[pytest-homeassistant-custom-component](https://github.com/MatthewFlamm/pytest-homeassistant-custom-component),
pinned to the Home Assistant version this is developed against:

```bash
uv venv --python 3.14
uv pip install -r requirements_test.txt
uv run pytest
uv run ruff check . && uv run ruff format --check .
```

## Credits

This integration is written from scratch. No code was copied from other
projects, but the protocol facts come from:

- [King1704G/custom_components-sunset_light_cheapass_amazon_light](https://github.com/King1704G/custom_components-sunset_light_cheapass_amazon_light),
  an earlier Home Assistant integration for the same lamps: UUIDs, frame
  layout, checksum, color, brightness and scene commands.
- [smarthomesven/homey-mergbw](https://github.com/smarthomesven/homey-mergbw),
  a Homey app for MeRGBW LED strips: protocol notes, the status request, and
  the one-connection limit.

## License

MIT, see [LICENSE](LICENSE).
