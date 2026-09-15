"""Public dynamic value contracts shared across client API boundaries."""

type JsonValue = (
    bool | int | float | str | list[JsonValue] | dict[str, JsonValue] | None
)
"""A recursively JSON-compatible value retaining standard Python shapes."""

type FeatureValue = JsonValue
"""The dynamic value reported by a device feature."""

type ValidationDetail = dict[str, JsonValue]
"""One dictionary-shaped API validation detail."""
