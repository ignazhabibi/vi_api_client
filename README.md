# Viessmann API Python Client

A Python library for accessing the [Viessmann Climate Solutions API](https://developer.viessmann-climatesolutions.com/start.html).
Designed for integration with Home Assistant and other async Python applications.

## Features

- **Asynchronous**: Built on `aiohttp` and `asyncio`.
- **OAuth2 Authentication**: Handles token retrieval and automatic renewal.
- **Auto-Discovery**: Automatically finds installations, gateways, and devices.
- **Recursive Feature Flattening**: Converts complex nested API responses into a simple, flat list of features (e.g., `heating.circuits.0.heating.curve.shift`).
- **Gateway-Scoped Refresh**: Refreshes multiple known devices with one normal-case request while preserving partial successes.
- **Command Execution**: Supports safe single-feature writes with automatic
  parameter resolution and explicit multi-parameter commands.
- **Fixture-backed Client**: Runs the same client workflows against bundled real-device
  responses without authentication, sessions, or network requests.
- **Strictly Typed**: Ships as a PEP 561 complete package with `py.typed`; the
  shipped code passes strict Pyright and dynamic values follow a typed JSON
  value contract.

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

The library ships real device data, so you can run this right away:

```python
import asyncio

from vi_api_client import FixtureViClient


async def main():
    client = FixtureViClient("Vitocal250A")

    installation = (await client.get_installations())[0]
    device = (await client.get_full_installation_status(installation.id))[0]

    outside = device.get_feature("heating.sensors.temperature.outside")
    if outside:
        print(f"Outside temperature: {outside.value} {outside.unit}")

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

        installation = (await client.get_installations())[0]
        device = (await client.get_full_installation_status(installation.id))[0]

        outside = device.get_feature("heating.sensors.temperature.outside")
        if outside:
            print(f"Outside temperature: {outside.value} {outside.unit}")

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
