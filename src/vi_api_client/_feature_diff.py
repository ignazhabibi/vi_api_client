"""Compare two raw API feature documents for the diff-features CLI command."""

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, cast

_ARROW = "→"


@dataclass(frozen=True)
class FeatureDiff:
    """The differences between a left and a right set of raw API features.

    ``structure`` and ``state`` map each changed feature name to its change
    lines, sorted by feature name.
    """

    left_count: int
    right_count: int
    added: list[str] = field(default_factory=list[str])
    removed: list[str] = field(default_factory=list[str])
    structure: dict[str, list[str]] = field(default_factory=dict[str, list[str]])
    state: dict[str, list[str]] = field(default_factory=dict[str, list[str]])

    @property
    def has_differences(self) -> bool:
        """Return whether any section lists a difference."""
        return bool(self.added or self.removed or self.structure or self.state)


def feature_entries(document: object) -> dict[str, Mapping[str, Any]]:
    """Return a feature document's raw API features keyed by feature name.

    Args:
        document: A fixture file or device export with a ``data`` list.

    Raises:
        ValueError: If the document has no ``data`` list of features with
            unique, non-empty names.
    """
    data = _as_mapping(document).get("data")
    if not isinstance(data, list):
        raise ValueError("A feature document must be an object with a 'data' list")
    entries: dict[str, Mapping[str, Any]] = {}
    for item in cast("list[object]", data):
        entry = _as_mapping(item)
        name = entry.get("feature")
        if not isinstance(name, str) or not name:
            raise ValueError("Every feature needs a non-empty 'feature' name")
        if name in entries:
            raise ValueError(f"Duplicate feature in document: {name}")
        entries[name] = entry
    return entries


def diff_features(
    left: Mapping[str, Mapping[str, Any]], right: Mapping[str, Mapping[str, Any]]
) -> FeatureDiff:
    """Compare raw API features by name without comparing their values.

    Properties and commands are compared only when the feature is enabled on
    both sides: the API returns a disabled feature without them, which is a
    state difference rather than a structure difference.
    """
    diff = FeatureDiff(
        left_count=len(left),
        right_count=len(right),
        added=sorted(set(right) - set(left)),
        removed=sorted(set(left) - set(right)),
    )
    for name in sorted(set(left) & set(right)):
        structure = _structure_changes(left[name], right[name])
        if structure:
            diff.structure[name] = structure
        state = _state_changes(left[name], right[name])
        if state:
            diff.state[name] = state
    return diff


def format_feature_diff(
    diff: FeatureDiff, left_label: str, right_label: str
) -> list[str]:
    """Return the text report of a feature comparison."""
    lines = [
        f"Comparing {left_label} ({diff.left_count} features) "
        f"with {right_label} ({diff.right_count} features)"
    ]
    if not diff.has_differences:
        lines.append("No differences.")
        return lines
    if diff.added:
        lines.extend(["", f"Added ({len(diff.added)}):"])
        lines.extend(f"  + {name}" for name in diff.added)
    if diff.removed:
        lines.extend(["", f"Removed ({len(diff.removed)}):"])
        lines.extend(f"  - {name}" for name in diff.removed)
    for title, changes in (
        ("Structure changed", diff.structure),
        ("State changed", diff.state),
    ):
        if changes:
            lines.extend(["", f"{title} ({len(changes)}):"])
            for name, change_lines in changes.items():
                lines.append(f"  {name}")
                lines.extend(f"    {line}" for line in change_lines)
    return lines


def _structure_changes(left: Mapping[str, Any], right: Mapping[str, Any]) -> list[str]:
    """Return the property, command, and deprecation changes of one feature."""
    changes: list[str] = []
    if left.get("isEnabled") is True and right.get("isEnabled") is True:
        changes.extend(
            _property_changes(
                _mapping(left, "properties"), _mapping(right, "properties")
            )
        )
        changes.extend(
            _command_changes(_mapping(left, "commands"), _mapping(right, "commands"))
        )
    if left.get("deprecated") != right.get("deprecated"):
        changes.append(
            f"deprecated: {_render(left.get('deprecated'))} {_ARROW} "
            f"{_render(right.get('deprecated'))}"
        )
    return changes


