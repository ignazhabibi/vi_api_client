"""Unit tests for CLI parsers."""

import pytest

from vi_api_client.utils import parse_cli_params


def test_parse_key_value():
    """Test standard key=value parsing."""
    # Arrange: Create list of key=value strings with different types.
    inputs = ["slope=1.4", "shift=0", "mode=active", "enabled=true"]
    expected = {"slope": 1.4, "shift": 0, "mode": "active", "enabled": True}

    # Act and Assert: Parse CLI params and verify type inference.
    assert parse_cli_params(inputs) == expected


def test_parse_json_string():
    """Test parsing a single JSON string."""
    # Arrange: Create single JSON object string input.
    # Note: in CLI, this comes as a list with one string
    inputs = ['{"slope": 1.4, "shift": 0}']
    expected = {"slope": 1.4, "shift": 0}

    # Act and Assert: Parse JSON string and verify dict extraction.
    assert parse_cli_params(inputs) == expected


def test_parse_mixed_types():
    """Test type inference."""
    # Arrange: Create key=value strings with int, float, bool, string values.
    inputs = ["int=42", "float=42.5", "bool_t=true", "bool_f=False", "str=hello"]

    # Act: Parse CLI params with automatic type inference.
    params = parse_cli_params(inputs)

    # Assert: Verify each value has correct type (int, float, bool, str).
    assert params["int"] == 42
    assert isinstance(params["int"], int)

    assert params["float"] == 42.5
    assert isinstance(params["float"], float)

    assert params["bool_t"] is True
    assert params["bool_f"] is False

    assert params["str"] == "hello"


def test_invalid_format():
    """Test invalid input format."""
    # Act and Assert: Invalid format should raise ValueError with specific message.
    with pytest.raises(ValueError, match="Expected key=value"):
        parse_cli_params(["invalid_arg"])


def test_nested_json_value():
    """Test parsing a JSON value within a key=value pair."""
    # Arrange: Create key=value with JSON dict as value.
    # e.g. schedule={"day": 1}
    inputs = ['schedule={"day": 1, "temp": 20}']

    # Act: Parse CLI params - value should be extracted as dict.
    params = parse_cli_params(inputs)

    # Assert: Nested JSON should be parsed as Python dict.
    assert isinstance(params["schedule"], dict)
    assert params["schedule"]["day"] == 1
