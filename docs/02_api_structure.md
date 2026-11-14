# API Structure & Design Concepts

This document explains the underlying structure of the Viessmann API and how `vi_api_client` simplifies it using the **Flat Architecture**.

## 1. The Viessmann Hierarchy

The API groups data into a strict hierarchy:

1.  **Installation**: Represents your physical location (House). Contains gateways.
2.  **Gateway**: The bridge between your boiler and the cloud.
3.  **Device**: The actual components (Heating System, Fuel Cell, Ventilation).
4.  **Feature**: A single data point or control capability (e.g., "Outside Temperature").

## 2. The "Data-Driven" Approach

Unlike traditional libraries that might have fixed methods like `get_boiler_temperature()`, this library is **data-driven**.

### Why?
Viessmann offers a vast array of devices: Gas boilers, Heat Pumps, Fuel cells, Hybrid systems.
*   A specific sensor feature name (e.g., `heating.sensors.temperature.outside`) might exist on 90% of devices, but `heating.burners.0.modulation` only exists on gas boilers.
*   New features are added by Viessmann constantly.

If we hardcoded methods, the library would constantly be out of date.

### How it works
Instead of hardcoded logic, the client:
1.  **Asks the API**: "What features does this device have?"
2.  **Receives a raw JSON**: Often complex and nested.
3.  **Flattens it**: The library converts the complex JSON into a simple list of `Feature` objects.

## 3. The Flat Architecture

### The Power of Flat Features

Complex device capabilities are exposed as individual, addressable Features.

For example, a "Heating Curve" is not a nested object you have to parse, but rather two independent features you can interact with directly.

**Before** — this is what the Viessmann API returns for the heating curve of a Vitocal 250-A:

```json
{
  "feature": "heating.circuits.0.heating.curve",
  "properties": {
    "slope": { "type": "number", "value": 0.6, "unit": "" },
    "shift": { "type": "number", "value": 6, "unit": "" }
  },
  "commands": {
    "setCurve": {
      "uri": "https://api.viessmann-climatesolutions.com/.../commands/setCurve",
      "params": {
        "slope": { "type": "number", "required": true, "constraints": { "min": 0.2, "max": 3.5, "stepping": 0.1 } },
        "shift": { "type": "number", "required": true, "constraints": { "min": -13, "max": 40, "stepping": 1 } }
      }
    }
  }
}
```

**After** — the library flattens this into two features and writes them with one call each:

```python
device.get_feature("heating.circuits.0.heating.curve.slope").value  # 0.6

device = await client.set_feature(device, "heating.circuits.0.heating.curve.slope", 0.7)
```

The API requires both `slope` and `shift` on every write. The library resolves
the required sibling parameter itself (the current `shift` stays `6`) and
validates the value against the reported constraints before sending. That is
the benefit of the library's internal complexity: you only ever see flat
features and one `set_feature` call.

### Example: Outside Temperature

A simple sensor becomes a single Feature:
*   **Name**: `heating.sensors.temperature.outside`
*   **Value**: `3.1`
*   **Type**: Read-only sensor

## 4. Anatomy of a Feature

A `Feature` object in this library represents a single property. The table
below lists the attributes you use every day; the subsections that follow cover
the details (deprecations, constraints, command rules), and the
[Models Reference](04_models_reference.md) documents the same model in full.

| Attribute | Description |
| :--- | :--- |
| `name` | Unique identifier (e.g., `heating.sensors.temperature.outside`) |
| `value` | The current value as a `FeatureValue` (e.g., `3.1` or `"on"`), following the recursive `JsonValue` contract |
| `unit` | Unit of measurement, if any (`celsius`, `bar`, `percent`) |
| `is_writable` | `True` if the API offers a command that changes this value; writing also needs `is_enabled` and `is_ready` |
| `constraints` | (Optional) `FeatureConstraints` rules for writing this feature's value |
| `deprecation` | (Optional) `FeatureDeprecation` removal notice the API reports for the feature's API feature |

Responses are validated at the client's trust boundary: known malformed
fields raise `ViResponseError`, while unknown additional fields from newer API
versions remain allowed. See the [Models Reference](04_models_reference.md)
for the full value contract.

### Removal notices

Viessmann announces the removal of an API feature in the feature response
itself, as a `deprecated` object next to `properties` and `commands`:

```json
"deprecated": {
  "removalDate": "2024-09-15",
  "info": "replaced by heating.dhw.sensors.temperature.dhwCylinder"
}
```

Every flat feature parsed from such an API feature carries the notice as
`feature.deprecation` (`FeatureDeprecation` with `removal_date` and `info`);
other features carry `None`:

```python
feature = device.get_feature("heating.dhw.sensors.temperature.hotWaterStorage")
if feature.deprecation is not None:
    print(feature.deprecation.removal_date)  # datetime.date(2024, 9, 15)
    print(feature.deprecation.info)  # "replaced by heating.dhw.sensors..."
```

The library only reports the notice. It does not act on it: a deprecated
feature is read, written, and listed like any other, also after its removal
date, and no warning is logged. `info` is the API's free text, passed on
verbatim; the library does not read the replacement name out of it and does
not map old feature names to new ones. The removal date is an announcement,
not a guarantee: devices still report features whose date has passed. A
deprecated API feature without properties produces no flat feature, so its
notice is not visible.

### Constraints and reported commands

A writable feature has a `.constraints` attribute (`FeatureConstraints`) with
the rules for writing its value:

*   `min` / `max` / `step`: Numerical constraints
*   `options`: List of allowed values (Enum)
*   `min_length` / `max_length`: String length constraints
*   `pattern`: Regex pattern for validation
*   `value_type`: API command value type (e.g., `number`, `boolean`, `string`)
*   `schedule`: `ScheduleConstraints` with the rules for writing a schedule, if the API reports them

