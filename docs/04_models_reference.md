# Models Reference

This section details the core data models used in the `vi_api_client` library.

## Installation

Represents an installation site (House).

| Property | Type | Description |
| :--- | :--- | :--- |
| `id` | `str` | Unique installation ID. |
| `description` | `str` | Description of the installation. |
| `alias` | `str` | Alias name. |
| `address` | `Dict` | Address information. |

## Gateway

Represents a communication gateway (Connectivity Device).

| Property | Type | Description |
| :--- | :--- | :--- |
| `serial` | `str` | Serial number of the gateway. |
| `version` | `str` | Firmware version. |
| `status` | `str` | Connection status. |
| `installation_id` | `str` | ID of the associated installation. |

## Device

Represents a physical device attached to a gateway (e.g. Heating System).

| Property | Type | Description |
| :--- | :--- | :--- |
| `id` | `str` | Internal ID of the device (often "0"). |
| `installation_id` | `str` | ID of the installation site. |
| `gateway_serial` | `str` | Serial number of the communication gateway. |
| `model_id` | `str` | Model name (e.g., "E3_Vitocal_16"). |
| `device_type` | `str` | Device type (e.g., "heating", "tcu"). |
| `status` | `str` | Connection status (e.g., "Online"). |
| `features` | `List[Feature]` | List of features supported by this device. |

**Note**: In previous versions, there was a distinction between `features` and `features_flat`. In the new Flat Architecture, `features` is always a flat list of simple properties.

## Feature

The core unit of information. A feature represents a single property (Sensor) or a single setting (Control).

| Property | Type | Description |
| :--- | :--- | :--- |
| `name` | `str` | Unique flat feature name (e.g., `heating.sensors.temperature.outside`). |
| `value` | `Any` | Primary value of the feature (scalar: number, string, boolean). |
| `unit` | `str` | Unit of measurement (e.g., "celsius", "kilowattHour"). |
| `is_ready` | `bool` | Whether the data point is currently valid. |
| `is_enabled` | `bool` | Whether this feature is supported by the device configuration. |
| `is_writable` | `bool` | `True` if this feature can be modified (has a `control` block). |
| `control` | `Optional[FeatureControl]` | Metadata for writing to this feature (if writable). |

### Formatting Values

To format a feature value for display with units:

```python
from vi_api_client.utils import format_feature
print(format_feature(feature))  # "25.5 celsius"
```

## FeatureControl

If a `Feature` is writable (`is_writable=True`), it contains a `control` object describing how to modify it.

This object abstracts away the complexity of Viessmann Commands. You rarely interact with it directly, but it's useful for introspection (e.g. building a UI).

| Property | Type | Description |
| :--- | :--- | :--- |
| `command_name` | `str` | The internal command name (e.g., `setCurve`). |
| `param_name` | `str` | The parameter name this feature maps to (e.g., `slope`). |
| `required_params` | `List[str]` | List of all parameters required for this command (dependency resolution). |
| `parent_feature_name` | `str` | Name of the parent feature (used for sibling lookups). |
| `uri` | `str` | The API endpoint for this specific command. |
| `min` | `float` | Minimum allowed value (numeric). |
| `max` | `float` | Maximum allowed value (numeric). |
| `step` | `float` | Step increment (numeric). |
| `options` | `List[str]` | List of valid options (enum). |
| `pattern` | `str` | Regex pattern for validation (string). |
| `min_length` | `int` | Minimum string length. |
| `max_length` | `int` | Maximum string length. |

## CommandResponse

Result of a command execution (returned by `set_feature`).

| Property | Type | Description |
| :--- | :--- | :--- |
| `success` | `bool` | `True` if the command succeeded. |
| `message` | `str` | Optional message from the API. |
| `reason` | `str` | Optional failure reason or details. |
