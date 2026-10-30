"""Real HTTP tests for the authentication and command boundary."""

import hashlib
import re
from contextlib import asynccontextmanager

import aiohttp
import pytest
from aiohttp import web


@asynccontextmanager
async def panel_server(handler):
    app = web.Application()
    app.router.add_post("/api/{endpoint}", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = runner.addresses[0][1]
    try:
        yield port
    finally:
        await runner.cleanup()


def identity():
    return {
        "info.dev.Model": "i60k",
        "info.dev.Mac": "02:11:22:33:44:55",
        "info.dev.SN": "TEST-ONLY-0001",
    }


async def test_basic_json_with_wrong_content_type_and_empty_call(api_module):
    """The actual firmware labels JSON as HTML and may return empty call state."""
    seen = []

    async def handler(request):
        seen.append(request.headers.get("Authorization"))
        if seen[-1] != "Basic dGVzdC11c2VyOnRlc3QtcGFzcw==":
            return web.Response(text="<html><title>Login</title></html>")
        fields = (await request.json())["info"]
        values = {**identity(), "info.sip.line.1.callStatus": ""}
        return web.json_response(
            {"code": "200", "info": {k: values[k] for k in fields if k in values}},
            content_type="text/html",
        )

    async with panel_server(handler) as port, aiohttp.ClientSession() as session:
        client = api_module.FanvilClient(
            session, "127.0.0.1", "test-user", "test-pass", port=port
        )
        data = await client.async_initialize()
        assert data["info.sip.line.1.callStatus"] == ""
        assert "info.io.DoorLook.2.Status" not in client.supported_fields
        assert api_module.device_unique_id(data) == "mac_021122334455"
        assert seen and all(value == seen[0] for value in seen)


@pytest.mark.parametrize(
    ("body", "status", "error"),
    [
        ("<html><title>Login</title></html>", 200, "FanvilAuthError"),
        ('{"code":"401"}', 200, "FanvilAuthError"),
        ('{"code":"403"}', 200, "FanvilAuthError"),
        ("{}", 401, "FanvilAuthError"),
        ('{"code":"503"}', 200, "FanvilApiError"),
        ('{"code":"200","info":[]}', 200, "FanvilProtocolError"),
        ("[]", 200, "FanvilProtocolError"),
        ("unexpected response", 200, "FanvilProtocolError"),
    ],
)
async def test_api_failure_modes(api_module, body, status, error):
    async def handler(request):
        return web.Response(text=body, status=status)

    async with panel_server(handler) as port, aiohttp.ClientSession() as session:
        client = api_module.FanvilClient(session, "127.0.0.1", "u", "p", port=port)
        with pytest.raises(getattr(api_module, error)):
            await client.async_read(("info.dev.Model",))


async def test_optional_fields_fallback_does_not_invent_ports(api_module):
    values = {**identity(), "info.io.DoorLook.1.Status": "close"}

    async def handler(request):
        fields = (await request.json())["info"]
        if any(key not in values for key in fields):
            return web.json_response({"code": "404"})
        return web.json_response(
            {"code": "200", "info": {k: values[k] for k in fields}}
        )

    async with panel_server(handler) as port, aiohttp.ClientSession() as session:
        client = api_module.FanvilClient(session, "127.0.0.1", "u", "p", port=port)
        data = await client.async_initialize()
        assert data["info.io.DoorLook.1.Status"] == "close"
        assert "info.io.DoorLook.2.Status" not in client.supported_fields
        assert await client.async_update() == data


async def test_dhcp_address_reuse_is_rejected(api_module):
    values = identity()

    async def handler(request):
        fields = (await request.json())["info"]
        return web.json_response(
            {"code": "200", "info": {k: values[k] for k in fields if k in values}}
        )

    async with panel_server(handler) as port, aiohttp.ClientSession() as session:
        client = api_module.FanvilClient(session, "127.0.0.1", "u", "p", port=port)
        await client.async_initialize()
        values["info.dev.Mac"] = "02:aa:bb:cc:dd:ee"
        with pytest.raises(api_module.FanvilProtocolError):
            await client.async_update()


async def test_any_model_accepted_but_model_required(api_module):
    """Untested models (i62 with two relays, ...) work; a missing model does not."""
    model = {"value": "i62"}

    async def handler(request):
        fields = (await request.json())["info"]
        values = {**identity(), "info.dev.Model": model["value"]}
        values["info.io.DoorLook.2.Status"] = "close"
        return web.json_response(
            {"code": "200", "info": {k: values[k] for k in fields if k in values}}
        )

    async with panel_server(handler) as port, aiohttp.ClientSession() as session:
        client = api_module.FanvilClient(session, "127.0.0.1", "u", "p", port=port)
        data = await client.async_initialize()
        assert data["info.dev.Model"] == "i62"
        assert data["info.io.DoorLook.2.Status"] == "close"
        model["value"] = ""
        client2 = api_module.FanvilClient(session, "127.0.0.1", "u", "p", port=port)
        with pytest.raises(api_module.FanvilUnsupportedModel):
            await client2.async_initialize()


async def test_only_explicit_calls_send_control_commands(api_module):
    commands = []

    async def handler(request):
        payload = await request.json()
        if request.match_info["endpoint"] == "device":
            commands.append(payload)
            return web.json_response({"code": "200"})
        fields = payload["info"]
        values = identity()
        return web.json_response(
            {"code": "200", "info": {k: values[k] for k in fields if k in values}}
        )

    async with panel_server(handler) as port, aiohttp.ClientSession() as session:
        client = api_module.FanvilClient(session, "127.0.0.1", "u", "p", port=port)
        await client.async_initialize()
        await client.async_update()
        assert commands == []
        await client.async_pulse_relay(2)
        assert commands == [
            {
                "type": {"cmd": "relayCtrl"},
                "dooropen": {"relay": "2", "action": "on", "mode": "once"},
            }
        ]
        with pytest.raises(ValueError):
            await client.async_pulse_relay(3)
        assert len(commands) == 1
        await client.async_reboot()
        assert commands[-1] == {"type": {"cmd": "reboot"}}


async def test_commands_not_retried_after_connection_drop(api_module):
    count = 0

    async def handler(request):
        nonlocal count
        count += 1
        request.transport.close()
        return web.Response()

    async with panel_server(handler) as port, aiohttp.ClientSession() as session:
        client = api_module.FanvilClient(session, "127.0.0.1", "u", "p", port=port)
        with pytest.raises(api_module.FanvilConnectionError):
            await client.async_pulse_relay(1)
        assert count == 1


async def test_credentials_not_forwarded_on_redirect(api_module):
    async def handler(request):
        raise web.HTTPFound("http://127.0.0.1:1/do-not-follow")

    async with panel_server(handler) as port, aiohttp.ClientSession() as session:
        client = api_module.FanvilClient(session, "127.0.0.1", "u", "p", port=port)
        with pytest.raises(api_module.FanvilAuthError):
            await client.async_pulse_relay(1)


async def test_digest_auth_validates_challenge_response(api_module):
    requests = []
    realm, nonce = "Fanvil-test", "test-nonce"

    def md5(text):
        return hashlib.md5(text.encode()).hexdigest()

    async def handler(request):
        authorization = request.headers.get("Authorization", "")
        requests.append(authorization)
        if not authorization.startswith("Digest "):
            return web.Response(
                status=401,
                headers={
                    "WWW-Authenticate": (
                        f'Digest realm="{realm}", nonce="{nonce}", '
                        'algorithm=MD5, qop="auth"'
                    )
                },
            )
        fields = dict(re.findall(r'(\w+)="([^"]*)"', authorization))
        nc = re.search(r"\bnc=([0-9a-f]+)", authorization)[1]
        ha1 = md5(f"u:{realm}:p")
        ha2 = md5(f"POST:{fields['uri']}")
        expected = md5(f"{ha1}:{nonce}:{nc}:{fields['cnonce']}:auth:{ha2}")
        assert fields["response"] == expected
        assert fields["username"] == "u"
        return web.json_response({"code": "200", "info": identity()})

    async with panel_server(handler) as port, aiohttp.ClientSession() as session:
        client = api_module.FanvilClient(
            session, "127.0.0.1", "u", "p", port=port, auth_type="digest"
        )
        assert (await client.async_read(identity()))["info.dev.Model"] == "i60k"
        assert requests[0] == ""
        assert len(requests) == 2


@pytest.mark.parametrize(
    "host", ["http://device", "u:p@device", "device/path", "", "foo bar"]
)
def test_reject_embedded_credentials_or_urls(api_module, host):
    with pytest.raises(ValueError):
        api_module.normalize_host(host)


def test_stable_identity_validation(api_module):
    assert api_module.device_unique_id({"info.dev.SN": "TEST-ONLY"}) == "sn_TEST-ONLY"
    with pytest.raises(api_module.FanvilProtocolError):
        api_module.device_unique_id({"info.dev.Mac": "00:00:00:00:00:00"})


STUB = (
    "<HTML><HEAD><TITLE>200 OK</TITLE></HEAD>"
    "<BODY><H1>200 OK Request Success</H1></BODY></HTML>"
)


async def test_read_retried_after_html_stub_but_command_is_not(api_module, monkeypatch):
    """i60K 2.12.51 sometimes answers with a stub right after a relay command."""
    monkeypatch.setattr(api_module, "READ_RETRY_DELAY", 0)
    calls = {"device": 0, "device_info": 0}

    async def handler(request):
        endpoint = request.match_info["endpoint"]
        calls[endpoint] += 1
        if calls[endpoint] == 1:
            return web.Response(text=STUB, content_type="text/html")
        fields = (await request.json())["info"]
        values = identity()
        return web.json_response(
            {"code": "200", "info": {k: values[k] for k in fields if k in values}}
        )

    async with panel_server(handler) as port, aiohttp.ClientSession() as session:
        client = api_module.FanvilClient(session, "127.0.0.1", "u", "p", port=port)
        data = await client.async_read(("info.dev.Model",))
        assert data == {"info.dev.Model": "i60k"}
        assert calls["device_info"] == 2
        with pytest.raises(api_module.FanvilBusyError):
            await client.async_relay(1, "on", "long")
        assert calls["device"] == 1


async def test_relay_hold_and_settings(api_module):
    seen = []

    async def handler(request):
        payload = await request.json()
        seen.append((request.match_info["endpoint"], payload))
        if request.match_info["endpoint"] == "config":
            return web.json_response(
                {
                    "code": "200",
                    "data": {
                        "sip.line.1.PhoneNumber": "201",
                        "sip.line.1.RegPswd": "must-not-leak",
                    },
                }
            )
        return web.json_response({"code": "200"})

    async with panel_server(handler) as port, aiohttp.ClientSession() as session:
        client = api_module.FanvilClient(session, "127.0.0.1", "u", "p", port=port)
        await client.async_relay(1, "on", "long")
        await client.async_relay(1, "off", "long")
        assert [p["dooropen"] for e, p in seen] == [
            {"relay": "1", "action": "on", "mode": "long"},
            {"relay": "1", "action": "off", "mode": "long"},
        ]
        with pytest.raises(ValueError):
            await client.async_relay(1, "toggle", "long")
        config = await client.async_read_settings()
        assert config == {"sip.line.1.PhoneNumber": "201"}
        requested = seen[-1][1]["config"]
        assert not any("Pswd" in key for key in requested)
        assert "phone.volume.HandFreeVol" in requested
        assert "ipc.RtspAudioEnable" in requested


def test_rtsp_url_escapes_credentials(api_module):
    client = api_module.FanvilClient(None, "10.0.0.5", "ad min", "p@ss:w/d")
    url = client.rtsp_url(1)
    assert url.startswith("rtsp://ad%20min:p%40ss%3Aw%2Fd@10.0.0.5/")
    assert url.endswith("/h264/stream.live1")


async def test_action_url_sync_never_touches_foreign_urls(api_module):
    import importlib

    action_url = importlib.import_module("fanvil_standalone.action_url")
    foreign = "http://crm.example/event?x=1"
    panel = {
        "cti.IncomingCallUrl": foreign,
        "cti.vimCti.Output1.Url": "",
        "cti.CallStopUrl": "http://old-ha:8123/api/webhook/hook1?event=ended",
        "cti.ActionUrlEnabled": "1",
    }
    writes = []

    async def handler(request):
        payload = await request.json()
        if payload["type"]["cmd"] == "get":
            return web.json_response(
                {
                    "code": "200",
                    "data": {k: panel.get(k, "") for k in payload["config"]},
                }
            )
        writes.append(payload["config"])
        rejected = {k: "" for k in payload["config"] if k == "cti.KeyTriggerUrl"}
        panel.update({k: v for k, v in payload["config"].items() if k not in rejected})
        return web.json_response({"code": "200", "data": rejected})

    async with panel_server(handler) as port, aiohttp.ClientSession() as session:
        client = api_module.FanvilClient(session, "127.0.0.1", "u", "p", port=port)
        events = ("incoming", "relay_1_open", "ended", "key")
        status = await action_url.async_sync(
            client, "http://10.0.0.2:8123", "hook1", True, events
        )
        assert status == {
            "incoming": "foreign",
            "relay_1_open": "ours",
            "ended": "ours",
            "key": "rejected",
        }
        assert panel["cti.IncomingCallUrl"] == foreign
        assert panel["cti.vimCti.Output1.Url"].startswith(
            "http://10.0.0.2:8123/api/webhook/hook1?event=relay_1_open&mac=$mac"
        )
        # Our URL with an old Home Assistant address is rewritten.
        assert panel["cti.CallStopUrl"].startswith("http://10.0.0.2:8123/")

        # Second run: nothing to write.
        writes.clear()
        await action_url.async_sync(
            client, "http://10.0.0.2:8123", "hook1", True, events[:3]
        )
        assert writes == []

        # Disabled: only our keys are cleared.
        await action_url.async_sync(client, "", "hook1", False, events)
        assert panel["cti.IncomingCallUrl"] == foreign
        assert panel["cti.vimCti.Output1.Url"] == ""
        assert panel["cti.CallStopUrl"] == ""


def test_action_url_request_parsing():
    import importlib

    action_url = importlib.import_module("fanvil_standalone.action_url")
    data = action_url.parse_request(
        {
            "event": "incoming",
            "mac": "0C:38:3E:63:FC:1F",
            "remote": "sip:100@pbx",
            "line": "0",
            "call_id": "-1",
            "display_remote": "$display_remote",
        }
    )
    assert data == {"event": "incoming", "mac": "0c383e63fc1f", "remote": "sip:100@pbx"}
    assert action_url.parse_request({"event": "rm -rf"}) is None
    assert action_url.valid_ipv4("172.17.0.80")
    assert not action_url.valid_ipv4("panel.local")
