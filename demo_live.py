"""Viessmann Library Demo Application (Live API).

Demonstrates authentication, discovery, and feature fetching.
"""

import asyncio
import contextlib
import logging
import os
import sys

import aiohttp

sys.path.insert(0, os.path.abspath("src"))

from vi_api_client import OAuth, ViClient
from vi_api_client.utils import format_feature

CLIENT_ID = os.getenv("VIESSMANN_CLIENT_ID", "YOUR_CLIENT_ID")
REDIRECT_URI = os.getenv("VIESSMANN_REDIRECT_URI", "http://localhost:4200/")
TOKEN_FILE = os.getenv("VIESSMANN_TOKEN_FILE", "tokens.json")

logging.basicConfig(format="%(levelname)s: %(message)s", level=logging.INFO)


def print_sample_features(features, limit=25):
    """Print a sample of features."""
    print(f"\n📋 Sample Features (first {limit}):")
    for feature in features[:limit]:
        val = format_feature(feature)
        if len(val) > 60:
            val = val[:57] + "..."
        marker = " ✏️" if feature.is_writable else ""
        print(f"      {feature.name:<55} : {val}{marker}")

    if len(features) > limit:
        print(f"   ... and {len(features) - limit} more.")


def print_writable_features(features, limit=10):
    """Print writable features with constraints."""
    writable = [f for f in features if f.is_writable]
    print(f"\n🛠  Writable Features ({len(writable)}):")

    for feature in writable[:limit]:
        ctrl = feature.control

        constraint_attrs = [
            ("min", ctrl.min),
            ("max", ctrl.max),
            ("step", ctrl.step),
            ("options", ctrl.options),
            ("pattern", ctrl.pattern),
            ("min_length", ctrl.min_length),
            ("max_length", ctrl.max_length),
        ]

        constraints = [
            f"{name}={value}" for name, value in constraint_attrs if value is not None
        ]

        constraint_str = f" ({', '.join(constraints)})" if constraints else ""
        print(f"   - {feature.name}")
        print(
            f"       Cmd: {ctrl.command_name}, Param: {ctrl.param_name}{constraint_str}"
        )

    if len(writable) > limit:
        print(f"   ... and {len(writable) - limit} more.")


async def discover_device(client):
    """Discover and return the first heating device."""
    gateways = await client.get_gateways()
    if not gateways:
        print("No gateways found.")
        return None

    gw = gateways[0]
    devices = await client.get_devices(gw.installation_id, gw.serial)
    if not devices:
        print("No devices found.")
        return None

    device = next((d for d in devices if d.id == "0"), devices[0])
    print(f"   Using Device: {device.id} ({device.model_id})")
    return device


async def main():
    """Run the live demo."""
    print("🚀 Viessmann Library Demo (Live)")
    print("=" * 40 + "\n")

    connector = aiohttp.TCPConnector(ssl=False)

    async with aiohttp.ClientSession(connector=connector) as session:
        auth = OAuth(CLIENT_ID, REDIRECT_URI, TOKEN_FILE, websession=session)

        try:
            await auth.async_get_access_token()
            print("✅ Authentication successful.\n")
        except Exception:
            print("⚠️  No valid tokens found.")
            print(f"Run: 'vi-client login --client-id {CLIENT_ID}'")
            return

        client = ViClient(auth)

        print("🔍 Discovering...")
        device = await discover_device(client)
        if not device:
            return

        print("\n📥 Fetching features...")
        features = await client.get_features(device, only_enabled=True)
        print(f"   Found {len(features)} enabled features.")

        print_sample_features(features)
        print_writable_features(features)

        print("\n✅ Demo complete!")


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())
