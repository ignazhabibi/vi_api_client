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

## Development and Tests

Install the development dependencies with the CI constraints to reproduce the
tested HTTP client and mock combination locally:

```bash
python -m pip install -c constraints-ci.txt '.[dev]'
pre-commit install
ruff check .
ruff format --check .
pyright --pythonpath python
pyright --project pyrightconfig.strict.json
python -m pytest -q
python scripts/check_coverage.py
python -m build
python scripts/verify_distributions.py
```

The shipped package is strictly typed and PEP 561 complete: the strict
Pyright pass covers `src/vi_api_client`, the built wheel and source
distribution ship `py.typed`, and the installed wheel passes
`pyright --verifytypes vi_api_client --ignoreexternal`. Tests remain under
standard type checking. The suite must keep at least 95% line
and branch coverage over `src/vi_api_client`; `scripts/check_coverage.py`
enforces both thresholds in the quality gate.

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

# 5. List the complete installation event history (last 7 days)
vi-client list-events --days 7
```

See [CLI Reference](https://github.com/ignazhabibi/vi_api_client/blob/main/docs/06_cli_reference.md) for more details.

### Python Code

The example reads tokens from `tokens.json`. Create that file once with
`vi-client login`, or log in from Python as shown in the
[Authentication reference](https://github.com/ignazhabibi/vi_api_client/blob/main/docs/03_auth_reference.md#first-login).

```python
import asyncio
import aiohttp
from vi_api_client import OAuth, ViClient, ViCommandRejectedError


async def main():
    async with aiohttp.ClientSession() as session:
        # Initialize Auth (tokens saved to tokens.json)
        auth = OAuth(
            client_id="YOUR_CLIENT_ID",
            redirect_uri="http://localhost:4200/",
            token_file="tokens.json",
            websession=session,
        )

        client = ViClient(auth)

        # 1. Get Installations & Gateways
        installations = await client.get_installations()
        if not installations:
            return
        installation = installations[0]

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
            return

        # 2. Discover and refresh devices behind one gateway
        devices = await client.get_devices(installation.id, gateway.serial)
        refresh = await client.refresh_gateway_devices(devices)
        for device_id, error in refresh.errors_by_device_id.items():
            print(f"Device {device_id} could not be refreshed: {error}")
        if not refresh.refreshed_devices:
            return
        device = refresh.refreshed_devices[0]
        print(f"Device: {device.model_id} ({device.status})")

        # 3. Iterate Features (Flat List)
        for feature in device.features:
            print(f"{feature.name}: {feature.value}")

        # 4. Write one Feature with automatic dependency resolution
        # Find a feature that can be set right now (e.g. heating curve slope)
        slope = next(
            (f for f in device.features if "curve.slope" in f.name and f.is_settable),
            None,
        )
        if slope:
            print("Setting slope to 1.4...")
            try:
                # Use the returned device for subsequent calls.
                device = await client.set_feature(device, slope, 1.4)
            except ViCommandRejectedError as error:
                print(f"The device rejected the write: {error}")
            else:
                print("✅ Success! Device updated.")

        # 5. Run any command an API feature reports
        holiday = "heating.operating.programs.holiday"
        if "schedule" in device.get_command_names(holiday):
            try:
                await client.run_command(
                    device,
                    holiday,
                    "schedule",
                    {"start": "2026-12-24", "end": "2026-12-31"},
                )
            except ViCommandRejectedError as error:
                print(f"The device rejected the command: {error}")


if __name__ == "__main__":
    asyncio.run(main())
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
8.  **[Architecture Guide](https://github.com/ignazhabibi/vi_api_client/blob/main/docs/08_architecture.md)**: System structure,
    responsibilities, data flows, and diagrams explained for developers and
    non-specialists.
