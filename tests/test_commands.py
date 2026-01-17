import pytest

from vi_api_client import MockViClient
from vi_api_client.models import Feature


@pytest.fixture
def feature_with_commands():
    data = {
        "feature": "heating.curve",
        "commands": {
            "setCurve": {
                "uri": "https://api.vi/commands/setCurve",
                "params": {
                    "slope": {
                        "required": True,
                        "type": "number",
                        "min": 0.2,
                        "max": 3.5,
                        "stepping": 0.1,
                    },
                    "shift": {
                        "required": True,
                        "type": "number",
                        "min": -13,
                        "max": 40,
                        "stepping": 1,
                    },
                    "mode": {"required": False, "type": "string"},
                },
            },
            "simpleCmd": {"uri": "https://api.vi/commands/simpleCmd", "params": {}},
        },
    }
    return Feature.from_api(data)


@pytest.mark.asyncio
async def test_execute_command_success_kwargs(feature_with_commands):
    client = MockViClient("Vitodens200W", auth=None)

    # Test valid kwargs execution
    response = await client.execute_command(
        feature_with_commands, "setCurve", shift=5, slope=1.2
    )
    assert response.success is True


@pytest.mark.asyncio
async def test_execute_command_success_mixed(feature_with_commands):
    client = MockViClient("Vitodens200W", auth=None)

    # Test mixed params (dict) and kwargs
    params = {"slope": 1.4}
    response = await client.execute_command(
        feature_with_commands, "setCurve", params=params, shift=5
    )
    assert response.success is True


@pytest.mark.asyncio
async def test_validation_missing_param(feature_with_commands):
    client = MockViClient("Vitodens200W", auth=None)

    # Missing required 'shift'
    with pytest.raises(ValueError) as excinfo:
        await client.execute_command(feature_with_commands, "setCurve", slope=1.4)
    assert "Missing required parameters" in str(excinfo.value)
    assert "shift" in str(excinfo.value)


@pytest.mark.asyncio
async def test_validation_unknown_command(feature_with_commands):
    client = MockViClient("Vitodens200W", auth=None)

    with pytest.raises(ValueError) as excinfo:
        await client.execute_command(feature_with_commands, "invalidCmd")
    assert "Command 'invalidCmd' not found" in str(excinfo.value)


@pytest.mark.asyncio
async def test_cmd_without_uri(feature_with_commands):
    client = MockViClient("Vitodens200W", auth=None)

    # Create invalid feature manually
    data = {
        "feature": "broken",
        "commands": {
            "oops": {"isExecutable": True}  # Missing URI
        },
    }
    feat = Feature.from_api(data)

    with pytest.raises(ValueError) as excinfo:
        await client.execute_command(feat, "oops")
    assert "has no URI definition" in str(excinfo.value)


@pytest.mark.asyncio
async def test_not_executable(feature_with_commands):
    client = MockViClient("Vitodens200W", auth=None)

    # Create feature where command is disabled
    data = {
        "feature": "test",
        "commands": {
            "disabledCmd": {
                "uri": "https://api.vi/cmd",
                "isExecutable": False,
                "params": {},
            }
        },
    }
    feat = Feature.from_api(data)

    with pytest.raises(ValueError) as excinfo:
        await client.execute_command(feat, "disabledCmd")

    assert "is currently not executable" in str(excinfo.value)
