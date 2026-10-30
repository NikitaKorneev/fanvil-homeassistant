# Fanvil for Home Assistant

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/docs/faq/custom_repositories)
[![Validate](https://github.com/NikitaKorneev/fanvil-homeassistant/actions/workflows/validate.yml/badge.svg)](https://github.com/NikitaKorneev/fanvil-homeassistant/actions/workflows/validate.yml)

**English** | [Русский](README.ru.md)

Local Home Assistant integration for **Fanvil IP door phones**. Watch the camera, open the door,
see who is calling and react to panel events in automations — directly over the device's HTTP API,
without cloud services.

## Features

| | |
|---|---|
| 📹 **Camera** | Live video (RTSP, H.264): main and sub stream, snapshots |
| 🔔 **Doorbell** | The call button as a Home Assistant doorbell event |
| 🚪 **Door log** | Who opened the door and how (card, PIN, call, exit button, API) and denied attempts |
| 🔊 **Speaker** | Text-to-speech and audio files on the panel speaker |
| 🔓 **Door relays** | "Open" pulse button and a "hold open" switch for every relay; relay state updates instantly |
| 📞 **Calls** | Incoming, outgoing, answered, ended and missed calls as events, with the caller's number |
| ☎️ **SIP** | Registration status, line number and server for each SIP line |
| 🚪 **Inputs & panel events** | Door sensors / exit buttons, call button, tamper, motion, boot |
| ⚙️ **Maintenance** | Reboot button, firmware, IP address, uptime, diagnostics download |

- **Instant events.** The integration configures the panel's *Action URL* to report events to a
  Home Assistant webhook. Polling stays as a fallback.
- **Plays well with other systems.** Action URLs already set by another system (a CRM, a PBX, a
  building management system) are never overwritten; Action URL can be switched off completely.
- **Follows DHCP.** If the panel gets a new IP address, the integration picks it up.
- **Local only.** No cloud, no extra software on the panel.

## Supported devices

| Series | Models | Status |
|---|---|---|
| Door phones | **i60, i60K, i62** | ✅ Supported (tested: i60K and i62, firmware 2.12.51) |
| Door phones | i61, i63, i64, i66, i67, i68, i62-2 Wire, i64-2 Wire | 🧪 Should work (basic functions), not tested yet |
| Indoor stations | i501, i504, i506, i51W, i52W, i53W, H501 and others | 🔜 Planned |
| Intercoms | i10S, i16S, A10 series | 🔜 Planned |

Fanvil door phones, indoor stations and intercoms share the same HTTP API, so new models are
mostly a matter of testing. Have a device that is not on the list? Open an issue — reports from
real hardware help a lot.

Requires **Home Assistant 2026.10** or newer.

## Installation

### HACS (recommended)

1. HACS → ⋮ → **Custom repositories** → add `https://github.com/NikitaKorneev/fanvil-homeassistant`,
   type **Integration**.
2. Find **Fanvil** in HACS and download it.
3. Restart Home Assistant.

### Manual

Copy `custom_components/fanvil` to `/config/custom_components/fanvil` and restart Home Assistant.

## Configuration

**Settings → Devices & services → Add integration → Fanvil**

| Field | Value |
|---|---|
| Host | Panel IP address or host name, without `http://` |
| Port | 80 (or the HTTPS port) |
| Username / password | HTTP API credentials (default `admin` / `admin`) |
| Authentication | Basic (Digest is also available) |

The RTSP video uses the same credentials.

**Options** (the **Configure** button of the integration):

| Option | Default | Meaning |
|---|---|---|
| Configure Action URL on the panel | on | Write webhook URLs to the panel for instant events |
| Home Assistant address for the panel | automatic | Override if the panel cannot reach the detected address |
| Polling interval | 30 s | How often the state is read over the HTTP API |

## Entities

| Entity | Description |
|---|---|
| `camera` Camera main stream | `camera.<device>_main_stream`: RTSP main stream (e.g. 1280×720), snapshot via API |
| `camera` Camera sub stream | `camera.<device>_sub_stream`: lower resolution (e.g. 640×480, ~0.5 Mbit/s) for dashboards and mobile |
| `binary_sensor` Relay N active, Input N open | Real relay and input states |
| `event` Doorbell | `ring` when the call button is pressed (device class *doorbell*) |
| `event` Door | `opened` / `denied` from the door log (attributes: `method`, `user`, `relay`, `card`, `sip_number`, `reason`) |
| `event` Call | `incoming`, `outgoing`, `answered`, `ended`, `missed` (attributes: `remote`, `display_remote`, `line`, `duration`) |
| `event` Panel event | `key`, `input_N_triggered` / `input_N_reset`, `boot`, `tamper`, `motion` |
| `media_player` Speaker | Plays TTS and audio on the panel (`tts.speak`, `media_player.play_media`) |
| `button` Open relay N (pulse) | Opens the relay for the time set on the panel |
| `switch` Relay N (hold open) | On while held open by this switch; a pulse, card or call does not turn it on |
| `button` Restart | Reboots the panel (configuration) |
| `sensor` Last door opening | Diagnostics: time of the last door log entry; method, user and result as attributes |
| `sensor` Line N call state | Diagnostics: raw call state of each line (the *Call* event is the main entity) |
| `sensor` SIP N number, SIP N registration code; `binary_sensor` SIP N registered | Diagnostics: SIP |
| `sensor` Action URL | Diagnostics: `on` / `partial` (some events are used by another system) / `off` |
| `sensor` Firmware, IP address, Uptime | Diagnostics |

The camera names start with *Camera*, so on the device page both streams stay together at the top of
the list. Updating from 0.4.0 renames `camera.<device>` to `camera.<device>_main_stream` (unless you
have changed the entity ID yourself); update dashboards and automations that use the old ID.

Every event is also fired on the event bus as `fanvil_event` with `device_id`, `event` and the call details.

### Automation examples

Say something at the door:

```yaml
action: tts.speak
target:
  entity_id: tts.home_assistant_cloud
data:
  media_player_entity_id: media_player.fanvil_i60k_speaker
  message: "Please leave the parcel at the door"
```

Doorbell notification:

```yaml
alias: Doorbell — notify with a snapshot
triggers:
  - trigger: state
    entity_id: event.fanvil_i60k_doorbell
actions:
  - action: camera.snapshot
    target:
      entity_id: camera.fanvil_i60k_main_stream
    data:
      filename: /config/www/doorbell.jpg
  - action: notify.mobile_app_phone
    data:
      message: "Someone is at the door"
      data:
        image: /local/doorbell.jpg
```

## How events work

With Action URL enabled, the integration writes URLs like
`http://<home-assistant>:8123/api/webhook/<id>?event=incoming&remote=$remote…` into the panel's
`cti.*` settings, one per event. The panel then sends an HTTP GET for each event.

- Only **empty** keys or keys that already point to this integration are written.
  A URL set by another system is left untouched — that event then works through polling only,
  and the *Action URL* sensor shows `partial` with the list of such events.
- Switching Action URL off, or removing the integration, clears **only this integration's** URLs.
- The URLs are checked again every 10 minutes (provisioning or a factory reset may wipe them).
- The webhook accepts requests only from the local network and only from the panel's MAC address.

### Doorbell

A press of the call button reaches Home Assistant as the `key` Action URL event and, when the
button dials a number, as an `outgoing` call. The *Doorbell* entity turns both into one `ring`
(repeats within 15 s are ignored), so the button keeps calling the apartment and Home Assistant
gets a doorbell at the same time.

### Door log

The panel keeps a log of every opening and failed attempt. The integration reads it right after a
relay opens and once a minute (to catch denied attempts), and never changes it. PIN codes are never
passed on. Uploading the log to a server (`doorphone.RelayLog*`) is not touched: it is often used by
Fanvil's cloud management.

### Speaker

The panel downloads audio from Home Assistant by itself. Firmware 2.12.51 accepts WAV only, so
every clip (TTS, media, an URL) is converted with ffmpeg to 16 kHz mono WAV and served under a
random one-time address for 5 minutes. The panel must be able to reach the Home Assistant address
shown by the *Action URL* sensor (or set in the options).

## Known firmware behaviour

Handled by the integration, listed here for troubleshooting (i60K, firmware 2.12.51):

- The API returns JSON with `Content-Type: text/html`; without credentials it returns a login page with HTTP 200.
- Right after a response the panel rejects the next request for ~0.3 s, so requests are spaced by 0.5 s.
- After a relay command a read may return an HTML stub; reads are retried, commands are never retried.
- While booting, the panel briefly shows the login page; re-authentication is requested only if it persists.
- `cti.CallRemoteCanceledUrl` and `cti.RejectIncomingCallUrl` are not accepted by this firmware.

## Troubleshooting

- **Cannot add the device:** check that the HTTP API is enabled and the credentials are the API ones
  (*Intercom Settings → Features → Basic Settings*). Too many failed logins temporarily lock the API.
  The exact reason is written to the Home Assistant log.
- **No instant events:** check the *Action URL* sensor and its attributes; make sure the panel can
  reach the Home Assistant address shown there, or set it in the options.
- **Diagnostics:** device page → ⋮ → *Download diagnostics* (passwords, MAC and serial number are redacted).

## Roadmap

- **Call button for Home Assistant only:** an option to switch the panel's function key so a press
  only sends an HTTP request to Home Assistant (no SIP call) — a pure smart doorbell.
- **Guest codes:** temporary PINs and cards with time periods (`api/persons`, `api/period`).
- **Call control:** dial a number and hang up from Home Assistant.
- **Volume and audio:** speaker and microphone volume, RTSP audio, speaker/microphone self-test.
- **Server-side access decisions** (firmware 2.12.55+): Home Assistant decides whether a card or
  PIN opens the door.
- More devices: i61–i68, indoor stations, intercoms.

## Development

```bash
python3 -m venv .venv
.venv/bin/pip install aiohttp pytest pytest-asyncio ruff
.venv/bin/pytest -q
.venv/bin/ruff check . && .venv/bin/ruff format --check .
```

The API client (`custom_components/fanvil/api.py`) does not depend on Home Assistant and is tested
against a local HTTP server. `scripts/probe.py` is a read-only check of a real panel.

References: Fanvil HTTP API Manual V0.8.7.3, Fanvil Action URL and Active URI Specification V0.0.5.

## License

[Apache License 2.0](LICENSE)
