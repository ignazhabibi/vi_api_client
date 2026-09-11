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

`MockViClient(device_name)` is a `ViClient` subtype backed by bundled device
responses. It supports the same high-level methods without constructing
authentication, sessions, or network transports.

## Discovery Methods

Methods to discover the structure of your heating system.

### `get_installations() -> list[Installation]`
Fetches all available installations.
*   **Returns**: List of `Installation` objects.

### `get_gateways() -> list[Gateway]`
Fetches all gateways (automatically linked to installations).
*   **Returns**: List of `Gateway` objects.

### `get_devices(installation_id: str, gateway_serial: str, include_features: bool = False, only_active_features: bool = False) -> list[Device]`
Fetches devices attached to a specific gateway.

*   **Parameters**:
    *   `installation_id`: Installation ID (string).
    *   `gateway_serial`: Gateway serial number.
    *   `include_features`: If `True`, automatically populates the `features` list (Default `False`).
    *   `only_active_features`: If `include_features=True`, only returns enabled and ready features (Default `False`).
*   **Returns**: List of `Device` objects. If `include_features=True`, the `features` property will be populated.

### `get_full_installation_status(installation_id: str, only_enabled: bool = True) -> list[Device]`
Fetches the complete status of an installation, including all devices and their features.

*   **Parameters**:
    *   `installation_id`: The ID of the installation to scan.
    *   `only_enabled`: if `True` (default), only returns enabled and ready features.
*   **Returns**: List of `Device` objects, where each device has its `features` attribute fully populated.
*   **Use Case**: Initial startup (e.g., Home Assistant integration load) to populate the entire entity registry at once.

## Feature Methods

Methods to read data and control the device.

### `get_features(device: Device, only_enabled: bool = False, feature_names: list[str] | None = None) -> list[Feature]`
Fetches features for a specific device. This is the primary method to read data.

*   **Parameters**:
    *   `device`: A `Device` object.
    *   `only_enabled`: if `True`, only returns features that are enabled and ready.
    *   `feature_names`: Optional list of feature names to fetch (e.g. `["heating.sensors.temperature.outside"]`). If None, fetches all features.
*   **Returns**: List of `Feature` objects.
*   **Performance**: If `feature_names` is provided, the request is optimized to fetch only those specific features.

### `update_device(device: Device, only_enabled: bool = True) -> Device`
Refreshes a specific device by refetching all its features.

*   **Parameters**:
    *   `device`: The `Device` object to update.
    *   `only_enabled`: if `True`, only returns enabled and ready features (default `True`).
*   **Returns**: A new `Device` instance with updated features.
*   **Best for**: Efficient polling. Use this instead of re-discovering the entire installation hierarchy if you already have a `Device` object.

### `update_gateway_devices(devices: list[Device]) -> GatewayDeviceRefreshResult`

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
result = await client.update_gateway_devices(devices)
for device in result.updated_devices:
    use_current_state(device)

for device_id, error in result.errors_by_device_id.items():
    handle_unavailable_device(device_id, error.error_type)
```

`get_features`, `update_device`, `get_devices`, and
`get_full_installation_status` use their single-device request and error
semantics rather than this gateway-scoped partial-result contract.

### `set_feature(device: Device, feature: Feature, target_value: Any) -> tuple[CommandResponse, Device]`
Sets a new value for a writable feature and returns an optimistically updated device.

*   **Parameters**:
    *   `device`: The `Device` object.
    *   `feature`: The `Feature` object you want to change (must be writable).
    *   `target_value`: The new value you want to set.
*   **Returns**: Tuple of `(CommandResponse, Device)`:
    *   `CommandResponse`: Object with `success`, `message`, and `reason` fields.
    *   `Device`: Updated device with the feature value optimistically set (on success) or unchanged (on failure).
*   **Raises**:
    *   `ValueError` if the feature is read-only or the value violates client-side constraints.
    *   `ViValidationError` if the API rejects the generated command payload.
    *   `ViConnectionError` if the API call fails.
*   **Magic**: This method automatically resolves the correct command name and parameter name from the feature's definition.
*   **Important**: Always use the returned `Device` for subsequent calls to ensure correct dependency resolution for interdependent features.

**Example**:
```python
# Set heating curve slope
response, device = await client.set_feature(device, slope_feature, 0.7)
if response.success:
    # Use updated device for next operation
    response, device = await client.set_feature(device, shift_feature, 7.0)
```

### `execute_command(feature: Feature, parameters: dict[str, Any]) -> CommandResponse`

Executes an explicit command parameter set for a writable feature. Unlike
`set_feature`, this operation does not resolve dependencies, validate against
the feature's single-value constraints, or update a `Device`: every supplied
parameter is sent unchanged.

Use it only when the caller already has the complete command payload, such as
an advanced integration writing both heating-curve values at once.

The method raises `ValueError` when the supplied feature is read-only. API and
connection failures use the corresponding `ViError` subclasses.

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
