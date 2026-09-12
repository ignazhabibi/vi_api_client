"""Feature command contracts shared by live and fixture-backed clients."""

from collections.abc import Callable
from dataclasses import replace
from typing import Any

import pytest

from vi_api_client.client import ViClient
from vi_api_client.fixture_client import FixtureViClient
from vi_api_client.models import Device, Feature, FeatureControl


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


def _create_live_client(adapter: _RecordingCommandAdapter) -> ViClient:
    """Create a live client with a recording command adapter."""
    client = ViClient.__new__(ViClient)
    client._command_adapter = adapter
    return client


def _create_fixture_client(adapter: _RecordingCommandAdapter) -> FixtureViClient:
    """Create a fixture-backed client with a recording command adapter."""
    client = FixtureViClient.__new__(FixtureViClient)
    client._command_adapter = adapter
    return client


def _feature(
    name: str,
    value: Any,
    control: FeatureControl | None = None,
    *,
    is_enabled: bool = True,
    is_ready: bool = True,
) -> Feature:
    """Build a feature for feature-command contract tests."""
    return Feature(name, value, None, is_enabled, is_ready, control)


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


@pytest.mark.parametrize("create_client", [_create_live_client, _create_fixture_client])
@pytest.mark.asyncio
async def test_set_feature_uses_current_canonical_feature_and_updates_snapshot(
    create_client: Callable[[_RecordingCommandAdapter], ViClient],
):
    """The current device feature controls validation, payload, and local update."""
    # Arrange: The supplied stale feature is read-only, while the snapshot is writable.
    adapter = _RecordingCommandAdapter()
    client = create_client(adapter)
    canonical_control = _control(
        required_params=["target", "enabled", "count", "label"]
    )
    canonical = _feature("heating.mode.target", "old", canonical_control)
    device = _device(
        [
            canonical,
            _feature("heating.mode.enabled", False),
            _feature("heating.mode.count", 0),
            _feature("heating.mode.label", ""),
        ]
    )
    stale = _feature("heating.mode.target", "stale")

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
        (_feature("absent", "value", _control()), [], "not present"),
        (
            _feature("heating.mode.target", "old"),
            [_feature("heating.mode.target", "old", _control(), is_enabled=False)],
            "disabled",
        ),
        (
            _feature("heating.mode.target", "old"),
            [_feature("heating.mode.target", "old", _control(), is_ready=False)],
            "not ready",
        ),
        (
            _feature("heating.mode.target", "old"),
            [
                _feature(
                    "heating.mode.target",
                    "old",
                    _control(required_params=["target", "other"]),
                )
            ],
            "Required dependency",
        ),
    ],
)
@pytest.mark.parametrize("create_client", [_create_live_client, _create_fixture_client])
@pytest.mark.asyncio
async def test_set_feature_rejects_invalid_local_contract_without_adapter_io(
    create_client: Callable[[_RecordingCommandAdapter], ViClient],
    feature: Feature,
    device_features: list[Feature],
    error: str,
):
    """Local membership, availability, and dependency failures precede adapter I/O."""
    # Arrange: Each scenario supplies an invalid current-device command contract.
    adapter = _RecordingCommandAdapter()
    client = create_client(adapter)
    device = _device(device_features)

    # Act and assert: Invalid local state never reaches the adapter.
    with pytest.raises(ValueError, match=error):
        await client.set_feature(device, feature, "new")
    assert adapter.calls == []


@pytest.mark.parametrize(
    ("sibling", "error"),
    [
        (_feature("heating.mode.other", "value", is_enabled=False), "disabled"),
        (_feature("heating.mode.other", "value", is_ready=False), "not ready"),
        (_feature("heating.mode.other", None), "has no value"),
    ],
)
@pytest.mark.parametrize("create_client", [_create_live_client, _create_fixture_client])
@pytest.mark.asyncio
async def test_set_feature_rejects_unavailable_required_dependencies_before_constraints(
    create_client: Callable[[_RecordingCommandAdapter], ViClient],
    sibling: Feature,
    error: str,
):
    """Required dependency validation precedes target constraint validation and I/O."""
    # Arrange: Both the sibling and target constraint are invalid.
    adapter = _RecordingCommandAdapter()
    client = create_client(adapter)
    control = replace(_control(required_params=["target", "other"]), max=10)
    target = _feature("heating.mode.target", "old", control)
    device = _device([target, sibling])

    # Act and assert: The dependency error takes precedence over the target constraint.
    with pytest.raises(ValueError, match=error):
        await client.set_feature(device, target, 11)
    assert adapter.calls == []


