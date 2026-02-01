# Viessmann API Python Client

> [!WARNING]
> This project is in an early stage of development. It is currently under active development, and changes (including breaking changes) are possible at any time.

A Python library for accessing the [Viessmann Climate Solutions API](https://developer.viessmann-climatesolutions.com/start.html).
Designed for integration with Home Assistant and other async Python applications.

## Features

- **Asynchronous**: Built on `aiohttp` and `asyncio`.
- **OAuth2 Authentication**: Handles token retrieval and automatic renewal.
- **Auto-Discovery**: Automatically finds installations, gateways, and devices.
- **Recursive Feature Flattening**: Converts complex nested API responses into a simple, flat list of features (e.g., `heating.circuits.0.heating.curve.shift`).
- **Command Execution**: Supports writing values with automatic parameter resolution (e.g. `setCurve`).
- **Analytics API**: Fetch historical energy consumption data (gas/electricity).
- **Mock Client**: Includes a robust `MockViClient` for offline development and testing.

## Installation

This is currently a local development package. **Requires Python 3.12+**.

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

## Quick Start

### CLI

The `vi-client` tool is the easiest way to explore the API.

```bash
# 1. Login
vi-client login --client-id <YOUR_CLIENT_ID>

# 2. List Devices
vi-client list-devices

# 3. List only enabled Features with values (auto-detects first device)
vi-client list-features --enabled --values

# 4. List Writable Features (Settings)
vi-client list-writable
```

See [CLI Reference](docs/06_cli_reference.md) for more details.

### Python Code

```python
import asyncio
import aiohttp
from vi_api_client import OAuth, ViClient

async def main():
    async with aiohttp.ClientSession() as session:
        # Initialize Auth (tokens saved to tokens.json)
        auth = OAuth(
            client_id="YOUR_CLIENT_ID",
            redirect_uri="http://localhost:4200/",
            token_file="tokens.json",
            websession=session
        )

        client = ViClient(auth)

        # 1. Get Installations & Gateways
        installations = await client.get_installations()
        gateways = await client.get_gateways()

        # 2. Get Devices (using first gateway and installation) with Features
        devices = await client.get_devices(
            installations[0].id,
            gateways[0].serial,
            include_features=True
        )
        device = devices[0] # Usually the heating system (ID: 0)
        print(f"Device: {device.model_id} ({device.status})")

        # 3. Iterate Features (Flat List)
        for feature in device.features:
             print(f"{feature.name}: {feature.value}")

        # 4. Write a Feature
        # Find a writable feature (e.g. heating curve slope)
        slope = next(
            (f for f in device.features if "curve.slope" in f.name and f.is_writable),
            None
        )
        if slope:
            print(f"Setting slope to 1.4...")
            await client.set_feature(device, slope, 1.4)

if __name__ == "__main__":
    asyncio.run(main())
```

## Demo Applications

- `demo_simple.py`: Minimal example to get started.
- `demo_live.py`: Connect to the real API and explore features interactivity.

## Documentation

The detailed documentation is available in the `docs/` directory:

1.  **[Getting Started](docs/01_getting_started.md)**: Installation and First Steps.
2.  **[API Structure & Concepts](docs/02_api_structure.md)**: Understanding the Flat Architecture.
3.  **[Authentication & Connection](docs/03_auth_reference.md)**: Tokens, Sessions, and Thread-Safety.
4.  **[Models Reference](docs/04_models_reference.md)**: Devices, Features, and Controls.
5.  **[Client Reference](docs/05_client_reference.md)**: The `ViClient` class methods.
6.  **[CLI Reference](docs/06_cli_reference.md)**: Using the command line interface.
7.  **[Exceptions Reference](docs/07_exceptions_reference.md)**: Handling errors.
