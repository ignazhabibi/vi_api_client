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

### Methods

#### `get_installations() -> List[Installation]`
Fetches all available installations.
*   **Returns**: List of `Installation` objects.

#### `get_gateways() -> List[Gateway]`
Fetches all gateways (automatically linked to installations).
*   **Returns**: List of `Gateway` objects.

#### `get_devices(installation_id: str, gateway_serial: str) -> List[Device]`
Fetches devices attached to a specific gateway as typed objects.
*   **Parameters**:
    *   `installation_id`: Installation ID (string).
    *   `gateway_serial`: Gateway serial number.
*   **Returns**: List of `Device` objects.

#### `get_full_installation_status(installation_id: str) -> List[Device]`
Deep fetch of a whole installation.
*   **Returns**: A list of `Device` objects, each populated with all its `Feature`s.
*   **Best for**: Getting a complete snapshot of the system state.

#### `update_device(device: Device, only_enabled: bool = True) -> Device`
Refreshes a single device.
*   **Best for**: Efficient polling (e.g. every 60s). Reuses IDs from the device object to minimize API calls.
*   **Returns**: A new `Device` instance with fresh features.

#### `get_features(device: Device, only_enabled: bool = False) -> List[Feature]`
Fetches features for a specific device.
*   **Parameters**:
    *   `device`: A `Device` object.
    *   `only_enabled`: if `True`, uses the optimized server-side filter to fetch only active features.
*   **Returns**: List of `Feature` objects.

#### `get_feature(device: Device, feature_name: str) -> Feature`
Fetches a specific feature by name.
*   **Parameters**:
    *   `device`: A `Device` object.
    *   `feature_name`: Name of the feature (e.g. `heating.circuits.0.heating.curve`).
*   **Returns**: A `Feature` object.
*   **Raises**: `ViNotFoundError` if the feature does not exist.

#### `execute_command(feature: Feature, command_name: str, params: Dict = {}) -> CommandResponse`
Executes a command on a feature.
*   **Parameters**:
    *   `feature`: The `Feature` object (containing command definitions).
    *   `command_name`: Name of the command to execute (e.g. `setMode`).
    *   `params`: Dictionary of parameters (e.g. `{"mode": "heating"}`).
*   **Returns**: `CommandResponse` object with `success`, `message`, and `reason` fields.
*   **Raises**: `ViValidationError` if parameters are invalid.

#### `get_consumption(device: Device, start_dt: datetime, end_dt: datetime, metric: str = "summary") -> List[Feature]`
Fetches energy consumption usage for a time range.
*   **Parameters**:
    *   `device`: A `Device` object.
    *   `start_dt`: Start date (format: ISO8601 string or datetime).
    *   `end_dt`: End date (format: ISO8601 string or datetime).
    *   `metric`: One of `"total"`, `"heating"`, `"dhw"`, or `"summary"`.
*   **Returns**: List of `Feature` objects containing consumption values.
