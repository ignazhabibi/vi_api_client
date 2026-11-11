"""Viessmann API Client."""

import logging

from ._types import FeatureValue, JsonValue, ValidationDetail
from .auth import AbstractAuth, OAuth
from .client import ViClient
from .const import DEFAULT_SCOPES, ENDPOINT_AUTHORIZE, ENDPOINT_TOKEN
from .exceptions import (
    ViAuthError,
    ViCommandRejectedError,
    ViConnectionError,
    ViDeviceCommunicationError,
    ViError,
    ViNotFoundError,
    ViRateLimitError,
    ViResponseError,
    ViServerInternalError,
    ViValidationError,
)
from .fixture_client import FixtureViClient
from .models import (
    Device,
    EventHistoryPage,
    Feature,
    FeatureConstraints,
    Gateway,
    GatewayDeviceRefreshResult,
    Installation,
    InstallationEvent,
    ScheduleConstraints,
)
from .privacy import redact_device, redact_feature, redact_sensitive
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
    "Device",
    "EventHistoryPage",
    "Feature",
    "FeatureConstraints",
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
]
