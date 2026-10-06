"""Feature command contracts of the client behind its command adapter."""

from dataclasses import replace
from typing import Any, cast

import pytest
from builders import StaticTokenAuth, build_feature

from vi_api_client import FeatureValue, JsonValue
from vi_api_client.client import ViClient
from vi_api_client.models import (
    Device,
    Feature,
    FeatureControl,
    ScheduleConstraints,
)


class _RecordingCommandAdapter:
    """Record command adapter calls and return a configurable response."""

    def __init__(self, success: bool = True) -> None:
        """Initialize a successful or rejected command response."""
        self.calls: list[tuple[FeatureControl, dict[str, Any]]] = []
        self.success = success

    async def execute_command(
        self, control: FeatureControl, parameters: dict[str, Any]
    ) -> dict[str, Any]:
        """Record the command and return its configured response."""
        self.calls.append((control, parameters))
        return {"data": {"success": self.success}}


def _create_client(adapter: _RecordingCommandAdapter) -> ViClient:
    """Create a client whose commands go to the recording adapter."""
    client = ViClient(StaticTokenAuth())
    client._command_adapter = adapter
    return client


def _device(features: list[Feature]) -> Device:
    """Build a device snapshot containing the provided features."""
    return Device(
        id="device-1",
        gateway_serial="gateway-1",
        installation_id="installation-1",
        model_id="model-1",
        device_type="heating",
        status="connected",
        features=features,
    )


def _control(
    *, required_params: list[str] | None = None, param_name: str = "target"
) -> FeatureControl:
    """Build command metadata for a target under ``heating.mode``."""
    return FeatureControl(
        command_name="setMode",
        param_name=param_name,
        required_params=[param_name] if required_params is None else required_params,
        parent_feature_name="heating.mode",
        uri="/commands/setMode",
    )


async def test_set_feature_uses_current_canonical_feature_and_updates_snapshot():
    """The current device feature controls validation, payload, and local update."""
    # Arrange: The supplied stale feature is read-only, while the snapshot is writable.
    adapter = _RecordingCommandAdapter()
    client = _create_client(adapter)
    canonical_control = _control(
        required_params=["target", "enabled", "count", "label"]
    )
    canonical = build_feature("heating.mode.target", "old", canonical_control)
    device = _device(
        [
            canonical,
            build_feature("heating.mode.enabled", False),
            build_feature("heating.mode.count", 0),
            build_feature("heating.mode.label", ""),
        ]
    )
    stale = build_feature("heating.mode.target", "stale")

    # Act: Set through the stale object using its canonical name.
    response, updated_device = await client.set_feature(device, stale, "new")

    # Assert: Current metadata and falsey required sibling values are used.
    assert response.success
    assert adapter.calls == [
        (
            canonical_control,
            {"target": "new", "enabled": False, "count": 0, "label": ""},
        )
    ]
    assert device.get_feature("heating.mode.target") == canonical
    assert updated_device is not device
    updated = updated_device.get_feature("heating.mode.target")
    assert updated == replace(canonical, value="new")


@pytest.mark.parametrize(
    ("feature", "device_features", "error"),
    [
        pytest.param(
            build_feature("absent", "value", _control()),
            [],
            "not present",
            id="feature-not-on-device",
        ),
        pytest.param(
            build_feature("heating.mode.target", "old"),
            [build_feature("heating.mode.target", "old", _control(), is_enabled=False)],
            "disabled",
            id="current-feature-disabled",
        ),
        pytest.param(
            build_feature("heating.mode.target", "old"),
            [build_feature("heating.mode.target", "old", _control(), is_ready=False)],
            "not ready",
            id="current-feature-not-ready",
        ),
        pytest.param(
            build_feature("heating.mode.target", "old"),
            [
                build_feature(
                    "heating.mode.target",
                    "old",
                    _control(required_params=["target", "other"]),
                )
            ],
            "Required dependency",
            id="required-sibling-missing",
        ),
    ],
)
async def test_set_feature_rejects_invalid_local_contract_without_adapter_io(
    feature: Feature,
    device_features: list[Feature],
    error: str,
):
    """Local membership, availability, and dependency failures precede adapter I/O."""
    # Arrange: Each scenario supplies an invalid current-device command contract.
    adapter = _RecordingCommandAdapter()
    client = _create_client(adapter)
    device = _device(device_features)

    # Act and assert: Invalid local state never reaches the adapter.
    with pytest.raises(ValueError, match=error):
        await client.set_feature(device, feature, "new")
    assert adapter.calls == []


