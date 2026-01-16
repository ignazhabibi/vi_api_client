"""Viessmann API Client."""

from .auth import AbstractAuth, OAuth
from .api import ViClient
from .mock_client import MockViClient
from .exceptions import (
    ViError, 
    ViAuthError, 
    ViConnectionError,
    ViNotFoundError,
    ViRateLimitError,
    ViValidationError,
    ViServerInternalError
)
from .models import Device, Feature

__all__ = [
    "AbstractAuth",
    "ViClient",
    "Device",
    "Feature",
    "MockViClient",
    "OAuth",
    "ViAuthError",
    "ViConnectionError",
    "ViError",
    "ViNotFoundError",
    "ViRateLimitError",
    "ViValidationError",
    "ViServerInternalError",
]
