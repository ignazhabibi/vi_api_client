# Models Reference

This section details the core data models used in the `vi_api_client` library.

Import public models and helpers from the package root:

```python
from vi_api_client import (
    Device,
    EventHistoryPage,
    EventHistoryWindow,
    Feature,
    FeatureConstraints,
    FeatureDeprecation,
    FeatureValue,
    Gateway,
    GatewayDeviceRefreshResult,
    Installation,
    InstallationEvent,
    JsonValue,
    ScheduleConstraints,
    ValidationDetail,
    format_feature,
    redact_device,
    redact_feature,
    redact_sensitive,
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
is validated before the write binding and command list are built; unknown additional fields
remain allowed. This behavior is identical for live and fixture-backed reads.

Feature commands apply the same contract to their inputs: `set_feature`
target values and `run_command` parameter values must be JSON values.
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

### `get_command_names(feature_name: str) -> tuple[str, ...]`

Returns the sorted names of every command the API feature of that name
reported in the snapshot, or an empty tuple. `feature_name` is the API feature
name, such as `heating.operating.programs.holiday`; unknown names return `()`.
The list includes commands the parser binds to no feature and commands of API
features without properties, and it never exposes whether a command is
executable or deprecated: the library sends the command and lets the API
decide.

```python
device.get_command_names("heating.operating.programs.holiday")
# ("changeEndDate", "schedule", "unschedule")
```

The command list travels with each snapshot-building read: `refresh_device`,
`refresh_gateway_devices` including its individual fallback, and
`get_full_installation_status`.

## Feature

A feature is one flat, addressable device property with its reported value and
capabilities. A writable feature reports `is_writable=True` and carries
`FeatureConstraints` rules describing how its value may be written.

`is_writable` says that the API offers a command for the feature. It stays
`True` while the feature is disabled or not ready, so a writable feature is a
stable capability, not a promise that a write succeeds right now. Check
`is_available` or `is_settable` before writing; `set_feature` raises
`ValueError` otherwise.

Which command the library sends for a writable feature is internal command
knowledge, not public API: the feature carries an internal write binding, so
consumers cannot construct writable features by hand. Consumers get writable
features from a client read and may copy them with `dataclasses.replace`.

A feature is writable in one of two ways. Usually a command parameter writes
its value. A boolean feature without such a parameter setter is writable when
its API feature reports one of the on/off command pairs in the library's
table, for example `enable` and `disable` for `heating.dhw.hygiene.enabled`;
it then carries `FeatureConstraints(value_type="boolean")`. The reported
`isExecutable` flags of a pair do not decide writability, so the feature does
not switch between writable and read-only as the device state changes; the
API decides whether a write is allowed. See
[Boolean features written through an on/off command pair](02_api_structure.md#boolean-features-written-through-an-onoff-command-pair)
for the table and its rules.

| Property | Type | Description | Example |
| :--- | :--- | :--- | :--- |
| `name` | `str` | Unique flat feature name. | `'heating.circuits.0.heating.curve.slope'` |
| `value` | `FeatureValue` | Primary value of the feature; scalar, null, list, or object. | `1.4` |
| `unit` | `str \| None` | Unit of measurement, when supplied. | `None` |
| `is_ready` | `bool` | Whether the data point is currently valid. | `True` |
| `is_enabled` | `bool` | Whether this feature is supported. | `True` |
| `is_available` | `bool` | `True` if the feature is enabled and ready. | `True` |
| `is_writable` | `bool` | `True` if the API offers a command that changes this feature. | `True` |
| `is_settable` | `bool` | `True` if the feature is writable and available, so `set_feature` would accept it in this snapshot. | `True` |
| `constraints` | `FeatureConstraints \| None` | Rules for writing this feature's value; every writable feature has them. | `FeatureConstraints(min=0.2)` |
| `deprecation` | `FeatureDeprecation \| None` | Removal notice the API reports for the feature's API feature. | `FeatureDeprecation(date(2024, 9, 15), 'replaced by ...')` |

### Formatting Values

To format a feature value for display with units:

```python
from vi_api_client import format_feature

