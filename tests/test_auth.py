"""Tests for vitoclient.auth module."""

import json
import os
import stat
import time
from pathlib import Path
from unittest.mock import MagicMock

import aiohttp
import pytest
from aioresponses import aioresponses

from vi_api_client.api import ViClient
from vi_api_client.auth import AbstractAuth, OAuth
from vi_api_client.const import API_BASE_URL, ENDPOINT_INSTALLATIONS, ENDPOINT_TOKEN
from vi_api_client.exceptions import ViAuthError


def test_abstract_auth_cannot_be_instantiated():
    """AbstractAuth should not be instantiated directly."""
    # Arrange, Act and Assert: Complete test in one step.
    with pytest.raises(TypeError):
        AbstractAuth(MagicMock())  # type: ignore[reportAbstractUsage]


@pytest.fixture
def oauth(tmp_path):
    """Create a ViessmannOAuth instance for testing."""
    token_file = tmp_path / "tokens.json"
    return OAuth(
        client_id="test_client_id",
        redirect_uri="http://localhost:4200/",
        token_file=str(token_file),
    )


@pytest.fixture
def oauth_with_tokens(tmp_path):
    """Create a OAuth with pre-existing tokens."""
    token_file = tmp_path / "tokens.json"
    tokens = {
        "access_token": "test_access_token",
        "refresh_token": "test_refresh_token",
        "expires_in": 3600,
        "expires_at": 9999999999,  # Far future
        "token_type": "Bearer",
    }
    token_file.write_text(json.dumps(tokens))
    return OAuth(
        client_id="test_client_id",
        redirect_uri="http://localhost:4200/",
        token_file=str(token_file),
    )


def test_get_authorization_url(oauth):
    """Test authorization URL generation."""
    # Arrange: Prepare test data and fixtures.
    # Included in fixture

    # Act: Execute the function being tested.
    url = oauth.get_authorization_url()

    # Assert: Verify the results match expectations.
    assert "authorize" in url
    assert "client_id=test_client_id" in url
    assert "redirect_uri=" in url
    assert "response_type=code" in url
    assert "code_challenge=" in url
    assert "code_challenge_method=S256" in url


def test_has_tokens_no_token(oauth):
    """Token info should be empty when no token exists."""
    # Act and Assert: Execute and verify in one step.
    assert oauth._token_info == {}


def test_has_tokens_with_token(oauth_with_tokens):
    """Token info should be populated when token file exists."""
    # Act and Assert: Execute and verify in one step.
    assert oauth_with_tokens._token_info.get("access_token") == "test_access_token"


def test_oauth_rejects_malformed_token_file_without_modifying_it(tmp_path):
    """Malformed token files should remain intact and explain the recovery action."""
    # Arrange: Store invalid JSON in the configured token file.
    token_file = tmp_path / "tokens.json"
    invalid_content = "{invalid"
    token_file.write_text(invalid_content, encoding="utf-8")

    # Act and assert: Loading the malformed token file should preserve its content.
    with pytest.raises(ViAuthError, match="Repair or remove the file"):
        OAuth(
            client_id="test_client_id",
            redirect_uri="http://localhost:4200/",
            token_file=token_file,
        )

    # Assert: The invalid file should remain available for manual recovery.
    assert token_file.read_text(encoding="utf-8") == invalid_content


def test_oauth_rejects_invalid_utf8_token_file_without_modifying_it(tmp_path):
    """Invalid UTF-8 token files should remain intact and raise a library error."""
    # Arrange: Store bytes that cannot be decoded as the credential document.
    token_file = tmp_path / "tokens.json"
    invalid_content = b"\xff"
    token_file.write_bytes(invalid_content)

    # Act and assert: Loading should preserve the corrupted credential document.
    with pytest.raises(ViAuthError, match="Repair or remove the file"):
        OAuth(
            client_id="test_client_id",
            redirect_uri="http://localhost:4200/",
            token_file=token_file,
        )

    # Assert: The original bytes should remain available for manual recovery.
    assert token_file.read_bytes() == invalid_content


