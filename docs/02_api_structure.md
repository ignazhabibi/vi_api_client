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

For example, a "Heating Curve" is not a nested object you have to parse, but rather two independent features you can interact with directly:


### Example: Outside Temperature

A simple sensor becomes a single Feature:
*   **Name**: `heating.sensors.temperature.outside`
*   **Value**: `12.5`
*   **Type**: Read-only sensor

### Example: Heating Curve (Complex became Simple)

The complex "Heating Curve" object is split into independent features:

1.  **Slope Feature**:
    *   **Name**: `heating.circuits.0.heating.curve.slope`
    *   **Value**: `1.4`
    *   **Control**: Writable (Command: `setCurve`, Param: `slope`)

2.  **Shift Feature**:
    *   **Name**: `heating.circuits.0.heating.curve.shift`
    *   **Value**: `0`
    *   **Control**: Writable (Command: `setCurve`, Param: `shift`)

**Benefit**: You don't need to know that "Slope" and "Shift" belong to the same parent object. You just set the slope, and the library handles the underlying complexity (finding the parent command, resolving parameters) for you.

## 4. Anatomy of a Feature

A `Feature` object in this library represents a single property.

| Attribute | Description |
| :--- | :--- |
| `name` | Unique identifier (e.g., `heating.sensors.temperature.outside`) |
| `value` | The current value (e.g., `12.5` or `"on"`) |
| `unit` | Unit of measurement, if any (`celsius`, `bar`, `percent`) |
| `is_writable` | `True` if this value can be changed |
| `control` | (Optional) Contains details on how to write to this feature |

### The `control` Object

If a feature is writable, it has a `.control` attribute with metadata:

*   `command_name`: The internal command to send (e.g., `setCurve`)
*   `param_name`: The parameter this feature maps to (e.g., `slope`)
*   `required_params`: List of **all** parameters required by this command, including `param_name` (e.g., `['slope', 'shift']`)
*   `parent_feature_name`: Name of the parent feature, used to resolve sibling dependencies (e.g., `heating.circuits.0.heating.curve`)
*   `uri`: The API URI endpoint for executing the command
*   `min` / `max` / `step`: Numerical constraints
*   `options`: List of allowed values (Enum)
*   `min_length` / `max_length`: String length constraints
*   `pattern`: Regex pattern for validation

## 5. Usage Pattern

Because of this flat design, usage is consistent:

**1. Reading (Get Features)**
```python
# Get a specific feature by name (e.g. heating curve slope)
features = await client.get_features(device, ["heating.circuits.0.heating.curve.slope"])
print(features[0].value)
# Output: 1.4
```

**2. Writing (Set Feature)**
```python
# Set a feature value directly
feature = features[0]
response, device = await client.set_feature(device, feature, 1.6)
if response.success:
    # Use updated device for next operation
    pass
```

The library handles the "magic" of mapping your simple `1.6` value back to the complex JSON structure required by the API.

## Summary

*   **Everything is a Feature**: No more "Properties" vs "Features".
*   **Flat Names**: Use full names like `heating.circuits.0.heating.curve.slope`.
*   **Simple Set**: Use `set_feature(device, feature, value)`.

## Next Steps

- **[Getting Started](01_getting_started.md)**: installation and basic usage.
- **[Authentication](03_auth_reference.md)**: setup tokens and sessions.
- **[Models Reference](04_models_reference.md)**: detailed documentation of `Feature`, `Device`, and `Command`.
- **[Client Reference](05_client_reference.md)**: methods on `ViClient`.
- **[CLI Reference](06_cli_reference.md)**: terminal usage.
- **[Exceptions Reference](07_exceptions_reference.md)**: error handling.
