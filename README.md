# Viessmann API Python Client

A Python library for accessing the [Viessmann Climate Solutions API](https://developer.viessmann-climatesolutions.com/start.html).
Designed for integration with Home Assistant and other async Python applications.

## Why this library

- **Your heating system as Python objects.** Installation, gateway, device,
  feature: typed, immutable classes that mirror the real world.
- **Flat features instead of nested JSON.** Every value is one feature with a
  name and a `value`, e.g. `heating.circuits.0.heating.curve.slope`, whatever
  device you have.
- **One call to change a setting.** `set_feature(device, name, value)` checks
  the value against the device's rules and fills in the other parameters the
  API requires.
- **Try everything offline.** `FixtureViClient` runs the same code against real
  device data, without an account.

The API returns the heating curve as one nested object whose `setCurve`
command needs both `slope` and `shift`. With this library:

```python
device = await client.set_feature(device, "heating.circuits.0.heating.curve.slope", 0.7)
# shift is filled in from its current value; 0.7 is checked against min 0.2 / max 3.5
```

The [API structure guide](https://github.com/ignazhabibi/vi_api_client/blob/main/docs/02_api_structure.md#3-the-flat-architecture)
shows the raw API response next to this call.

Under the hood: async (`aiohttp`), OAuth2 with automatic token renewal,
gateway-wide refresh of several devices, any reported command through
`run_command`, and a strictly typed, PEP 561 complete package. Feature values
follow a typed JSON value contract (`FeatureValue`), because the library reads
whatever features a device reports instead of hardcoding them.

## Installation

**Requires Python 3.14+.** Install the released package from PyPI:

```bash
python3.14 -m venv .venv
source .venv/bin/activate
pip install vi_api_client
```

To work on the library itself, install it from a clone in editable mode:

```bash
git clone https://github.com/ignazhabibi/vi_api_client.git
cd vi_api_client
pip install -e .
```

## Quick Start

### Try it without an account

The library ships real device data, so you can run this right away.

**Step by step**, from installation to feature:

```python
import asyncio

from vi_api_client import FixtureViClient


async def main():
    client = FixtureViClient("Vitocal250A")

    installations = await client.get_installations()
    installation = installations[0]

    gateways = await client.get_gateways(installation.id)
    gateway = gateways[0]

    devices = await client.get_devices(installation.id, gateway.serial)
    device = devices[0]

    device = await client.refresh_device(device)

    for feature in device.features:
        print(feature.name, feature.value)

    device = await client.set_feature(device, "heating.dhw.temperature.main", 50)


asyncio.run(main())
```

Each step returns plain objects: installations, gateways, devices, and the
features of a device, each with a `name` and a `value`. `refresh_device` loads
the features, and `set_feature` changes one value and returns the updated
device.

**The shortcut** you will usually write: `get_full_installation_status` walks
the gateways and devices for you and returns every device of the installation
with its features already loaded.

```python
import asyncio

from vi_api_client import FixtureViClient


async def main():
    client = FixtureViClient("Vitocal250A")

    installations = await client.get_installations()
    installation = installations[0]

    devices = await client.get_full_installation_status(installation.id)
    device = devices[0]

    for feature in device.features:
        print(feature.name, feature.value)

    device = await client.set_feature(device, "heating.dhw.temperature.main", 50)


asyncio.run(main())
```

### With your Viessmann account

Log in once with the CLI; it saves your tokens to `tokens.json`:

```bash
vi-client login --client-id <YOUR_CLIENT_ID>
```

Then use `ViClient` instead of `FixtureViClient`. Everything else stays the same:

```python
import asyncio

import aiohttp

from vi_api_client import OAuth, ViClient


async def main():
    async with aiohttp.ClientSession() as session:
        auth = OAuth(
            client_id="YOUR_CLIENT_ID",
            redirect_uri="http://localhost:4200/",
            token_file="tokens.json",
            websession=session,
        )
        client = ViClient(auth)

        installations = await client.get_installations()
        installation = installations[0]

        devices = await client.get_full_installation_status(installation.id)
        device = devices[0]

        for feature in device.features:
            print(feature.name, feature.value)

        device = await client.set_feature(device, "heating.dhw.temperature.main", 50)


asyncio.run(main())
```

### CLI

```bash
vi-client list-devices
vi-client list-features --available --values
vi-client list-writable
vi-client --help
```

## Documentation

The detailed documentation is available in the `docs/` directory:

1.  **[Getting Started](https://github.com/ignazhabibi/vi_api_client/blob/main/docs/01_getting_started.md)**: Installation and First Steps.
2.  **[API Structure & Concepts](https://github.com/ignazhabibi/vi_api_client/blob/main/docs/02_api_structure.md)**: Understanding the Flat Architecture.
3.  **[Authentication & Connection](https://github.com/ignazhabibi/vi_api_client/blob/main/docs/03_auth_reference.md)**: Tokens, Sessions, and Thread-Safety.
4.  **[Models Reference](https://github.com/ignazhabibi/vi_api_client/blob/main/docs/04_models_reference.md)**: Devices, Features, and Constraints.
5.  **[Client Reference](https://github.com/ignazhabibi/vi_api_client/blob/main/docs/05_client_reference.md)**: The `ViClient` class methods.
6.  **[CLI Reference](https://github.com/ignazhabibi/vi_api_client/blob/main/docs/06_cli_reference.md)**: Using the command line interface.
7.  **[Exceptions Reference](https://github.com/ignazhabibi/vi_api_client/blob/main/docs/07_exceptions_reference.md)**: Handling errors.
8.  **[Architecture Guide](https://github.com/ignazhabibi/vi_api_client/blob/main/docs/08_architecture.md)** (for contributors): System structure,
    responsibilities, data flows, and diagrams explained for developers and
    non-specialists. Consumers can skip this page.

## Public API

Import consumer-facing symbols from `vi_api_client`:

```python
from vi_api_client import (
    AbstractAuth,
    DEFAULT_SCOPES,
    Device,
    ENDPOINT_AUTHORIZE,
    ENDPOINT_TOKEN,
    EventHistoryPage,
    EventHistoryWindow,
    Feature,
    FeatureConstraints,
    FeatureDeprecation,
    FeatureValue,
    Gateway,
    GatewayDeviceRefreshResult,
    Installation,
    InstallationEvent,
    JsonValue,
    FixtureViClient,
    OAuth,
    ScheduleConstraints,
    ValidationDetail,
    ViAuthError,
    ViClient,
    ViCommandRejectedError,
    ViConnectionError,
    ViDeviceCommunicationError,
    ViError,
    ViNotFoundError,
    ViRateLimitError,
    ViResponseError,
    ViServerInternalError,
    ViValidationError,
    format_feature,
    redact_device,
    redact_feature,
    redact_sensitive,
    validate_json_value,
)
```

`DEFAULT_SCOPES`, `ENDPOINT_AUTHORIZE`, and `ENDPOINT_TOKEN` support external
OAuth integrations. `JsonValue`, `FeatureValue`, `ValidationDetail`, and
`validate_json_value` form the public recursive JSON value contract that
dynamic feature values and API validation details follow. `redact_sensitive`,
`redact_feature`, and `redact_device` redact secrets, address data,
identifiers, and coordinates in log text, API documents, and model objects.
CLI helpers, transport details, persistence, and parsing remain available from
their dedicated modules.

## Development

See [CONTRIBUTING.md](https://github.com/ignazhabibi/vi_api_client/blob/main/CONTRIBUTING.md)
for the development setup and the local quality gate.
