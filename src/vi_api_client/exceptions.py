"""Exceptions for Viessmann API Client."""

class VitoError(Exception):
    """Base exception for Viessmann API."""
    pass

class VitoAuthError(VitoError):
    """Authentication failed."""
    pass

class VitoConnectionError(VitoError):
    """Connection issue with the API."""
    pass
