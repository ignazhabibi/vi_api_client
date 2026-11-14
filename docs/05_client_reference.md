# ViClient Reference

This page describes the `ViClient` class, the main entry point for interacting with the Viessmann API.

## ViClient

```python
from vi_api_client import ViClient
```

### Constructor

| Parameter | Type | Description |
| :--- | :--- | :--- |
| `auth` | `AbstractAuth` | An authenticated `Auth` instance (e.g., `OAuth`). |

## Client contract

`ViClient` exposes typed discovery, refresh, and write operations rather than a
generic HTTP interface. Use `set_feature(device, feature, value)` for a safe
single-feature write with dependency resolution, or
`run_command(device, api_feature_name, command_name, parameters)` for any command
an API feature reports. Both operations report a rejected command through
`ViCommandRejectedError`, so success always means that no exception was raised.

An application that supplies `OAuth(websession=session)` owns and closes that
session. An `AbstractAuth` provider closes only a session it created itself.

Applications own request concurrency, retry, backoff, polling, and stale-state
policy. `ViClient` does not automatically retry authentication, rate-limit,
server, or connection failures, and does not provide a client-wide rate limiter
or concurrency control. A gateway-scoped device refresh keeps its existing
partial-result behavior; consumers decide how to use stale state and partial
availability.

`ViClient` is the live client when configured with authentication: its public
workflows read from the Viessmann API. `FixtureViClient(device_name)` is the
fixture-backed client: it runs those public workflows against bundled fixture
responses without authentication or network requests. Its bundled fixture
catalog defines each selectable fixture name, model identity, device type, and,
where known, the device roles.
A fixture added from a device export reports the model ID that the exported
device reported, for example `E3_Vitocal_16` for `Vitocal250A`; older fixtures
without a known model ID report a placeholder. A fixture can model several
devices on one gateway: `get_devices` then lists device `"0"` and every further
catalogued device, each device reads its own features, and a gateway-scoped
refresh reads the features of all of them.

Fixture contents are recorded API data, not a compatibility contract. A
fixture refresh or replacement can change its features, values, model ID, or
devices in a minor release; the release notes name such changes. The fixture
names that `get_available_fixture_devices()` lists stay stable within a major
version.

| Fixture | Device | Model ID | Real model ID |
| :--- | :--- | :--- | :--- |
| `VitoairFs300E` | `0` | `E3_ViAir_300F_13` | yes |
| `Vitocal200A` | `0` | `Vitocal200A-SystemController` | no |
| `Vitocal200A` | `1` | `Vitocal200A-OutdoorUnit` | no |
| `Vitocal200S` | `0` | `Vitocal200S` | no |
| `Vitocal222S` | `0` | `Vitocal222S` | no |
| `Vitocal250A` | `0` | `E3_Vitocal_16` | yes |
| `Vitocal250A-TwoCircuits` | `0` | `E3_Vitocal_17` | yes |
| `Vitocal252A` | `0` | `Vitocal252A` | no |
| `Vitocal262A-T2H-R290` | `0` | `E3_Vitocal_262_02` | yes |
| `Vitocal300G-CU401B` | `0` | `CU401B_G` | yes |
| `Vitocal333G-with-Vitovent300F` | `0` | `Vitocal333G-with-Vitovent300F` | no |
| `Vitocharge03` | `0` | `Vitocharge03` | no |
| `VitochargeEvolution08` | `0` | `E3_VitoCharge_Evolution_08` | yes |
| `Vitodens100W` | `0` | `Vitodens100W` | no |
| `Vitodens200W` | `0` | `Vitodens200W` | no |
| `Vitodens300W-B3HF-19` | `0` | `E3_Vitodens_300_0419` | yes |
| `Vitopure350` | `0` | `E3_VitoPure` | yes |
`FixtureViClient.get_available_fixture_devices()` lists those selectable names,
and an unknown name raises `ValueError`.

### Which read method when

Every read fetches device identity or features from the API; none works from a
stored snapshot alone.

