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
`execute_command(feature, parameters)` when the complete command payload is
already known.

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
catalog defines each selectable fixture name, model identity, and device type;
`FixtureViClient.get_available_fixture_devices()` lists those selectable names,
and an unknown name raises `ValueError`.

## Discovery Methods

Methods to discover the structure of your heating system.

### `get_installations() -> list[Installation]`
Fetches all available installations.
*   **Returns**: List of `Installation` objects.

### `get_gateways() -> list[Gateway]`
Fetches all gateways (automatically linked to installations).
*   **Returns**: List of `Gateway` objects.

### `get_devices(installation_id: str, gateway_serial: str, include_features: bool = False, only_enabled: bool = False) -> list[Device]`
Fetches devices attached to a specific gateway.

*   **Parameters**:
    *   `installation_id`: Installation ID (string).
    *   `gateway_serial`: Gateway serial number.
    *   `include_features`: If `True`, performs device feature hydration (Default `False`).
    *   `only_enabled`: If `include_features=True`, only returns enabled and ready features (Default `False`).
*   **Returns**: List of device snapshots. If `include_features=True`, device
    feature hydration produces new snapshots from the feature responses.

### `get_full_installation_status(installation_id: str, only_enabled: bool = True) -> list[Device]`
Fetches the complete status of an installation as refreshed device snapshots.

*   **Parameters**:
    *   `installation_id`: The ID of the installation to scan.
    *   `only_enabled`: if `True` (default), only returns enabled and ready features.
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

This is a one-page read. Traversing a full window is deliberately out of
scope here; callers request further pages by passing `next_cursor` as
`cursor`. The `list-events` CLI command is one such consumer and follows
cursors up to a configurable page safety limit.

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

**Endpoint note**: the request targets
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

### `get_features(device: Device, only_enabled: bool = False, feature_names: list[str] | None = None) -> list[Feature]`
Fetches features for a specific device. This is the primary method to read data.

*   **Parameters**:
    *   `device`: A `Device` object.
    *   `only_enabled`: if `True`, only returns features that are enabled and ready.
    *   `feature_names`: Optional list of feature names or API feature names to return (e.g. `["heating.circuits.0.heating.curve.slope"]` or `["heating.circuits.0.heating.curve"]`). An API feature name selects every feature parsed from it. If None, returns all features.
*   **Returns**: List of `Feature` objects. Unknown names select nothing; they do not raise.
*   **Matching**: The client always fetches the device's features and matches names locally after parsing, so live and fixture-backed clients return the same features. A name that is both a feature name and an API feature name, such as `heating.circuits.0.name`, selects both matches. The request therefore carries the whole device feature set even when only a few names are requested; the number of API calls does not change.

### `refresh_device(device: Device, only_enabled: bool = True) -> Device`
Refreshes a specific device by refetching all its features.

*   **Parameters**:
    *   `device`: The `Device` object to refresh.
    *   `only_enabled`: if `True`, only returns enabled and ready features (default `True`).
*   **Returns**: A refreshed device snapshot with features from an API read response.
*   **Best for**: Efficient polling. Use this instead of re-discovering the entire installation hierarchy if you already have a `Device` object.

### `export_device_fixture(device: Device) -> dict[str, JsonValue]`
Exports a device's features as an anonymized fixture document, for example to
contribute a new fixture.

*   **Parameters**:
    *   `device`: The `Device` to export. It only identifies the device; features already on it are not used.
*   **Returns**: `{"device": {"modelId": ..., "deviceType": ..., "capturedAt": "YYYY-MM-DD"}, "data": [...]}`. `data` holds the device's raw API features, read fresh and including disabled and not-ready features. `capturedAt` is the UTC date of the export.
*   **Anonymization**: The whole document is masked with `mask_identifiers`, so it contains no installation IDs, gateway or device serials, or coordinates. Free text, such as user-chosen circuit names, stays.
*   **Raises**: `ViResponseError` for a malformed response, and the applicable `ViError` subclass for API failures.

`FixtureViClient` exports its bundled fixture the same way.

```python
import json
from pathlib import Path

document = await client.export_device_fixture(device)
Path("export.json").write_text(json.dumps(document), encoding="utf-8")
```

`mask_identifiers(document)` applies the same masking to any JSON-compatible
API document and returns a masked copy:

*   A string, or a `/`-separated segment of a string such as a URI path segment, that consists of six or more digits becomes the same number of `#` characters. This is the rule PyViCare's `dump_secure` applies. Numbers, such as counters, and shorter IDs such as device `"0"` stay.
*   The numeric value of a `latitude` or `longitude` entry becomes `0`.

### `refresh_gateway_devices(devices: list[Device]) -> GatewayDeviceRefreshResult`

Refreshes known devices belonging to one installation and gateway. The normal
path uses one POST feature-filter request and retrieves enabled and ready
features only.

