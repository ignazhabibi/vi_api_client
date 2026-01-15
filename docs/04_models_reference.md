# Models Reference

This section details the core data models used in the `vi_api_client` library.

## Device

Represents a physical device attached to a gateway.

### Properties

| Property | Type | Description |
| :--- | :--- | :--- |
| `installation_id` | `int` | ID of the installation site. |
| `gateway_serial` | `str` | Serial number of the communication gateway. |
| `device_id` | `str` | Internal ID of the device (often "0"). |
| `model` | `str` | Model name (e.g., "Vitodens 200-W"). |
| `status` | `str` | Connection status (e.g., "Online"). |

## Feature

The core unit of information in the Viessmann API. A feature represents a sensor, a status, or a configuration setting.

### Properties

| Property | Type | Description |
| :--- | :--- | :--- |
| `name` | `str` | Unique feature name (e.g., `heating.sensors.temperature.outside`). |
| `value` | `Any` | Primary scalar value of the feature (if applicable). |
| `unit` | `str` | Unit of measurement (e.g., "celsius", "kilowattHour"). |
| `formatted_value` | `str` | Helper property returning value + unit string. |
| `properties` | `Dict` | Raw dictionary of all properties returned by the API. |
| `is_ready` | `bool` | Whether the datum is currently available. |
| `is_enabled` | `bool` | Whether this feature is supported by the device. |
| `commands` | `Dict[str, Command]` | Dictionary of available commands for this feature. |

### Methods

#### `expand() -> List[Feature]`
Expands a complex feature (one with multiple properties, like heating curve slope & shift) into a list of simple scalar features.
*   **Returns**: A list of `Feature` objects, each representing a single property.
*   **Use Case**: Essential for Home Assistant integration where one entity should track only one value.

## Command

Represents an executable action on a Feature (e.g., `setTemperature`).

### Properties

| Property | Type | Description |
| :--- | :--- | :--- |
| `name` | `str` | Command name (e.g., `setMode`). |
| `uri` | `str` | API URI for execution. |
| `is_executable` | `bool` | Whether the command can currently be executed. |
| `params` | `Dict` | Definition of required parameters and constraints. |

### Parameter Constraints

Parameters often have constraints defined in `params`. The library ensures these are met before sending a request.

*   `min` / `max`: Minimum and maximum numeric values.
*   `step`: Allowed step size/increment.
*   `enum`: List of allowed string values.
*   `regex`: Regular expression pattern for string validation.

### Usage Example

```python
feature = ... # get feature
if "setMode" in feature.commands:
    cmd = feature.commands["setMode"]
    if cmd.is_executable:
        # Check params structure
        print(cmd.params) 
```
