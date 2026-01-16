# Models Reference

This section details the core data models used in the `vi_api_client` library.

## Installation

Represents an installation site.

### Properties

| Property | Type | Description |
| :--- | :--- | :--- |
| `id` | `str` | Unique installation ID. |
| `description` | `str` | Description of the installation. |
| `alias` | `str` | Alias name. |
| `address` | `Dict` | Address information. |

## Gateway

Represents a communication gateway.

### Properties

| Property | Type | Description |
| :--- | :--- | :--- |
| `serial` | `str` | Serial number of the gateway. |
| `version` | `str` | Firmware version. |
| `status` | `str` | Connection status. |
| `installation_id` | `str` | ID of the associated installation. |

## Device

Represents a physical device attached to a gateway.

### Properties

| Property | Type | Description |
| :--- | :--- | :--- |
| `id` | `str` | Internal ID of the device (often "0"). |
| `installation_id` | `str` | ID of the installation site. |
| `gateway_serial` | `str` | Serial number of the communication gateway. |
| `model_id` | `str` | Model name (e.g., "E3_Vitocal_16"). |
| `device_type` | `str` | Device type (e.g., "heating", "tcu"). |
| `status` | `str` | Connection status (e.g., "Online"). |
| `features` | `List[Feature]` | List of features attached to this device. |

### Properties (Computed)

| Property | Type | Description |
| :--- | :--- | :--- |
| `features_flat` | `List[Feature]` | Flattened list of all features (expanded). |

## Feature

The core unit of information in the Viessmann API. A feature represents a sensor, a status, or a configuration setting.

### Properties

| Property | Type | Description |
| :--- | :--- | :--- |
| `name` | `str` | Unique feature name (e.g., `heating.sensors.temperature.outside`). |
| `value` | `Any` | Primary scalar value of the feature (if applicable). |
| `unit` | `str` | Unit of measurement (e.g., "celsius", "kilowattHour"). |
| `properties` | `Dict` | Raw dictionary of all properties returned by the API. |
| `is_ready` | `bool` | Whether the datum is currently available. |
| `is_enabled` | `bool` | Whether this feature is supported by the device. |
| `commands` | `Dict[str, Command]` | Dictionary of available commands for this feature. |

### Methods

#### `expand() -> List[Feature]`
Expands a complex feature (one with multiple properties, like heating curve slope & shift) into a list of simple scalar features.
*   **Returns**: A list of `Feature` objects, each representing a single property.
*   **Use Case**: Essential for Home Assistant integration where one entity should track only one value.

### Formatting Values

To format a feature value for display, use the utility function:

```python
from vi_api_client.utils import format_feature

formatted = format_feature(feature)  # Returns "25.5 celsius"
```

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

## CommandResponse

Result of a command execution.

### Properties

| Property | Type | Description |
| :--- | :--- | :--- |
| `success` | `bool` | Whether the command succeeded. |
| `message` | `str` | Optional message from the API. |
| `reason` | `str` | Optional reason (e.g., for failures). |
