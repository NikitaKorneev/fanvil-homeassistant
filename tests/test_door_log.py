"""Door log: parsing and following the end of the log."""

import importlib
from datetime import UTC, datetime

import aiohttp
from aiohttp import web
from test_api import panel_server

door_log = importlib.import_module("fanvil_standalone.door_log")


def row(position, **values):
    return {
        "id": str(position),
        "userName": "",
        "extra": "",
        "relay": 1,
        "openType": "13",
        "openResult": "0",
        "openTime": str(1792755000 + position),
        "reason": "0",
        "extraJson": "",
        "pictureData": "x" * 100,
        **values,
    }


def test_parse_entry_hides_pin_and_names_method():
    entry = door_log.parse_entry(
        row(
            3,
            openType="0",
            openResult="1",
            reason="1",
            userName="*",
            extra="P:1234",
            extraJson='{"localPwd":"1234"}',
        )
    )
    assert entry == {
        "id": "3",
        "result": "denied",
        "method": "pin",
        "relay": 1,
        "reason": "wrong_pin",
        "time": datetime.fromtimestamp(1792755003, UTC),
    }
    assert "1234" not in repr(entry)
    card = door_log.parse_entry(
        row(4, openType="2", extraJson='{"cardNumber":"22006"}', userName="Ivan")
    )
    assert card["method"] == "card" and card["card"] == "22006"
    assert card["user"] == "Ivan" and card["result"] == "opened"
    assert door_log.parse_entry(row(5, openType="17"))["method"] == "other_17"


async def test_tracker_locates_end_then_reports_only_new_entries(api_module):
    log = [row(position) for position in range(1, 38)]
    requests = []

    async def handler(request):
        index = (await request.json())["deviceLog"]["index"]
        requests.append(index)
        first, _, last = index.partition("-")
        first, last = int(first), int(last or first)
        return web.json_response(
            {"code": "200", "data": log[first - 1 : last], "total": 0},
            content_type="text/html",
        )

    seen = []
    async with panel_server(handler) as port, aiohttp.ClientSession() as session:
        client = api_module.FanvilClient(session, "127.0.0.1", "u", "p", port=port)
        tracker = door_log.DoorLogTracker(client, seen.append)
        await tracker.async_check()
        assert seen == [None]
        assert tracker.last_entry["id"] == "37"
        assert "pictureData" not in repr(tracker.last_entry)

        log.extend(row(position, openType="2") for position in range(38, 63))
        await tracker.async_check()
        assert [entry["id"] for entry in seen[1:]] == [str(p) for p in range(38, 63)]
        assert tracker.last_entry["id"] == "62"

        # Nothing new: one request, no events.
        requests.clear()
        await tracker.async_check()
        assert requests == ["62-82"] and len(seen) == 26

        # The log was cleared: locate the end again without reporting old entries.
        log[:] = [row(position) for position in range(1, 4)]
        await tracker.async_check()
        assert len(seen) == 27 and seen[-1] is None
        assert tracker.last_entry["id"] == "3"


async def test_empty_log_then_first_entry(api_module):
    log = []

    async def handler(request):
        index = (await request.json())["deviceLog"]["index"]
        first, _, last = index.partition("-")
        first, last = int(first), int(last or first)
        return web.json_response({"code": "200", "data": log[first - 1 : last]})

    seen = []
    async with panel_server(handler) as port, aiohttp.ClientSession() as session:
        client = api_module.FanvilClient(session, "127.0.0.1", "u", "p", port=port)
        tracker = door_log.DoorLogTracker(client, seen.append)
        await tracker.async_check()
        assert seen == [None] and tracker.last_entry is None
        log.append(row(1, openType="9"))
        await tracker.async_check()
        assert seen[-1]["method"] == "input"


async def test_audio_commands(api_module):
    bodies = []

    async def handler(request):
        bodies.append((request.match_info["endpoint"], await request.json()))
        return web.json_response({"type": {"cmd": "play"}, "code": "200"})

    async with panel_server(handler) as port, aiohttp.ClientSession() as session:
        client = api_module.FanvilClient(session, "127.0.0.1", "u", "p", port=port)
        await client.async_play_audio("http://ha:8123/api/fanvil/audio/t.wav")
        await client.async_stop_audio()
    assert bodies == [
        (
            "audio_file",
            {
                "type": {"cmd": "play"},
                "audioPlay": {
                    "path": "url",
                    "name": "http://ha:8123/api/fanvil/audio/t.wav",
                    "times": "1",
                },
            },
        ),
        ("audio_file", {"type": {"cmd": "stop"}, "audioPlay": {"path": "url"}}),
    ]