def test_save_tokens_rejects_file_that_becomes_malformed(oauth):
    """Token saves should not overwrite a file corrupted after initialization."""
    # Arrange: Initialize OAuth, then replace its absent token file with invalid JSON.
    invalid_content = "{invalid"
    oauth.token_file.write_text(invalid_content, encoding="utf-8")
    oauth._token_info = {"access_token": "new-token"}

    # Act and assert: Saving should preserve the malformed file and explain recovery.
    with pytest.raises(ViAuthError, match="Repair or remove the file"):
        oauth._save_tokens()

    # Assert: The malformed token file should not be overwritten by the new token.
    assert oauth.token_file.read_text(encoding="utf-8") == invalid_content


def test_save_tokens_merges_new_tokens_with_existing_configuration(oauth):
    """Token updates should retain configuration and unknown stored fields."""
    # Arrange: Store configuration and a field from a future credential format.
    oauth.token_file.write_text(
        json.dumps(
            {
                "client_id": "configured-client",
                "custom_metadata": {"source": "user"},
                "access_token": "old-token",
            }
        ),
        encoding="utf-8",
    )
    oauth._token_info = {"access_token": "new-token", "refresh_token": "refresh"}

    # Act: Persist the updated token information.
    oauth._save_tokens()

    # Assert: Existing non-token content should survive the update.
    assert json.loads(oauth.token_file.read_text(encoding="utf-8")) == {
        "client_id": "configured-client",
        "custom_metadata": {"source": "user"},
        "access_token": "new-token",
        "refresh_token": "refresh",
    }


def test_save_tokens_reports_missing_parent_directory_without_creating_it(tmp_path):
    """Credential persistence should not create missing parent directories."""
    # Arrange: Configure OAuth to write below a directory that does not exist.
    missing_parent = tmp_path / "missing"
    oauth = OAuth(
        client_id="test_client_id",
        redirect_uri="http://localhost:4200/",
        token_file=missing_parent / "tokens.json",
    )
    oauth._token_info = {"access_token": "new-token"}

    # Act and assert: Saving should explain the filesystem failure.
    with pytest.raises(ViAuthError, match="parent directory"):
        oauth._save_tokens()

    # Assert: Persistence should not create the directory as a side effect.
    assert not missing_parent.exists()


def test_save_tokens_uses_atomic_replacement_in_the_token_directory(oauth, monkeypatch):
    """Credential updates should replace the destination with a sibling temp file."""
    # Arrange: Track the low-level replacement while preserving its behavior.
    replacement_calls = []
    original_replace = os.replace

    def track_replace(source, destination):
        replacement_calls.append((source, destination))
        original_replace(source, destination)

    monkeypatch.setattr("vi_api_client.credentials.os.replace", track_replace)
    oauth._token_info = {"access_token": "new-token"}

    # Act: Save new credentials.
    oauth._save_tokens()

    # Assert: The temporary file should be a sibling of the destination.
    source, destination = replacement_calls[0]
    assert Path(source).parent == oauth.token_file.parent
    assert destination == oauth.token_file


def test_save_tokens_preserves_original_file_when_replacement_fails(oauth, monkeypatch):
    """Failed replacement should retain the previous credential document."""
    # Arrange: Seed a credential document and make the final replacement fail.
    original_content = '{"access_token": "old-token"}'
    oauth.token_file.write_text(original_content, encoding="utf-8")
    oauth._token_info = {"access_token": "new-token"}

    def raise_replace(source, destination):
        raise OSError("simulated replacement failure")

    monkeypatch.setattr("vi_api_client.credentials.os.replace", raise_replace)

    # Act and assert: A failed replacement should become a library auth error.
    with pytest.raises(ViAuthError, match="save token"):
        oauth._save_tokens()

    # Assert: The old file and no temporary artifacts should remain.
    assert oauth.token_file.read_text(encoding="utf-8") == original_content
    assert list(oauth.token_file.parent.glob(".tokens.json.*.tmp")) == []


