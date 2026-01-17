"""Unit tests for parameter validation logic."""

import pytest

from vi_api_client.validation import validate_command_params


def test_validation_success():
    """Test that valid parameters pass without error."""
    params_def = {
        "slope": {"type": "number", "min": 0.2, "max": 3.5},
        "shift": {"type": "number", "min": -13, "max": 40},
        "mode": {"type": "string", "enum": ["active", "standby"]},
    }

    # legitimate params
    params = {"slope": 1.4, "shift": 0, "mode": "active"}
    validate_command_params("testCmd", params_def, params)


def test_missing_required_parameter():
    """Test that missing required parameters raise ValueError."""
    params_def = {
        "slope": {"type": "number", "required": True},
        "shift": {"type": "number", "required": False},
    }

    # Missing 'slope'
    with pytest.raises(ValueError, match="Missing required parameters"):
        validate_command_params("testCmd", params_def, {"shift": 5})


def test_type_validation():
    """Test strict type checking."""
    params_def = {
        "val_num": {"type": "number"},
        "val_bool": {"type": "boolean"},
        "val_str": {"type": "string"},
    }

    # 1. Number expected, got string
    with pytest.raises(TypeError, match="must be a number"):
        validate_command_params("testCmd", params_def, {"val_num": "1.5"})

    # 2. Boolean expected, got int (0/1) or string
    with pytest.raises(TypeError, match="must be a boolean"):
        validate_command_params("testCmd", params_def, {"val_bool": 1})

    # 3. String expected, got number
    with pytest.raises(TypeError, match="must be a string"):
        validate_command_params("testCmd", params_def, {"val_str": 123})


def test_number_constraints():
    """Test min/max constraints for numbers."""
    params_def = {"slope": {"type": "number", "min": 1.0, "max": 2.0}}

    # Too low
    with pytest.raises(ValueError, match="less than minimum"):
        validate_command_params("testCmd", params_def, {"slope": 0.9})

    # Too high
    with pytest.raises(ValueError, match="greater than maximum"):
        validate_command_params("testCmd", params_def, {"slope": 2.1})

    # Boundary ok
    validate_command_params("testCmd", params_def, {"slope": 1.0})
    validate_command_params("testCmd", params_def, {"slope": 2.0})


def test_enum_constraints():
    """Test string enum constraints."""
    params_def = {"mode": {"type": "string", "enum": ["eco", "comfort"]}}

    # Invalid option
    with pytest.raises(ValueError, match="not a valid option"):
        validate_command_params("testCmd", params_def, {"mode": "sport"})

    # Valid option
    validate_command_params("testCmd", params_def, {"mode": "eco"})


def test_regex_constraints():
    """Test string regex constraints."""
    params_def = {
        "schedule": {
            "type": "string",
            "constraints": {"regEx": "^[0-9]{2}:[0-9]{2}$"},  # HH:MM format
        }
    }

    # Invalid pattern
    with pytest.raises(ValueError, match="does not match required pattern"):
        validate_command_params(
            "testCmd", params_def, {"schedule": "12:3"}
        )  # Too short

    # Valid pattern
    validate_command_params("testCmd", params_def, {"schedule": "12:30"})
