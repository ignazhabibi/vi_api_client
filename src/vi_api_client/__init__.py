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
    EventHistoryPage,
    Feature,
    FeatureControl,
    Gateway,
    GatewayDeviceRefreshResult,
    Installation,
    InstallationEvent,
    ScheduleConstraints,
)
from .utils import format_feature, mask_identifiers
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
    "EventHistoryPage",
    "Feature",
    "FeatureControl",
    "FeatureValue",
    "FixtureViClient",
    "Gateway",
    "GatewayDeviceRefreshResult",
    "Installation",
    "InstallationEvent",
    "JsonValue",
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
    "mask_identifiers",
    "validate_json_value",
]
