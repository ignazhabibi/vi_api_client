"""Validation helpers for JSON values crossing client trust boundaries."""

from math import isfinite

from ._types import JsonValue
from .exceptions import ViResponseError


def validate_json_value(value: object, *, path: str = "value") -> JsonValue:
    """Return a JSON-compatible value or raise a library-owned response error.

    Args:
        value: Value received at an API or persisted-data boundary.
        path: Human-readable location used in validation errors.

    Returns:
        The original value with its normal JSON-compatible Python shapes intact.

    Raises:
        ViResponseError: If the value cannot be represented by JSON.
    """
    if value is None or isinstance(value, (bool, str, int)):
        return value
    if isinstance(value, float):
        if not isfinite(value):
            raise ViResponseError(f"{path} must be a finite number")
        return value
    if isinstance(value, list):
        validated_list: list[JsonValue] = []
        for index, item in enumerate(value):
            validated_list.append(validate_json_value(item, path=f"{path}[{index}]"))
        return validated_list
    if isinstance(value, dict):
        validated_object: dict[str, JsonValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ViResponseError(f"{path} object keys must be strings")
            validated_object[key] = validate_json_value(item, path=f"{path}.{key}")
        return validated_object
    raise ViResponseError(f"{path} contains a non-JSON nested value")
