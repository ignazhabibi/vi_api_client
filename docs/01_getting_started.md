# Getting Started

This guide covers the first steps to get up and running with the `vi_api_client` library. It assumes you have a valid Viessmann Developer Account and `CLIENT_ID`.

## Installation

This is currently a local development package.

```bash
# Clone the repository
git clone https://github.com/ignazhabibi/vi_api_client.git
cd vi_api_client

# Create virtual env
python3 -m venv .venv
source .venv/bin/activate

# Install in editable mode
pip install -e .
```

## First Steps

### 1. Simple Authentication & Device List

The easiest way to start is using the CLI to generate a token, or using the `ViClient` with an existing token.

```python
import asyncio
import os

import aiohttp

from vi_api_client import ViClient
from vi_api_client.auth import OAuth

# Configuration
CLIENT_ID = os.getenv("VIESSMANN_CLIENT_ID")
REDIRECT_URI = "http://localhost:4200/"
TOKEN_FILE = "tokens.json"

async def main():
    async with aiohttp.ClientSession() as session:
        # 1. Setup Authentication (auto-handles token refresh)
        auth = OAuth(
            client_id=CLIENT_ID,
            redirect_uri=REDIRECT_URI,
            token_file=TOKEN_FILE,
            websession=session,
        )

        # 2. Initialize Client
        client = ViClient(auth)

        # 3. Discovery: Installation -> Gateway -> Device
        installations = await client.get_installations()
        if not installations:
            print("No installations found.")
            return

        inst_id = installations[0].id
        print(f"Using Installation: {inst_id}")

        gateways = await client.get_gateways()
        if not gateways:
            print("No gateways found.")
            return

        gw_serial = gateways[0].serial
        print(f"Using Gateway: {gw_serial}")

        devices = await client.get_devices(inst_id, gw_serial)
        if not devices:
            print("No devices found.")
            return

        # Pick the first device (usually id="0")
        device = devices[0]
        print(f"Using Device: {device.id} ({device.model_id})")

        # Continued below...
        await read_features(client, device)

if __name__ == "__main__":
    asyncio.run(main())
```

### 2. Reading Features

Once you have a `Device` object, you can query its features.

```python
from vi_api_client.utils import format_feature

async def read_features(client, device):
    # Fetch all features for the device
    features = await client.get_features(device)

    print(f"Found {len(features)} features.")

    # Access a specific feature
    outside_temp = next((f for f in features if f.name == "heating.sensors.temperature.outside"), None)

    if outside_temp:
        # format_feature provides a string with unit (e.g., "12.5 celsius")
        print(f"Outside Temperature: {format_feature(outside_temp)}")
        # .value gives the raw scalar (e.g., 12.5)
        print(f"Raw Value: {outside_temp.value}")
```

### 3. Executing Commands

To change settings (e.g., set heating mode), you execute commands on a feature.

```python
async def set_heating_mode(client, device):
    feature_name = "heating.circuits.0.operating.modes.active"

    # 1. Fetch the feature
    feature = await client.get_feature(device, feature_name)

    # 2. Check if a command exists and is executable
    cmd_name = "setMode"
    if cmd_name in feature.commands:
        cmd = feature.commands[cmd_name]
        if cmd.is_executable:
            print(f"Executing {cmd_name}...")

            # 3. Execute with parameters
            result = await client.execute_command(
                feature,
                cmd_name,
                {"mode": "heating"}
            )
            if result.success:
                print("Command executed successfully!")
            else:
                print(f"Command failed: {result.reason}")
        else:
            print(f"Command {cmd_name} is currently not executable.")
    else:
        print(f"Command {cmd_name} not found on this feature.")
```

## Next Steps

- **[API Concepts](02_api_structure.md)**: understand the data-driven design.
- **[Authentication](03_auth_reference.md)**: setup tokens and sessions.
- **[Models Reference](04_models_reference.md)**: detailed documentation of `Feature`, `Device`, and `Command`.
- **[Client Reference](05_client_reference.md)**: methods on `ViClient`.
- **[CLI Reference](06_cli_reference.md)**: terminal usage.
