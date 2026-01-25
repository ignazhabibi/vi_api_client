"""Tests for vitoclient.auth module."""

import json
from unittest.mock import MagicMock

import aiohttp
import pytest
from aioresponses import aioresponses

from vi_api_client.auth import AbstractAuth, OAuth
from vi_api_client.const import ENDPOINT_TOKEN


def test_abstract_auth_cannot_be_instantiated():
    """AbstractAuth should not be instantiated directly."""
    # Arrange, Act and Assert: Complete test in one step.
    with pytest.raises(TypeError):
        AbstractAuth(MagicMock())


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