@pytest.mark.parametrize("create_client", [_create_live_client, _create_fixture_client])
@pytest.mark.asyncio
async def test_set_feature_uses_canonical_constraints_before_adapter_io(
    create_client: Callable[[_RecordingCommandAdapter], ViClient],
):
    """Target constraints come from the current device feature before adapter I/O."""
    # Arrange: The caller supplies stale permissive metadata for a constrained target.
    adapter = _RecordingCommandAdapter()
    client = create_client(adapter)
    canonical = _feature("heating.mode.target", 5, replace(_control(), max=10))
    stale = _feature("heating.mode.target", 5, _control())
    device = _device([canonical])

    # Act and assert: The canonical maximum rejects before either adapter can run.
    with pytest.raises(ValueError, match="max"):
        await client.set_feature(device, stale, 11)
    assert adapter.calls == []


@pytest.mark.parametrize("create_client", [_create_live_client, _create_fixture_client])
@pytest.mark.asyncio
async def test_set_feature_omits_optional_siblings_and_preserves_rejected_device(
    create_client: Callable[[_RecordingCommandAdapter], ViClient],
):
    """Only required dependencies are sent and API rejection retains the snapshot."""
    # Arrange: The optional sibling is available but must not be sent automatically.
    adapter = _RecordingCommandAdapter(success=False)
    client = create_client(adapter)
    control = _control(required_params=[])
    canonical = _feature("heating.mode.target", "old", control)
    device = _device([canonical, _feature("heating.mode.optional", "present")])

    # Act: The command adapter rejects the generated command.
    response, returned_device = await client.set_feature(device, canonical, "new")

    # Assert: Optional state is absent and the exact original snapshot returns.
    assert not response.success
    assert adapter.calls == [(control, {"target": "new"})]
    assert returned_device is device


@pytest.mark.parametrize("create_client", [_create_live_client, _create_fixture_client])
@pytest.mark.asyncio
async def test_execute_command_requires_complete_available_command_without_mutation(
    create_client: Callable[[_RecordingCommandAdapter], ViClient],
):
    """Explicit commands require complete payloads but preserve additional parameters."""
    # Arrange: The complete payload includes an allowed extra parameter.
    adapter = _RecordingCommandAdapter()
    client = create_client(adapter)
    control = _control(required_params=["target", "dependency"])
    feature = _feature("heating.mode.target", "old", control)
    parameters = {"target": "new", "dependency": None, "extra": "kept"}

    # Act: Submit the explicit payload without high-level augmentation.
    response = await client.execute_command(feature, parameters)

    # Assert: The adapter receives the original mapping unchanged.
    assert response.success
    assert adapter.calls == [(control, parameters)]
    assert parameters == {"target": "new", "dependency": None, "extra": "kept"}


@pytest.mark.parametrize(
    ("feature", "parameters", "error"),
    [
        (_feature("target", "old"), {"target": "new"}, "read-only"),
        (
            _feature("target", "old", _control(), is_enabled=False),
            {"target": "new"},
            "disabled",
        ),
        (
            _feature("target", "old", _control(), is_ready=False),
            {"target": "new"},
            "not ready",
        ),
        (_feature("target", "old", _control()), {}, "target parameter"),
        (
            _feature("target", "old", _control(required_params=["target", "other"])),
            {"target": "new"},
            "required parameter",
        ),
    ],
)
@pytest.mark.parametrize("create_client", [_create_live_client, _create_fixture_client])
@pytest.mark.asyncio
async def test_execute_command_rejects_invalid_local_contract_without_adapter_io(
    create_client: Callable[[_RecordingCommandAdapter], ViClient],
    feature: Feature,
    parameters: dict[str, Any],
    error: str,
):
    """Explicit-command preconditions reject before adapter I/O."""
    # Arrange: Each feature or payload violates the low-level command contract.
    adapter = _RecordingCommandAdapter()
    client = create_client(adapter)

    # Act and assert: Local invalidity leaves the adapter untouched.
    with pytest.raises(ValueError, match=error):
        await client.execute_command(feature, parameters)
    assert adapter.calls == []
