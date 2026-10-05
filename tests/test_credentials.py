"""Tests for reading, merging, and safely replacing the credential document."""

import json
import os
import stat
from pathlib import Path

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


def test_read_rejects_non_json_credential_values(token_file):
    """Documents containing non-JSON values should reject without modification."""
    # Arrange: Store a document whose expiry value cannot be represented as JSON.
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
def test_read_rejects_non_object_documents(token_file, document_content: str):
    """Documents that are not JSON objects should reject with recovery guidance."""
    # Arrange: Store a JSON document with a non-object root.
    token_file.write_text(document_content, encoding="utf-8")

    # Act and assert: The non-object root rejects as an auth error.
    with pytest.raises(ViAuthError, match="must contain a JSON object"):
        CredentialDocument(token_file).read()


@pytest.mark.parametrize(
    ("credential_fields", "message"),
    [
        (["not-an-object"], "must be a JSON object"),
        ({"access_token": 1}, "access_token must be a string"),
        ({"client_id": 1}, "client_id must be a string"),
        ({"nested": object()}, "invalid JSON data"),
    ],
    ids=["not-an-object", "token-not-text", "configuration-not-text", "non-json"],
)
def test_update_rejects_invalid_fields_without_overwriting(
    token_file, credential_fields, message
):
    """Invalid updates must leave the existing document byte-for-byte intact."""
    # Arrange: Persist a valid document that must survive the update.
    original_content = '{"access_token": "existing"}'
    token_file.write_text(original_content, encoding="utf-8")

    # Act and assert: The invalid update rejects without writing.
    with pytest.raises(ViAuthError, match=message):
        CredentialDocument(token_file).update(credential_fields)

    # Assert: The stored document is unchanged.
    assert token_file.read_text(encoding="utf-8") == original_content


@pytest.mark.parametrize(
    ("field_name", "invalid_value", "message"),
    [
        ("access_token", 1, "must be a string"),
        ("refresh_token", 1, "must be a string"),
        ("token_type", 1, "must be a string"),
        ("client_id", 1, "must be a string"),
        ("redirect_uri", 1, "must be a string"),
        ("expires_in", "600", "must be a finite number"),
        ("expires_at", True, "must be a finite number"),
    ],
    ids=[
        "access-token",
        "refresh-token",
        "token-type",
        "client-id",
        "redirect-uri",
        "expires-in",
        "expires-at",
    ],
)
def test_read_rejects_invalid_known_fields(
    token_file, field_name: str, invalid_value, message: str
):
    """Known credential fields with invalid types should reject on read."""
    # Arrange: Store a document with one invalid known field.
    token_file.write_text(json.dumps({field_name: invalid_value}), encoding="utf-8")

    # Act and assert: The invalid field rejects as an auth error naming it.
    with pytest.raises(ViAuthError, match=f"{field_name} {message}"):
        CredentialDocument(token_file).read()


def test_read_rejects_invalid_utf8_without_modifying_the_document(token_file):
    """Undecodable documents should remain intact and explain recovery."""
    # Arrange: Store bytes that cannot be decoded as UTF-8.
    invalid_content = b"\xff"
    token_file.write_bytes(invalid_content)

    # Act and assert: Reading reports the corrupted document.
    with pytest.raises(ViAuthError, match="Repair or remove the file"):
        CredentialDocument(token_file).read()

    # Assert: The original bytes remain available for manual recovery.
    assert token_file.read_bytes() == invalid_content


def test_update_rejects_file_that_becomes_malformed(token_file):
    """Updates should not overwrite a document that became malformed."""
    # Arrange: Corrupt the document after it was configured.
    invalid_content = "{invalid"
    token_file.write_text(invalid_content, encoding="utf-8")
    credentials = {"access_token": "new-token"}

    # Act and assert: Saving should preserve the malformed file and explain recovery.
    with pytest.raises(ViAuthError, match="Repair or remove the file"):
        CredentialDocument(token_file).update(credentials)

    # Assert: The malformed token file should not be overwritten by the new token.
    assert token_file.read_text(encoding="utf-8") == invalid_content


def test_update_merges_new_tokens_with_existing_configuration(token_file):
    """Token updates should retain configuration and unknown stored fields."""
    # Arrange: Store configuration and a field from a future credential format.
    token_file.write_text(
        json.dumps(
            {
                "client_id": "configured-client",
                "custom_metadata": {"source": "user"},
                "access_token": "old-token",
            }
        ),
        encoding="utf-8",
    )
    credentials = {"access_token": "new-token", "refresh_token": "refresh"}

    # Act: Persist the updated token information.
    CredentialDocument(token_file).update(credentials)

    # Assert: Existing non-token content should survive the update.
    assert json.loads(token_file.read_text(encoding="utf-8")) == {
        "client_id": "configured-client",
        "custom_metadata": {"source": "user"},
        "access_token": "new-token",
        "refresh_token": "refresh",
    }