@pytest.mark.parametrize(
    ("sibling", "error"),
    [
        pytest.param(
            build_feature("heating.mode.other", "value", is_enabled=False),
            "disabled",
            id="sibling-disabled",
        ),
        pytest.param(
            build_feature("heating.mode.other", "value", is_ready=False),
            "not ready",
            id="sibling-not-ready",
        ),
        pytest.param(
            build_feature("heating.mode.other", None),
            "has no value",
            id="sibling-without-value",
        ),
    ],
)
async def test_set_feature_rejects_unavailable_required_dependencies_before_constraints(
    sibling: Feature,
    error: str,
):
    """Required dependency validation precedes target constraint validation and I/O."""
    # Arrange: Both the sibling and target constraint are invalid.
    adapter = _RecordingCommandAdapter()
    client = _create_client(adapter)
    control = replace(_control(required_params=["target", "other"]), max=10)
    target = build_feature("heating.mode.target", "old", control)
    device = _device([target, sibling])

    # Act and assert: The dependency error takes precedence over the target constraint.
    with pytest.raises(ValueError, match=error):
        await client.set_feature(device, target, 11)
    assert adapter.calls == []


async def test_set_feature_uses_canonical_constraints_before_adapter_io():
    """Target constraints come from the current device feature before adapter I/O."""
    # Arrange: The caller supplies stale permissive metadata for a constrained target.
    adapter = _RecordingCommandAdapter()
    client = _create_client(adapter)
    canonical = build_feature("heating.mode.target", 5, replace(_control(), max=10))
    stale = build_feature("heating.mode.target", 5, _control())
    device = _device([canonical])

    # Act and assert: The canonical maximum rejects before either adapter can run.
    with pytest.raises(ValueError, match="max"):
        await client.set_feature(device, stale, 11)
    assert adapter.calls == []


@pytest.mark.parametrize(
    ("initial_value", "written_value", "options"),
    [
        pytest.param("low", "high", ["low", "high"], id="string-options"),
        pytest.param(1, 2, [1, 2, 3], id="numeric-options"),
    ],
)
async def test_set_feature_accepts_declared_option_values(
    initial_value: Any,
    written_value: Any,
    options: list,
):
    """Enum-constrained targets accept values from the declared options."""
    # Arrange: The target declares a closed set of allowed values.
    adapter = _RecordingCommandAdapter()
    client = _create_client(adapter)
    control = replace(_control(), options=options)
    target = build_feature("heating.mode.target", initial_value, control)
    device = _device([target])

    # Act: Write one of the declared option values.
    response, updated_device = await client.set_feature(device, target, written_value)

    # Assert: The payload carries the option value and the snapshot updates.
    assert response.success
    assert adapter.calls == [(control, {"target": written_value})]
    updated = updated_device.get_feature("heating.mode.target")
    assert updated is not None
    assert updated == replace(target, value=written_value)


@pytest.mark.parametrize(
    ("control_overrides", "target_value", "error"),
    [
        pytest.param({"min": 2.0}, 1, "< min", id="below-min"),
        pytest.param({"max": 3.5}, 5.0, "> max", id="above-max"),
        pytest.param(
            {"min": 0.2, "step": 0.1}, 0.25, "does not align with step", id="off-step"
        ),
        pytest.param(
            {"options": ["low", "high"]},
            "medium",
            "allowed options",
            id="outside-options",
        ),
        pytest.param({"min_length": 3}, "ab", "min_length", id="below-min-length"),
        pytest.param({"max_length": 3}, "toolong", "max_length", id="above-max-length"),
        pytest.param(
            {"pattern": "^[a-z]+$"},
            "UPPER",
            "does not match pattern",
            id="pattern-mismatch",
        ),
        pytest.param(
            {"pattern": r"^[\d]{2}-[\d]{2}$"},
            "12-31\n",
            "does not match pattern",
            id="pattern-trailing-newline",
        ),
    ],
)
async def test_set_feature_rejects_values_outside_canonical_constraints(
    control_overrides: dict[str, Any],
    target_value: Any,
    error: str,
):
    # Arrange: The current device feature carries the violated constraint.
    adapter = _RecordingCommandAdapter()
    client = _create_client(adapter)
    control = replace(_control(), **control_overrides)
    target = build_feature("heating.mode.target", "old", control)
    device = _device([target])

    # Act and assert: The constraint violation rejects before I/O.
    with pytest.raises(ValueError, match=error):
        await client.set_feature(device, target, target_value)
    assert adapter.calls == []


