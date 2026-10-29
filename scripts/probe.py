"""Read-only probe: never calls relay/reboot/configuration endpoints."""

import argparse
import asyncio
import getpass
import importlib
import json
import socket
import sys
import types
from pathlib import Path

import aiohttp


def load_api():
    """Load the standalone API client without importing Home Assistant."""
    package = types.ModuleType("fanvil_standalone")
    package.__path__ = [
        str(Path(__file__).resolve().parents[1] / "custom_components" / "fanvil")
    ]
    sys.modules[package.__name__] = package
    return importlib.import_module("fanvil_standalone.api")


async def main(args: argparse.Namespace, password: str) -> int:
    api = load_api()
    try:
        async with aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(family=socket.AF_INET)
        ) as session:
            client = api.FanvilClient(
                session,
                args.host,
                args.username,
                password,
                port=args.port,
                use_ssl=args.https,
                auth_type=args.auth,
            )
            info = await client.async_initialize()
            # Device identifiers are unnecessary in a shareable probe report.
            for key in ("info.dev.Mac", "info.dev.SN"):
                if key in info:
                    info[key] = "<redacted>"
            print(json.dumps({"info": info}, indent=2, ensure_ascii=False))
    except api.FanvilError as err:
        print(f"Probe failed: {err}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True)
    parser.add_argument("--username", required=True)
    parser.add_argument("--port", type=int, default=80)
    parser.add_argument("--https", action="store_true")
    parser.add_argument("--auth", choices=("basic", "digest"), default="basic")
    parser.add_argument("--password-stdin", action="store_true")
    arguments = parser.parse_args()
    secret = (
        sys.stdin.readline().rstrip("\r\n")
        if arguments.password_stdin
        else getpass.getpass("API password: ")
    )
    raise SystemExit(asyncio.run(main(arguments, secret)))
