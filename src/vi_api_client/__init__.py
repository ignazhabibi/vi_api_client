"""Viessmann API Client."""

from .api import ViClient
from .auth import AbstractAuth, OAuth
from .exceptions import (
    ViAuthError,
    ViConnectionError,
    ViError,
    ViNotFoundError,
    ViRateLimitError,
    ViServerInternalError,
    ViValidationError,
)
from .mock_client import MockViClient
from .models import Device, Feature

__all__ = [
    "AbstractAuth",
    "Device",
    "Feature",
    "MockViClient",
    "OAuth",
    "ViAuthError",
    "ViClient",
    "ViConnectionError",
    "ViError",
    "ViNotFoundError",
    "ViRateLimitError",
    "ViServerInternalError",
    "ViValidationError",
]
