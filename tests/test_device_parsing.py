
import pytest
from vi_api_client import MockViClient, ViNotFoundError

@pytest.mark.asyncio
async def test_mock_client_parsing(available_mock_devices):
    """Test that the MockClient can parse all provided sample files without error."""
    
    # Iterate over all available devices
    for device_name in available_mock_devices:
        client = MockViClient(device_name)
        
        # Test: Get Features (Returns Models now)
        features = await client.get_features(0, "mock", "0")
        assert len(features) > 0, f"{device_name}: No features found"
        
        # Test 3: Flattening
        # Ensure expand() works without crashing on any feature of any device
        flat_count = 0
        for f in features:
            expanded = f.expand()
            flat_count += len(expanded)
            # Basic sanity check on values
            for item in expanded:
                # Accessing formatted_value triggers value/unit logic
                assert isinstance(item.formatted_value, str)
                
        print(f"Device {device_name}: {len(features)} feature models -> {flat_count} flat features")

@pytest.mark.asyncio
async def test_mock_specific_feature():
    """Test fetching a specific feature from the mock."""
    # We know Vitodens200W has 'heating.burner'
    client = MockViClient("Vitodens200W")
    
    feature = await client.get_feature(0, "mock", "0", "heating.burner")
    assert feature is not None
    assert feature.get("feature") == "heating.burner"
    
    # Test missing feature
    with pytest.raises(ViNotFoundError):
        await client.get_feature(0, "mock", "0", "non_existent.feature")

@pytest.mark.asyncio
async def test_mock_device_list():
    """Test getting the device list."""
    client = MockViClient("Vitocal200")
    devices = await client.get_devices(0, "mock")
    
    assert len(devices) == 1
    assert devices[0].model_id == "Vitocal200"

@pytest.mark.asyncio
async def test_feature_filtering_and_expansion():
    """Test that empty features are filtered and schedules are expanded."""
    client = MockViClient("Vitodens200W")
    features_models = await client.get_features(0, "mock", "0")
    
    # Flatten features manually to inspect
    flat_features = []
    for f in features_models:
        flat_features.extend(f.expand())
        
    flat_names = [f.name for f in flat_features]
    
    # 1. Test Filtering: 'heating.operating' (pure structure) should be GONE
    assert "heating.operating" not in flat_names, "Empty structural feature was not filtered out"
    
    # 2. Test Expansion: 'heating.circuits.0.heating.schedule' should NOT be expanded 
    # because 'active' and 'entries' are both priority keys.
    # It should appear as a single feature 'heating.circuits.0.heating.schedule'
    schedule_feat = next((f for f in flat_features if f.name == "heating.circuits.0.heating.schedule"), None)
    
    assert schedule_feat is not None, "Schedule feature missing"
    # value should extract 'active' (boolean) or 'entries' depending on priority
    # active is higher priority than entries in VALUE_PRIORITY_KEYS
    # So value should probably be the active state.
    # Let's check if we can access the underlying properties if needed
    assert "active" in schedule_feat.properties
    assert "entries" in schedule_feat.properties

@pytest.mark.asyncio
async def test_redundant_suffix_removal():
    """Test that redundant suffixes (e.g. name.name) are removed."""
    # Vitocal252 has 'heating.circuits.0.name' with property 'name'
    client = MockViClient("Vitocal252")
    features_models = await client.get_features(0, "mock", "0")
    
    flat_features = []
    for f in features_models:
        flat_features.extend(f.expand())
    
    flat_names = [f.name for f in flat_features]
    
    # Check for the clean name
    assert "heating.circuits.0.name" in flat_names, "Clean name feature missing"
    # Check that the redundant one is GONE
    assert "heating.circuits.0.name.name" not in flat_names, "Redundant name.name feature present"
