# MeRGBW Sunset Lamp for Home Assistant

A Home Assistant integration for the inexpensive Bluetooth "sunset projection
lamps" that are controlled with the **MeRGBW** phone app. The lamps advertise
themselves as `Sunset lights`.

- Bluetooth discovery: lamps show up on the Integrations page by themselves,
  also through ESPHome Bluetooth proxies.
- One device and one light entity per lamp, with RGB color, brightness and the
  lamp's built-in scenes as effects.
- No flash on turn-on: color and brightness are sent before the lamp powers up.
- Connects only when it has something to send and disconnects after 10 seconds
  idle. That frees the proxy's connection slot, and the lamp's single
  connection, so the phone app keeps working.
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

## How the light behaves

The lamp does not report its state, so the entity shows the last state that was
sent (`assumed_state`), and keeps it across restarts.

- **Turning on from off** sends color (or scene) and brightness, then power,
  then color and brightness once more. The repeat is there because it is not
  yet known whether the lamp applies settings while it is off.
- **Turning on while on** sends only what changed. If nothing changed, it sends
  power on again, in case the lamp was switched off with its remote.
- **Brightness** 1–255 is mapped to 1–100 %, rounding up, so a low brightness
  never turns into 0 %.
- **Color temperature** is converted to RGB by Home Assistant.
- **Transitions** are not supported.
- **Effects** run the lamp's built-in scenes. Setting a color, or the effect
  `off`, ends a scene.

### Scenes

The app shows twenty scenes with IDs `0x80`–`0x93`. Only these have been
matched to their app names so far:

| Effect | ID |
|---|---|
| Green Prairie | `0x81` |
| Ghost | `0x84` |
| Disco | `0x87` |
| Alarm | `0x88` |
| Savanah | `0x8B` |

The others are called `Scene N` by position (`Scene 1` is `0x80`, `Scene 20` is
`0x93`). The app's other names are Fantasy, Sunset, Forest, Sunrise,
Midsummer, Tropicaltwilight, Rubyglow, Aurora, Lake Placid, Neon, Sundowner,
Bluestar, Redrose, Rating and Autumn. If you match one up, please open an
issue or a pull request.

## Action: `mergbw.send_raw`

For working out the protocol. Sends bytes to a lamp as they are, and can return
what the lamp sends back on its notify characteristic. It does not change the
light entity's state.

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
testing.

- Advertised service UUID `00003519-0000-1000-8000-00805f9b34fb`, local name
  `Sunset lights`. MeRGBW LED strips share the service UUID but use a
  different protocol, so discovery also requires the name.
- GATT service `0000fff0-…`. Writes go to `0000fff3-…`, notifications come
  from `0000fff4-…`.
- The lamp accepts one connection at a time.

Frames:

```
0x55  command  0xFF  length  payload…  checksum
```

`length` is the length of the whole frame (payload + 5). The checksum is the
sum of all preceding bytes with carries folded back in
(`while s > 0xFF: s = (s >> 8) + (s & 0xFF)`), then inverted (`~s & 0xFF`).

| Command | Meaning | Payload | Example |
|---|---|---|---|
| `0x00` | status request | none | `5500ff05a5` |
| `0x01` | power | `01` on, `00` off | `5501ff0601a2` |
| `0x03` | color | R, G, B | `5503ff08ff8c28ea` |
| `0x05` | brightness | percent, 0–100 | `5505ff06643b` |
| `0x06` | scene | scene ID | `5506ff06811d` |

### Not verified yet

- Whether the lamp takes color and brightness while powered off. If it does,
  the repeat after power on can go (`RESEND_AFTER_POWER_ON` in `const.py`).
- Whether the one-byte brightness actually dims, and whether 1 % is visible.
  The strips use two bytes instead, big-endian, `(i + 5) * 10`.
- Whether the lamp checks the checksum at all.
- The full scene map.
- Whether the status request gets an answer. If it reports power, the entity
  no longer needs to assume its state.

`mergbw.send_raw` is meant for answering these.

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
