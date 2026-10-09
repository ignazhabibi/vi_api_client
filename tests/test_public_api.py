"""Contract tests for the consumer-facing package-root API."""

import importlib
import logging

import vi_api_client


def test_package_logger_has_a_null_handler():
    """The package logger should not require consumer logging configuration."""
    package_handlers = logging.getLogger("vi_api_client").handlers
    assert any(isinstance(handler, logging.NullHandler) for handler in package_handlers)


def test_package_root_exposes_only_the_documented_consumer_api():
    """Package-root exports should match the supported consumer API exactly."""
    # Arrange: List the names consumers are allowed to import from the root.
    expected_exports = {
        "AbstractAuth",
        "CommandResponse",
        "DEFAULT_SCOPES",
        "Device",
        "ENDPOINT_AUTHORIZE",
        "ENDPOINT_TOKEN",
        "EventHistoryPage",
        "Feature",
        "FeatureControl",
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
        "ViConnectionError",
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
    non_public_exports = {
        "API_BASE_URL",
        "AUTH_BASE_URL",
        "ENDPOINT_FEATURES",
        "ENDPOINT_GATEWAYS",
        "ENDPOINT_INSTALLATIONS",
        "SCOPE_IOT_USER",
        "SCOPE_OFFLINE_ACCESS",
        "parse_cli_params",
        "api_feature_to_flat_features",
    }

    # Assert: The root hides the helpers while their module paths still provide them.
    assert all(not hasattr(vi_api_client, export) for export in non_public_exports)
    utils = importlib.import_module("vi_api_client.utils")
    assert hasattr(utils, "parse_cli_params")
