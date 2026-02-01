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

## Discovery Methods

Methods to discover the structure of your heating system.

### `get_installations() -> List[Installation]`
Fetches all available installations.
*   **Returns**: List of `Installation` objects.

### `get_gateways() -> List[Gateway]`
Fetches all gateways (automatically linked to installations).
*   **Returns**: List of `Gateway` objects.

### `get_devices(installation_id: str, gateway_serial: str, include_features: bool = False, only_active_features: bool = False) -> List[Device]`
Fetches devices attached to a specific gateway.

*   **Parameters**:
    *   `installation_id`: Installation ID (string).
    *   `gateway_serial`: Gateway serial number.
    *   `include_features`: If `True`, automatically populates the `features` list (Default `False`).
    *   `only_active_features`: If `include_features=True`, only fetches enabled features (Default `False`).
*   **Returns**: List of `Device` objects. If `include_features=True`, the `features` property will be populated.

### `get_full_installation_status(installation_id: str, only_enabled: bool = True) -> List[Device]`
Fetches the complete status of an installation, including all devices and their features.

*   **Parameters**:
    *   `installation_id`: The ID of the installation to scan.
    *   `only_enabled`: if `True` (default), only fetches active features.
*   **Returns**: List of `Device` objects, where each device has its `features` attribute fully populated.
*   **Use Case**: Initial startup (e.g., Home Assistant integration load) to populate the entire entity registry at once.

## Feature Methods

Methods to read data and control the device.

### `get_features(device: Device, only_enabled: bool = False, feature_names: List[str] = None) -> List[Feature]`
Fetches features for a specific device. This is the primary method to read data.

*   **Parameters**:
    *   `device`: A `Device` object.
    *   `only_enabled`: if `True`, only returns features that are enabled by the device configuration.
    *   `feature_names`: Optional list of feature names to fetch (e.g. `["heating.sensors.temperature.outside"]`). If None, fetches all features.
*   **Returns**: List of `Feature` objects.
*   **Performance**: If `feature_names` is provided, the request is optimized to fetch only those specific features.

### `update_device(device: Device, only_enabled: bool = True) -> Device`
Refreshes a specific device by refetching all its features.

*   **Parameters**:
    *   `device`: The `Device` object to update.
    *   `only_enabled`: if `True`, only fetches active features (Performance optimization).
*   **Returns**: A new `Device` instance with updated features.
*   **Best for**: Efficient polling. Use this instead of re-discovering the entire installation hierarchy if you already have a `Device` object.

### `set_feature(device: Device, feature: Feature, target_value: Any) -> CommandResponse`
Sets a new value for a writable feature.

*   **Parameters**:
    *   `device`: The `Device` object.
    *   `feature`: The `Feature` object you want to change (must be writable).
    *   `target_value`: The new value you want to set.
*   **Returns**: `CommandResponse` object with `success`, `message`, and `reason` fields.
*   **Raises**:
    *   `ViValidationError` if the value violates constraints (min/max/options).
    *   `ViConnectionError` if the API call fails.
*   **Magic**: This method automatically resolves the correct command name and parameter name from the feature's definition.

## Analytics Methods

### `get_consumption(device: Device, start_dt: datetime, end_dt: datetime, metric: str = "summary", resolution: str = "1d") -> List[Feature]`
Fetches energy consumption usage for a time range.

*   **Parameters**:
    *   `device`: A `Device` object.
    *   `start_dt`: Start date (datetime or ISO string).
    *   `end_dt`: End date (datetime or ISO string).
    *   `metric`: The data metric to fetch (e.g. 'summary', 'dhw'). Default: `"summary"`.
    *   `resolution`: Resolution of data (`"1d"`, `"1w"`, `"1m"`, `"1y"`). Default: `"1d"`.
*   **Returns**: List of `Feature` objects containing consumption values.

## Next Steps

- **[Getting Started](01_getting_started.md)**: installation and basic usage.
- **[API Concepts](02_api_structure.md)**: understand the data-driven design.
- **[Authentication](03_auth_reference.md)**: setup tokens and sessions.
- **[Models Reference](04_models_reference.md)**: detailed documentation of `Feature`, `Device`, and `Command`.
- **[CLI Reference](06_cli_reference.md)**: terminal usage.
- **[Exceptions Reference](07_exceptions_reference.md)**: error handling.
