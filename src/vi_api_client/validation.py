"""Validation of JSON values and of values written to the API.

JSON values crossing client trust boundaries are checked against the JSON
value contract; values a caller writes are checked against the type and
constraint rules the API reports, before any request is sent.
"""

import itertools
import re
from math import isfinite
from typing import TypeIs, cast

from ._types import FeatureValue, JsonValue
from .exceptions import ViResponseError
from .models import FeatureConstraints, ScheduleConstraints

_SCHEDULE_DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_SCHEDULE_TIME_PATTERN = re.compile(r"([01][0-9]|2[0-4]):([0-5][0-9])")

# Float modulo can land just below the step (0.3 % 0.1 is about 0.1), so a
# remainder this close to either end of the step counts as aligned.
_STEP_TOLERANCE = 1e-9


def is_json_number(value: object) -> TypeIs[int | float]:
    """Return whether a value is a JSON number; bool counts as int in Python."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def validate_json_value(value: object, *, path: str = "value") -> JsonValue:
    """Return a JSON-compatible value or raise a library-owned response error.

    Args:
        value: Value received at an API or persisted-data boundary.
        path: Human-readable location used in validation errors.

    Returns:
        A validated copy: lists and dicts are rebuilt, other values are
        returned as is.

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
        # Narrowing ``object`` to a bare generic container leaves the element
        # types unknown; every element is re-validated one by one below.
        items = cast("list[object]", value)
        validated_list: list[JsonValue] = []
        for index, item in enumerate(items):
            validated_list.append(validate_json_value(item, path=f"{path}[{index}]"))
        return validated_list
    if isinstance(value, dict):
        # Same narrowing limitation as lists; every entry is re-validated below.
        entries = cast("dict[object, object]", value)
        validated_object: dict[str, JsonValue] = {}
        for key, item in entries.items():
            if not isinstance(key, str):
                raise ViResponseError(f"{path} object keys must be strings")
            validated_object[key] = validate_json_value(item, path=f"{path}.{key}")
        return validated_object
    raise ViResponseError(f"{path} contains a non-JSON nested value")


def reject_non_json_parameters(parameters: dict[str, JsonValue]) -> None:
    """Reject command parameter values the JSON value contract excludes.

    The parameters come from the caller, so a violation is a ValueError
    rather than the ViResponseError the shared validator raises for API
    data.

    Raises:
        ValueError: If a parameter key or value is not JSON-compatible.
    """
    try:
        validate_json_value(parameters, path="Command parameters")
    except ViResponseError as error:
        raise ValueError(str(error)) from error


def validate_value_constraints(
    constraints: FeatureConstraints, command_name: str, value: FeatureValue
) -> None:
    """Validate value against all constraints using type-based dispatch.

    Args:
        constraints: The constraint rules of the feature being written.
        command_name: The bound command, only for error wording.
        value: The JSON value to check.

    Raises:
        ValueError: If value has the wrong type for the command parameter
            or violates any constraints.
    """
    if not _matches_value_type(constraints.value_type, value):
        raise ValueError(
            f"Value {value!r} is not of type '{constraints.value_type}' "
            f"expected by command '{command_name}'"
        )
    if constraints.options and value not in constraints.options:
        raise ValueError(
            f"Value {value} is not in allowed options: {constraints.options}"
        )

    if constraints.value_type == "Schedule":
        _validate_schedule(constraints.schedule, value)
    elif isinstance(value, int | float):
        _validate_numeric_constraints(constraints, value)
    elif isinstance(value, str):
        _validate_string_constraints(constraints, value)


def _validate_numeric_constraints(
    constraints: FeatureConstraints, value: int | float
) -> None:
    """Validate numeric bounds and step."""
    if constraints.min is not None and value < constraints.min:
        raise ValueError(f"Value {value} < min ({constraints.min})")
    if constraints.max is not None and value > constraints.max:
        raise ValueError(f"Value {value} > max ({constraints.max})")

    if constraints.step is not None and constraints.step > 0:
        base = constraints.min if constraints.min is not None else 0
        offset = value - base
        remainder = offset % constraints.step
        is_aligned = (
            remainder < _STEP_TOLERANCE
            or abs(remainder - constraints.step) < _STEP_TOLERANCE
        )

        if not is_aligned:
            raise ValueError(
                f"Value {value} does not align with step {constraints.step} "
                f"(starting from {base})"
            )


