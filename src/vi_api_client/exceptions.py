"""Exceptions for Viessmann API Client."""

from typing import Any


class ViError(Exception):
    """Base class for all Viessmann errors."""

    def __init__(
        self,
        message: str,
        error_id: str | None = None,
        error_type: str | None = None,
    ) -> None:
        """Initialize the error.

        Args:
            message: The error message.
            error_id: Optional unique error identifier from the API.
            error_type: Optional Viessmann API error classification.
        """
        super().__init__(message)
        self.error_id = error_id
        self.error_type = error_type


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
    """429 Rate Limit Exceeded with optional server retry guidance."""

    def __init__(
        self,
        message: str,
        error_id: str | None = None,
        error_type: str | None = None,
        *,
        retry_after: float | None = None,
    ) -> None:
        """Initialize a rate-limit error.

        Args:
            message: The error message.
            error_id: Optional unique error identifier from the API.
            error_type: Optional Viessmann API error classification.
            retry_after: Optional server-recommended delay in seconds.
        """
        super().__init__(message, error_id, error_type)
        self.retry_after = retry_after


class ViResponseError(ViError):
    """A successful HTTP response that violates the expected API contract."""

    pass


class ViValidationError(ViError):
    """400 Bad Request or 422 Validation Error."""

    def __init__(
        self,
        message: str,
        error_id: str | None = None,
        validation_errors: list[dict[str, Any]] | None = None,
        error_type: str | None = None,
    ) -> None:
        """Initialize validation error.

        Args:
            message: The error message.
            error_id: Optional unique error ID.
            validation_errors: List of detailed validation issues.
            error_type: Optional Viessmann API error classification.
        """
        detailed_msg = message
        if validation_errors:
            # Build a pretty error message from details
            details = "; ".join(
                [
                    f"{error.get('message')} (path: {error.get('path')})"
                    for error in validation_errors
                ]
            )
            detailed_msg = f"{message}: {details}"

        super().__init__(detailed_msg, error_id, error_type)
        self.validation_errors = validation_errors


class ViServerInternalError(ViError):
    """500/502 Internal Server Error."""

    pass
