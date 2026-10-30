# Changelog

## 0.4.2 — 2026-10-30

- *Volume* slider (0–9): sets the talk, ring and tone volume at once (talk volume never below 1).
- *Camera audio* switch (`ipc.RtspAudioEnable`): microphone sound in both camera streams; plays in
  the WebRTC live view (G.711).

## 0.4.1 — 2026-10-30

- Cameras are named *Camera main stream* and *Camera sub stream*, with entity IDs
  `camera.<device>_main_stream` and `camera.<device>_sub_stream`; they stay together at the top of the
  device page. The 0.4.0 main camera `camera.<device>` is renamed automatically unless its entity ID
  was changed by the user.
- The *Hold open* switch shows only the hold it set: a pulse, a card or a call no longer turns it on.
  The *Relay N active* binary sensor still shows the real relay state.
- *Last door opening* and *Line N call state* moved to diagnostics.

## 0.4.0 — 2026-10-30

- Camera sub stream (`stream.live1`) as a second camera entity.
- Doorbell event entity: the call button press (`key`) and the outgoing call it starts become one `ring`.
- Door log (`api/device_log`): `event` *Door* (`opened` / `denied` with method, user, relay, card,
  reason) and the *Last door opening* sensor. PIN codes are never exposed.
- Speaker `media_player`: TTS and audio on the panel (`api/audio_file`). Audio is converted to
  16 kHz mono WAV with ffmpeg, since firmware 2.12.51 ignores MP3 URLs.
- Fanvil i62 (two relays) tested.
- Requires the `ffmpeg` and `media_source` integrations (part of the default configuration).

## 0.3.0 — 2026-10-29

First public release.

- Any Fanvil device with the HTTP API can be added; tested on i60K (firmware 2.12.51).
  Relays, inputs and SIP lines are discovered from what the device reports.
- Camera (RTSP + snapshot), relays (pulse and hold open), SIP status and numbers, inputs, reboot.
- Instant events through Action URL: calls, relays, inputs, SIP registration, boot, IP change, call button,
  tamper, motion. URLs of other systems are never overwritten; Action URL can be switched off.
- `event` entities and the `fanvil_event` bus event for automations.
- Follows panel IP changes (Action URL and DHCP).
- Diagnostics download with secrets redacted.
