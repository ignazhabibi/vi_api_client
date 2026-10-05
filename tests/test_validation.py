"""Tests for the public recursive JSON value validation."""

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

    # Assert: Validation keeps the JSON shapes in a rebuilt copy.
    assert result == value
    assert result is not value
    assert isinstance(result, dict)
    assert isinstance(result["list"], list)


@pytest.mark.parametrize(
    ("value", "message"),
    [
        pytest.param({1: "not a string key"}, "object keys", id="non-text-key"),
        pytest.param(
            {"nested": {"invalid": object()}}, "nested value", id="nested-object"
        ),
        pytest.param(math.nan, "finite number", id="nan"),
    ],
)
def test_validate_json_value_rejects_non_json_python_values(
    value: object, message: str
):
    """The public boundary rejects values JSON cannot represent."""
    # Act and assert: Invalid values become library-owned response errors.
    with pytest.raises(ViResponseError, match=message):
        vi_api_client.validate_json_value(value)
