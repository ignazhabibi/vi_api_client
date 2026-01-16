# Client Reference

This page describes the `Client` class, the main entry point for interacting with the Viessmann API.

## Client

```python
from vi_api_client import Client
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

#### `get_devices(installation_id, gateway_serial) -> List[Device]`
Fetches devices attached to a specific gateway as typed objects.

#### `get_full_installation_status(installation_id) -> List[Device]`
Deep fetch of a whole installation.
*   **Returns**: A list of `Device` objects, each populated with all its `Feature`s.
*   **Best for**: Getting a complete snapshot of the system state.

#### `update_device(device, only_enabled=True) -> Device`
Refreshes a single device.
*   **Best for**: Efficient polling (e.g. every 60s). Reuses IDs from the device object to minimize API calls.
*   **Returns**: A new `Device` instance with fresh features.

#### `get_features(inst_id, gw_serial, dev_id, only_enabled=False) -> List[Feature]`
Fetches features for a specific device.
*   **Parameters**:
    *   `only_enabled`: if `True`, uses the optimized server-side filter to fetch only active features.
*   **Returns**: List of `Feature` objects.

#### `get_feature(inst_id, gw_serial, dev_id, feature_name) -> Dict`
Fetches the raw JSON data for a specific feature.
*   **Returns**: Dictionary containing the raw API response for that feature.


#### `execute_command(feature, command_name, params) -> Dict`
Executes a command on a feature.
*   **Parameters**:
    *   `feature`: The `Feature` object (containing command definitions).
    *   `command_name`: Name of the command to execute (e.g. `setMode`).
    *   `params`: Dictionary of parameters (e.g. `{"mode": "heating"}`).
*   **Returns**: Result dictionary from the API.
*   **Raises**: `ViValidationError` if parameters are invalid.

#### `get_today_consumption(...)`
(Not officially documented in this reference yet, see `analytics` module).