def _validate_string_constraints(constraints: FeatureConstraints, value: str) -> None:
    """Validate string length and pattern."""
    if constraints.min_length is not None and len(value) < constraints.min_length:
        raise ValueError(
            f"Value length {len(value)} < min_length ({constraints.min_length})"
        )
    if constraints.max_length is not None and len(value) > constraints.max_length:
        raise ValueError(
            f"Value length {len(value)} > max_length ({constraints.max_length})"
        )
    if constraints.pattern and not re.fullmatch(constraints.pattern, value):
        raise ValueError(
            f"Value '{value}' does not match pattern '{constraints.pattern}'"
        )


def _validate_schedule(
    constraints: ScheduleConstraints | None, value: FeatureValue
) -> None:
    """Validate a weekly plan against the general shape and the API rules.

    A plan maps every weekday to a list of time slots with ``start`` and
    ``end`` times in ``HH:MM``; ``24:00`` ends a slot at midnight. The API
    replaces the whole plan, so all seven days are required; a day without
    slots is an empty list. The rules the API reports per schedule (slot
    count, modes, time grid, overlaps) are checked when present. Other slot
    fields such as ``position`` are left to the API.

    Raises:
        ValueError: If the plan violates its shape or a reported rule.
    """
    if not isinstance(value, dict):
        raise ValueError("Schedule must be an object mapping weekdays to slots")
    rules = constraints or ScheduleConstraints()
    for day, slots in value.items():
        if day not in _SCHEDULE_DAYS:
            raise ValueError(f"Schedule day '{day}' is not one of mon to sun")
        if not isinstance(slots, list):
            raise ValueError(f"Schedule day '{day}' must be a list of slots")
        if rules.max_entries is not None and len(slots) > rules.max_entries:
            raise ValueError(
                f"Schedule day '{day}' has {len(slots)} slots, "
                f"at most {rules.max_entries} are allowed"
            )
        slot_times = [_schedule_slot_times(slot, day, rules) for slot in slots]
        if rules.overlap_allowed is False:
            ordered_times = sorted(slot_times)
            for (_, previous_end), (next_start, _) in itertools.pairwise(ordered_times):
                if next_start < previous_end:
                    raise ValueError(f"Schedule day '{day}' slots overlap")
    missing_days = [day for day in _SCHEDULE_DAYS if day not in value]
    if missing_days:
        raise ValueError(
            f"Schedule must contain every weekday, missing: {', '.join(missing_days)}"
        )


def _matches_value_type(value_type: str | None, value: FeatureValue) -> bool:
    """Return whether a value fits the command parameter's reported type.

    Unknown or missing types are not checked. Booleans never count as numbers,
    and an integer parameter also accepts a whole-number float such as ``2.0``.
    """
    if isinstance(value, bool):
        return value_type not in {"number", "integer", "string"}
    match value_type:
        case "number":
            return isinstance(value, int | float)
        case "integer":
            return isinstance(value, int) or (
                isinstance(value, float) and value.is_integer()
            )
        case "string":
            return isinstance(value, str)
        case "boolean":
            return False
        case _:
            return True


def _schedule_slot_times(
    slot: JsonValue, day: str, rules: ScheduleConstraints
) -> tuple[int, int]:
    """Return a slot's start and end in minutes after checking its fields.

    Raises:
        ValueError: If the slot is not an object, its times are invalid or out
            of order, or its mode is not one of the reported modes.
    """
    if not isinstance(slot, dict):
        raise ValueError(f"Schedule day '{day}' slots must be objects")
    start = _schedule_minutes(slot.get("start"), day, rules.resolution)
    end = _schedule_minutes(slot.get("end"), day, rules.resolution)
    if start >= end:
        raise ValueError(f"Schedule day '{day}' slot must start before it ends")
    mode = slot.get("mode")
    if rules.modes is not None and mode not in rules.modes:
        raise ValueError(
            f"Schedule day '{day}' mode {mode!r} is not one of {list(rules.modes)}"
        )
    return start, end


def _schedule_minutes(time_text: object, day: str, resolution: int | None) -> int:
    """Return a schedule time of day in minutes after midnight.

    Raises:
        ValueError: If the time is not ``HH:MM`` up to ``24:00`` or does not
            follow the reported time grid.
    """
    match = (
        _SCHEDULE_TIME_PATTERN.fullmatch(time_text)
        if isinstance(time_text, str)
        else None
    )
    if match is None:
        raise ValueError(
            f"Schedule day '{day}' times must use HH:MM, got {time_text!r}"
        )
    minutes = int(match.group(1)) * 60 + int(match.group(2))
    if minutes > 24 * 60:
        raise ValueError(f"Schedule day '{day}' time {time_text} is after 24:00")
    if resolution is not None and minutes % resolution:
        raise ValueError(
            f"Schedule day '{day}' time {time_text} is not on the "
            f"{resolution}-minute grid"
        )
    return minutes
