# Getting Started

This guide covers the first steps to get up and running with the `vi_api_client` library. It assumes you have a valid Viessmann Developer Account and `CLIENT_ID`.

## Installation

This is currently a local development package.

```bash
# Clone the repository
git clone https://github.com/ignazhabibi/vi_api_client.git
cd vi_api_client

# Create virtual env
python3.14 -m venv .venv
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

from vi_api_client import OAuth, ViClient

# Configuration
CLIENT_ID = os.environ["VIESSMANN_CLIENT_ID"]
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

        installation = installations[0]
        print(f"Using Installation: {installation.id}")

        gateways = await client.get_gateways()
        gateway = next(
            (
                gateway
                for gateway in gateways
                if gateway.installation_id == installation.id
            ),
            None,
        )
        if gateway is None:
            print("No gateway found for the selected installation.")
            return

        print(f"Using Gateway: {gateway.serial}")

        # Pick the heating device (usually id="0")
        # include_features=True ensures we get the full data immediately
        devices = await client.get_devices(
            installation.id,
            gateway.serial,
            include_features=True,
        )

        if not devices:
            print("No devices found.")
            return

        device = next((d for d in devices if d.id == "0"), devices[0])
        print(f"Using Device: {device.id} ({device.model_id})")
        print(f"Features: {len(device.features)}")

        # A) Iterate over all features
        for feature in device.features:
            print(f"- {feature.name}: {feature.value}")

        # B) Access a specific feature directly (O(1))
        outside_temp = device.get_feature("heating.sensors.temperature.outside")
        if outside_temp:
            print(f"Outside Temp: {outside_temp.value}")


if __name__ == "__main__":
    asyncio.run(main())
```
### 2. Executing Commands

To change settings (e.g., set heating mode), you use the high-level `set_feature` method.

```python
async def set_heating_mode(client, device):
    feature_name = "heating.circuits.0.operating.modes.active"

    # 1. Fetch the feature
    # We use get_features with feature_names list for efficiency
    features = await client.get_features(device, feature_names=[feature_name])
    if not features:
        print("Feature not found")
        return

    feature = features[0]

    # 2. Check if writable
    if feature.is_writable:
        print(f"Setting mode to 'heating'...")

        # 3. Execute High-Level Set
        # This handles validations, parameter resolving, and API calls automatically
        # Returns tuple: (CommandResponse, Device)
        response, device = await client.set_feature(device, feature, "heating")

        if response.success:
            print("Command executed successfully!")
            # Use the updated device for subsequent calls
        else:
            print(f"Command failed: {response.reason}")
    else:
        print(f"Feature '{feature.name}' is read-only.")
```

## Next Steps

- **[API Concepts](02_api_structure.md)**: understand the data-driven design.
- **[Authentication](03_auth_reference.md)**: setup tokens and sessions.
- **[Models Reference](04_models_reference.md)**: detailed documentation of `Feature`, `FeatureControl`, `Device`, and command results.
- **[Client Reference](05_client_reference.md)**: methods on `ViClient`.
- **[CLI Reference](06_cli_reference.md)**: terminal usage.
- **[Exceptions Reference](07_exceptions_reference.md)**: error handling.
