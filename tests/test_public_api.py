"""Contract tests for the consumer-facing package-root API."""

import importlib

import pytest

import vi_api_client
from vi_api_client.utils import mask_pii


def test_package_root_exposes_only_the_documented_consumer_api():
    """Package-root exports should match the supported consumer API exactly."""
    expected_exports = {
        "AbstractAuth",
        "CommandResponse",
        "DEFAULT_SCOPES",
        "Device",
        "ENDPOINT_AUTHORIZE",
        "ENDPOINT_TOKEN",
        "Feature",
        "FeatureControl",
        "Gateway",
        "GatewayDeviceRefreshResult",
        "Installation",
        "FixtureViClient",
        "OAuth",
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
    }

    assert set(vi_api_client.__all__) == expected_exports
    assert all(hasattr(vi_api_client, export) for export in expected_exports)


def test_removed_client_names_are_not_importable():
    """The breaking rename should leave no transitional client imports."""
    # Assert: Retired public class and module paths must be unavailable.
    assert not hasattr(vi_api_client, "MockViClient")
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("vi_api_client.api")
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("vi_api_client.mock_client")


def test_package_root_hides_technical_helpers_but_preserves_utility_imports():
    """Technical helpers should stay in their existing modules, not the root API."""
    non_public_exports = {
        "API_BASE_URL",
        "AUTH_BASE_URL",
        "ENDPOINT_FEATURES",
        "ENDPOINT_GATEWAYS",
        "ENDPOINT_INSTALLATIONS",
        "SCOPE_IOT_USER",
        "SCOPE_OFFLINE_ACCESS",
        "mask_pii",
        "parse_cli_params",
        "parse_feature_flat",
    }

    assert all(not hasattr(vi_api_client, export) for export in non_public_exports)
    assert mask_pii("Authorization: Bearer secret") == "Authorization: Bearer ***"
