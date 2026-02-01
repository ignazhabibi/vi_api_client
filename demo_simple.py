"""Simple Viessmann API Demo.

This script implements the examples from docs/01_getting_started.md.
It demonstrates the minimal code needed to read a temperature value.
"""

import asyncio
import contextlib
import os
import sys
from pathlib import Path

import aiohttp

# Ensure we can import the local package
sys.path.insert(0, str(Path("src").resolve()))

from vi_api_client import ViClient
from vi_api_client.auth import OAuth
from vi_api_client.utils import format_feature

# Configuration
# Best practice: Load from environment variable, fallback to example
CLIENT_ID = os.getenv("VIESSMANN_CLIENT_ID", "YOUR_CLIENT_ID")
REDIRECT_URI = os.getenv("VIESSMANN_REDIRECT_URI", "http://localhost:4200/")
TOKEN_FILE = os.getenv("VIESSMANN_TOKEN_FILE", "tokens.json")


async def main():
    """Run the simple demo."""
    connector = aiohttp.TCPConnector(ssl=False)  # Use insecure for demo

    async with aiohttp.ClientSession(connector=connector) as session:
        # 1. Setup Authentication
        auth = OAuth(
            client_id=CLIENT_ID,
            redirect_uri=REDIRECT_URI,
            token_file=TOKEN_FILE,
            websession=session,
        )

        try:
            await auth.async_get_access_token()
        except Exception:
            print("Please login first using: vi-client login")
            return

        # 2. Initialize Client
        client = ViClient(auth)

        # 3. Discovery
        gateways = await client.get_gateways()
        if not gateways:
            print("No gateways found.")
            return

        gateway = gateways[0]
        devices = await client.get_devices(
            gateway.installation_id, gateway.serial, include_features=True
        )

        # Prefer "0" (Heating System) over Gateway
        device = next((device for device in devices if device.id == "0"), devices[0])

        print(f"Connected to: {device.model_id} (ID: {device.id})")

        # 4. Read Specific Feature
        print("\nReading Outside Temperature...")
        # Direct access (hydrated device)
        feature = device.get_feature("heating.sensors.temperature.outside")

        if feature:
            print(f"Value: {format_feature(feature)}")
            print(f"Raw:   {feature.value}")
        else:
            print("Feature not found.")


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())