| Method | Purpose | API requests |
| :--- | :--- | :--- |
| `get_devices(installation_id, gateway_serial)` | Discover the devices of one gateway, without features. | 1: the device list |
| `get_features(device)` or `get_features(device, feature_names=...)` | Read (selected) features of one device. | 1: the device's feature read |
| `refresh_device(device)` | Read one device's features as a new device snapshot; the workhorse for polling. | 1: the device's feature read |
| `get_full_installation_status(installation_id)` | Scan a whole installation: gateways, their devices, and each device's features. | 1 gateways read, plus 1 device list per matching gateway, plus 1 feature read per device |
| `refresh_gateway_devices(devices)` | Refresh the features of several devices on one gateway with one bulk read. | 1 bulk feature read; a fallback adds up to 1 individual feature read per device |

## Discovery Methods

Methods to discover the structure of your heating system.

### `get_installations() -> list[Installation]`
Fetches all available installations.
*   **Returns**: List of `Installation` objects.

### `get_gateways() -> list[Gateway]`
Fetches all gateways (automatically linked to installations).
*   **Returns**: List of `Gateway` objects.

### `get_devices(installation_id: str, gateway_serial: str) -> list[Device]`
Fetches devices attached to a specific gateway.

*   **Parameters**:
    *   `installation_id`: Installation ID (string).
    *   `gateway_serial`: Gateway serial number.
*   **Returns**: List of device snapshots without features. Load features with
    `refresh_device`, `get_features`, or `get_full_installation_status`.

### `get_full_installation_status(installation_id: str, only_available: bool = True) -> list[Device]`
Fetches the complete status of an installation as refreshed device snapshots.

*   **Parameters**:
    *   `installation_id`: The ID of the installation to scan.
    *   `only_available`: if `True` (default), only returns enabled and ready features.
*   **Returns**: List of refreshed device snapshots whose features came from API
    read responses.
*   **Use Case**: Initial startup (e.g., Home Assistant integration load) to populate the entire entity registry at once.

## Event History Methods

Methods to read installation-scoped history data.

### `get_event_history(installation_id: str, *, days: int | None = None, cursor: str | None = None, limit: int | None = None) -> EventHistoryPage`

Fetches one page of an installation's event history.

*   **Parameters**:
    *   `installation_id`: Installation ID (string).
    *   `days`: Rolling lookback window in days, sent as the provider's
        `lastNDays` filter. Required unless `cursor` is supplied; mutually
        exclusive with `cursor`.
    *   `cursor`: Opaque continuation cursor reported by a previous page.
    *   `limit`: Optional page size between 1 and the documented maximum of
        1000. When omitted, the provider default applies.
*   **Returns**: An `EventHistoryPage` with its `events` and the `next_cursor`
    when the provider reported one.
*   **Raises**:
    *   `ValueError` when both or neither of `days` and `cursor` are supplied,
        the lookback is not positive, or the limit is out of range.
    *   `ViResponseError` for malformed successful responses.
    *   The applicable `ViError` subclass for authentication, rate-limit,
        connection, server, or unknown API failures.

This is a one-page read. Callers request further pages by passing
`next_cursor` as `cursor`; `get_event_history_window` does that for a whole
lookback window.

**Example**:
```python
page = await client.get_event_history(installation_id, days=7, limit=50)
for event in page.events:
    use_event(event.event_type, event.body)
if page.next_cursor:
    next_page = await client.get_event_history(
        installation_id, cursor=page.next_cursor, limit=50
    )
```

### `get_event_history_window(installation_id: str, days: int, *, limit: int | None = None, max_pages: int = 50) -> EventHistoryWindow`

Collects an installation's event history across one lookback window by
following the continuation cursor.

*   **Parameters**:
    *   `installation_id`: Installation ID (string).
    *   `days`: Rolling lookback window in days.
    *   `limit`: Optional page size between 1 and the documented maximum of
        1000, applied to every request. When omitted, the provider default
        applies.
    *   `max_pages`: Safety limit on the number of pages fetched (default 50).