def _command_changes(left: Mapping[str, Any], right: Mapping[str, Any]) -> list[str]:
    """Return added and removed commands and their parameter changes."""
    changes = [f"+ command {name}" for name in sorted(set(right) - set(left))]
    changes.extend(f"- command {name}" for name in sorted(set(left) - set(right)))
    for name in sorted(set(left) & set(right)):
        changes.extend(
            _parameter_changes(
                name,
                _mapping(_as_mapping(left[name]), "params"),
                _mapping(_as_mapping(right[name]), "params"),
            )
        )
    return changes


def _property_changes(left: Mapping[str, Any], right: Mapping[str, Any]) -> list[str]:
    """Return added and removed properties and their type and unit changes."""
    changes = [f"+ property {name}" for name in sorted(set(right) - set(left))]
    changes.extend(f"- property {name}" for name in sorted(set(left) - set(right)))
    for name in sorted(set(left) & set(right)):
        left_property = _as_mapping(left[name])
        right_property = _as_mapping(right[name])
        changes.extend(
            _field_changes(
                f"property {name}", left_property, right_property, ("type", "unit")
            )
        )
    return changes


def _parameter_changes(
    command: str, left: Mapping[str, Any], right: Mapping[str, Any]
) -> list[str]:
    """Return added and removed parameters and their contract changes."""
    changes = [f"+ {command}.{name}" for name in sorted(set(right) - set(left))]
    changes.extend(f"- {command}.{name}" for name in sorted(set(left) - set(right)))
    for name in sorted(set(left) & set(right)):
        label = f"{command}.{name}"
        left_parameter = _as_mapping(left[name])
        right_parameter = _as_mapping(right[name])
        changes.extend(
            _field_changes(label, left_parameter, right_parameter, ("type", "required"))
        )
        left_constraints = _mapping(left_parameter, "constraints")
        right_constraints = _mapping(right_parameter, "constraints")
        changes.extend(
            _field_changes(
                label,
                left_constraints,
                right_constraints,
                sorted(set(left_constraints) | set(right_constraints)),
            )
        )
    return changes


def _state_changes(left: Mapping[str, Any], right: Mapping[str, Any]) -> list[str]:
    """Return the enabled, ready, and command executability changes."""
    changes = _field_changes("", left, right, ("isEnabled", "isReady"))
    left_commands = _mapping(left, "commands")
    right_commands = _mapping(right, "commands")
    for name in sorted(set(left_commands) & set(right_commands)):
        changes.extend(
            _field_changes(
                f"{name}.",
                _as_mapping(left_commands[name]),
                _as_mapping(right_commands[name]),
                ("isExecutable",),
            )
        )
    return changes


def _field_changes(
    label: str,
    left: Mapping[str, Any],
    right: Mapping[str, Any],
    fields: Sequence[str],
) -> list[str]:
    """Return one ``label field: old → new`` line per differing field.

    A label ending in ``.`` is joined to the field name without a space.
    """
    changes: list[str] = []
    for field_name in fields:
        left_value = left.get(field_name)
        right_value = right.get(field_name)
        if left_value == right_value:
            continue
        prefix = (
            f"{label}{field_name}"
            if label.endswith(".") or not label
            else (f"{label} {field_name}")
        )
        if field_name not in left:
            changes.append(f"{prefix}: + {_render(right_value)}")
        elif field_name not in right:
            changes.append(f"{prefix}: - {_render(left_value)}")
        else:
            changes.append(
                f"{prefix}: {_render(left_value)} {_ARROW} {_render(right_value)}"
            )
    return changes


def _mapping(entry: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    """Return a nested object of a raw entry, or an empty one if it is missing."""
    return _as_mapping(entry.get(key))


def _as_mapping(value: object) -> Mapping[str, Any]:
    """Return a raw JSON object, or an empty one for any other value."""
    return cast("Mapping[str, Any]", value) if isinstance(value, Mapping) else {}


def _render(value: object) -> str:
    """Return a compact JSON rendering of a raw value, ``none`` when absent."""
    if value is None:
        return "none"
    return json.dumps(value, ensure_ascii=False, sort_keys=True)
