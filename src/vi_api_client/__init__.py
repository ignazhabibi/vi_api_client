"""Viessmann API Client."""

import logging

from ._types import FeatureValue, JsonValue, ValidationDetail
from .auth import AbstractAuth, OAuth
from .client import ViClient
from .const import DEFAULT_SCOPES, ENDPOINT_AUTHORIZE, ENDPOINT_TOKEN
from .exceptions import (
    ViAuthError,
    ViConnectionError,
    ViError,
    ViNotFoundError,
    ViRateLimitError,
    ViResponseError,
    ViServerInternalError,
    ViValidationError,
)
from .fixture_client import FixtureViClient
from .models import (
    CommandResponse,
    Device,
    Feature,
    FeatureControl,
    Gateway,
    GatewayDeviceRefreshResult,
    Installation,
)
from .utils import format_feature
from .validation import validate_json_value

# Library best practice: consume the package logger without requiring
# consumer-side logging configuration.
logging.getLogger(__name__).addHandler(logging.NullHandler())

__all__ = [
    "DEFAULT_SCOPES",
    "ENDPOINT_AUTHORIZE",
    "ENDPOINT_TOKEN",
    "AbstractAuth",
    "CommandResponse",
    "Device",
    "Feature",
    "FeatureControl",
    "FeatureValue",
    "FixtureViClient",
    "Gateway",
    "GatewayDeviceRefreshResult",
    "Installation",
    "JsonValue",
    "OAuth",
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
    "validate_json_value",
]