def test_update_reports_missing_parent_directory_without_creating_it(tmp_path):
    """Credential persistence should not create missing parent directories."""
    # Arrange: Point the document below a directory that does not exist.
    missing_parent = tmp_path / "missing"
    token_file = missing_parent / "tokens.json"
    credentials = {"access_token": "new-token"}

    # Act and assert: Saving should explain the filesystem failure.
    with pytest.raises(ViAuthError, match="parent directory"):
        CredentialDocument(token_file).update(credentials)

    # Assert: Persistence should not create the directory as a side effect.
    assert not missing_parent.exists()


def test_update_uses_atomic_replacement_in_the_token_directory(token_file, monkeypatch):
    """Credential updates should replace the destination with a sibling temp file."""
    # Arrange: Track the low-level replacement while preserving its behavior.
    replacement_calls = []
    original_replace = os.replace

    def track_replace(source, destination):
        replacement_calls.append((source, destination))
        original_replace(source, destination)

    monkeypatch.setattr("vi_api_client.credentials.os.replace", track_replace)
    credentials = {"access_token": "new-token"}

    # Act: Save new credentials.
    CredentialDocument(token_file).update(credentials)

    # Assert: One replacement moved a sibling temporary file onto the destination.
    ((source, destination),) = replacement_calls
    assert Path(source).parent == token_file.parent
    assert destination == token_file


def test_update_preserves_original_file_when_replacement_fails(token_file, monkeypatch):
    """Failed replacement should retain the previous credential document."""
    # Arrange: Seed a credential document and make the final replacement fail.
    original_content = '{"access_token": "old-token"}'
    token_file.write_text(original_content, encoding="utf-8")
    credentials = {"access_token": "new-token"}

    def raise_replace(source, destination):
        raise OSError("simulated replacement failure")

    monkeypatch.setattr("vi_api_client.credentials.os.replace", raise_replace)

    # Act and assert: A failed replacement should become a library auth error.
    with pytest.raises(ViAuthError, match="save token"):
        CredentialDocument(token_file).update(credentials)

    # Assert: The old file and no temporary artifacts should remain.
    assert token_file.read_text(encoding="utf-8") == original_content
    assert list(token_file.parent.glob(".tokens.json.*.tmp")) == []


def test_update_reports_temporary_file_write_failures(token_file, monkeypatch):
    """Credential persistence should translate temporary-file creation failures."""
    # Arrange: Make temporary-file creation fail before the token file is replaced.
    token_file.write_text('{"access_token": "old-token"}', encoding="utf-8")
    credentials = {"access_token": "new-token"}

    def raise_temporary_file_error(*args, **kwargs):
        raise OSError("simulated write failure")

    monkeypatch.setattr(
        "vi_api_client.credentials.NamedTemporaryFile", raise_temporary_file_error
    )

    # Act and assert: The operating-system failure should be a library auth error.
    with pytest.raises(ViAuthError, match="save token"):
        CredentialDocument(token_file).update(credentials)

    # Assert: A failed write should leave the existing credentials unchanged.
    assert token_file.read_text(encoding="utf-8") == '{"access_token": "old-token"}'


def test_update_removes_temporary_file_after_serialization_failure(
    token_file, monkeypatch
):
    """A serialization failure should not leave a temporary credential file."""
    # Arrange: Make writing the temporary JSON document fail after it is created.
    credentials = {"access_token": "new-token"}

    def raise_serialization_error(*args, **kwargs):
        raise OSError("simulated serialization failure")

    monkeypatch.setattr(
        "vi_api_client.credentials.json.dump", raise_serialization_error
    )

    # Act and assert: Saving should report the error through the auth boundary.
    with pytest.raises(ViAuthError, match="save token"):
        CredentialDocument(token_file).update(credentials)

    # Assert: The failed write should not leave credentials or temporary files behind.
    assert not token_file.exists()
    assert list(token_file.parent.glob(".tokens.json.*.tmp")) == []


def test_update_restricts_file_permissions_to_owner(token_file):
    """Credential documents should be owner-readable and owner-writable only."""
    # Arrange: Prepare the credentials to persist.
    credentials = {"access_token": "new-token"}

    # Act: Persist the credentials.
    CredentialDocument(token_file).update(credentials)

    # Assert: The file mode should not grant group or other access.
    assert stat.S_IMODE(token_file.stat().st_mode) == 0o600
