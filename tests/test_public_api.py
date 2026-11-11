"""Contract tests for the consumer-facing package-root API."""

import importlib
import logging
from dataclasses import fields

import vi_api_client
from vi_api_client import Feature, FeatureConstraints, ViClient


def test_package_logger_has_a_null_handler():
    """The package logger should not require consumer logging configuration."""
    package_handlers = logging.getLogger("vi_api_client").handlers
    assert any(isinstance(handler, logging.NullHandler) for handler in package_handlers)


def test_package_root_exposes_only_the_documented_consumer_api():
    """Package-root exports should match the supported consumer API exactly."""
    # Arrange: List the names consumers are allowed to import from the root.
    expected_exports = {
        "AbstractAuth",
        "DEFAULT_SCOPES",
        "Device",
        "ENDPOINT_AUTHORIZE",
        "ENDPOINT_TOKEN",
        "EventHistoryPage",
        "EventHistoryWindow",
        "Feature",
        "FeatureConstraints",
        "FeatureDeprecation",
        "FeatureValue",
        "Gateway",
        "GatewayDeviceRefreshResult",
        "Installation",
        "InstallationEvent",
        "JsonValue",
        "FixtureViClient",
        "OAuth",
        "ScheduleConstraints",
        "ValidationDetail",
        "ViAuthError",
        "ViClient",
        "ViCommandRejectedError",
        "ViConnectionError",
        "ViDeviceCommunicationError",
        "ViError",
        "ViNotFoundError",
        "ViRateLimitError",
        "ViResponseError",
        "ViServerInternalError",
        "ViValidationError",
        "format_feature",
        "redact_device",
        "redact_feature",
        "redact_sensitive",
        "validate_json_value",
    }

    # Assert: __all__ is exact and every listed name resolves.
    assert set(vi_api_client.__all__) == expected_exports
    assert all(hasattr(vi_api_client, export) for export in expected_exports)


def test_package_root_hides_technical_helpers_but_preserves_utility_imports():
    """Technical helpers should stay in their existing modules, not the root API."""
    # Arrange: List helpers that consumers may only import from their modules.
    # The write operations removed in the 7.0.0 rework stay absent.
    removed_public_exports = {
        "CommandResponse",
        "execute_command",
    }

    non_public_exports = {
        "API_BASE_URL",
        "AUTH_BASE_URL",
        "ENDPOINT_FEATURES",
        "ENDPOINT_GATEWAYS",
        "ENDPOINT_INSTALLATIONS",
        "SCOPE_IOT_USER",
        "SCOPE_OFFLINE_ACCESS",
        "parse_cli_params",
        "parse_command_outcome",
        "parse_device",
        "parse_event_history_page",
        "parse_gateway",
        "parse_installation",
        "parse_installation_event",
        "api_feature_to_flat_features",
        "CommandPairWriteBinding",
        "ParameterWriteBinding",
        "DeviceCommand",
        "CommandParameter",
        "FeatureControl",
    }

    # Assert: The root hides the helpers while their module paths still provide them.
    assert all(not hasattr(vi_api_client, export) for export in non_public_exports)
    assert all(not hasattr(vi_api_client, export) for export in removed_public_exports)
    assert not any(hasattr(ViClient, export) for export in removed_public_exports)
    utils = importlib.import_module("vi_api_client.utils")
    assert hasattr(utils, "parse_cli_params")


def test_exported_models_have_no_api_constructor():
    """API parsing stays internal; exported classes are plain data models."""
    # Arrange: Inspect every exported object that is a class.
    exported_classes = [
        exported
        for exported in (getattr(vi_api_client, name) for name in vi_api_client.__all__)
        if isinstance(exported, type)
    ]

    # Assert: The package exports models, none of which carries a from_api
    # constructor.
    assert exported_classes
    assert all(not hasattr(model, "from_api") for model in exported_classes)


def test_exported_feature_models_expose_no_command_details():
    """Command knowledge is internal; public models expose constraint rules."""
    # Arrange: The fields a consumer may read on the feature models.
    forbidden_fields = {
        "command_name",
        "parameter_name",
        "required_parameters",
        "parent_feature_name",
        "uri",
    }
    feature_fields = {field.name for field in fields(Feature)}
    constraints_fields = {field.name for field in fields(FeatureConstraints)}

    # Assert: No command detail appears on the public models.
    assert feature_fields.isdisjoint(forbidden_fields)
    assert constraints_fields.isdisjoint(forbidden_fields)
    assert not hasattr(Feature, "control")
