# Getting Started

This guide covers the first steps to get up and running with the `vi_api_client` library. It assumes you have a valid Viessmann Developer Account and `CLIENT_ID`.

## Installation

```bash
pip install vi_api_client
```

## First Steps

### 1. Simple Authentication & Device List

The easiest way to start is using the CLI to generate a token, or using the `ViCareClient` with an existing token.

```python
import asyncio
import os
from vi_api_client import ViCareClient
from vi_api_client.auth import OAuth

# Configuration
CLIENT_ID = os.getenv("VIESSMANN_CLIENT_ID")
TOKEN_FILE = "token.json"

async def main():
    # 1. Setup Authentication (auto-handles token refresh)
    auth = OAuth(
        client_id=CLIENT_ID,
        token_file=TOKEN_FILE
    )
    
    # 2. Initialize Client
    client = ViCareClient(auth)

    # 3. Discovery: Installation -> Gateway -> Device
    installations = await client.get_installations()
    if not installations:
        print("No installations found.")
        return
        
    inst_id = installations[0]["id"]
    print(f"Using Installation: {inst_id}")
    
    gateways = await client.get_gateways()
    if not gateways:
        print("No gateways found.")
        return
        
    gw_serial = gateways[0]["serial"]
    print(f"Using Gateway: {gw_serial}")
    
    devices = await client.get_devices(inst_id, gw_serial)
    if not devices:
        print("No devices found.")
        return
        
    # Pick the first device (usually id="0")
    device_info = devices[0]
    dev_id = device_info["id"]
    print(f"Using Device: {dev_id} ({device_info['modelId']})")
    
    # Continued below...
    await read_features(client, inst_id, gw_serial, dev_id)

if __name__ == "__main__":
    asyncio.run(main())
```

### 2. Reading Features

Once you have the `installation_id`, `gateway_serial`, and `device_id`, you can query features.

```python
async def read_features(client, inst_id, gw_serial, dev_id):
    # Fetch all features
    features = await client.get_features_models(inst_id, gw_serial, dev_id)
    
    print(f"Found {len(features)} features.")
    
    # Access a specific feature
    # Note: Features are typically named like 'heating.circuits.0.operating.modes.active'
    outside_temp = next((f for f in features if f.name == "heating.sensors.temperature.outside"), None)
    
    if outside_temp:
        # formatted_value provides a string with unit (e.g., "12.5 celsius")
        print(f"Outside Temperature: {outside_temp.formatted_value}")
        # .value gives the raw scalar (e.g., 12.5)
        print(f"Raw Value: {outside_temp.value}")
```

### 3. Executing Commands

To change settings (e.g., set heating mode), you execute commands on a feature.

```python
async def set_heating_mode(client, inst_id, gw_serial, dev_id):
    feature_name = "heating.circuits.0.operating.modes.active"
    
    # 1. Fetch the feature to inspect available commands
    feature_data = await client.get_feature(inst_id, gw_serial, dev_id, feature_name)
    from vi_api_client.models import Feature
    feature = Feature.from_api(feature_data)
    
    # 2. Check if a command exists and is executable
    cmd_name = "setMode"
    if cmd_name in feature.commands:
        cmd = feature.commands[cmd_name]
        if cmd.is_executable:
            print(f"Executing {cmd_name}...")
            
            # 3. Execute with parameters
            # Parameters must match the API definition (see list-commands CLI)
            result = await client.execute_command(
                feature, 
                cmd_name, 
                {"mode": "heating"}
            )
            print("Command executed successfully!")
        else:
            print(f"Command {cmd_name} is currently not executable (maybe wrong state?).")
    else:
        print(f"Command {cmd_name} not found on this feature.")
```

## Next Steps

- **[API Concepts](02_api_structure.md)**: understand the data-driven design.
- **[Authentication](03_auth_reference.md)**: setup tokens and sessions.
- **[Models Reference](04_models_reference.md)**: detailed documentation of `Feature`, `Device`, and `Command`.
- **[Client Reference](05_client_reference.md)**: methods on `ViCareClient`.
- **[CLI Reference](06_cli_reference.md)**: terminal usage.