def test_save_tokens_reports_temporary_file_write_failures(oauth, monkeypatch):
    """Credential persistence should translate temporary-file creation failures."""
    # Arrange: Make temporary-file creation fail before the token file is replaced.
    oauth.token_file.write_text('{"access_token": "old-token"}', encoding="utf-8")
    oauth._token_info = {"access_token": "new-token"}

    def raise_temporary_file_error(*args, **kwargs):
        raise OSError("simulated write failure")

    monkeypatch.setattr(
        "vi_api_client.credentials.NamedTemporaryFile", raise_temporary_file_error
    )

    # Act and assert: The operating-system failure should be a library auth error.
    with pytest.raises(ViAuthError, match="save token"):
        oauth._save_tokens()

    # Assert: A failed write should leave the existing credentials unchanged.
    assert (
        oauth.token_file.read_text(encoding="utf-8") == '{"access_token": "old-token"}'
    )


def test_save_tokens_removes_temporary_file_after_serialization_failure(
    oauth, monkeypatch
):
    """A serialization failure should not leave a temporary credential file."""
    # Arrange: Make writing the temporary JSON document fail after it is created.
    oauth._token_info = {"access_token": "new-token"}

    def raise_serialization_error(*args, **kwargs):
        raise OSError("simulated serialization failure")

    monkeypatch.setattr(
        "vi_api_client.credentials.json.dump", raise_serialization_error
    )

    # Act and assert: Saving should report the error through the auth boundary.
    with pytest.raises(ViAuthError, match="save token"):
        oauth._save_tokens()

    # Assert: The failed write should not leave credentials or temporary files behind.
    assert not oauth.token_file.exists()
    assert list(oauth.token_file.parent.glob(".tokens.json.*.tmp")) == []


def test_save_tokens_restricts_file_permissions_to_owner(oauth):
    """Credential documents should be owner-readable and owner-writable only."""
    # Arrange: Prepare token data.
    oauth._token_info = {"access_token": "new-token"}

    # Act: Persist the credentials.
    oauth._save_tokens()

    # Assert: The file mode should not grant group or other access.
    assert stat.S_IMODE(oauth.token_file.stat().st_mode) == 0o600


@pytest.mark.asyncio
async def test_async_get_access_token_with_valid_token(oauth_with_tokens):
    """Test getting access token when token is valid."""
    # Arrange: Create ViAuth instance and configure mock token endpoint.
    async with aiohttp.ClientSession() as session:
        oauth_with_tokens.websession = session

        # Act: Request token using authorization code.
        token = await oauth_with_tokens.async_get_access_token()

        # Assert: Verify the results match expectations.
        assert token == "test_access_token"


@pytest.mark.asyncio
async def test_async_refresh_access_token(oauth_with_tokens, load_fixture_json):
    """Test token refresh."""
    # Arrange: Create ViAuth with expired token and mock refresh endpoint.
    # Set expires_at to past to force refresh
    oauth_with_tokens._token_info["expires_at"] = 0
    data = load_fixture_json("auth_token.json")

    with aioresponses() as m:
        m.post(ENDPOINT_TOKEN, payload=data)

        async with aiohttp.ClientSession() as session:
            oauth_with_tokens.websession = session

            # Act: Get access token (should trigger refresh).
            await oauth_with_tokens.async_refresh_access_token()

        # Assert: Verify the results match expectations.
        assert oauth_with_tokens._token_info["access_token"] == "refreshed_access_token"
        assert (
            json.loads(oauth_with_tokens.token_file.read_text(encoding="utf-8"))[
                "access_token"
            ]
            == "refreshed_access_token"
        )


@pytest.mark.asyncio
async def test_code_exchange_persists_token_json(oauth, load_fixture_json):
    """Successful code exchange should retain the credential JSON format."""
    # Arrange: Start OAuth and mock a successful token endpoint response.
    oauth.get_authorization_url()
    token_data = load_fixture_json("auth_token.json")

    with aioresponses() as mock_responses:
        mock_responses.post(ENDPOINT_TOKEN, payload=token_data)

        async with aiohttp.ClientSession() as session:
            oauth.websession = session

            # Act: Exchange the authorization code for tokens.
            await oauth.async_fetch_details_from_code("accepted-code")

    # Assert: The persisted document should contain the existing token fields.
    saved_tokens = json.loads(oauth.token_file.read_text(encoding="utf-8"))
    assert saved_tokens["access_token"] == token_data["access_token"]
    assert saved_tokens["refresh_token"] == token_data["refresh_token"]
    assert saved_tokens["expires_in"] == token_data["expires_in"]
    assert isinstance(saved_tokens["expires_at"], float)


