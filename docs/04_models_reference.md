# Models Reference

This section details the core data models used in the `vi_api_client` library.

Import public models and helpers from the package root:

```python
from vi_api_client import (
    CommandResponse,
    Device,
    Feature,
    FeatureControl,
    FeatureValue,
    Gateway,
    GatewayDeviceRefreshResult,
    Installation,
    JsonValue,
    ValidationDetail,
    format_feature,
    validate_json_value,
)
```

## Dynamic JSON values

`JsonValue` is the library's recursive JSON contract: `None`, `bool`, `int`,
`float`, `str`, `list[JsonValue]`, or `dict[str, JsonValue]`. `FeatureValue`
uses that same contract because a feature can report any of those shapes.
`ValidationDetail` is a dictionary-shaped `dict[str, JsonValue]` API validation
detail. The client preserves the normal Python list and dictionary shapes.

At a response or persisted-data boundary, `validate_json_value(value)` rejects
non-JSON Python objects with `ViResponseError`. Additional unknown object fields
remain representable; later response-specific validators decide which known
fields are required.

Discovery validates the known fields used to construct installation, gateway,
and device snapshots. Required identifiers accept documented string or integer
representations and are normalized to strings; malformed known fields raise
`ViResponseError`. Unknown additional API fields remain forward-compatible.

Feature reads apply the same boundary rule before flattening: each response
entry needs a non-empty feature name, JSON-object properties, object-shaped
commands, and boolean enabled/ready flags. Known command and parameter metadata
is validated before `FeatureControl` construction; unknown additional fields
remain allowed. This behavior is identical for live and fixture-backed reads.

Feature commands apply the same contract to their inputs: `set_feature`
target values and `execute_command` parameter values must be JSON values.
Non-JSON parameter values raise `ValueError` before any request is sent.

## Installation

Represents an installation site (House).

| Property | Type | Description | Example |
| :--- | :--- | :--- | :--- |
| `id` | `str` | Unique installation ID. | `'123456789'` |
| `description` | `str` | Description of the installation. | `'Home'` |
| `alias` | `str` | Alias name. | `'My House'` |
| `address` | `Mapping[str, JsonValue]` | Read-only address information. | `{'city': 'Berlin', ...}` |

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
Each `Device` is a device snapshot: an immutable representation of the device
and the features known when it was created. A snapshot with no features does
not indicate whether device feature hydration has occurred, because an API
feature response can validly be empty.

| Property | Type | Description | Example |
| :--- | :--- | :--- | :--- |
| `id` | `str` | Internal ID of the device (often "0"). | `'0'` |
| `installation_id` | `str` | ID of the installation site. | `'123456789'` |
| `gateway_serial` | `str` | Serial number of the communication gateway. | `'1234567890123456'` |
| `model_id` | `str` | Model name (e.g., "E3_Vitocal_16"). | `'E3_Vitocal_250A'` |
| `device_type` | `str` | Device type (e.g., "heating", "tcu"). | `'heating'` |
| `status` | `str` | Connection status (e.g., "Online"). | `'Online'` |
| `features` | `Sequence[Feature]` | Read-only features supported by this device. | `(Feature(...),)` |



## Feature

A feature is one flat, addressable device property with its reported value and
capabilities. A writable feature reports `is_writable=True` and has
`FeatureControl` metadata describing how a feature command can target it.

| Property | Type | Description | Example |
| :--- | :--- | :--- | :--- |
| `name` | `str` | Unique flat feature name. | `'heating.circuits.0.heating.curve.slope'` |
| `value` | `FeatureValue` | Primary value of the feature; scalar, null, list, or object. | `1.4` |
| `unit` | `str \| None` | Unit of measurement, when supplied. | `None` |
| `is_ready` | `bool` | Whether the data point is currently valid. | `True` |
| `is_enabled` | `bool` | Whether this feature is supported. | `True` |
| `is_writable` | `bool` | `True` if this feature can be modified. | `True` |
| `control` | `FeatureControl \| None` | Metadata for writing to this feature. | `FeatureControl(...)` |

### Formatting Values

To format a feature value for display with units:

```python
from vi_api_client import format_feature

print(format_feature(feature))  # "25.5 celsius"
```

## FeatureControl

If a `Feature` is writable (`is_writable=True`), it contains `FeatureControl`
command metadata describing how a feature command can modify it. `FeatureControl`
is not an executed command.

This object abstracts away the complexity of Viessmann Commands. You rarely interact with it directly, but it's useful for introspection (e.g. building a UI).

