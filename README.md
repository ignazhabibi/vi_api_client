# Viessmann API Python Client

A Python library for accessing the [Viessmann Climate Solutions API](https://developer.viessmann.com/).  
Designed for integration with Home Assistant and other async Python applications.

## Features

- **Asynchronous**: Built on `aiohttp` and `asyncio`.
- **OAuth2 Authentication**: Handles token retrieval and automatic renewal.
- **Auto-Discovery**: Automatically finds installations, gateways, and devices.
- **Recursive Feature Flattening**: Converts complex nested JSON into a simple list of sensors (e.g., `heating.circuits.0.heating.curve.shift`).
- **Command Execution**: Supports writing values and executing commands on devices (e.g. `setCurve`).
- **Analytics API**: Fetch historical energy consumption data (gas/electricity).
- **Mock Client**: Includes a robust `MockViClient` for offline development and testing.

## Installation

This is currently a local development package.

```bash
# Clone the repository
git clone <repository-url>
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
```

See [CLI Reference](docs/05_cli_reference.md) for more details.

### Python Code

```python
import asyncio
import aiohttp
from vi_api_client import OAuth, ViClient
from vi_api_client.utils import format_feature

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

        # Get installations
        installations = await client.get_installations()
        print(f"Found {len(installations)} installations")
        
        # Access nested structures flattened
        devices = await client.get_full_installation_status(installations[0].id)
        device = devices[0]
        
        for feature in device.features_flat:
             print(f"{feature.name}: {format_feature(feature)}")
             
if __name__ == "__main__":
    asyncio.run(main())
```

## Demo Applications

- `demo_mock.py`: Run offline without credentials using the Mock Client.
- `demo_live.py`: Connect to the real API.

## Documentation

The detailed documentation is available in the `docs/` directory:

1.  **[Getting Started](docs/01_getting_started.md)**: Installation and First Steps.
2.  **[API Structure & Concepts](docs/02_api_structure.md)**: Understanding the data-driven design.
3.  **[Authentication & Connection](docs/03_auth_reference.md)**: Tokens, Sessions, and Thread-Safety.
4.  **[Models Reference](docs/04_models_reference.md)**: Devices, Features, and Commands.
5.  **[Client Reference](docs/05_client_reference.md)**: The `ViClient` class.
6.  **[CLI Reference](docs/06_cli_reference.md)**: Using the command line interface.
7.  **[Exceptions Reference](docs/07_exceptions_reference.md)**: Handling errors.