@pytest.mark.asyncio
async def test_code_exchange_failure_does_not_write_tokens(oauth):
    """Rejected authorization codes should not create or alter token storage."""
    # Arrange: Start an authorization flow and mock a rejected token exchange.
    oauth.get_authorization_url()
    with aioresponses() as mock_responses:
        mock_responses.post(
            ENDPOINT_TOKEN, status=400, body="invalid authorization code"
        )

        async with aiohttp.ClientSession() as session:
            oauth.websession = session

            # Act and assert: A rejected token exchange should raise a library error.
            with pytest.raises(ViAuthError, match="Failed to fetch token"):
                await oauth.async_fetch_details_from_code("rejected-code")

    # Assert: Failed authentication should not create a token file.
    assert not oauth.token_file.exists()


def test_token_persistence(tmp_path):
    """Test that tokens are saved and loaded correctly."""
    # Arrange: Prepare test data and fixtures.
    token_file = tmp_path / "tokens.json"

    # Create OAuth and manually set token info
    oauth = OAuth(
        client_id="test_client_id",
        redirect_uri="http://localhost:4200/",
        token_file=str(token_file),
    )

    # Manually set token info to simulate successful auth
    oauth._token_info = {
        "access_token": "saved_token",
        "refresh_token": "saved_refresh",
        "expires_in": 3600,
    }

    # Act: Execute the function being tested.
    oauth._save_tokens()

    # Create new instance and verify tokens are loaded
    oauth2 = OAuth(
        client_id="test_client_id",
        redirect_uri="http://localhost:4200/",
        token_file=str(token_file),
    )

    # Assert: Verify the results match expectations.
    assert oauth2._token_info["access_token"] == "saved_token"
    assert oauth2._token_info["refresh_token"] == "saved_refresh"


def test_pkce_verifier_generated_on_auth_url(oauth):
    """Test that PKCE verifier is generated when auth URL is requested."""
    # Arrange: Prepare test data and fixtures.
    assert oauth._pkce_verifier is None

    # Act: Execute the function being tested.
    oauth.get_authorization_url()

    # Assert: Verify the results match expectations.
    assert oauth._pkce_verifier is not None


@pytest.mark.asyncio
async def test_oauth_creates_and_closes_internal_websession(tmp_path):
    """OAuth should manage a session when the caller does not supply one."""
    # Arrange: Store a valid token and mock the installations endpoint.
    token_file = tmp_path / "tokens.json"
    token_file.write_text(
        json.dumps(
            {
                "access_token": "test_access_token",
                "expires_at": time.time() + 3600,
            }
        ),
        encoding="utf-8",
    )
    oauth = OAuth(
        client_id="test_client_id",
        redirect_uri="http://localhost:4200/",
        token_file=token_file,
    )
    installations_url = f"{API_BASE_URL}{ENDPOINT_INSTALLATIONS}"

    with aioresponses() as mock_responses:
        mock_responses.get(installations_url, payload={"data": []})

        # Act: Make a client request within the OAuth resource context.
        async with oauth:
            installations = await ViClient(oauth).get_installations()
            internal_websession = oauth.websession

    # Assert: The request should work and the internally owned session should close.
    assert installations == []
    assert internal_websession is not None
    assert internal_websession.closed is True
    assert oauth.websession is None


@pytest.mark.asyncio
async def test_oauth_keeps_external_websession_open(tmp_path):
    """OAuth should not close a session supplied by the caller."""
    # Arrange: Create an external session and an OAuth provider that uses it.
    token_file = tmp_path / "tokens.json"
    async with aiohttp.ClientSession() as external_websession:
        oauth = OAuth(
            client_id="test_client_id",
            redirect_uri="http://localhost:4200/",
            token_file=token_file,
            websession=external_websession,
        )

        # Act: Enter and exit the OAuth resource context.
        async with oauth:
            pass

        # Assert: OAuth should leave the caller-owned session open.
        assert external_websession.closed is False
