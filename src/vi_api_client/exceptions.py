"""Exceptions for Viessmann API Client."""

from typing import Any


class ViError(Exception):
    """Base class for all Viessmann errors."""

    def __init__(self, message: str, error_id: str | None = None):
        """Initialize the error."""
        super().__init__(message)
        self.error_id = error_id


class ViConnectionError(ViError):
    """Network connection issues (DNS, Timeout, etc)."""

    pass


class ViAuthError(ViError):
    """401 Unauthorized or 403 Forbidden."""

    pass


class ViNotFoundError(ViError):
    """404 Resource not found."""

    pass


class ViRateLimitError(ViError):
    """429 Rate Limit Exceeded."""

    pass


class ViValidationError(ViError):
    """400 Bad Request or 422 Validation Error."""

    def __init__(
        self,
        message: str,
        error_id: str | None = None,
        validation_errors: list[dict[str, Any]] | None = None,
    ):
        """Initialize validation error."""
        detailed_msg = message
        if validation_errors:
            # Baue eine schöne Fehlermeldung aus den Details
            details = "; ".join(
                [
                    f"{e.get('message')} (path: {e.get('path')})"
                    for e in validation_errors
                ]
            )
            detailed_msg = f"{message}: {details}"

        super().__init__(detailed_msg, error_id)
        self.validation_errors = validation_errors


class ViServerInternalError(ViError):
    """500/502 Internal Server Error."""

    pass
