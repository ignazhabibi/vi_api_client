"""Parameter validation logic for Viessmann commands.

This module encapsulates all logic related to validating command parameters
against the API's constraints (type, min/max, enum, regex).
"""

import re
from typing import Any


def validate_command_params(
    command_name: str, params_def: dict[str, Any], params: dict[str, Any]
) -> None:
    """Validate parameters against the parameter definitions.

    Args:
        command_name: Name of the command (for error messages).
        params_def: The parameters definition dictionary (e.g. command.params).
        params: The actual parameters provided by the user.

    Raises:
        ValueError: If required params are missing or constraints are violated.
        TypeError: If parameter types are incorrect.
    """
    # Verify all required parameters are present.
    missing_params = []

    for param_name, param_info in params_def.items():
        if param_info.get("required", False) and param_name not in params:
            missing_params.append(param_name)

    if missing_params:
        raise ValueError(
            f"Missing required parameters for command '{command_name}': "
            f"{missing_params}. "
            f"Required: {[k for k, v in params_def.items() if v.get('required')]}"
        )

    # 2. Check Constraints for provided parameters
    for param_name, value in params.items():
        # Skip parameters not defined in the API
        if param_name not in params_def:
            continue

        _validate_single_param(param_name, value, params_def[param_name])


def _validate_single_param(name: str, value: Any, rules: dict[str, Any]) -> None:
    """Validate a single parameter against its rules."""
    p_type = rules.get("type")

    # Use dispatcher for type validation.
    validators = {
        "number": _validate_number,
        "string": _validate_string,
        "boolean": _validate_boolean,
    }

    validator = validators.get(p_type)
    if validator:
        validator(name, value, rules)


def _validate_number(name: str, value: Any, rules: dict[str, Any]) -> None:
    if not isinstance(value, (int, float)):
        raise TypeError(
            f"Parameter '{name}' must be a number, got {type(value).__name__}"
        )

    # Number Constraints
    min_val = rules.get("min")
    if min_val is not None and value < min_val:
        raise ValueError(
            f"Parameter '{name}' ({value}) is less than minimum ({min_val})"
        )

    max_val = rules.get("max")
    if max_val is not None and value > max_val:
        raise ValueError(
            f"Parameter '{name}' ({value}) is greater than maximum ({max_val})"
        )


def _validate_boolean(name: str, value: Any, rules: dict[str, Any]) -> None:
    if not isinstance(value, bool):
        # Be strict about booleans to avoid ambiguity
        raise TypeError(
            f"Parameter '{name}' must be a boolean, got {type(value).__name__}"
        )


def _validate_string(name: str, value: Any, rules: dict[str, Any]) -> None:
    if not isinstance(value, str):
        raise TypeError(
            f"Parameter '{name}' must be a string, got {type(value).__name__}"
        )

    # Enum Constraint
    enum_vals = rules.get("enum")
    if enum_vals and value not in enum_vals:
        raise ValueError(
            f"Parameter '{name}' ('{value}') is not a valid option. "
            f"Allowed: {enum_vals}"
        )

    # Regex Constraint
    regex_pattern = rules.get("constraints", {}).get("regEx")
    if regex_pattern and not re.match(regex_pattern, value):
        raise ValueError(
            f"Parameter '{name}' ('{value}') does not match required pattern: "
            f"{regex_pattern}"
        )
