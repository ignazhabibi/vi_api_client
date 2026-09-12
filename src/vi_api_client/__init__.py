"""Viessmann API Client."""

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

__all__ = [
    "DEFAULT_SCOPES",
    "ENDPOINT_AUTHORIZE",
    "ENDPOINT_TOKEN",
    "AbstractAuth",
    "CommandResponse",
    "Device",
    "Feature",
    "FeatureControl",
    "FixtureViClient",
    "Gateway",
    "GatewayDeviceRefreshResult",
    "Installation",
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
]
