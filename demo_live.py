"""Viessmann Library Demo Application (Live API).

This script demonstrates how to use the 'vitoclient' library to:
1. Authenticate against the Viessmann API.
2. Discover installations and devices.
3. Fetch data using strongly-typed models.
"""

import asyncio
import contextlib
import logging
import os
import sys

import aiohttp

# Ensure we can import the local package
sys.path.insert(0, os.path.abspath("src"))

from vi_api_client import OAuth, ViClient
from vi_api_client.utils import format_feature

# Configuration
# Best practice: Load from environment variable, fallback to placeholder
CLIENT_ID = os.getenv("VIESSMANN_CLIENT_ID", "YOUR_CLIENT_ID")
REDIRECT_URI = os.getenv("VIESSMANN_REDIRECT_URI", "http://localhost:4200/")
TOKEN_FILE = os.getenv("VIESSMANN_TOKEN_FILE", "tokens.json")

# Configure formatted logging
logging.basicConfig(format="%(levelname)s: %(message)s", level=logging.INFO)


async def main():
    """Run the live demo."""
    print("🚀 Viessmann Library Demo (Live)")
    print("==============================\n")

    # 1. Setup Authentication
    # -----------------------
    # Use the --insecure flag context for corporate/proxy environments if needed
    # For this demo, we'll try to detect if we need it (simple check)
    # or just default to standard.
    connector = aiohttp.TCPConnector(ssl=False)

    async with aiohttp.ClientSession(connector=connector) as session:
        auth = OAuth(
            client_id=CLIENT_ID,
            redirect_uri=REDIRECT_URI,
            token_file=TOKEN_FILE,
            websession=session,
        )

        # 2. Check Authentication
        # -----------------------
        try:
            # Attempt to get a token. If this fails, we need to login.
            await auth.async_get_access_token()
            print("✅ Authentication successful (cached tokens found).")
        except Exception:
            print("⚠️  No valid tokens found.")
            print(
                "Please use the CLI tool to login first: "
                f"'vi-client login --client-id {CLIENT_ID}'"
            )
            return

        # 3. Initialize Client
        # --------------------
        client = ViClient(auth)

        # 4. Discover Installations
        # -------------------------
        print("\n🔍 Discovering Installations...")
        installations = await client.get_installations()

        if not installations:
            print("No installations found.")
            return

        inst_id = installations[0].id
        print(f"   Found Installation ID: {inst_id}")

        # 5. Fetch Full System Status
        # ---------------------------
        # We use the helper method designed for Home Assistant (Coordinator Pattern).
        # It fetches Gateways -> Devices -> Features in one optimized flow.
        print("\n📥 Fetching full system status (this may take a moment)...")

        try:
            devices = await client.get_full_installation_status(inst_id)
            print(f"   Success! Received data for {len(devices)} devices.")

            # 6. Display Data
            # ---------------
            for device in devices:
                print(f"\n📱 Device: {device.device_type} (Model: {device.model_id})")
                print(f"   ID: {device.id} | Status: {device.status}")
                print(f"   Available Features: {len(device.features)}")

                # Show only enabled features with values
                enabled_features = [f for f in device.features if f.is_enabled]
                print(f"   Enabled Features ({len(enabled_features)}):")

                # Print first 10 as sample
                for feature in enabled_features[:10]:
                    # Use format_feature for display

                    val = format_feature(feature)
                    if len(val) > 80:
                        val = val[:77] + "..."
                    print(f"     - {feature.name:<75} : {val}")

                if len(enabled_features) > 10:
                    print(f"     ... and {len(enabled_features) - 10} more.")

                # Show commands
                commandable_features = [f for f in device.features if f.commands]
                if commandable_features:
                    print(
                        f"\n   🛠  Available Commands "
                        f"({len(commandable_features)} features):"
                    )
                    for feature in commandable_features:
                        # Show command name and execution status
                        cmd_list = []
                        for cmd_name, cmd in feature.commands.items():
                            is_exec = "✅" if cmd.is_executable else "❌"
                            cmd_list.append(f"{is_exec} {cmd_name}")

                        print(f"     - {feature.name:<75} : {', '.join(cmd_list)}")

        except Exception as e:
            print(f"❌ Error fetching data: {e}")


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())
