# API Structure & Design Concepts

This document explains the underlying structure of the Viessmann API and why `vi_api_client` is designed the way it is.

## 1. The Viessmann Hierarchy

The API groups data into a strict hierarchy:

1.  **Installation**: When connecting your heating system, you are registering a new installation. The installations contains your gateway and the device, which is the heating system itself.
2.  **Gateway**: (aka. wi-fi module) device that connects an HVAC installation to the cloud.
3.  **Device**: Device that is a part of the installation. A device is for example the heating system itself or room control elements.
4.  **Feature**: Object representing some part of gateway/device state. The feature contains commands.

The client automatically navigates this hierarchy. When you call `client.get_features()`, it resolves features for a specific device.

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

A `Feature` is more than just a value. It is a self-contained object describing a specific aspect of the gateway or device state.
    
### Feature Components
Feature names use dot convention (e.g., `heating.circuits.0.heating.schedule`), which is similar to namespaces. This means features can contain components.
*   **Heating Circuit**: The heating circuit ensures that all rooms in a house are comfortably warm. They are enumerated, beginning with 0, 1... (e.g., `heating.circuits.0`, `heating.circuits.1`).

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

## 4. Mental Model: Flat vs. Structured

The library offers two ways to look at the same data. Choosing the right one is key to effective usage.

### The "Inventory" vs. "Toolbox" Analogy

| View | Attribute | Analogy | Use Case |
| :--- | :--- | :--- | :--- |
| **Flat** | `device.features_flat` | **Inventory** | **Reading Values (Sensors)**. You want a list of everything the device knows (Temperature, Slope, Error Code). You don't care how it's organized. |
| **Structured** | `device.features` | **Toolbox** | **Interaction (Controls)**. You want to *do* something. You need the "Object" that holds the Command (`setCurve`) and the Context (Slope *and* Shift). |

### Code Example

**1. Reading (Flat)**
```python
# Just give me the value!
for f in device.features_flat:
    if f.name == "heating.circuits.0.heating.curve.slope":
        print(f"Slope: {f.value}")
```

**2. Writing (Structured)**
```python
# I need to change settings!
# 1. Get the PARENT feature (the toolbox)
feature = device.get_feature("heating.circuits.0.heating.curve")

# 2. Access sibling properties directly (Context)
current_slope = feature.properties["slope"]["value"]
current_shift = feature.properties["shift"]["value"]

# 3. Execute Command (needs both)
await client.execute_command(feature, "setCurve", {
    "slope": 1.6,
    "shift": current_shift
})
```

## Summary

*   **Don't look for specific Python methods** for every sensor.
*   **Look for Feature Names** (using `vi-client list-features`).
*   **Use the CLI** to explore what YOUR device supports.