print(format_feature(feature))  # "25.5 celsius"
```

## FeatureDeprecation

Frozen dataclass for the removal notice the API reports for an API feature in
its `deprecated` object. Every flat feature parsed from that API feature
carries it as `Feature.deprecation`, including the single feature of a
schedule or time series and the synthetic consumption aliases.

| Property | Type | Description | Example |
| :--- | :--- | :--- | :--- |
| `removal_date` | `datetime.date \| None` | The announced removal date, from `removalDate`. | `date(2024, 9, 15)` |
| `info` | `str \| None` | The API's free text about the removal, verbatim. | `'replaced by heating.dhw.sensors.temperature.dhwCylinder'` |

The notice is information only. The library neither acts on it nor maps
names: a deprecated feature is read, written, and listed like any other, also
after its removal date, no warning is logged, and the replacement name is not
read out of `info`. Devices still report features whose removal date has
passed, so treat the date as an announcement. The notice takes part in
`Feature` equality and `repr`, survives `dataclasses.replace` and the
command-updated snapshot of `set_feature`, and is not redacted because it
carries no installation, gateway, or device identifier.

When present, `deprecated` must be an object, `removalDate` an ISO calendar
date string (`YYYY-MM-DD`), and `info` a string; a violation raises
`ViResponseError`. A missing field becomes `None`, and unknown additional
keys are ignored. The command flag `isDeprecated` is validated but neither
stored nor exposed.

## FeatureConstraints

If a `Feature` is writable (`is_writable=True`), it carries `FeatureConstraints`
with the rules the API reports for writing its value. Missing rules are `None`
and are not checked client-side. Any field the API does not report is `None`.

| Property | Type | Description | Example |
| :--- | :--- | :--- | :--- |
| `min` | `float \| None` | Minimum allowed value (numeric). | `0.2` |
| `max` | `float \| None` | Maximum allowed value (numeric). | `3.5` |
| `step` | `float \| None` | Step increment (numeric). | `0.1` |
| `value_type` | `str \| None` | API command value type, e.g. `number`, `boolean`, or `string`. | `'number'` |
| `options` | `Sequence[JsonValue] \| None` | Read-only valid enum values. | `('eco', 'comfort')` |
| `pattern` | `str \| None` | Regex the whole string value must match. | `'^[a-z]+$'` |
| `min_length` | `int \| None` | Minimum string length. | `1` |
| `max_length` | `int \| None` | Maximum string length. | `20` |
| `schedule` | `ScheduleConstraints \| None` | Rules for writing a schedule, if the API reports them. | `ScheduleConstraints(max_entries=4, ...)` |

## ScheduleConstraints

Frozen rules the API reports for a `Schedule` command parameter. Any field the
API does not report is `None`, and the matching rule is not checked.

| Property | Type | Description | Example |
| :--- | :--- | :--- | :--- |
| `max_entries` | `int \| None` | Most time slots allowed per day. | `4` |
| `modes` | `Sequence[str] \| None` | Read-only modes a slot may use. | `('normal', 'comfort')` |
| `resolution` | `int \| None` | Minute grid that slot times must fall on. | `10` |
| `overlap_allowed` | `bool \| None` | Whether slots on one day may overlap. | `False` |
| `default_mode` | `str \| None` | Mode the device uses outside all slots. | `'reduced'` |

A schedule feature's value is the weekly plan itself, a mapping from `mon` to
`sun` to lists of slots such as
`{"start": "06:00", "end": "22:00", "mode": "normal", "position": 0}`. Whether
the plan is currently in effect is the separate read-only feature
`<schedule name>.active`. `set_feature` takes a plan of the same shape and
checks it before sending: the plan must contain all seven days `mon` to `sun`
(a day without slots is an empty list), because the API replaces the whole
plan; times must be `HH:MM` up to
`24:00` on the reported grid, each slot must start before it ends, and the
slot count, modes, and overlaps must follow the reported rules. A violation
raises `ValueError` without a request.

## Command outcomes

A command is not answered with a model. `set_feature` returns the
command-updated `Device` snapshot and `run_command` returns nothing; a
command the API rejects raises `ViCommandRejectedError` with the rejection
`message` and `reason`, so success always means that no exception was raised.

Command responses validate their known fields at the response boundary:
`success` accepts the documented boolean representations — a JSON boolean or
a case-insensitive `"true"`/`"false"` string — and a missing or otherwise
malformed `success`, or a supplied `message` or `reason` that is neither a
string nor JSON `null`, raises `ViResponseError`. Responses may arrive as the
root object or wrapped in a `data` object.

**Usage**:
```python
device = await client.set_feature(device, feature, value)
# Reaching this line means the API accepted the command.
```

## GatewayDeviceRefreshResult

Frozen result dataclass returned by `refresh_gateway_devices`. Its collection
attributes are immutable snapshots.

| Property | Type | Description |
| :--- | :--- | :--- |
| `refreshed_devices` | `Sequence[Device]` | Read-only new device instances that refreshed successfully, in their relative input order. |
| `errors_by_device_id` | `Mapping[str, ViError]` | Read-only recognized device-specific failures keyed by device ID. Failed original devices are not included in `refreshed_devices`. |
| `is_complete` | `bool` | `True` when no device-specific failures occurred. |

## InstallationEvent

Frozen dataclass for one installation event from the event history, as
returned by `EventHistoryPage.events`.

| Property | Type | Description | Example |
| :--- | :--- | :--- | :--- |
| `event_type` | `str` | Provider event type. | `'heating.circuits.0.heating.curve.changed'` |
| `created_at` | `str` | When the provider recorded the event. | `'2026-09-20T10:15:30.000Z'` |
| `event_timestamp` | `str` | When the event occurred. | `'2026-09-20T10:15:30.000Z'` |
| `gateway_serial` | `str \| None` | Serial of the reporting gateway, when known. | `'1234567890123456'` |
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
other malformed response, event, or cursor. How far back the provider retains
events is not verified by this library; callers should treat an exhausted or
empty page as the end of the available window.

## EventHistoryWindow

Frozen dataclass for the events of one lookback window collected across its
pages, as returned by `ViClient.get_event_history_window`.

| Property | Type | Description |
| :--- | :--- | :--- |
| `events` | `Sequence[InstallationEvent]` | Read-only events of every fetched page, in the order the pages returned them. |
| `next_cursor` | `str \| None` | The continuation cursor that remained when the traversal stopped at its page safety limit; `None` when the provider reported no further page. |
| `pages_fetched` | `int` | Number of pages the traversal requested. |
| `earliest_timestamp` | `str \| None` | Earliest `event_timestamp` in the window, or `None` without events. |
| `latest_timestamp` | `str \| None` | Latest `event_timestamp` in the window, or `None` without events. |

A window is complete when `next_cursor` is `None`. A remaining cursor means the
safety limit stopped the traversal; pass it to `get_event_history` as `cursor`
to continue. The two timestamp properties compare timestamps as points in
time, so differing ISO-8601 offsets or precision still order correctly; if a
provider timestamp cannot be parsed, the whole window falls back to a lexical
comparison. A complete window reports exhausted pagination, not proven
provider retention of the requested lookback.

## Next Steps

- **[Getting Started](01_getting_started.md)**: installation and basic usage.
- **[API Concepts](02_api_structure.md)**: understand the data-driven design.
- **[Authentication](03_auth_reference.md)**: setup tokens and sessions.
- **[Client Reference](05_client_reference.md)**: methods on `ViClient`.
- **[CLI Reference](06_cli_reference.md)**: terminal usage.
- **[Exceptions Reference](07_exceptions_reference.md)**: error handling.