*   **Parameters**:
    *   `devices`: Existing devices from exactly one installation and gateway. Device IDs must be unique.
*   **Returns**: A `GatewayDeviceRefreshResult`. Successful new devices preserve their relative input order; recognized device-specific failures are keyed separately by device ID.
*   **Fallback**: Devices omitted from a valid bulk response are retried individually. A gateway-wide `DEVICE_COMMUNICATION_ERROR` retries all requested devices individually.
*   **Raises**:
    *   `ValueError` for mixed installations, mixed gateways, or duplicate device IDs.
    *   `ViResponseError` for malformed successful responses.
    *   The applicable `ViError` subclass for global authentication, rate-limit, connection, server, or unknown API failures.
*   **Boundary behavior**: Empty input returns an empty complete result without I/O. Failed original devices are not returned in `updated_devices`.

```python
result = await client.refresh_gateway_devices(devices)
for device in result.updated_devices:
    use_current_state(device)

for device_id, error in result.errors_by_device_id.items():
    handle_unavailable_device(device_id, error.error_type)
```

`get_features`, `refresh_device`, `get_devices`, and
`get_full_installation_status` use their single-device request and error
semantics rather than this gateway-scoped partial-result contract.

### `set_feature(device: Device, feature: Feature, target_value: FeatureValue) -> tuple[CommandResponse, Device]`
Sends a feature command for the feature with the same name in the supplied
current device snapshot and returns a command-updated device snapshot after a
successful command response, without an API read-back.

*   **Parameters**:
    *   `device`: The `Device` object.
    *   `feature`: A `Feature` whose name is present in `device`; the current
        device feature must be writable, enabled, and ready.
    *   `target_value`: The new value to set; any `JsonValue` (null, boolean,
        number, string, list, or string-keyed object).
*   **Returns**: Tuple of `(CommandResponse, Device)`:
    *   `CommandResponse`: Object with `success`, `message`, and `reason` fields.
    *   `Device`: Command-updated device snapshot with the feature value set
        locally on success, or the original snapshot on failure.
*   **Raises**:
    *   `ValueError` if the feature is absent from the device, unavailable,
        missing a required enabled and ready sibling value, has a different
        type than the command parameter reports (`number`, `integer`,
        `string`, or `boolean`; a boolean never counts as a number), violates
        client-side constraints, or a command parameter value is not a JSON
        value.
    *   `ViValidationError` if the API rejects the generated command payload.
    *   `ViResponseError` if the command URI is outside the Vi API or the
        successful command response violates the API contract; no request is
        sent to a URI outside the Vi API.
    *   `ViConnectionError` if the API call fails.
*   **Magic**: This method uses the current device feature's command metadata,
    always includes its target parameter, and resolves each other required
    parameter from an enabled, ready sibling feature with a non-`None` value.
    Optional sibling parameters are not added automatically.
*   **Important**: The returned device is a local command-updated snapshot, not
    an API refresh. Always use it for subsequent commands, then refresh when
    authoritative API state is needed.

**Example**:
```python
# Set heating curve slope
response, device = await client.set_feature(device, slope_feature, 0.7)
if response.success:
    # Use updated device for next operation
    response, device = await client.set_feature(device, shift_feature, 7.0)
```

### `execute_command(feature: Feature, parameters: dict[str, JsonValue]) -> CommandResponse`

Executes an explicit command parameter set for a writable feature. Unlike
`set_feature`, this operation does not resolve dependencies, validate against
the feature's single-value constraints, or update a `Device`: every supplied
parameter is sent unchanged.

Use it only when the caller already has the complete command payload, such as
an advanced integration writing both heating-curve values at once.

The feature must be writable, enabled, and ready. The supplied payload must
contain the target parameter and every required parameter; additional
parameters are allowed and the mapping is sent unchanged. Every parameter
value must be within the JSON value contract; a non-JSON value raises
`ValueError` before any request is sent. The method raises `ValueError` for
local contract violations. API and connection failures use the corresponding
`ViError` subclasses, and a successful command response that violates the
response contract raises `ViResponseError`. A command URI outside the Vi API
also raises `ViResponseError`, before the access token is sent anywhere.

```python
response = await client.execute_command(slope_feature, {"slope": 0.7, "shift": 7.0})
```

## Next Steps

- **[Getting Started](01_getting_started.md)**: installation and basic usage.
- **[API Concepts](02_api_structure.md)**: understand the data-driven design.
- **[Authentication](03_auth_reference.md)**: setup tokens and sessions.
- **[Models Reference](04_models_reference.md)**: detailed documentation of `Feature`, `FeatureControl`, `Device`, and command results.
- **[CLI Reference](06_cli_reference.md)**: terminal usage.
- **[Exceptions Reference](07_exceptions_reference.md)**: error handling.