async def test_set_feature_omits_optional_siblings_and_preserves_rejected_device():
    """Only required dependencies are sent and API rejection retains the snapshot."""
    # Arrange: The optional sibling is available but must not be sent automatically.
    adapter = _RecordingCommandAdapter(success=False)
    client = _create_client(adapter)
    control = _control(required_params=[])
    canonical = build_feature("heating.mode.target", "old", control)
    device = _device([canonical, build_feature("heating.mode.optional", "present")])

    # Act: The command adapter rejects the generated command.
    response, returned_device = await client.set_feature(device, canonical, "new")

    # Assert: Optional state is absent and the exact original snapshot returns.
    assert not response.success
    assert adapter.calls == [(control, {"target": "new"})]
    assert returned_device is device


async def test_execute_command_requires_complete_available_command_without_mutation():
    """Explicit commands require complete payloads but preserve additional parameters."""
    # Arrange: The complete payload includes an allowed extra parameter.
    adapter = _RecordingCommandAdapter()
    client = _create_client(adapter)
    control = _control(required_params=["target", "dependency"])
    feature = build_feature("heating.mode.target", "old", control)
    parameters = {"target": "new", "dependency": None, "extra": "kept"}

    # Act: Submit the explicit payload without high-level augmentation.
    response = await client.execute_command(feature, parameters)

    # Assert: The adapter receives the original mapping unchanged.
    assert response.success
    assert adapter.calls == [(control, parameters)]
    assert parameters == {"target": "new", "dependency": None, "extra": "kept"}


async def test_set_feature_accepts_json_object_target_value():
    """Target values may use any JSON value shape, including nested objects."""
    # Arrange: The unconstrained target accepts a structured JSON value.
    adapter = _RecordingCommandAdapter()
    client = _create_client(adapter)
    control = _control()
    target = build_feature("heating.mode.target", "old", control)
    device = _device([target])
    target_value: FeatureValue = {"entries": [1, "two", None]}

    # Act: Write the structured JSON value.
    response, updated_device = await client.set_feature(device, target, target_value)

    # Assert: The payload carries the exact JSON value and the snapshot updates.
    assert response.success
    assert adapter.calls == [(control, {"target": target_value})]
    updated = updated_device.get_feature("heating.mode.target")
    assert updated == replace(target, value=target_value)


async def test_set_feature_rejects_non_finite_target_value_without_adapter_io():
    """Target values outside the JSON value contract reject before adapter I/O."""
    # Arrange: A non-finite number is outside the JSON value contract.
    adapter = _RecordingCommandAdapter()
    client = _create_client(adapter)
    target = build_feature("heating.mode.target", "old", _control())
    device = _device([target])

    # Act and assert: The invalid target never reaches the adapter.
    with pytest.raises(ValueError, match="finite"):
        await client.set_feature(device, target, float("nan"))
    assert adapter.calls == []


async def test_set_feature_rejects_non_json_target_value_without_adapter_io():
    """Target values JSON cannot represent reject before adapter I/O."""
    # Arrange: The target value is a Python object JSON cannot represent.
    adapter = _RecordingCommandAdapter()
    client = _create_client(adapter)
    target = build_feature("heating.mode.target", "old", _control())
    device = _device([target])

    # Act and assert: The invalid target never reaches the adapter.
    with pytest.raises(ValueError, match="non-JSON"):
        await client.set_feature(device, target, cast(FeatureValue, object()))
    assert adapter.calls == []


