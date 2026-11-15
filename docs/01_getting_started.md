# Getting Started

**Requires Python 3.14+.** Install the released package from PyPI with
`pip install vi_api_client`, ideally inside a virtual environment.

## 1. Try it without an account

`FixtureViClient` works like the real client, but uses device data bundled
with the library: no login, no network. Changes are simulated, nothing is sent.

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


asyncio.run(main())
```

The library models four things: an **installation** (your home), its
**gateway**, the **devices** behind it, and the **features** of each device.
Each step returns a plain list, and you pick the entry you need.
`get_devices` lists the devices without their features; `refresh_device` loads
them.

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


asyncio.run(main())
```

## 2. Read values

Every feature has a dot-separated name, a `value`, and an optional `unit`.
To read one feature, ask the device for it by name:

```python
outside = device.get_feature("heating.sensors.temperature.outside")
if outside:
    print(f"Outside temperature: {outside.value} {outside.unit}")
```

`get_feature` returns `None` if the device does not have that feature.

The feature list in step 1 contains only enabled and ready features
(`only_available=True`, the default). Pass `only_available=False` to
`get_full_installation_status` or `refresh_device` to also see disabled ones.

## 3. Change a setting

```python
device = await client.set_feature(device, "heating.dhw.temperature.main", 50)
```

`set_feature` takes the feature name directly, checks the value against the
device's rules before sending it, and returns an updated `device`. Keep using
the returned one.

- A value outside the rules raises `ValueError` and is not sent,
  e.g. `Value 70 > max (60)`.
- If the device refuses the change, `ViCommandRejectedError` is raised.

`feature.is_settable` tells you whether a feature can be changed right now, and
`feature.constraints` holds its rules (`min`, `max`, `step`, `options`).
`vi-client list-writable` shows all of them for your device.

## 4. Get fresh values

A `Device` is a snapshot. Ask for a new one when you need current values:

```python
device = await client.refresh_device(device)
```

## 5. Use your real heating

You need a client ID from the
[Viessmann Developer Portal](https://developer.viessmann-climatesolutions.com/start.html).
Log in once with the CLI; it saves your tokens to `tokens.json`:

```bash
vi-client login --client-id <YOUR_CLIENT_ID>
```

Then replace the fixture client with `ViClient`:

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
        # ... everything else as above


asyncio.run(main())
```

## Next Steps

- [Client Reference](05_client_reference.md): all methods, including
  gateway-wide refresh and `run_command` for multi-parameter commands.
- [Models Reference](04_models_reference.md): `Device`, `Feature`, and
  `FeatureConstraints` in detail.
- [Authentication](03_auth_reference.md): logging in from Python and token handling.
- [CLI Reference](06_cli_reference.md) and [Exceptions](07_exceptions_reference.md).
