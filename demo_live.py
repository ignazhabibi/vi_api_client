"""Viessmann Library Demo Application (IoT API).

Demonstrates authentication, discovery, and feature fetching.
"""

import asyncio
import contextlib
import logging
import os
import sys
from pathlib import Path

import aiohttp

sys.path.insert(0, str(Path("src").resolve()))


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
        value = format_feature(feature)
        if len(value) > 60:
            value = value[:57] + "..."
        marker = " ✏️" if feature.is_writable else ""
        print(f"      {feature.name:<55} : {value}{marker}")

    if len(features) > limit:
        print(f"   ... and {len(features) - limit} more.")


def print_writable_features(features, limit=10):
    """Print writable features with constraints."""
    writable = [feature for feature in features if feature.is_writable]
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

    gateway = gateways[0]
    devices = await client.get_devices(
        gateway.installation_id,
        gateway.serial,
        include_features=True,
        only_active_features=True,
    )
    if not devices:
        print("No devices found.")
        return None

    device = next((device for device in devices if device.id == "0"), devices[0])
    print(f"   Using Device: {device.id} ({device.model_id})")
    return device


async def main():
    """Run the live demo."""
    print("🚀 Viessmann Library Demo (IoT API)")
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

        print("\n📥 Features (pre-fetched)...")
        features = device.features
        print(f"   Found {len(features)} enabled features.")

        print_sample_features(features)
        print_writable_features(features)

        print("\n✅ Demo complete!")


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())
