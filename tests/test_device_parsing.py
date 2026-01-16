
import pytest
from vi_api_client import MockViClient, ViNotFoundError

@pytest.mark.asyncio
async def test_mock_client_parsing(available_mock_devices):
    """Test that the MockClient can parse all provided sample files without error."""
    
    # Iterate over all available devices
    for device_name in available_mock_devices:
        client = MockViClient(device_name)
        
        # Test: Get Features (Returns Models now)
        from vi_api_client.models import Device
        device = Device(id="0", gateway_serial="mock", installation_id="0", model_id=device_name, device_type="heating", status="ok")
        features = await client.get_features(device)
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
                from vi_api_client.utils import format_feature
                assert isinstance(format_feature(item), str)
                
        print(f"Device {device_name}: {len(features)} feature models -> {flat_count} flat features")

@pytest.mark.asyncio
async def test_mock_specific_feature():
    """Test fetching a specific feature from the mock."""
    # We know Vitodens200W has 'heating.burner'
    client = MockViClient("Vitodens200W")
    
    client = MockViClient("Vitodens200W")
    from vi_api_client.models import Device
    device = Device(id="0", gateway_serial="mock", installation_id="0", model_id="Vitodens200W", device_type="heating", status="ok")
    
    feature = await client.get_feature(device, "heating.burner")
    assert feature is not None
    assert feature.name == "heating.burner"
    
    # Test missing feature
    with pytest.raises(ViNotFoundError):
        await client.get_feature(device, "non_existent.feature")

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
    from vi_api_client.models import Device
    device = Device(id="0", gateway_serial="mock", installation_id="0", model_id="Vitodens200W", device_type="heating", status="ok")
    features_models = await client.get_features(device)
    
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
    from vi_api_client.models import Device
    device = Device(id="0", gateway_serial="mock", installation_id="0", model_id="Vitocal252", device_type="heating", status="ok")
    features_models = await client.get_features(device)
    
    flat_features = []
    for f in features_models:
        flat_features.extend(f.expand())
    
    flat_names = [f.name for f in flat_features]
    
    # Check for the clean name
    assert "heating.circuits.0.name" in flat_names, "Clean name feature missing"
    # Check that the redundant one is GONE
    assert "heating.circuits.0.name.name" not in flat_names, "Redundant name.name feature present"

# =============================================================================
# Fixture Coverage Tests
# =============================================================================

@pytest.mark.asyncio
async def test_all_features_produce_flat_output(available_mock_devices):
    """Ensure every feature with properties produces at least one flat sensor.
    
    This test validates that our expand() logic handles all real API data correctly.
    Structural features (empty properties) are excluded - they're just containers.
    """
    from vi_api_client import MockViClient
    from vi_api_client.models import Device
    
    problematic = []
    
    for device_name in available_mock_devices:
        client = MockViClient(device_name)
        device = Device(
            id="0", gateway_serial="mock", installation_id="0",
            model_id=device_name, device_type="heating", status="ok"
        )
        features = await client.get_features(device)
        
        for f in features:
            # Skip structural features (no properties = just a container)
            if not f.properties:
                continue
                
            # Skip disabled features
            if not f.is_enabled:
                continue
            
            # A feature with properties should either have a value OR expand
            has_value = f.value is not None
            expanded = f.expand()
            can_expand = len(expanded) > 0
            
            if not has_value and not can_expand:
                problematic.append(f"{device_name}:{f.name} (props: {list(f.properties.keys())})")
    
    if problematic:
        msg = f"Features with properties but no value and no expansion ({len(problematic)}):\n"
        for item in problematic[:10]:
            msg += f"  - {item}\n"
        if len(problematic) > 10:
            msg += f"  ... and {len(problematic)-10} more\n"
        msg += "\nThis may indicate a new property key type not handled by expand()."
        pytest.fail(msg)


@pytest.mark.asyncio
async def test_flat_features_have_extractable_values(available_mock_devices):
    """Ensure all flattened features have an extractable value.
    
    After expand(), each sub-feature should have a non-None .value.
    This validates that our flattening logic produces usable sensors.
    """
    from vi_api_client import MockViClient
    from vi_api_client.models import Device
    
    problematic = []
    
    for device_name in available_mock_devices:
        client = MockViClient(device_name)
        device = Device(
            id="0", gateway_serial="mock", installation_id="0",
            model_id=device_name, device_type="heating", status="ok"
        )
        features = await client.get_features(device)
        
        for f in features:
            if not f.is_enabled:
                continue
                
            for flat_f in f.expand():
                if flat_f.value is None:
                    problematic.append(f"{device_name}:{flat_f.name}")
    
    if problematic:
        msg = f"Flat features with None value ({len(problematic)}):\n"
        for item in problematic[:10]:
            msg += f"  - {item}\n"
        if len(problematic) > 10:
            msg += f"  ... and {len(problematic)-10} more\n"
        pytest.fail(msg)


def test_command_params_have_valid_structure(available_mock_devices, load_mock_device):
    """Ensure all command parameters have expected structure (type, required, constraints)."""
    
    missing_structure = []
    
    for device_name in available_mock_devices:
        data = load_mock_device(device_name)
        for feature_data in data.get("data", []):
            feature_name = feature_data.get("feature", "unknown")
            commands = feature_data.get("commands", {})
            
            for cmd_name, cmd_data in commands.items():
                params = cmd_data.get("params", {})
                for param_name, param_spec in params.items():
                    # Each param should have 'type' at minimum
                    if not isinstance(param_spec, dict):
                        missing_structure.append(
                            f"{device_name}:{feature_name}.{cmd_name}({param_name}) - not a dict"
                        )
                    elif "type" not in param_spec:
                        missing_structure.append(
                            f"{device_name}:{feature_name}.{cmd_name}({param_name}) - missing 'type'"
                        )
    
    if missing_structure:
        msg = f"Command params with invalid structure ({len(missing_structure)}):\n"
        for item in missing_structure[:10]:
            msg += f"  - {item}\n"
        pytest.fail(msg)
