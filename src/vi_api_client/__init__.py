"""Viessmann API Client."""

from .api import ViClient
from .auth import AbstractAuth, OAuth
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
from .mock_client import MockViClient
from .models import Device, Feature, GatewayDeviceRefreshResult
from .utils import mask_pii

__all__ = [
    "AbstractAuth",
    "Device",
    "Feature",
    "GatewayDeviceRefreshResult",
    "MockViClient",
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
    "mask_pii",
]
