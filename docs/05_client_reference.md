# Client Reference

This page describes the `ViCareClient` class, the main entry point for interacting with the Viessmann API.

## ViCareClient

```python
from vi_api_client import ViCareClient
```

### Constructor

| Parameter | Type | Description |
| :--- | :--- | :--- |
| `auth` | `AbstractAuth` | An authenticated `Auth` instance (e.g., `PKCEAuth`). |

### Methods

#### `get_transposed_devices() -> List[Dict]`
Discovers all installations, gateways, and devices available to the user.
*   **Returns**: A list of dictionaries containing flattened ID information (`installation_id`, `gateway_serial`, `device_id`, `model`, `status`).
*   **Best for**: Initial discovery in CLI or scripts.

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
