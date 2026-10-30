"""Constants for the Fanvil integration."""

DOMAIN = "fanvil"
CONF_AUTH_TYPE = "auth_type"
CONF_POLL_INTERVAL = "poll_interval"
CONF_ACTION_URL = "action_url"
CONF_HA_URL = "ha_url"
CONF_WEBHOOK_ID = "webhook_id"
DEFAULT_ACTION_URL = True
# Check the panel's Action URL keys every 10 minutes (provisioning may wipe them).
ACTION_URL_RESYNC = 600
EVENT_FANVIL = "fanvil_event"
SIGNAL_EVENT = "fanvil_event_{}"
SIGNAL_DOOR_LOG = "fanvil_door_log_{}"
# Door log: read after a relay opened, and check for failed attempts every minute.
DOOR_LOG_AFTER_RELAY = 2
DOOR_LOG_INTERVAL = 60
# Doorbell: the call button press arrives as "key" and as an outgoing call.
DOORBELL_DEBOUNCE = 15
# Speaker: audio converted for the panel is downloadable for 5 minutes.
AUDIO_TTL = 300
AUDIO_MAX_SECONDS = 300
DEFAULT_POLL_INTERVAL = 30
# A rebooting panel shows the login page for a while: 3 polls in a row before reauth.
AUTH_FAILURES_BEFORE_REAUTH = 3
# SIP account settings change rarely: read them every N polls.
SIP_CONFIG_EVERY = 10
# Firmware 2.12.51 sometimes answers a read with an HTML stub; reads are retried.
READ_ATTEMPTS = 3
READ_RETRY_DELAY = 1.0
# Minimal pause between two requests to the panel (it needs ~0.3 s).
REQUEST_GAP = 0.5
# Keep config writes small (one request ~2 KB).
CONFIG_WRITE_MAX = 2000
# Give the panel time to apply a relay command before reading the state.
COMMAND_REFRESH_DELAY = 1.5
# Re-read after a pulse, once the panel has closed the relay (5 s by default).
PULSE_REFRESH_DELAY = 8

MODEL = "info.dev.Model"
MAC = "info.dev.Mac"
SERIAL = "info.dev.SN"
FIRMWARE = "info.sys.FwVersion"
HARDWARE = "info.sys.HwVersion"
IP_ADDRESS = "info.net.IPAddress"
UPTIME = "info.sys.UpTime"

IDENTITY_FIELDS = (MODEL, MAC, SERIAL, FIRMWARE, HARDWARE, IP_ADDRESS, UPTIME)


def registration_key(line: int) -> str:
    """Return the SIP registration field."""
    return f"info.sip.line.{line}.registerCode"


def call_key(line: int) -> str:
    """Return the call field (line zero is a direct IP call)."""
    return f"info.sip.line.{line}.callStatus"


def relay_key(relay: int) -> str:
    """Keep the spelling used in the Fanvil manual and firmware."""
    return f"info.io.DoorLook.{relay}.Status"


def input_key(port: int) -> str:
    """Return the input field."""
    return f"info.io.input.{port}.Status"


# Models verified on real hardware (info.dev.Model, lower case). Any Fanvil
# device with the HTTP API is accepted; the others get an informational log line.
TESTED_MODELS = frozenset({"i60", "i60k", "i62"})

# A returned key is
# not enough to prove a physical port exists; verify the profile on real hardware.
STATUS_FIELDS = (
    *(registration_key(line) for line in (1, 2)),
    *(call_key(line) for line in (0, 1, 2)),
    *(relay_key(relay) for relay in (1, 2)),
    *(input_key(port) for port in (1, 2)),
)


def sip_number_key(line: int) -> str:
    """Return the configured SIP extension (api/config)."""
    return f"sip.line.{line}.PhoneNumber"


SIP_CONFIG_FIELDS = tuple(
    f"sip.line.{line}.{name}"
    for line in (1, 2)
    for name in ("PhoneNumber", "DisplayName", "RegAddr", "RegEnabled")
)

# Camera streams: rtsp .../stream.live0 and stream.live1.
STREAMS = ("main_stream", "sub_stream")