async def test_set_feature_rejects_non_json_required_dependency_value_without_adapter_io():
    """Resolved dependency values outside the JSON contract reject before I/O."""
    # Arrange: The required sibling reports a value JSON cannot represent.
    adapter = _RecordingCommandAdapter()
    client = _create_client(adapter)
    control = _control(required_params=["target", "other"])
    target = build_feature("heating.mode.target", "old", control)
    sibling = build_feature("heating.mode.other", cast(FeatureValue, object()))
    device = _device([target, sibling])

    # Act and assert: The invalid dependency never reaches the adapter.
    with pytest.raises(ValueError, match="non-JSON"):
        await client.set_feature(device, target, "new")
    assert adapter.calls == []


async def test_execute_command_rejects_non_json_parameter_values_without_adapter_io():
    """Parameter values outside the JSON value contract reject before I/O."""
    # Arrange: One explicit parameter value is outside the JSON value contract.
    adapter = _RecordingCommandAdapter()
    client = _create_client(adapter)
    feature = build_feature("heating.mode.target", "old", _control())
    parameters: dict[str, JsonValue] = {"target": float("nan")}

    # Act and assert: The invalid parameter never reaches the adapter.
    with pytest.raises(ValueError, match="finite"):
        await client.execute_command(feature, parameters)
    assert adapter.calls == []


@pytest.mark.parametrize(
    ("feature", "parameters", "error"),
    [
        pytest.param(
            build_feature("target", "old"),
            {"target": "new"},
            "read-only",
            id="read-only-feature",
        ),
        pytest.param(
            build_feature("target", "old", _control(), is_enabled=False),
            {"target": "new"},
            "disabled",
            id="disabled-feature",
        ),
        pytest.param(
            build_feature("target", "old", _control(), is_ready=False),
            {"target": "new"},
            "not ready",
            id="not-ready-feature",
        ),
        pytest.param(
            build_feature("target", "old", _control()),
            {},
            "target parameter",
            id="missing-target-parameter",
        ),
        pytest.param(
            build_feature(
                "target", "old", _control(required_params=["target", "other"])
            ),
            {"target": "new"},
            "required parameter",
            id="missing-required-parameter",
        ),
    ],
)
async def test_execute_command_rejects_invalid_local_contract_without_adapter_io(
    feature: Feature,
    parameters: dict[str, Any],
    error: str,
):
    """Explicit-command preconditions reject before adapter I/O."""
    # Arrange: Each feature or payload violates the low-level command contract.
    adapter = _RecordingCommandAdapter()
    client = _create_client(adapter)

    # Act and assert: Local invalidity leaves the adapter untouched.
    with pytest.raises(ValueError, match=error):
        await client.execute_command(feature, parameters)
    assert adapter.calls == []


_SCHEDULE_RULES = ScheduleConstraints(
    max_entries=2, modes=("on",), resolution=10, overlap_allowed=False
)


def _schedule_control(rules: ScheduleConstraints | None = _SCHEDULE_RULES):
    """Build command metadata for writing a whole schedule."""
    return FeatureControl(
        command_name="setSchedule",
        param_name="newSchedule",
        required_params=["newSchedule"],
        parent_feature_name="heating.dhw.schedule",
        uri="/commands/setSchedule",
        value_type="Schedule",
        schedule=rules,
    )


def _slot(start: str, end: str, mode: str = "on") -> JsonValue:
    """Build one schedule time slot."""
    return {"start": start, "end": end, "mode": mode, "position": 0}


def _week(**days: JsonValue) -> dict[str, JsonValue]:
    """Build a full weekly plan with the given days and empty other days."""
    plan: dict[str, JsonValue] = {
        day: [] for day in ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
    }
    plan.update(days)
    return plan


