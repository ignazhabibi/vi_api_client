"""Tests for the public exception signatures."""

from vi_api_client._types import ValidationDetail
from vi_api_client.exceptions import ViRateLimitError, ViValidationError


def test_validation_error_keeps_existing_positional_arguments() -> None:
    # Arrange: Use the public positional signature supported before error types.
    validation_errors: list[ValidationDetail] = [
        {"message": "Invalid", "path": "feature"}
    ]

    # Act: Construct the error with its existing three positional arguments.
    error = ViValidationError("Bad request", "error-123", validation_errors)

    # Assert: Existing arguments retain their meaning and error type is optional.
    assert error.error_id == "error-123"
    assert error.error_type is None
    assert error.validation_errors == validation_errors


def test_rate_limit_error_keeps_existing_positional_arguments() -> None:
    """Rate-limit errors should retain their existing positional signature."""
    # Arrange and Act: Construct with positional arguments supported before retry data.
    error = ViRateLimitError("Rate limited", "error-123", "RATE_LIMIT")

    # Assert: Existing values retain their meanings and retry data defaults to none.
    assert error.error_id == "error-123"
    assert error.error_type == "RATE_LIMIT"
    assert error.retry_after is None