*   **Returns**: An `EventHistoryWindow` with every collected event, the
    number of pages fetched, and the remaining `next_cursor` when the
    traversal stopped at the safety limit. `next_cursor` is `None` when the
    provider reported no further page.
*   **Raises**:
    *   `ValueError` when the installation ID is empty, the lookback or the
        page safety limit is not positive, or the limit is out of range.
        These raise before any request is sent.
    *   `ViResponseError` for malformed successful responses.
    *   The applicable `ViError` subclass for authentication, rate-limit,
        connection, server, or unknown API failures. An error on a later page
        raises and returns no partial window.

The first request uses the lookback; every following request passes only the
continuation cursor, so the lookback is never restarted. Pages are requested
one after the other. Each page is one API request, so a long window with a
small `limit` costs many requests; `max_pages` bounds that.

**Example**:
```python
window = await client.get_event_history_window(installation_id, 7, limit=100)
for event in window.events:
    use_event(event.event_type, event.body)
if window.next_cursor is not None:
    # The safety limit stopped the traversal; the window is incomplete.
    more = await client.get_event_history(
        installation_id, cursor=window.next_cursor, limit=100
    )
```

**Endpoint note**: the requests target
`GET /iot/v2/events-history/installations/{installationId}/events`, the
spelling from Viessmann's 2023 endpoint announcement. The current
developer-portal OpenAPI export spells the route `eventhistory`, but a
read-only probe against a real installation verified the live API: the
announcement route returned HTTP 200 with the expected `data` list, a
`cursor.next`, and the documented event fields, while the portal-export
spelling returned HTTP 404 `ENDPOINT_NOT_FOUND`. The probe script and its
sanitized output are documented in the implementing pull request
([#120](https://github.com/ignazhabibi/vi_api_client/pull/120)). How far back
the provider retains events is not verified; treat an empty or cursor-less
page as the end of the available window.

## Feature Methods

Methods to read data and control the device.

### `get_features(device: Device, only_available: bool = True, feature_names: list[str] | None = None) -> list[Feature]`
Fetches features for a specific device. This is the primary method to read data.

*   **Parameters**:
    *   `device`: A `Device` object.
    *   `only_available`: if `True` (default), only returns features that are enabled and ready.
    *   `feature_names`: Optional list of feature names or API feature names to return (e.g. `["heating.circuits.0.heating.curve.slope"]` or `["heating.circuits.0.heating.curve"]`). An API feature name selects every feature parsed from it. If None, returns all features.
*   **Returns**: List of `Feature` objects. Unknown names select nothing; they do not raise.
*   **Matching**: Names are matched locally after one read of all the device's features, so live and fixture-backed clients return the same features. A name that is both a feature name and an API feature name selects both.
*   **Default filter**: With the default `only_available=True`, a targeted read returns no disabled or not-ready feature.

### `refresh_device(device: Device, only_available: bool = True) -> Device`
Refreshes a specific device by refetching all its features.

*   **Parameters**:
    *   `device`: The `Device` object to refresh.
    *   `only_available`: if `True` (default), only returns enabled and ready features.
*   **Returns**: A refreshed device snapshot with features from an API read response.
*   **Best for**: Efficient polling. Use this instead of re-discovering the entire installation hierarchy if you already have a `Device` object.

### `export_device_fixture(device: Device) -> dict[str, JsonValue]`
Exports a device's features as an anonymized fixture document, for example to
contribute a new fixture.

*   **Parameters**:
    *   `device`: The `Device` to export. It only identifies the device; features already on it are not used.
*   **Returns**: `{"device": {"modelId": ..., "deviceType": ..., "roles": [...], "capturedAt": "YYYY-MM-DD"}, "data": [...]}`. `data` holds the device's raw API features, read fresh and including disabled and not-ready features. `capturedAt` is the UTC date of the export.
*   **Anonymization**: The whole document is redacted with `redact_sensitive` (see [Redacting Sensitive Data](#redacting-sensitive-data)), so it contains no installation IDs, gateway or device serials, or coordinates. Free text, such as user-chosen circuit names, stays.
*   **Raises**: `ViResponseError` for a malformed response, and the applicable `ViError` subclass for API failures.

`FixtureViClient` exports its bundled fixture the same way.

```python
import json
from pathlib import Path

document = await client.export_device_fixture(device)
Path("export.json").write_text(json.dumps(document), encoding="utf-8")
```

### Redacting Sensitive Data

One rule set in `vi_api_client.privacy` decides what is sensitive, so logs,
fixture exports, and consumer diagnostics redact the same data:

| Category | Detection | Replacement |
| --- | --- | --- |
| Secrets | keys ending in `token`, `secret`, or `password`, `credential`/`credentials` keys, and `Bearer <token>` in text | `placeholder` |
| Address data | the keys `address`, `alias`, and `description` | `placeholder` |
| Identifiers | runs of six or more digits that are not part of a longer word, number, or version, in values, URIs, and text | `#` of the same length |
| Location | numeric `latitude` and `longitude` values | `0` |

Free text such as circuit names, URLs apart from their identifiers, raw device
messages, and error codes are kept. Keys that only contain a matching word, such
as `busAddress` or `token_type`, are kept too. Replacements keep the structure
and the type of numbers, so a redacted feature document still parses as a
fixture.

*   `redact_sensitive(value, *, placeholder="<redacted>")`: returns redacted log text for a `str` and a redacted copy for any other JSON value.
*   `redact_feature(feature, *, placeholder=...)`: returns a `Feature` whose value, the option values in constraints, and the URIs of its internal write command (both URIs of an on/off command pair) are redacted. The last part of the feature name acts as the value's key, so `....houseLocation.latitude` becomes `0`.
*   `redact_device(device, *, placeholder=...)`: returns a `Device` whose ID, gateway serial, installation ID, and features are redacted.

```python
from vi_api_client import redact_device, redact_sensitive

logger.debug("Request: %s", redact_sensitive(url))
diagnostics_device = redact_device(device, placeholder="**REDACTED**")
```

Which data a diagnostics report contains, how it is serialized, and labels such
as `device_1` stay with the consuming application.

### `refresh_gateway_devices(devices: list[Device]) -> GatewayDeviceRefreshResult`

Refreshes known devices belonging to one installation and gateway. The normal
path uses one POST feature-filter request and retrieves available features
only.

*   **Parameters**:
    *   `devices`: Existing devices from exactly one installation and gateway. Device IDs must be unique.
*   **Returns**: A `GatewayDeviceRefreshResult`. Successful new devices preserve their relative input order; recognized device-specific failures are keyed separately by device ID.
*   **Fallback**: Devices omitted from a valid bulk response are retried individually. A gateway-wide device communication error retries all requested devices individually.
*   **Raises**:
    *   `ValueError` for mixed installations, mixed gateways, or duplicate device IDs.
    *   `ViResponseError` for malformed successful responses.
    *   The applicable `ViError` subclass for global authentication, rate-limit, connection, server, or unknown API failures.
*   **Boundary behavior**: Empty input returns an empty complete result without I/O. Failed original devices are not returned in `refreshed_devices`.

```python
result = await client.refresh_gateway_devices(devices)
for device in result.refreshed_devices:
    use_current_state(device)

for device_id, error in result.errors_by_device_id.items():
    handle_unavailable_device(device_id, error.error_type)
```

`get_features`, `refresh_device`, `get_devices`, and
`get_full_installation_status` use their single-device request and error
semantics rather than this gateway-scoped partial-result contract.

### Writing a value end to end

```python
device = await client.refresh_device(device)
slope = device.get_feature("heating.circuits.0.heating.curve.slope")
if slope is not None and slope.is_settable:
    try:
        device = await client.set_feature(device, slope, 0.7)
    except ValueError as error:
        ...  # rejected locally, nothing was sent
    except ViCommandRejectedError as error:
        ...  # the device refused; keep the previous snapshot
```

Read `slope.constraints` to offer valid values first. Keep the returned
snapshot for the next write, and refresh when you need the device's own state.

### `set_feature(device: Device, feature: Feature | str, target_value: FeatureValue) -> Device`
Sends the command that writes one feature of the current snapshot and returns
a snapshot with the value set locally, without reading it back from the API.

*   **Parameters**:
    *   `device`: The current device snapshot.
    *   `feature`: A `Feature` or feature name of `device`. Only the name
        counts; the feature in `device` must be writable, enabled, and ready.
    *   `target_value`: The new value, any `JsonValue`.
*   **Returns**: The updated `Device` snapshot. Use it for the next write.
*   **Which features are writable**: A feature is written through a command
    whose name starts with `set`, or, for some booleans, through an on/off
    command pair such as `enable` and `disable`
    ([command pairs](02_api_structure.md#boolean-features-written-through-an-onoff-command-pair)).
    A feature that only another command takes, such as a holiday date with
    `schedule`, is read-only; the error names `run_command` and the commands
    to use ([setter commands](02_api_structure.md#only-setter-commands-write-a-value)).
*   **Other parameters**: Further required parameters of the command are
    taken from enabled, ready sibling features of the snapshot.
*   **Raises**:
    *   `ValueError` before any request, when the feature is missing,
        unavailable, or read-only, a required sibling value is missing, or
        the value has the wrong type or breaks a constraint.
    *   `ViCommandRejectedError` when the device refuses the command; see
        [Command Rejections](07_exceptions_reference.md#command-rejections).
    *   `ViResponseError` for a command URI outside the Vi API or a malformed
        response, and other `ViError` subclasses for connection,
        authentication, rate-limit, server, or device failures.

**Example**:
```python
# Set heating curve slope
device = await client.set_feature(device, slope_feature, 0.7)
# Use the same updated snapshot for the next operation
device = await client.set_feature(device, shift_feature, 7.0)
```

### `run_command(device: Device, api_feature_name: str, command_name: str, parameters: dict[str, JsonValue] | None = None) -> None`

Runs one reported command of a device's API feature, addressed by names, and
returns nothing. Use it when the caller knows the command to run, such as an
advanced integration writing both heating-curve values at once or planning a
holiday with `schedule` and cancelling it with `unschedule`.

The `device` must come from `refresh_device`, `refresh_gateway_devices`, or
`get_full_installation_status`; a snapshot built from `get_features` has no
command list and raises `ValueError`. Any reported command can run, also one
the API reports as not executable at the moment: the API decides. No snapshot
is changed.

`Device.get_command_parameters` lists the parameters a command takes, with
their requirement, type, and allowed values. Missing required parameters,
unknown parameter names, and values that break the reported type or
constraints raise `ValueError` before any request. A rejected command raises
`ViCommandRejectedError`; other failures raise the same errors as
`set_feature`.

```python
# Run the two-parameter curve command in one call
await client.run_command(
    device,
    "heating.circuits.0.heating.curve",
    "setCurve",
    {"slope": 0.7, "shift": 7.0},
)
# Plan a holiday: its dates are read-only features, because `schedule`
# does more than store a value
await client.run_command(
    device,
    "heating.operating.programs.holiday",
    "schedule",
    {"start": "2026-12-24", "end": "2026-12-31"},
)
# Cancel a holiday program without parameters
await client.run_command(device, "heating.operating.programs.holiday", "unschedule")
```

## Next Steps

- **[Getting Started](01_getting_started.md)**: installation and basic usage.
- **[API Concepts](02_api_structure.md)**: understand the data-driven design.
- **[Authentication](03_auth_reference.md)**: setup tokens and sessions.
- **[Models Reference](04_models_reference.md)**: detailed documentation of `Feature`, `FeatureConstraints`, `Device`, and command outcomes.
- **[CLI Reference](06_cli_reference.md)**: terminal usage.
- **[Exceptions Reference](07_exceptions_reference.md)**: error handling.
