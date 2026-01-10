# Viessmann API Python Client

A Python library for accessing the [Viessmann Climate Solutions API](https://developer.viessmann.com/).  
Designed for integration with Home Assistant and other async Python applications.

## Features

- **Asynchronous**: Built on `aiohttp` and `asyncio`.
- **OAuth2 Authentication**: Handles PKCE flow, token retrieval, and auto-refresh.
- **Type Hinted**: Fully typed for better development experience.
- **CLI Tool**: Included for quick testing and API exploration.

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

## Demo Application

We provide a specialized demo script `demo.py` that serves as a reference implementation. It demonstrates:
- Authentication flow
- Device discovery
- Fetching full installation status (Gateways -> Devices -> Features)
- Working with typed Data Models (`Device` and `Feature`)

**Usage:**

```bash
# Export your Client ID (so you don't have to edit the code)
export VIESSMANN_CLIENT_ID=<YOUR_CLIENT_ID>

# Run the demo
python demo.py
```

## Library Usage

### Basic Example

Here is how to use the library in your own async code:

```python
import asyncio
import aiohttp
from vi_api_client import OAuth, Client

CLIENT_ID = "YOUR_CLIENT_ID"
REDIRECT_URI = "http://localhost:4200/"
TOKEN_FILE = "tokens.json"

async def main():
    # 1. Create an aiohttp session
    async with aiohttp.ClientSession() as session:
        # 2. Initialize Auth handler
        # It automatically loads/saves tokens to the specified file
        auth = OAuth(
            client_id=CLIENT_ID,
            redirect_uri=REDIRECT_URI,
            token_file=TOKEN_FILE,
            websession=session
        )

        # 3. Initialize Client
        client = Client(auth)

        try:
            # 4. Fetch Data
            devices = await client.get_installations()
            print(f"Found {len(devices)} installations")
            
            # See CLI implementation for more complex examples (discovery, features)
            
        except Exception as e:
            print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(main())
```

### Simplified Feature Access

You can easily get a list of features with their values pre-formatted:

```python
# Get enabled features with values (returns dicts with 'name' and 'value')
features = await client.get_features_with_values(
    installation_id, gateway_serial, device_id, only_enabled=True
)

for f in features:
    print(f"{f['name']}: {f['value']}")
# Output: heating.sensors.temperature.outside: 12.5 celsius
```

### Feature Flattening & Expansion

The library automatically handles complex API features (heating curves, statistics, summaries) by "flattening" them into simple, scalar features. This is ideal for integrations like Home Assistant.

Instead of iterating `device.features`, use `device.features_flat`:

```python
devices = await client.get_full_installation_status(inst_id)
device = devices[0]

for feature in device.features_flat:
    # Now you get scalar values for everything:
    # - heating.curve (Complex) -> heating.curve.slope: 0.8
    #                              heating.curve.shift: 3
    #
    # - statistics (Complex)    -> statistics.starts: 350
    #                              statistics.hours: 50
    
    print(f"{feature.name}: {feature.value}")
```

### Custom Authentication
For integrations like Home Assistant, you can implement the `AbstractAuth` class to handle tokens your own way, rather than using `OAuth`.

## CLI Tool Usage

The package includes a `vi_api_client` command line tool for testing authentication and exploring the API.

> **Note**: Tokens are saved to `tokens.json` in your current directory. Do not commit this file!

### 1. Login
Initiate the OAuth2 flow. You need your Client ID from the Viessmann Developer Portal.

```bash
vi_api_client login --client-id <YOUR_CLIENT_ID>
# Or use environment variable:
# export VIESSMANN_CLIENT_ID=<YOUR_CLIENT_ID>
# vi_api_client login
```
Follow the URL, log in, and paste the code back into the terminal.

### 2. List Devices
View all installations, gateways, and devices available to your account.

```bash
vi_api_client list-devices
```

### 3. List Features
Feature names vary by device model (e.g., heat pumps vs gas boilers). Use this to see exactly what is available.

```bash
# List all features (names only)
vi_api_client list-features

# List only enabled features (names only)
vi_api_client list-features --enabled

# List enabled features WITH values
vi_api_client list-features --enabled --values
```
*Note: This auto-detects the first device. You can specify `--gateway-serial` and `--device-id` if needed.*

### 4. Fetch Data
Get the current value of a specific feature.

```bash
vi_api_client get-feature "heating.sensors.temperature.outside"
```

### Corporate Proxy / SSL Issues
If you are testing from a corporate network that intercepts SSL (e.g., Zscaler), you may encounter certificate errors. Use the `--insecure` flag to bypass verification:

```bash
vi_api_client list-devices --insecure
vi_api_client get-feature "heating.circuits.0" --insecure
```
