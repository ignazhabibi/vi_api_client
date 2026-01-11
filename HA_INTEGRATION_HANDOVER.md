# Home Assistant Integration Implementation Guide

This guide is designed for the coding agent implementing the `vi_heat` (formerly `viessmann_hybrid`) integration using the `vi_api_client` library.

## 1. Architecture Strategy

We use the **DataUpdateCoordinator** pattern.
> [!IMPORTANT]
> Do NOT make individual API calls per sensor. This is inefficient.

### The Coordinator (`coordinator.py`)
-   **Poll Interval**: ~60 seconds.
-   **Fetch Method**: Use `client.get_full_installation_status(installation_id)`.
    -   This returns a `List[Device]`.
-   **Crucial**: Use `device.features_flat` instead of `device.features`!
    -   The library automatically "flattens" complex features (like heating curves or summaries) into multiple simple scalar features (e.g., `heating.curve.slope` and `heating.curve.shift`).
-   **Data Storage**: The coordinator's `data` should validly be a dictionary or object making lookup easy, e.g., mapping `device_id -> Device` object.

```python
# Pseudo-code for Coordinator update
async def _async_update_data(self):
    devices = await self.client.get_full_installation_status(self.installation_id)
    # Map by device ID for easy entity access
    return {d.id: d for d in devices}
```

## 2. Entity Implementation

Create a base entity class that inherits from `CoordinatorEntity` and `Entity`.

### `sensor.py`
The sensor platform should iterate over the devices in `coordinator.data` and create entities for specific supported features.

#### Key Library Methods to Use
-   `feature.name`: The string ID (e.g., `heating.sensors.temperature.outside`).
-   `feature.value`: The raw value.
-   `feature.formatted_value`: A string representation (useful for state attributes or debugging, but for sensors prefer raw value + device_class).
-   `feature.unit`: To verify unit of measurement.

#### Implementation Pattern
1.  **Define Mappings**: Create a constant `SENSOR_TYPES` mapping feature names to HA `SensorEntityDescription`.
2.  **Safety**: Always check if `feature.is_enabled` before creating/updating an entity.

### 3. Strategy: Dynamic Discovery (Gas vs Heat Pump)

This integration **must not** hardcode entity lists based on device models.
-   A **Gas Boiler** (Vitodens) has `heating.burner` features.
-   A **Heat Pump** (Vitocal) has `heating.compressor` features.

**Correct Approach:**
By iterating over `device.features_flat`, you only see what the API actually returns for that specific device.
-   If the API returns `heating.burner`, your loop matches it -> **Entity Created**.
-   If the API does *not* return it (Heat Pump), your loop never sees it -> **No Entity Created**.

This automatically solves the "Empty Entities" problem without complex model mapping logic.

## 4. Concrete Example: Outside Temperature

**Goal**: Implement `heating.sensors.temperature.outside`.

```python
from homeassistant.components.sensor import SensorEntity, SensorDeviceClass, SensorStateClass
from homeassistant.const import UnitOfTemperature
from .const import DOMAIN

class ViessmannSensor(CoordinatorEntity, SensorEntity):
    def __init__(self, coordinator, device_id, feature_name):
        super().__init__(coordinator)
        self._device_id = device_id
        self._feature_name = feature_name
        self._attr_unique_id = f"{device_id}_{feature_name}"
        # Set device info etc.

    @property
    def feature_data(self):
        """Retrieve the specific feature from coordinator data."""
        device = self.coordinator.data.get(self._device_id)
        if not device:
            return None
        # Use features_flat so we find 'heating.curve.slope' etc.
        return next((f for f in device.features_flat if f.name == self._feature_name), None)

    @property
    def native_value(self):
        feat = self.feature_data
        if feat:
             return feat.value # Now fully handled by library model!
        return None

# In setup_entry:
entities = []
for device_id, device in coordinator.data.items():
    # Iterate over FLATTENED features to find candidates
    for feature in device.features_flat:
        if feature.name == "heating.sensors.temperature.outside":
             entities.append(ViessmannSensor(coordinator, device_id, feature.name))
        
        # Example for Expanded Feature
        if feature.name == "heating.circuits.0.heating.curve.slope":
             entities.append(ViessmannSensor(coordinator, device_id, feature.name))
```

