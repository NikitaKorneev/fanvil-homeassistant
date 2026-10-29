# Changelog

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
