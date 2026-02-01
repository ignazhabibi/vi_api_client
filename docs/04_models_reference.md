# Models Reference

This section details the core data models used in the `vi_api_client` library.

## Installation

Represents an installation site (House).

| Property | Type | Description | Example |
| :--- | :--- | :--- | :--- |
| `id` | `str` | Unique installation ID. | `'123456789'` |
| `description` | `str` | Description of the installation. | `'Home'` |
| `alias` | `str` | Alias name. | `'My House'` |
| `address` | `Dict` | Address information. | `{'city': 'Berlin', ...}` |

## Gateway

Represents a communication gateway (Connectivity Device).

| Property | Type | Description | Example |
| :--- | :--- | :--- | :--- |
| `serial` | `str` | Serial number of the gateway. | `'1234567890123456'` |
| `version` | `str` | Firmware version. | `'1.2.3'` |
| `status` | `str` | Connection status. | `'Online'` |
| `installation_id` | `str` | ID of the associated installation. | `'123456789'` |

## Device

Represents a physical device attached to a gateway (e.g. Heating System).

| Property | Type | Description | Example |
| :--- | :--- | :--- | :--- |
| `id` | `str` | Internal ID of the device (often "0"). | `'0'` |
| `installation_id` | `str` | ID of the installation site. | `'123456789'` |
| `gateway_serial` | `str` | Serial number of the communication gateway. | `'1234567890123456'` |
| `model_id` | `str` | Model name (e.g., "E3_Vitocal_16"). | `'E3_Vitocal_250A'` |
| `device_type` | `str` | Device type (e.g., "heating", "tcu"). | `'heating'` |
| `status` | `str` | Connection status (e.g., "Online"). | `'Online'` |
| `features` | `List[Feature]` | List of features supported by this device. | `[Feature(...)]` |



## Feature

The core unit of information. A feature represents a single property (Sensor) or a single setting (Control).

| Property | Type | Description | Example |
| :--- | :--- | :--- | :--- |
| `name` | `str` | Unique flat feature name. | `'heating.circuits.0.heating.curve.slope'` |
| `value` | `Any` | Primary value of the feature (scalar). | `1.4` |
| `unit` | `str` | Unit of measurement. | `None` |
| `is_ready` | `bool` | Whether the data point is currently valid. | `True` |
| `is_enabled` | `bool` | Whether this feature is supported. | `True` |
| `is_writable` | `bool` | `True` if this feature can be modified. | `True` |
| `control` | `Optional[FeatureControl]` | Metadata for writing to this feature. | `FeatureControl(...)` |

### Formatting Values

To format a feature value for display with units:

```python
from vi_api_client.utils import format_feature
print(format_feature(feature))  # "25.5 celsius"
```

## FeatureControl

If a `Feature` is writable (`is_writable=True`), it contains a `control` object describing how to modify it.

This object abstracts away the complexity of Viessmann Commands. You rarely interact with it directly, but it's useful for introspection (e.g. building a UI).

| Property | Type | Description | Example |
| :--- | :--- | :--- | :--- |
| `command_name` | `str` | The internal command name. | `'setCurve'` |
| `param_name` | `str` | The parameter name this feature maps to. | `'slope'` |
| `required_params` | `List[str]` | List of all parameters required for this command. | `['slope', 'shift']` |
| `parent_feature_name` | `str` | Name of the parent feature (used for sibling lookups). | `'heating.circuits.0.heating.curve'` |
| `uri` | `str` | The API endpoint for this specific command. | `'.../features/heating.circuits.0...'` |
| `min` | `float` | Minimum allowed value (numeric). | `0.2` |
| `max` | `float` | Maximum allowed value (numeric). | `3.5` |
| `step` | `float` | Step increment (numeric). | `0.1` |
| `options` | `List[str]` | List of valid options (enum). | `['eco', 'comfort']` |
| `pattern` | `str` | Regex pattern for validation (string). | `'^[a-z]+$'` |
| `min_length` | `int` | Minimum string length. | `1` |
| `max_length` | `int` | Maximum string length. | `20` |

## CommandResponse

Result of a command execution (first element of tuple returned by `set_feature`).

| Property | Type | Description | Example |
| :--- | :--- | :--- | :--- |
| `success` | `bool` | `True` if the command succeeded. | `True` |
| `message` | `str` | Optional message from the API. | `'Command accepted'` |
| `reason` | `str` | Optional failure reason or details. | `'Feature not ready'` |

**Usage**:
```python
response, updated_device = await client.set_feature(device, feature, value)
if response.success:
    # Command succeeded, device is optimistically updated
    pass
```

## Next Steps

- **[Getting Started](01_getting_started.md)**: installation and basic usage.
- **[API Concepts](02_api_structure.md)**: understand the data-driven design.
- **[Authentication](03_auth_reference.md)**: setup tokens and sessions.
- **[Client Reference](05_client_reference.md)**: methods on `ViClient`.
- **[CLI Reference](06_cli_reference.md)**: terminal usage.
- **[Exceptions Reference](07_exceptions_reference.md)**: error handling.