## 4. Special Handling: Analytics (Energy Dashboard)

For consumption data (Energy Dashboard), use the specialized helper `client.get_today_consumption(...)`. This method automatically handles the "Today 00:00 - 23:59" time window logic for you.

-   **Coordinator Strategy**: Create a separate `DataUpdateCoordinator` for analytics.
-   **Interval**: Poll less frequently (e.g., every 15-60 minutes). Analytics data is not "live" (often delayed by hours).
-   **Method**: `await client.get_today_consumption(gw, device, metric="summary")`
-   **Return Value**: Returns a `List[Feature]` containing 3 features:
    -   `analytics.heating.power.consumption.total`
    -   `analytics.heating.power.consumption.heating`
    -   `analytics.heating.power.consumption.dhw`
-   **Sensors**: Map these features directly to HA Sensors with `state_class: total_increasing` and `device_class: energy`.

> [!TIP]
> Do not mix `get_full_installation_status` (Live) and `get_today_consumption` (Analytics) in the same coordinator, API rate limits and response times differ.

## 5. Library Reference & HA Mapping

The library `vi_api_client` provides typed models. Here is how they map to Home Assistant concepts:

### `Device` Object
-   `device.id`: Maps to identifiers in `DeviceInfo`. This groups all entities under one device in HA.
-   `device.model_id`: Maps to `model` in `DeviceInfo` (and name usually combines Type + Model).
-   `device.features`: The source for generating Entity instances.

### `Feature` Object
-   `feature.name`: Used as part of the `unique_id` for entities (e.g., `{device_id}_{feature_name}`).
-   `feature.value`: The value to be used for `native_value`.
    -   *Note*: This can be a scalar (int, float, str, bool) OR a List (e.g. `[1, 2, 3]` for usage history).
    -   Home Assistant sensors usually expect a single state string. If `feature.value` is a list, you might want to:
        -   Store the last value as the state (`value[-1]`).
        -   Or store the full list in `extra_state_attributes`.
-   `feature.is_enabled`: Check this before creating an entity.

## 6. Testing Strategy

For the integration's own test suite (`tests/`), you MUST use the Mock Client to simulate devices.

### Why?
-   Ensures tests run offline (CI/CD friendly).
-   Guarantees deterministic data (Vitodens always returns same features).

### How?
In your `conftest.py` or test fixtures, instantiate the mock client instead of mocking `aiohttp` responses manually.

```python
# tests/conftest.py
import pytest
from vi_api_client import MockViessmannClient

@pytest.fixture
def mock_client():
    # Load a specific device scenario (e.g. Gas Boiler)
    return MockViessmannClient(device_name="Vitodens200W", auth=None)

# tests/test_sensor.py
async def test_sensor_creation(hass, mock_client):
    # Inject mock_client into your Coordinator
    coordinator = ViessmannCoordinator(hass, client=mock_client)
    await coordinator.async_refresh()
    
    # Assert
    state = hass.states.get("sensor.heating_sensors_temperature_outside")
    assert state.state == "16.7" # Known value from Vitodens200W.json
```

### Available Mock Scenarios
Use these names to test specific device types:

**Heat Pumps (Heat/Cool/Compressor Features):**
-   `Vitocal151A`, `Vitocal200`, `Vitocal252`, `Vitocal300G`

**Gas/Oil Boilers (Burner Features):**
-   `Vitodens050W`, `Vitodens200W`, `Vitodens300W`
-   `VitolaUniferral` (Oil/Older model)

**Ventilation:**
-   `Vitopure350`