How a command is sent is internal to the library: a writable feature carries
an internal write command (command, parameter, required parameters, URI) that
is not part of the public model, and every command an API feature reports is
listed on the device through
`Device.get_command_names("heating.circuits.0.heating.curve")`.

### Only setter commands write a value

A command binds a value to a feature only when its name starts with `set`,
such as `setCurve` or `setTemperature`. Other commands also take parameters
that match properties, but they do more than store a value: `schedule` plans a
holiday, `activate` on `device.time.daylightSaving` switches daylight saving
on, and `triggerDaily` turns a weekly hygiene run into a daily one. Their
features are read-only (`is_writable` is `False`, `constraints` is `None`),
for example `heating.operating.programs.holiday.start` and `.end`,
`device.time.daylightSaving.begin` and `.end` on devices without `setActive`,
and `heating.dhw.hygiene.trigger.startHour` and `.startMinute`.

These commands stay in the device command list and are sent with
`run_command`, where the caller names the command and supplies every
parameter:

```python
device.get_command_names("heating.operating.programs.holiday")
# ('changeEndDate', 'schedule', 'unschedule')

await client.run_command(
    device,
    "heating.operating.programs.holiday",
    "schedule",
    {"start": "2026-12-24", "end": "2026-12-31"},
)
```

`set_feature` on such a feature raises `ValueError`; the message names
`run_command` and the commands its API feature reports. The `set` prefix is
the only place where a command name decides something; the on/off command
pairs below come from an explicit table.

### Boolean features written through an on/off command pair

Some devices change a boolean only through two commands without parameters,
for example `enable` and `disable` for `heating.dhw.hygiene.enabled`, while
other devices offer a parameter setter such as `setEnabled` for the same
feature. The library hides the difference:
`set_feature(device, "heating.dhw.hygiene.enabled", True)` works on both.

Which command pairs count is an explicit table in the library, not a rule
over command names:

| API feature | Property | On command | Off command |
| :--- | :--- | :--- | :--- |
| `heating.dhw.oneTimeCharge` | `active` | `activate` | `deactivate` |
| `heating.dhw.hygiene` | `enabled` | `enable` | `disable` |
| `heating.circuits.<n>.operating.programs.comfort` | `active` | `activate` | `deactivate` |
| `heating.circuits.<n>.operating.programs.eco` | `active` | `activate` | `deactivate` |
| `heating.circuits.<n>.operating.programs.forcedLastFromSchedule` | `active` | `activate` | `deactivate` |
| `ventilation.quickmodes.<name>` | `active` | `activate` | `deactivate` |

A table entry makes the feature writable when all of these hold:

1.  The property is reported with a boolean value.
2.  Both commands are reported, and neither has a required parameter. An
    optional parameter, such as `temperature` on the comfort program's
    `activate`, does not count and is never sent.
3.  No parameter setter writes the property. A setter always wins, so a
    device that offers both is written through the setter.

Such a feature has `FeatureConstraints(value_type="boolean")`. `True` sends
the on command and `False` the off command, each with an empty JSON object.

The `isExecutable` flags the API reports for the two commands are not part of
the rule. They follow the device state, so a rule based on them would let a
feature change between writable and read-only from one refresh to the next.
The library also does not check locally whether the device allows the
requested direction right now: the API decides, and a command it does not
allow raises `ViCommandRejectedError`. A device can report a pair it never
allows; writing such a feature then fails on every attempt.

## 5. Usage Pattern

Because of this flat design, usage is consistent:

**1. Reading (Get Features)**
```python
# Get a specific feature by name (e.g. heating curve slope)
features = await client.get_features(
    device,
    feature_names=["heating.circuits.0.heating.curve.slope"],
)
print(features[0].value)
# Output: 0.6
```

**2. Writing (Set Feature)**
```python
# Set a feature value directly
feature = features[0]
device = await client.set_feature(device, feature, 0.7)
# No exception means the API accepted the write; use the updated device for
# the next operation.
```

The library handles the "magic" of mapping your simple `0.7` value back to the complex JSON structure required by the API.

## 6. Gateway-Scoped Device Refresh

When several known devices belong to the same installation and gateway,
`refresh_gateway_devices(devices)` refreshes their available features
with one gateway-scoped bulk feature fetch in the normal case. Returned feature
URIs are matched to complete, URL-decoded device-ID path segments. Gateway-owned
features and features for devices outside the requested set are not added to a
device.

The operation returns successful devices separately from device-specific
errors. A missing device is retried through its existing per-device endpoint;
global failures still abort the operation. Existing discovery and single-device
methods keep their established behavior. The client remains cache-free, so the
consumer owns polling cadence and stale-state policy.

## Summary

*   **Everything is a Feature**: Scalar properties are exposed as addressable `Feature` objects.
*   **Flat Names**: Use full names like `heating.circuits.0.heating.curve.slope`.
*   **Simple Set**: Use `set_feature(device, feature, value)`.
*   **Explicit Multi-Device Refresh**: Use `refresh_gateway_devices` for known devices behind one gateway.

## Next Steps

- **[Getting Started](01_getting_started.md)**: installation and basic usage.
- **[Authentication](03_auth_reference.md)**: setup tokens and sessions.
- **[Models Reference](04_models_reference.md)**: detailed documentation of `Feature`, `FeatureConstraints`, `Device`, and command outcomes.
- **[Client Reference](05_client_reference.md)**: methods on `ViClient`.
- **[CLI Reference](06_cli_reference.md)**: terminal usage.
- **[Exceptions Reference](07_exceptions_reference.md)**: error handling.
