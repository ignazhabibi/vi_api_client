"""Tests for the public recursive JSON value contract."""

import math

import pytest

import vi_api_client
from vi_api_client.exceptions import ViResponseError


def test_validate_json_value_preserves_nested_json_shapes():
    """The public boundary accepts the complete JSON value range."""
    # Arrange: Build a payload containing every supported JSON value shape.
    value = {
        "null": None,
        "boolean": True,
        "number": 1.5,
        "string": "comfort",
        "list": [1, {"nested": "value"}],
    }

    # Act: Validate the public JSON value.
    result = vi_api_client.validate_json_value(value)

    # Assert: Validation retains the original compatible Python runtime shapes.
    assert result == value
    assert isinstance(result, dict)
    assert isinstance(result["list"], list)


@pytest.mark.parametrize(
    ("value", "description"),
    [
        ({1: "not a string key"}, "object keys"),
        ({"nested": {"invalid": object()}}, "nested value"),
        (math.nan, "finite number"),
    ],
)
def test_validate_json_value_rejects_non_json_python_values(
    value: object, description: str
):
    """The public boundary rejects values JSON cannot represent."""
    # Act and assert: Invalid values become library-owned response errors.
    with pytest.raises(ViResponseError, match=description):
        vi_api_client.validate_json_value(value)