| Property | Type | Description | Example |
| :--- | :--- | :--- | :--- |
| `command_name` | `str` | The internal command name. | `'setCurve'` |
| `param_name` | `str` | The parameter name this feature maps to. | `'slope'` |
| `required_params` | `Sequence[str]` | Read-only parameters marked required by the API (or with no marker); explicitly optional parameters are excluded. | `('slope', 'shift')` |
| `parent_feature_name` | `str` | Name of the parent feature (used for sibling lookups). | `'heating.circuits.0.heating.curve'` |
| `uri` | `str` | The API endpoint for this specific command. | `'.../features/heating.circuits.0...'` |
| `min` | `float \| None` | Minimum allowed value (numeric). | `0.2` |
| `max` | `float \| None` | Maximum allowed value (numeric). | `3.5` |
| `step` | `float \| None` | Step increment (numeric). | `0.1` |
| `value_type` | `str \| None` | API command value type, e.g. `number`, `boolean`, or `string`. | `'number'` |
| `options` | `Sequence[JsonValue] \| None` | Read-only valid enum values. | `('eco', 'comfort')` |
| `pattern` | `str \| None` | Regex pattern for validation (string). | `'^[a-z]+$'` |
| `min_length` | `int \| None` | Minimum string length. | `1` |
| `max_length` | `int \| None` | Maximum string length. | `20` |

## CommandResponse

Result of a command execution. It is returned directly by `execute_command`
and as the first element of the tuple returned by `set_feature`.

| Property | Type | Description | Example |
| :--- | :--- | :--- | :--- |
| `success` | `bool` | `True` if the command succeeded. | `True` |
| `message` | `str \| None` | Optional message from the API. | `'Command accepted'` |
| `reason` | `str \| None` | Optional failure reason or details. | `'Feature not ready'` |

Command responses validate their known fields: `success` accepts the
documented boolean representations — a JSON boolean or a case-insensitive
`"true"`/`"false"` string — and normalizes them to a boolean. A missing or
otherwise malformed `success`, or a supplied `message` or `reason` that is
neither a string nor JSON `null`, raises `ViResponseError`; absent and
explicitly null optional text fields become `None`. Unknown additional fields
remain allowed. Responses may arrive as the root object or wrapped in a `data`
envelope.

**Usage**:
```python
response, updated_device = await client.set_feature(device, feature, value)
if response.success:
    # Command succeeded; this is a command-updated device snapshot.
    pass
```

## GatewayDeviceRefreshResult

Frozen result dataclass returned by `update_gateway_devices`. Its collection
attributes are immutable snapshots.

| Property | Type | Description |
| :--- | :--- | :--- |
| `updated_devices` | `Sequence[Device]` | Read-only new device instances that refreshed successfully, in their relative input order. |
| `errors_by_device_id` | `Mapping[str, ViError]` | Read-only recognized device-specific failures keyed by device ID. Failed original devices are not included in `updated_devices`. |
| `is_complete` | `bool` | `True` when no device-specific failures occurred. |

## InstallationEvent

Frozen dataclass for one installation event from the event history, as
returned by `EventHistoryPage.events`.

| Property | Type | Description | Example |
| :--- | :--- | :--- | :--- |
| `event_type` | `str` | Provider event type. | `'heating.circuits.0.heating.curve.changed'` |
| `created_at` | `str` | When the provider recorded the event. | `'2026-09-20T10:15:30.000Z'` |
| `event_timestamp` | `str` | When the event occurred. | `'2026-09-20T10:15:30.000Z'` |
| `gateway_serial` | `str \| None` | Serial of the reporting gateway, when known. | `'7630175843100101'` |
| `body` | `JsonValue` | The complete event body, exactly as reported. | `{'slope': 1.2, 'shift': 4}` |
| `fields` | `Mapping[str, JsonValue]` | Read-only complete event mapping, including unknown fields. | |

Known fields are validated at the API trust boundary: `eventType`,
`createdAt`, and `eventTimestamp` must be non-empty strings, and a supplied
`gatewaySerial`, `editedBy`, or `origin` must be a string while `audiences`
must be a list of strings. Malformed known fields raise `ViResponseError`. The `body` is
kept exactly as reported because its structure depends on the event type, and
unknown fields remain available through `fields`. The complete event is
validated against the recursive JSON value contract, so non-JSON nested values
reject as `ViResponseError`.

## EventHistoryPage

Frozen dataclass for one page of an installation's event history, as
returned by `ViClient.get_event_history`.

| Property | Type | Description |
| :--- | :--- | :--- |
| `events` | `Sequence[InstallationEvent]` | Read-only events of the requested page, in provider order. |
| `next_cursor` | `str \| None` | Opaque continuation cursor for the next page, when the provider reported one. |

A successful response must carry a `data` list of event objects; an optional
`cursor` object may carry a `next` string. The provider reports the final
page with an empty `next` string, which this library exposes as a `None`
`next_cursor`; a non-string `next` raises `ViResponseError`, as does any
other malformed envelope, event, or cursor. How far back the provider retains
events is not verified by this library; callers should treat an exhausted or
empty page as the end of the available window.

## Next Steps

- **[Getting Started](01_getting_started.md)**: installation and basic usage.
- **[API Concepts](02_api_structure.md)**: understand the data-driven design.
- **[Authentication](03_auth_reference.md)**: setup tokens and sessions.
- **[Client Reference](05_client_reference.md)**: methods on `ViClient`.
- **[CLI Reference](06_cli_reference.md)**: terminal usage.
- **[Exceptions Reference](07_exceptions_reference.md)**: error handling.
