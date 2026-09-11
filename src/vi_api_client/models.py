"""Data models for Viessmann API objects (Flat Architecture)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from .exceptions import ViError


@dataclass(frozen=True)
class FeatureControl:
    """Encapsulates write logic for a specific feature.

    Attributes:
        command_name: Name of the command to execute (e.g. 'setCurve').
        param_name: Name of the parameter mapping to this feature (e.g. 'slope').
        required_params: Read-only parameter names required by this command.
            Used for dependency resolution (e.g. ['slope', 'shift']).
        parent_feature_name: Name of the parent feature in the API.
            Used to find sibling features during dependency resolution.
        uri: The API URI to POST the command to.
        min: Minimum value (numeric constraint).
        max: Maximum value (numeric constraint).
        step: Step size (numeric constraint).
        value_type: Viessmann command parameter type, such as ``number`` or
            ``boolean``.
        options: Read-only allowed values (enum constraint).
        min_length: Minimum length of string value.
        max_length: Maximum length of string value.
        pattern: Regex pattern for string validation.
    """

    command_name: str
    param_name: str
    required_params: Sequence[str]
    parent_feature_name: str
    uri: str
    min: float | None = None
    max: float | None = None
    step: float | None = None
    value_type: str | None = None
    options: Sequence[Any] | None = None
    min_length: int | None = None
    max_length: int | None = None
    pattern: str | None = None

    def __post_init__(self) -> None:
        """Store caller-owned sequences as immutable snapshots."""
        object.__setattr__(self, "required_params", tuple(self.required_params))
        if self.options is not None:
            object.__setattr__(self, "options", tuple(self.options))


@dataclass(frozen=True)
class Feature:
    """Representation of a Viessmann feature (Flat).

    Attributes:
        name: Unique name of the feature (e.g. 'heating...curve.slope').
        value: The current value of the feature (type varies).
        unit: Optional unit string (e.g. 'celsius').
        is_enabled: Whether the feature is currently enabled on the device.
        is_ready: Whether the feature is ready for interaction.
        control: Optional control object if the feature is writable.
    """

    name: str
    value: Any
    unit: str | None
    is_enabled: bool
    is_ready: bool
    control: FeatureControl | None = None

    @property
    def is_writable(self) -> bool:
        """Check if feature is writable."""
        return self.control is not None


@dataclass(frozen=True)
class Device:
    """Representation of a Viessmann device.

    Attributes:
        id: Unique device identifier (GUID).
        gateway_serial: Serial number of the connected gateway.
        installation_id: ID of the installation.
        model_id: Model identifier (e.g. 'Simple_Device').
        device_type: Type classification (e.g. 'heating').
        status: Connection status (e.g. 'Online').
        features: Read-only associated features.
    """

    id: str
    gateway_serial: str
    installation_id: str
    model_id: str
    device_type: str
    status: str
    features: Sequence[Feature] = field(default_factory=tuple)

    # Internal cache for O(1) lookup
    _features_by_name: Mapping[str, Feature] = field(
        init=False, repr=False, default_factory=lambda: MappingProxyType({})
    )

    def __post_init__(self) -> None:
        """Store features and their lookup cache as immutable snapshots.

        Raises:
            ValueError: If more than one feature has the same name.
        """
        features = tuple(self.features)
        feature_map: dict[str, Feature] = {}
        for feature in features:
            if feature.name in feature_map:
                raise ValueError(f"Duplicate feature name: {feature.name}")
            feature_map[feature.name] = feature
        object.__setattr__(self, "features", features)
        object.__setattr__(self, "_features_by_name", MappingProxyType(feature_map))

    def get_feature(self, name: str) -> Feature | None:
        """O(1) lookup helper.

        Args:
            name: The exact name of the feature to find.

        Returns:
            The feature object if found, otherwise None.
        """
        if self._features_by_name is None:
            return None
        return self._features_by_name.get(name)

    @classmethod
    def from_api(
        cls, data: dict[str, Any], gateway_serial: str, installation_id: str
    ) -> Device:
        """Create Device from API data.

        Args:
            data: The dictionary returned by the API for a device.
            gateway_serial: Serial number of the gateway this device belongs to.
            installation_id: ID of the installation this device belongs to.

        Returns:
            A new Device instance.
        """
        return cls(
            id=data.get("id", ""),
            gateway_serial=gateway_serial,
            installation_id=installation_id,
            model_id=data.get("modelId", ""),
            device_type=data.get("deviceType", ""),
            status=data.get("status", ""),
        )


@dataclass(frozen=True)
class GatewayDeviceRefreshResult:
    """Result of refreshing devices through one gateway-scoped request.

    Attributes:
        updated_devices: Devices whose enabled and ready features were refreshed.
        errors_by_device_id: Device-specific failures keyed by device ID.
    """

    updated_devices: Sequence[Device]
    errors_by_device_id: Mapping[str, ViError]

    def __post_init__(self) -> None:
        """Store caller-owned collections as immutable snapshots."""
        object.__setattr__(self, "updated_devices", tuple(self.updated_devices))
        object.__setattr__(
            self,
            "errors_by_device_id",
            MappingProxyType(dict(self.errors_by_device_id)),
        )

    @property
    def is_complete(self) -> bool:
        """Return whether every requested device was refreshed."""
        return not self.errors_by_device_id


@dataclass(frozen=True)
class CommandResponse:
    """Response from a command execution.

    Attributes:
        success: Whether the command was accepted by the API.
        message: Optional success/error message.
        reason: Optional failure reason/code.
    """

    success: bool
    message: str | None = None
    reason: str | None = None

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> CommandResponse:
        """Create from API response.

        Args:
            data: The JSON response dictionary from the API.

        Returns:
            A CommandResponse instance indicating success/failure.
        """
        root = data.get("data", data)
        # Handle string "True"/"False" or boolean
        success_raw = root.get("success")
        if isinstance(success_raw, str):
            success = success_raw.lower() == "true"
        else:
            success = bool(success_raw)

        return cls(
            success=success, message=root.get("message"), reason=root.get("reason")
        )


@dataclass(frozen=True)
class Installation:
    """Representation of an installation.

    Attributes:
        id: Unique installation ID (numeric string).
        description: User-provided description.
        alias: User-provided alias.
        address: Read-only physical address mapping.
    """

    id: str
    description: str
    alias: str
    address: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Store caller-owned address data as an immutable snapshot."""
        object.__setattr__(self, "address", MappingProxyType(dict(self.address)))

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> Installation:
        """Create Installation from API data.

        Args:
            data: The JSON dictionary representing an installation.

        Returns:
            A new Installation instance.
        """
        return cls(
            id=str(data.get("id", "")),
            description=data.get("description", ""),
            alias=data.get("alias", ""),
            address=data.get("address", {}),
        )


@dataclass(frozen=True)
class Gateway:
    """Representation of a gateway.

    Attributes:
        serial: Gateway serial number.
        version: Firmware version.
        status: Connection status.
        installation_id: ID of the parent installation.
    """

    serial: str
    version: str
    status: str
    installation_id: str

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> Gateway:
        """Create Gateway from API data.

        Args:
            data: The JSON dictionary representing a gateway.

        Returns:
            A new Gateway instance.
        """
        return cls(
            serial=data.get("serial", ""),
            version=data.get("version", ""),
            status=data.get("status", ""),
            installation_id=str(data.get("installationId", "")),
        )
