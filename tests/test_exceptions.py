"""Tests for the public exception signatures."""

from vi_api_client._types import ValidationDetail
from vi_api_client.exceptions import (
    ViCommandRejectedError,
    ViDeviceCommunicationError,
    ViRateLimitError,
    ViValidationError,
)


def test_validation_error_keeps_existing_positional_arguments() -> None:
    """Validation errors should retain their existing positional signature."""
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
    error = ViRateLimitError("Rate limited", "error-123", "RATE_LIMIT")

    assert error.error_id == "error-123"
    assert error.error_type == "RATE_LIMIT"
    assert error.retry_after is None


def test_command_rejected_error_carries_rejection_details() -> None:
    """A rejected command keeps its message and reason on the exception."""
    error = ViCommandRejectedError("Not allowed", "COMMAND_NOT_EXECUTABLE")

    assert str(error) == "Not allowed"
    assert error.message == "Not allowed"
    assert error.reason == "COMMAND_NOT_EXECUTABLE"
    assert error.error_id is None
    assert error.error_type is None


def test_command_rejected_error_falls_back_without_rejection_details() -> None:
    """A rejection without a message still raises a descriptive error."""
    error = ViCommandRejectedError()

    assert str(error) == "Command rejected"
    assert error.message is None
    assert error.reason is None


def test_command_rejected_error_accepts_error_envelope_fields() -> None:
    """A rejected command keeps the HTTP envelope's identification fields."""
    error = ViCommandRejectedError(
        "Device communication error",
        "COMMAND_NOT_EXECUTABLE",
        "trace-123",
        "DEVICE_COMMUNICATION_ERROR",
    )

    assert error.error_id == "trace-123"
    assert error.error_type == "DEVICE_COMMUNICATION_ERROR"


def test_device_communication_error_keeps_the_envelope_fields() -> None:
    """A device communication failure keeps the error classification fields."""
    error = ViDeviceCommunicationError(
        "Device communication error (reason: DEVICE_OFFLINE)",
        "trace-123",
        "DEVICE_COMMUNICATION_ERROR",
    )

    assert str(error) == "Device communication error (reason: DEVICE_OFFLINE)"
    assert error.error_id == "trace-123"
    assert error.error_type == "DEVICE_COMMUNICATION_ERROR"
