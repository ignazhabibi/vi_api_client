"""Tests for the OAuth credential document error contracts."""

import json

import pytest

from vi_api_client.credentials import CredentialDocument
from vi_api_client.exceptions import ViAuthError


def test_read_reports_unreadable_documents(tmp_path):
    """Reading an unreadable document should raise a library auth error."""
    # Arrange: Point the document at a directory, which cannot be read as a file.
    document = CredentialDocument(tmp_path)

    # Act and assert: The read failure becomes an auth error naming the path.
    with pytest.raises(ViAuthError, match="Could not read token file"):
        document.read()


def test_read_rejects_non_json_credential_values(tmp_path):
    """Documents containing non-JSON values should reject without modification."""
    # Arrange: Store a document whose expiry value cannot be represented as JSON.
    token_file = tmp_path / "tokens.json"
    original_content = '{"expires_at": NaN}'
    token_file.write_text(original_content, encoding="utf-8")

    # Act and assert: The invalid value rejects as an auth error.
    with pytest.raises(ViAuthError, match="invalid JSON data"):
        CredentialDocument(token_file).read()

    # Assert: The malformed source remains available for manual recovery.
    assert token_file.read_text(encoding="utf-8") == original_content


@pytest.mark.parametrize(
    "document_content",
    ["[]", '"tokens"', "5"],
    ids=["list", "string", "number"],
)
def test_read_rejects_non_object_documents(tmp_path, document_content: str):
    """Documents that are not JSON objects should reject with recovery guidance."""
    # Arrange: Store a JSON document with a non-object root.
    token_file = tmp_path / "tokens.json"
    token_file.write_text(document_content, encoding="utf-8")

    # Act and assert: The non-object root rejects as an auth error.
    with pytest.raises(ViAuthError, match="must contain a JSON object"):
        CredentialDocument(token_file).read()


def test_update_rejects_non_object_payloads(tmp_path):
    """Non-object update payloads should reject before reading the document."""
    # Arrange: Point the document at an absent file and supply a list payload.
    token_file = tmp_path / "tokens.json"

    # Act and assert: The non-object payload rejects as an auth error.
    with pytest.raises(ViAuthError, match="must be a JSON object"):
        CredentialDocument(token_file).update(["not-an-object"])


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    [
        ("expires_in", "600"),
        ("expires_at", True),
    ],
    ids=["string-expiry", "boolean-expiry"],
)
def test_read_rejects_invalid_expiry_fields(tmp_path, field_name: str, invalid_value):
    """Known expiry fields with invalid types should reject on read."""
    # Arrange: Store a document with an invalid known expiry field.
    token_file = tmp_path / "tokens.json"
    token_file.write_text(json.dumps({field_name: invalid_value}), encoding="utf-8")

    # Act and assert: The invalid expiry field rejects as an auth error.
    with pytest.raises(ViAuthError, match="must be a finite number"):
        CredentialDocument(token_file).read()
