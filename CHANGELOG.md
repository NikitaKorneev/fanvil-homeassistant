# Changelog

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
