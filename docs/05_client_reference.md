# Client Reference

This page describes the `ViCareClient` class, the main entry point for interacting with the Viessmann API.

## ViCareClient

```python
from vi_api_client import ViCareClient
```

### Constructor

| Parameter | Type | Description |
| :--- | :--- | :--- |
| `auth` | `AbstractAuth` | An authenticated `Auth` instance (e.g., `OAuth`). |

### Methods

#### `get_installations() -> List[Dict]`
Fetches all available installations.
*   **Returns**: List of dictionaries (containing `id`, `alias`, etc.).

#### `get_gateways() -> List[Dict]`
Fetches all gateways (automatically linked to installations).

#### `get_devices(installation_id, gateway_serial) -> List[Dict]`
Fetches devices attached to a specific gateway.

#### `get_full_installation_status(installation_id) -> List[Device]`
Deep fetch of a whole installation.
*   **Returns**: A list of `Device` objects, each populated with all its `Feature`s.
*   **Best for**: Getting a complete snapshot of the system state.

#### `get_features_models(inst_id, gw_serial, dev_id) -> List[Feature]`
Fetches all features for a specific device.
*   **Returns**: A list of `Feature` objects, automatically expanded if they contain complex data (unless raw is requested).

#### `get_feature(inst_id, gw_serial, dev_id, feature_name) -> Dict`
Fetches the raw JSON data for a specific feature.
*   **Returns**: Dictionary containing the raw API response for that feature.

#### `get_enabled_features(inst_id, gw_serial, dev_id) -> List[Dict]`
Fetches the list of all feature *names* that are currently enabled on the device.
*   **Returns**: List of dictionaries with metadata (lighter payload than full feature list).

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
