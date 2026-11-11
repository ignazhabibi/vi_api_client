"""Immutable data models for Viessmann API objects and flat features."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType

from ._types import FeatureValue, JsonValue
from .exceptions import ViError


@dataclass(frozen=True)
class ScheduleConstraints:
    """Rules the API reports for writing a schedule.

    Attributes:
        max_entries: Maximum number of time slots per day.
        modes: Read-only modes a time slot may use.
        resolution: Grid in minutes that slot start and end times must follow.
        overlap_allowed: Whether time slots of one day may overlap.
        default_mode: Mode that applies outside the time slots.
    """

    max_entries: int | None = None
    modes: Sequence[str] | None = None
    resolution: int | None = None
    overlap_allowed: bool | None = None
    default_mode: str | None = None

    def __post_init__(self) -> None:
        """Store caller-owned mode sequences as immutable snapshots."""
        if self.modes is not None:
            object.__setattr__(self, "modes", tuple(self.modes))


@dataclass(frozen=True)
class FeatureConstraints:
    """Rules the API reports for writing one feature value.

    Attributes:
        value_type: Viessmann command parameter type, such as ``number`` or
            ``boolean``.
        min: Minimum value (numeric constraint).
        max: Maximum value (numeric constraint).
        step: Step size (numeric constraint).
        options: Read-only allowed values (enum constraint).
        min_length: Minimum length of string value.
        max_length: Maximum length of string value.
        pattern: Regex pattern for string validation.
        schedule: Rules for writing a schedule, if the API reports them.
    """

    value_type: str | None = None
    min: float | None = None
    max: float | None = None
    step: float | None = None
    options: Sequence[JsonValue] | None = None
    min_length: int | None = None
    max_length: int | None = None
    pattern: str | None = None
    schedule: ScheduleConstraints | None = None

    def __post_init__(self) -> None:
        """Store caller-owned option values as immutable snapshots."""
        if self.options is not None:
            object.__setattr__(self, "options", tuple(self.options))


@dataclass(frozen=True)
class ParameterWriteBinding:
    """Internal knowledge of the command that writes one feature.

    Not part of the public API and not exported from the package root: it
    records how the library sends a command (name, parameter, required
    parameter names, URI of the command's API feature) so consumers cannot
    construct writable features by hand.
    """

    api_feature_name: str
    command_name: str
    parameter_name: str
    required_parameters: Sequence[str]
    uri: str

    def __post_init__(self) -> None:
        """Store caller-owned parameter names as immutable snapshots."""
        object.__setattr__(self, "required_parameters", tuple(self.required_parameters))


@dataclass(frozen=True)
class CommandPairWriteBinding:
    """Internal knowledge of the on/off command pair that writes one feature.

    Not part of the public API. A boolean feature without a parameter setter
    is written by sending one of two commands without parameters: the on
    command for ``True`` and the off command for ``False``.
    """

    api_feature_name: str
    on_command_name: str
    off_command_name: str
    on_uri: str
    off_uri: str


# The write bindings a feature can carry.
type WriteBinding = ParameterWriteBinding | CommandPairWriteBinding


@dataclass(frozen=True)
class CommandParameter:
    """Internal metadata of one command parameter, not public API."""

    name: str
    required: bool | None = None
    value_type: str | None = None
    constraints: FeatureConstraints | None = None


@dataclass(frozen=True)
class DeviceCommand:
    """Internal metadata of one command an API feature reports."""

    name: str
    uri: str
    parameters: tuple[CommandParameter, ...] = ()


@dataclass(frozen=True)
class Feature:
    """One flat Viessmann feature with its current value.

    Attributes:
        name: Unique name of the feature (e.g. 'heating...curve.slope').
        value: The current value of the feature (type varies).
        unit: Optional unit string (e.g. 'celsius').
        is_enabled: Whether the feature is currently enabled on the device.
        is_ready: Whether the feature is ready for interaction.
        constraints: Optional `FeatureConstraints` rules for writing the
            value of a writable feature.
    """

    name: str
    value: FeatureValue
    unit: str | None
    is_enabled: bool
    is_ready: bool
    constraints: FeatureConstraints | None = None
    # Internal command knowledge: how the library sends the value's command.
    # The name carries no underscore so package modules can read and maintain
    # the binding under pyright strict, but the field is deliberately internal
    # and not exported from the package root; only the parser attaches it, so
    # hand-built features are read-only. It is kept out of the repr because
    # the URI carries installation, gateway, and device identifiers.
    write_binding: WriteBinding | None = field(default=None, repr=False)

    @property
    def is_writable(self) -> bool:
        """Return whether the feature carries an internal write binding."""
        return self.write_binding is not None

    @property
    def is_available(self) -> bool:
        """Return whether the feature is enabled and ready on the device."""
        return self.is_enabled and self.is_ready

    @property
    def is_settable(self) -> bool:
        """Return whether `set_feature` would accept a write in this snapshot.

        ``is_writable`` only says that the API offers a way to change the
        value; ``is_settable`` additionally requires the feature to be
        available. `set_feature` keeps its own checks with more specific
        error messages.
        """
        return self.is_writable and self.is_available


@dataclass(frozen=True)
class Device:
    """Immutable device snapshot.

    Attributes:
        id: Unique device identifier (GUID).
        gateway_serial: Serial number of the connected gateway.
        installation_id: ID of the installation.
        model_id: Model identifier (e.g. 'Simple_Device').
        device_type: Type classification (e.g. 'heating').
        status: Connection status (e.g. 'Online').
        features: Read-only features known when this snapshot was created.
    """

    id: str
    gateway_serial: str
    installation_id: str
    model_id: str
    device_type: str
    status: str
    features: Sequence[Feature] = field(default_factory=tuple)
    # Internal command knowledge: the name carries no underscore so package
    # modules can maintain it under pyright strict, but the field is not part
    # of the public compatibility contract. It is kept out of the repr because
    # command URIs carry installation, gateway, and device IDs.
    commands_by_api_feature: Mapping[str, tuple[DeviceCommand, ...]] = field(
        default_factory=dict[str, tuple[DeviceCommand, ...]],
        repr=False,
    )

    # Name index derived from `features`, so it is left out of comparisons.
    _features_by_name: Mapping[str, Feature] = field(
        init=False,
        repr=False,
        compare=False,
        default_factory=lambda: MappingProxyType({}),
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
        object.__setattr__(
            self,
            "commands_by_api_feature",
            MappingProxyType(dict(self.commands_by_api_feature)),
        )

    def get_feature(self, name: str) -> Feature | None:
        """Return the feature with this exact name, or None.

        Args:
            name: The exact name of the feature to find.

        Returns:
            The feature object if found, otherwise None.
        """
        return self._features_by_name.get(name)

    def get_command_names(self, feature_name: str) -> tuple[str, ...]:
        """Return the sorted command names one API feature reports, or ().

        Args:
            feature_name: The name of the API feature, such as
                ``heating.operating.programs.holiday``.

        Returns:
            The names of every command the API feature reported in the
            snapshot this device was built from, in sorted order.
        """
        commands = self.commands_by_api_feature.get(feature_name, ())
        return tuple(sorted(command.name for command in commands))


@dataclass(frozen=True)
class GatewayDeviceRefreshResult:
    """Result of refreshing devices through one gateway-scoped request.

    Attributes:
        refreshed_devices: Devices whose available features were refreshed.
        errors_by_device_id: Device-specific failures keyed by device ID.
    """

    refreshed_devices: Sequence[Device]
    errors_by_device_id: Mapping[str, ViError]

    def __post_init__(self) -> None:
        """Store caller-owned collections as immutable snapshots."""
        object.__setattr__(self, "refreshed_devices", tuple(self.refreshed_devices))
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
    address: Mapping[str, JsonValue] = field(default_factory=dict[str, JsonValue])

    def __post_init__(self) -> None:
        """Store caller-owned address data as an immutable snapshot."""
        object.__setattr__(self, "address", MappingProxyType(dict(self.address)))


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


@dataclass(frozen=True)
class InstallationEvent:
    """One installation event from the event history.

    Attributes:
        event_type: The provider event type (e.g. 'heating.curve.changed').
        created_at: When the provider recorded the event.
        event_timestamp: When the event occurred.
        gateway_serial: Serial of the gateway that reported the event, if known.
        body: The complete event body with its provider-specific structure.
        fields: Read-only complete event mapping, including unknown fields.
    """

    event_type: str
    created_at: str
    event_timestamp: str
    gateway_serial: str | None
    body: JsonValue
    fields: Mapping[str, JsonValue] = field(default_factory=dict[str, JsonValue])

    def __post_init__(self) -> None:
        """Store the complete event mapping as an immutable snapshot."""
        object.__setattr__(self, "fields", MappingProxyType(dict(self.fields)))


@dataclass(frozen=True)
class EventHistoryPage:
    """One page of an installation's event history.

    Attributes:
        events: The events returned for the requested page.
        next_cursor: The opaque continuation cursor for the next page, when
            the provider reported one.
    """

    events: Sequence[InstallationEvent]
    next_cursor: str | None = None

    def __post_init__(self) -> None:
        """Store caller-owned event collections as immutable snapshots."""
        object.__setattr__(self, "events", tuple(self.events))
