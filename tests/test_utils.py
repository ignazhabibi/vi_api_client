"""Unit tests for CLI parsers."""

import pytest

from vi_api_client.utils import parse_cli_params


def test_parse_key_value():
    """Test standard key=value parsing."""
    # --- ARRANGE ---
    inputs = ["slope=1.4", "shift=0", "mode=active", "enabled=true"]
    expected = {"slope": 1.4, "shift": 0, "mode": "active", "enabled": True}

    # --- ACT & ASSERT ---
    assert parse_cli_params(inputs) == expected


def test_parse_json_string():
    """Test parsing a single JSON string."""
    # --- ARRANGE ---
    # Note: in CLI, this comes as a list with one string
    inputs = ['{"slope": 1.4, "shift": 0}']
    expected = {"slope": 1.4, "shift": 0}

    # --- ACT & ASSERT ---
    assert parse_cli_params(inputs) == expected


def test_parse_mixed_types():
    """Test type inference."""
    # --- ARRANGE ---
    inputs = ["int=42", "float=42.5", "bool_t=true", "bool_f=False", "str=hello"]

    # --- ACT ---
    params = parse_cli_params(inputs)

    # --- ASSERT ---
    assert params["int"] == 42
    assert isinstance(params["int"], int)

    assert params["float"] == 42.5
    assert isinstance(params["float"], float)

    assert params["bool_t"] is True
    assert params["bool_f"] is False

    assert params["str"] == "hello"


def test_invalid_format():
    """Test invalid input format."""
    # --- ACT & ASSERT ---
    with pytest.raises(ValueError, match="Expected key=value"):
        parse_cli_params(["invalid_arg"])


def test_nested_json_value():
    """Test parsing a JSON value within a key=value pair."""
    # --- ARRANGE ---
    # e.g. schedule={"day": 1}
    inputs = ['schedule={"day": 1, "temp": 20}']

    # --- ACT ---
    params = parse_cli_params(inputs)

    # --- ASSERT ---
    assert isinstance(params["schedule"], dict)
    assert params["schedule"]["day"] == 1
