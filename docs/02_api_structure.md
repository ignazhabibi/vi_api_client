# API Structure & Design Concepts

This document explains the underlying structure of the Viessmann API and why `vi_api_client` is designed the way it is.

## 1. The Viessmann Hierarchy

The API groups data into a strict hierarchy:

1.  **Installation**: A physical location (e.g., "Home").
2.  **Gateway**: The communication module (e.g., Vitoconnect).
3.  **Device**: The actual heating appliance (e.g., Vitodens, Heat Pump).
4.  **Feature**: A specific data point (Sensor) or configuration (Parameter).

The client automatically navigates this hierarchy. When you call `client.get_features_models()`, it has already resolved the Installation and Gateway.

## 2. The "Data-Driven" Approach

Unlike traditional libraries that might have fixed methods like `get_boiler_temperature()`, this library is **data-driven**.

### Why?
Viessmann offers a vast array of devices: Gas boilers, Heat Pumps, Fuel cells, Hybrid systems.
*   A specific sensor feature name (e.g., `heating.sensors.temperature.outside`) might exist on 90% of devices, but `heating.burners.0.modulation` only exists on gas boilers.
*   New features are added by Viessmann constantly.

If we hardcoded methods, the library would constantly be out of date or broken for specific models.

### How it works
Instead / hardcoded logic, the client:
1.  **Asks the API**: "What features does this specific device have?"
2.  **Receives a list**: E.g., `["heating.circuits.0", "heating.dhw", ...]`
3.  **Exposes them**: You access features by their **Name**.

This means if your new Heat Pump has a feature `heating.compressors.0.statistics`, you can access it immediately without waiting for a library update.

## 3. Anatomy of a Feature

A `Feature` is more than just a value. It is a self-contained object describing a specific aspect of the device.

### Example: Outside Temperature

**Raw JSON (simplified):**
```json
{
  "feature": "heating.sensors.temperature.outside",
  "isEnabled": true,
  "isReady": true,
  "properties": {
    "temperature": { "value": 12.5, "unit": "celsius" }
  }
}
```

The library wraps this in a `Feature` object:
*   `feature.name`: `"heating.sensors.temperature.outside"`
*   `feature.value`: `12.5` (Automatic extraction of the primary value)
*   `feature.formatted_value`: `"12.5 celsius"`

### Example: Heating Curve (Complex Feature)

Some features have multiple properties and controls (commands).

**Raw JSON:**
```json
{
  "feature": "heating.circuits.0.heating.curve",
  "properties": {
    "slope": { "value": 1.4 },
    "shift": { "value": 0 }
  },
  "commands": {
    "setCurve": {
      "uri": "...",
      "params": {
        "slope": { "type": "number", "constraints": {"min": 0.2, "max": 3.5} },
        "shift": { "type": "number", "constraints": {"min": -13, "max": 40} }
      }
    }
  }
}
```

**In the Library:**
*   **Properties**: access properties via `feature.properties["slope"]["value"]` OR use `feature.expand()` to get two separate features (`...curve.slope` and `...curve.shift`).
*   **Commands**: `feature.commands["setCurve"]` allows you to modify the curve, automatically respecting the `min`/`max` constraints defined in the JSON.

## Summary

*   **Don't look for specific Python methods** for every sensor.
*   **Look for Feature Names** (using `vi-client list-features`).
*   **Use the CLI** to explore what YOUR device supports.