async def test_set_feature_writes_a_valid_schedule_and_keeps_the_plan_shape():
    """A written plan is sent as is and becomes the snapshot value unchanged."""
    # Arrange: A writable schedule and a plan within all reported rules.
    adapter = _RecordingCommandAdapter()
    client = _create_client(adapter)
    control = _schedule_control()
    schedule = build_feature("heating.dhw.schedule", {"mon": []}, control)
    device = _device([schedule])
    plan: FeatureValue = _week(mon=[_slot("05:30", "09:00"), _slot("18:30", "24:00")])

    # Act: Write the plan.
    response, updated_device = await client.set_feature(device, schedule, plan)

    # Assert: The plan is sent and read back in the same shape.
    assert response.success
    assert adapter.calls == [(control, {"newSchedule": plan})]
    updated_schedule = updated_device.get_feature("heating.dhw.schedule")
    assert updated_schedule is not None
    assert updated_schedule.value == plan


@pytest.mark.parametrize(
    ("plan", "error"),
    [
        pytest.param([], "must be an object", id="not-an-object"),
        pytest.param(_week(monday=[]), "not one of mon to sun", id="unknown-day"),
        pytest.param(
            {"mon": [], "tue": []},
            "missing: wed, thu, fri, sat, sun",
            id="missing-days",
        ),
        pytest.param(_week(mon={}), "must be a list", id="day-not-a-list"),
        pytest.param(_week(mon=["05:30"]), "must be objects", id="slot-not-an-object"),
        pytest.param(
            _week(mon=[_slot("5:30", "09:00")]), "HH:MM", id="time-without-padding"
        ),
        pytest.param(_week(mon=[_slot("05:30", "24:10")]), "after 24:00", id="past-24"),
        pytest.param(
            _week(mon=[{"end": "09:00", "mode": "on"}]), "HH:MM", id="start-missing"
        ),
        pytest.param(
            _week(mon=[_slot("09:00", "05:30")]),
            "start before it ends",
            id="end-before-start",
        ),
        pytest.param(
            _week(mon=[_slot("05:00", "06:00")] * 3),
            "at most 2",
            id="too-many-slots",
        ),
        pytest.param(
            _week(mon=[_slot("05:00", "06:00", "comfort")]),
            "'comfort' is not one of",
            id="unknown-mode",
        ),
        pytest.param(
            _week(mon=[_slot("05:35", "06:00")]),
            "10-minute grid",
            id="off-grid",
        ),
        pytest.param(
            _week(mon=[_slot("08:00", "10:00"), _slot("05:00", "08:10")]),
            "overlap",
            id="overlapping-slots",
        ),
    ],
)
async def test_set_feature_rejects_schedules_that_break_their_rules(plan, error):
    # Arrange: The schedule reports rules that the plan breaks.
    adapter = _RecordingCommandAdapter()
    client = _create_client(adapter)
    schedule = build_feature("heating.dhw.schedule", {"mon": []}, _schedule_control())
    device = _device([schedule])

    # Act and assert: The plan is rejected before any command is sent.
    with pytest.raises(ValueError, match=error):
        await client.set_feature(device, schedule, plan)
    assert adapter.calls == []


async def test_set_feature_allows_overlaps_and_any_mode_without_reported_rules():
    """Without reported rules only the general plan shape is checked."""
    # Arrange: The schedule reports no rules at all.
    adapter = _RecordingCommandAdapter()
    client = _create_client(adapter)
    control = _schedule_control(rules=None)
    schedule = build_feature("heating.dhw.schedule", {"mon": []}, control)
    device = _device([schedule])
    plan: FeatureValue = _week(
        mon=[_slot("08:00", "10:00", "comfort"), _slot("09:05", "11:00", "x")]
    )

    # Act: Write the plan.
    response, _ = await client.set_feature(device, schedule, plan)

    # Assert: The plan is sent unchanged.
    assert response.success
    assert adapter.calls == [(control, {"newSchedule": plan})]


async def test_set_feature_allows_touching_slots_when_overlaps_are_forbidden():
    adapter = _RecordingCommandAdapter()
    client = _create_client(adapter)
    schedule = build_feature("heating.dhw.schedule", {"mon": []}, _schedule_control())
    device = _device([schedule])
    plan: FeatureValue = _week(mon=[_slot("06:00", "08:00"), _slot("08:00", "09:00")])

    response, _ = await client.set_feature(device, schedule, plan)

    assert response.success
