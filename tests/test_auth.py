"""Tests for the OAuth authentication and credential workflows."""

import asyncio
import base64
import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Self, cast
from unittest.mock import MagicMock
from urllib.parse import parse_qs, urlsplit

import aiohttp
import pytest
from aioresponses import aioresponses
from yarl import URL

from vi_api_client.auth import OAuth
from vi_api_client.client import ViClient
from vi_api_client.const import (
    API_BASE_URL,
    DEFAULT_SCOPES,
    ENDPOINT_INSTALLATIONS,
    ENDPOINT_TOKEN,
)
from vi_api_client.exceptions import ViAuthError


class _BlockingRefreshResponse:
    """Delay a token response until a test releases it."""

    def __init__(
        self,
        started: asyncio.Event,
        release: asyncio.Event,
        status: int,
        error: Exception | None,
    ) -> None:
        """Initialize the controllable response."""
        self.status = status
        self._started = started
        self._release = release
        self._error = error

    async def __aenter__(self) -> Self:
        """Wait until the test releases the token response."""
        self._started.set()
        await self._release.wait()
        if self._error is not None:
            raise self._error
        return self

    async def __aexit__(self, *args: object) -> None:
        """Leave the token response context."""

    async def json(self) -> dict[str, object]:
        """Return a successful refreshed token payload."""
        return {
            "access_token": "refreshed_access_token",
            "refresh_token": "refreshed_refresh_token",
            "expires_in": 3600,
        }

    async def text(self) -> str:
        """Return a token endpoint failure body."""
        return "refresh rejected"


class _BlockingRefreshSession:
    """Provide one controllable refresh request boundary."""

    def __init__(self, status: int = 200, error: Exception | None = None) -> None:
        """Initialize refresh request tracking."""
        self.calls = 0
        self.status = status
        self.error = error
        self.closed = False
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    def post(self, *args: object, **kwargs: object) -> _BlockingRefreshResponse:
        """Record and return a delayed token response."""
        self.calls += 1
        return _BlockingRefreshResponse(
            self.started, self.release, self.status, self.error
        )

    async def close(self) -> None:
        """Record that an owned session was closed."""
        self.closed = True


async def _release_refresh_when_started(session: _BlockingRefreshSession) -> None:
    """Release a delayed refresh after its HTTP request has started."""
    await session.started.wait()
    session.release.set()


def test_oauth_websession_is_read_only_after_constructor_injection(tmp_path):
    """OAuth should expose but not replace a caller-provided session."""
    # Arrange: Construct OAuth with a caller-owned session reference.
    external_websession = MagicMock(spec=aiohttp.ClientSession)
    oauth = OAuth(
        client_id="test_client_id",
        redirect_uri="http://localhost:4200/",
        token_file=tmp_path / "tokens.json",
        websession=external_websession,
    )

    # Act and assert: The public reference is observable but cannot be replaced.
    assert oauth.websession is external_websession
    with pytest.raises(AttributeError):
        oauth.websession = MagicMock(spec=aiohttp.ClientSession)  # type: ignore[misc]


@pytest.fixture
def oauth(tmp_path):
    """Create an OAuth instance for testing."""
    token_file = tmp_path / "tokens.json"
    return OAuth(
        client_id="test_client_id",
        redirect_uri="http://localhost:4200/",
        token_file=str(token_file),
    )


@pytest.fixture
def oauth_with_tokens(tmp_path):
    """Create an OAuth instance with pre-existing tokens."""
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


def _oauth_with_websession(
    oauth: OAuth, websession: aiohttp.ClientSession | _BlockingRefreshSession
) -> OAuth:
    """Recreate OAuth with the fixture credentials and a supplied session."""
    oauth_with_websession = OAuth(
        client_id=oauth.client_id,
        redirect_uri=oauth.redirect_uri,
        token_file=oauth.token_file,
        websession=cast(aiohttp.ClientSession, websession),
    )
    oauth_with_websession._token_info = oauth._token_info.copy()
    oauth_with_websession._pkce_verifier = oauth._pkce_verifier
    return oauth_with_websession


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


@pytest.mark.asyncio
async def test_async_get_access_token_with_valid_token(oauth_with_tokens):
    """An unexpired stored token is returned without a refresh."""
    # Arrange: Bind the preloaded OAuth instance to a real client session.
    async with aiohttp.ClientSession() as session:
        oauth_with_tokens = _oauth_with_websession(oauth_with_tokens, session)

        # Act: Request the access token.
        token = await oauth_with_tokens.async_get_access_token()

        # Assert: The unexpired persisted token returns without refresh.
        assert token == "test_access_token"


@pytest.mark.asyncio
async def test_explicit_refresh_replaces_the_token_in_use_and_on_disk(
    oauth_with_tokens, load_fixture_json
):
    """An explicit refresh stores the new token even if the old one is valid."""
    # Arrange: Mock the token endpoint with a refreshed token.
    data = load_fixture_json("auth_token.json")

    with aioresponses() as mock_responses:
        mock_responses.post(ENDPOINT_TOKEN, payload=data)

        async with aiohttp.ClientSession() as session:
            oauth = _oauth_with_websession(oauth_with_tokens, session)

            # Act: Refresh explicitly, then request the token in use.
            await oauth.async_refresh_access_token()
            token = await oauth.async_get_access_token()

    # Assert: The refreshed token is used and persisted.
    assert token == "refreshed_access_token"
    saved = json.loads(oauth.token_file.read_text(encoding="utf-8"))
    assert saved["access_token"] == "refreshed_access_token"


@pytest.mark.asyncio
async def test_overlapping_access_token_refreshes_share_one_request(
    oauth_with_tokens,
) -> None:
    """Overlapping automatic refreshes should share one token request."""
    # Arrange: Expire the token and pause the first refresh at the HTTP boundary.
    oauth_with_tokens._token_info["expires_at"] = 0
    session = _BlockingRefreshSession()
    oauth_with_tokens = _oauth_with_websession(oauth_with_tokens, session)

    # Act: Request a token concurrently from both callers.
    first_token, second_token, _ = await asyncio.gather(
        oauth_with_tokens.async_get_access_token(),
        oauth_with_tokens.async_get_access_token(),
        _release_refresh_when_started(session),
    )

    # Assert: Both callers receive the refresh result from one HTTP request.
    assert (first_token, second_token) == (
        "refreshed_access_token",
        "refreshed_access_token",
    )
    assert session.calls == 1


@pytest.mark.asyncio
async def test_overlapping_explicit_and_automatic_refreshes_share_one_request(
    oauth_with_tokens,
) -> None:
    """Explicit and automatic refresh callers should share one refresh."""
    # Arrange: Expire the token and delay the shared refresh.
    oauth_with_tokens._token_info["expires_at"] = 0
    session = _BlockingRefreshSession()
    oauth_with_tokens = _oauth_with_websession(oauth_with_tokens, session)

    # Act: Start an explicit refresh alongside automatic token retrieval.
    _, token, _ = await asyncio.gather(
        oauth_with_tokens.async_refresh_access_token(),
        oauth_with_tokens.async_get_access_token(),
        _release_refresh_when_started(session),
    )

    # Assert: Both calls use the same successful refresh.
    assert token == "refreshed_access_token"
    assert session.calls == 1


@pytest.mark.asyncio
async def test_overlapping_explicit_refreshes_share_one_request(
    oauth_with_tokens,
) -> None:
    """Overlapping explicit refreshes should share one token request."""
    # Arrange: Delay the first explicit refresh at the HTTP boundary.
    session = _BlockingRefreshSession()
    oauth_with_tokens = _oauth_with_websession(oauth_with_tokens, session)

    # Act: Start two explicit refreshes at the same time.
    _, _, _ = await asyncio.gather(
        oauth_with_tokens.async_refresh_access_token(),
        oauth_with_tokens.async_refresh_access_token(),
        _release_refresh_when_started(session),
    )

    # Assert: Both calls use the same token request.
    assert session.calls == 1


@pytest.mark.asyncio
async def test_later_explicit_refresh_starts_a_new_request(oauth_with_tokens) -> None:
    """A completed explicit refresh should not suppress a later forced refresh."""
    # Arrange: Allow token responses to complete immediately.
    session = _BlockingRefreshSession()
    session.release.set()
    oauth_with_tokens = _oauth_with_websession(oauth_with_tokens, session)

    # Act: Refresh twice without overlap.
    await oauth_with_tokens.async_refresh_access_token()
    await oauth_with_tokens.async_refresh_access_token()

    # Assert: Each completed explicit refresh sends a new request.
    assert session.calls == 2


@pytest.mark.asyncio
async def test_cancelling_one_refresh_waiter_keeps_the_shared_refresh_running(
    oauth_with_tokens,
) -> None:
    """Cancelling one caller should not cancel a shared refresh."""
    # Arrange: Start a delayed automatic refresh.
    oauth_with_tokens._token_info["expires_at"] = 0
    session = _BlockingRefreshSession()
    oauth_with_tokens = _oauth_with_websession(oauth_with_tokens, session)
    cancelled_caller = asyncio.create_task(oauth_with_tokens.async_get_access_token())
    await session.started.wait()

    async def cancel_and_release() -> None:
        """Cancel one waiter after the remaining waiter joins the refresh."""
        cancelled_caller.cancel()
        session.release.set()

    # Act: Join the refresh from a second caller while cancelling the first.
    remaining_token, _ = await asyncio.gather(
        oauth_with_tokens.async_get_access_token(), cancel_and_release()
    )

    # Assert: The remaining caller receives the result from the one request.
    with pytest.raises(asyncio.CancelledError):
        await cancelled_caller
    assert remaining_token == "refreshed_access_token"
    assert session.calls == 1


@pytest.mark.asyncio
async def test_failed_shared_refresh_is_visible_to_waiters_and_can_retry(
    oauth_with_tokens,
) -> None:
    """Shared failures should propagate once and permit a later retry."""
    # Arrange: Make the shared refresh fail after both callers have started.
    oauth_with_tokens._token_info["expires_at"] = 0
    session = _BlockingRefreshSession(status=400)
    oauth_with_tokens = _oauth_with_websession(oauth_with_tokens, session)

    first_caller, second_caller, _ = await asyncio.gather(
        asyncio.create_task(oauth_with_tokens.async_get_access_token()),
        asyncio.create_task(oauth_with_tokens.async_get_access_token()),
        asyncio.create_task(_release_refresh_when_started(session)),
        return_exceptions=True,
    )

    # Assert: Both waiters observe the refresh failure.
    assert isinstance(first_caller, ViAuthError)
    assert isinstance(second_caller, ViAuthError)
    assert session.calls == 1

    # Act: Allow a later caller to refresh successfully.
    session.status = 200
    token = await oauth_with_tokens.async_get_access_token()

    # Assert: The failed operation was not retained indefinitely.
    assert token == "refreshed_access_token"
    assert session.calls == 2


@pytest.mark.asyncio
async def test_close_waits_for_refresh_then_closes_an_owned_session(
    oauth_with_tokens, monkeypatch
) -> None:
    """Closing should settle a refresh before closing its owned session."""
    # Arrange: Make OAuth lazily create a delayed, owned transport session.
    oauth_with_tokens._token_info["expires_at"] = 0
    session = _BlockingRefreshSession()
    monkeypatch.setattr("vi_api_client.auth.aiohttp.ClientSession", lambda: session)
    refresh = asyncio.create_task(oauth_with_tokens.async_get_access_token())
    await session.started.wait()

    # Act: Begin closing while the refresh remains in flight, then release it.
    close = asyncio.create_task(oauth_with_tokens.async_close())
    session.release.set()
    token, _ = await asyncio.gather(refresh, close)

    # Assert: The refresh completes and the internally owned session closes.
    assert token == "refreshed_access_token"
    assert session.closed is True
    assert oauth_with_tokens.websession is None


@pytest.mark.asyncio
async def test_close_keeps_an_external_session_open_while_refreshing(
    oauth_with_tokens,
) -> None:
    """Closing should not close a caller-provided session around a refresh."""
    # Arrange: Start a delayed refresh through an externally managed session.
    oauth_with_tokens._token_info["expires_at"] = 0
    session = _BlockingRefreshSession()
    oauth_with_tokens = _oauth_with_websession(oauth_with_tokens, session)
    refresh = asyncio.create_task(oauth_with_tokens.async_get_access_token())
    await session.started.wait()

    # Act: Close the provider while the refresh is in flight.
    close = asyncio.create_task(oauth_with_tokens.async_close())
    session.release.set()
    await asyncio.gather(refresh, close)

    # Assert: Caller-owned sessions remain available.
    assert session.closed is False


@pytest.mark.asyncio
async def test_close_logs_and_cleans_up_a_cancelled_callers_refresh_failure(
    oauth_with_tokens, monkeypatch, caplog
) -> None:
    """Closing should handle a refresh failure left by a cancelled caller."""
    # Arrange: Start a failed refresh with an owned delayed transport session.
    oauth_with_tokens._token_info["expires_at"] = 0
    session = _BlockingRefreshSession(status=400)
    monkeypatch.setattr("vi_api_client.auth.aiohttp.ClientSession", lambda: session)
    cancelled_caller = asyncio.create_task(oauth_with_tokens.async_get_access_token())
    await session.started.wait()
    cancelled_caller.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cancelled_caller

    # Act: Let close observe the failed shared refresh.
    session.release.set()
    await oauth_with_tokens.async_close()
    await oauth_with_tokens.async_close()

    # Assert: Closing does not retry, logs once, and closes the owned session.
    assert session.calls == 1
    assert session.closed is True
    assert (
        sum(
            "Token refresh failed while closing authentication" in record.message
            for record in caplog.records
        )
        == 1
    )


@pytest.mark.asyncio
async def test_close_cleans_up_a_cancelled_callers_transport_failure(
    oauth_with_tokens, monkeypatch, caplog
) -> None:
    """Closing should still close an owned session after a transport failure."""
    # Arrange: Start a refresh that fails while opening the token response.
    oauth_with_tokens._token_info["expires_at"] = 0
    session = _BlockingRefreshSession(error=aiohttp.ClientConnectionError())
    monkeypatch.setattr("vi_api_client.auth.aiohttp.ClientSession", lambda: session)
    cancelled_caller = asyncio.create_task(oauth_with_tokens.async_get_access_token())
    await session.started.wait()
    cancelled_caller.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cancelled_caller

    # Act: Let close observe and clean up the transport failure.
    session.release.set()
    await oauth_with_tokens.async_close()

    # Assert: Closing logs the failure and releases the owned transport session.
    assert session.calls == 1
    assert session.closed is True
    assert any(
        "Token refresh failed while closing authentication" in record.message
        for record in caplog.records
    )


@pytest.mark.asyncio
async def test_code_exchange_persists_token_json(oauth, load_fixture_json):
    """Successful code exchange should retain the credential JSON format."""
    # Arrange: Start OAuth and mock a successful token endpoint response.
    oauth.get_authorization_url()
    token_data = load_fixture_json("auth_token.json")
    token_data["future"] = {"enabled": True}

    with aioresponses() as mock_responses:
        mock_responses.post(ENDPOINT_TOKEN, payload=token_data)

        async with aiohttp.ClientSession() as session:
            oauth = _oauth_with_websession(oauth, session)

            # Act: Exchange the authorization code for tokens.
            await oauth.async_exchange_code_for_tokens("accepted-code")

    # Assert: The persisted document should contain the existing token fields.
    saved_tokens = json.loads(oauth.token_file.read_text(encoding="utf-8"))
    assert saved_tokens["access_token"] == token_data["access_token"]
    assert saved_tokens["refresh_token"] == token_data["refresh_token"]
    assert saved_tokens["expires_in"] == token_data["expires_in"]
    assert saved_tokens["future"] == {"enabled": True}
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
            oauth = _oauth_with_websession(oauth, session)

            # Act and assert: A rejected token exchange should raise a library error.
            with pytest.raises(ViAuthError, match="Failed to fetch token"):
                await oauth.async_exchange_code_for_tokens("rejected-code")

    # Assert: Failed authentication should not create a token file.
    assert not oauth.token_file.exists()


@pytest.mark.asyncio
async def test_code_exchange_rejects_malformed_successful_token_json(oauth):
    """A 200 response with malformed JSON should raise a library auth error."""
    # Arrange: Start the flow and return a 200 body that is not valid JSON.
    oauth.get_authorization_url()
    with aioresponses() as mock_responses:
        mock_responses.post(ENDPOINT_TOKEN, status=200, body="{invalid")

        async with aiohttp.ClientSession() as session:
            oauth = _oauth_with_websession(oauth, session)

            # Act and assert: The malformed success body rejects as a library error.
            with pytest.raises(ViAuthError, match="invalid JSON"):
                await oauth.async_exchange_code_for_tokens("accepted-code")

    # Assert: No credential document is created from the failed exchange.
    assert not oauth.token_file.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("token_data", "message"),
    [
        ({"access_token": 1}, "access_token must be a non-empty string"),
        ({"refresh_token": "new"}, "access_token must be a non-empty string"),
        ([], "must be a JSON object"),
    ],
)
async def test_code_exchange_rejects_invalid_token_data_without_overwriting(
    oauth, token_data, message
):
    """Invalid successful token data must preserve stored credentials."""
    # Arrange: Persist a valid token before receiving an invalid success response.
    original_content = '{"access_token": "existing"}'
    oauth.token_file.write_text(original_content, encoding="utf-8")
    oauth.get_authorization_url()

    with aioresponses() as mock_responses:
        mock_responses.post(ENDPOINT_TOKEN, status=200, payload=token_data)
        async with aiohttp.ClientSession() as session:
            oauth = _oauth_with_websession(oauth, session)

            # Act and assert: The public code exchange rejects malformed token data.
            with pytest.raises(ViAuthError, match=message):
                await oauth.async_exchange_code_for_tokens("accepted-code")

    # Assert: No invalid response can replace the saved credential document.
    assert oauth.token_file.read_text(encoding="utf-8") == original_content


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
async def test_oauth_recreates_an_internal_websession_after_closing(tmp_path):
    """OAuth should create a new owned session for a request after closing."""
    # Arrange: Store a valid token and mock repeated installation requests.
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
        mock_responses.get(installations_url, payload={"data": []}, repeat=True)

        # Act: Request, close its owned session, then request again.
        await ViClient(oauth).get_installations()
        first_websession = oauth.websession
        await oauth.async_close()
        await ViClient(oauth).get_installations()
        second_websession = oauth.websession
        await oauth.async_close()

    # Assert: The second request creates and closes a distinct owned session.
    assert first_websession is not None
    assert first_websession.closed is True
    assert second_websession is not None
    assert second_websession is not first_websession
    assert second_websession.closed is True
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
        assert oauth.websession is external_websession


@pytest.mark.asyncio
async def test_async_get_access_token_without_tokens_raises(oauth):
    """Requesting a token before authentication should explain the requirement."""
    # Act and assert: The token request raises with actionable guidance.
    with pytest.raises(ViAuthError, match="No tokens loaded"):
        await oauth.async_get_access_token()


@pytest.mark.asyncio
async def test_expired_tokens_without_refresh_token_fall_back_with_warning(
    oauth, caplog
):
    """Expired tokens without a refresh token should fall back with a warning."""
    # Arrange: Store an expired access token without a refresh token.
    oauth._token_info = {"access_token": "expired-token", "expires_at": 0}

    # Act: Request the access token.
    with caplog.at_level(logging.WARNING):
        token = await oauth.async_get_access_token()

    # Assert: The possibly expired token returns with a logged warning.
    assert token == "expired-token"
    assert any(
        "No refresh token available" in record.message for record in caplog.records
    )


@pytest.mark.asyncio
async def test_token_state_without_access_token_raises(oauth):
    """Token state without a usable access token should reject the request."""
    # Arrange: Store expiry state without a usable access token value.
    oauth._token_info = {"expires_at": 9999999999}

    # Act and assert: The token request explains the invalid state.
    with pytest.raises(ViAuthError, match="No valid access token"):
        await oauth.async_get_access_token()


@pytest.mark.asyncio
async def test_code_exchange_without_pkce_verifier_raises(oauth):
    """Exchanging a code before generating the authorization URL should reject."""
    # Act and assert: The exchange explains the missing verifier.
    with pytest.raises(ViAuthError, match="PKCE Verifier missing"):
        await oauth.async_exchange_code_for_tokens("accepted-code")


@pytest.mark.asyncio
async def test_refresh_without_refresh_token_raises(oauth_with_tokens):
    """Refreshing without a stored refresh token should reject the request."""
    # Arrange: Remove the refresh token from the persisted token state.
    oauth_with_tokens._token_info.pop("refresh_token")

    # Act and assert: The refresh explains the missing token.
    with pytest.raises(ViAuthError, match="No refresh token available"):
        await oauth_with_tokens.async_refresh_access_token()


@pytest.mark.parametrize(
    ("token_body", "message"),
    [
        ('{"access_token": "t", "expires_in": NaN}', "invalid JSON data"),
        ('{"access_token": "t", "refresh_token": 5}', "refresh_token must be a string"),
        ('{"access_token": "t", "token_type": 5}', "token_type must be a string"),
        ('{"access_token": "t", "expires_in": -1}', "non-negative finite number"),
        ('{"access_token": "t", "expires_in": true}', "non-negative finite number"),
    ],
)
@pytest.mark.asyncio
async def test_code_exchange_rejects_malformed_token_fields(
    oauth, token_body: str, message: str
):
    """Invalid successful token fields should reject without writing."""
    # Arrange: Start the flow and return a 200 body with malformed fields.
    oauth.get_authorization_url()
    with aioresponses() as mock_responses:
        mock_responses.post(ENDPOINT_TOKEN, status=200, body=token_body)

        async with aiohttp.ClientSession() as session:
            oauth = _oauth_with_websession(oauth, session)

            # Act and assert: The exchange rejects the malformed field.
            with pytest.raises(ViAuthError, match=message):
                await oauth.async_exchange_code_for_tokens("accepted-code")

    # Assert: No credential document is created from the failed exchange.
    assert not oauth.token_file.exists()


@pytest.mark.asyncio
async def test_code_exchange_without_expires_in_skips_computed_expiry(oauth):
    """Token responses without expires_in should not compute an absolute expiry."""
    # Arrange: Start the flow and return a minimal valid token response.
    oauth.get_authorization_url()
    with aioresponses() as mock_responses:
        mock_responses.post(ENDPOINT_TOKEN, status=200, payload={"access_token": "t"})

        async with aiohttp.ClientSession() as session:
            oauth = _oauth_with_websession(oauth, session)

            # Act: Exchange the code for the minimal token.
            await oauth.async_exchange_code_for_tokens("accepted-code")

    # Assert: The token persists without a computed absolute expiry.
    saved = json.loads(oauth.token_file.read_text(encoding="utf-8"))
    assert saved == {"access_token": "t"}


@pytest.mark.asyncio
async def test_async_close_tolerates_an_externally_closed_owned_session(tmp_path):
    """Closing should stay safe when the owned session was already closed."""
    # Arrange: Create an owned session and close it out from under the provider.
    token_file = tmp_path / "tokens.json"
    token_file.write_text(
        json.dumps({"access_token": "t", "expires_at": time.time() + 3600}),
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
        await ViClient(oauth).get_installations()
        assert oauth.websession is not None

        # Act: Close the provider after its owned session already closed.
        await oauth.websession.close()
        await oauth.async_close()

    # Assert: The close completes and clears the session reference.
    assert oauth.websession is None


def _write_token_document(token_file: Path, **fields: object) -> None:
    """Persist a token document with a refresh token and the given fields."""
    document = {
        "access_token": "stored-access",
        "refresh_token": "stored-refresh",
        **fields,
    }
    token_file.write_text(json.dumps(document), encoding="utf-8")


def _token_request_form(mock_responses: aioresponses) -> dict[str, str]:
    """Return the form data of the single request sent to the token endpoint."""
    (request,) = mock_responses.requests[("POST", URL(ENDPOINT_TOKEN))]
    return request.kwargs["data"]


@pytest.mark.parametrize(
    ("seconds_left", "expected_token"),
    [(30, "refreshed-access"), (120, "stored-access")],
    ids=["inside-margin-refreshes", "outside-margin-reuses"],
)
@pytest.mark.asyncio
async def test_access_token_is_renewed_shortly_before_it_expires(
    tmp_path, seconds_left, expected_token
):
    """Tokens expiring within the 60-second margin are refreshed before use."""
    # Arrange: Store a token that expires in the given number of seconds.
    token_file = tmp_path / "tokens.json"
    _write_token_document(token_file, expires_at=time.time() + seconds_left)

    with aioresponses() as mock_responses:
        mock_responses.post(
            ENDPOINT_TOKEN, payload={"access_token": "refreshed-access"}
        )
        async with aiohttp.ClientSession() as session:
            oauth = OAuth("client", "https://example.invalid", token_file, session)

            # Act: Request a token for the next API call.
            token = await oauth.async_get_access_token()

    # Assert: Only the token inside the margin was refreshed.
    assert token == expected_token


@pytest.mark.asyncio
async def test_refresh_sends_the_stored_refresh_token_and_keeps_it(tmp_path):
    """A refresh response without a new refresh token keeps the stored one."""
    # Arrange: Store an expired token and answer without a refresh token.
    token_file = tmp_path / "tokens.json"
    _write_token_document(token_file, expires_at=0)

    with aioresponses() as mock_responses:
        mock_responses.post(
            ENDPOINT_TOKEN,
            payload={"access_token": "refreshed-access", "expires_in": 3600},
        )
        async with aiohttp.ClientSession() as session:
            oauth = OAuth("client", "https://example.invalid", token_file, session)

            # Act: Request a token, which refreshes the expired one.
            token = await oauth.async_get_access_token()

        # Assert: The refresh grant carries the stored token, which is kept.
        assert _token_request_form(mock_responses) == {
            "client_id": "client",
            "grant_type": "refresh_token",
            "refresh_token": "stored-refresh",
        }
    assert token == "refreshed-access"
    saved = json.loads(token_file.read_text(encoding="utf-8"))
    assert saved["refresh_token"] == "stored-refresh"
    assert saved["access_token"] == "refreshed-access"


@pytest.mark.asyncio
async def test_code_exchange_sends_the_verifier_matching_the_login_challenge(
    tmp_path,
):
    """The code exchange must send the PKCE verifier behind the login URL."""
    # Arrange: Create the login URL and read its PKCE challenge.
    token_file = tmp_path / "tokens.json"
    with aioresponses() as mock_responses:
        mock_responses.post(ENDPOINT_TOKEN, payload={"access_token": "new-access"})
        async with aiohttp.ClientSession() as session:
            oauth = OAuth("client", "https://example.invalid/cb", token_file, session)
            query = parse_qs(urlsplit(oauth.get_authorization_url()).query)

            # Act: Exchange the authorization code for tokens.
            await oauth.async_exchange_code_for_tokens("auth-code")

        # Assert: The login URL and the exchange form belong to one PKCE pair.
        form = _token_request_form(mock_responses)
    verifier_digest = hashlib.sha256(form["code_verifier"].encode()).digest()
    expected_challenge = base64.urlsafe_b64encode(verifier_digest).rstrip(b"=")
    assert query["code_challenge"] == [expected_challenge.decode()]
    assert query["code_challenge_method"] == ["S256"]
    assert query["client_id"] == ["client"]
    assert query["redirect_uri"] == ["https://example.invalid/cb"]
    assert query["scope"] == [DEFAULT_SCOPES]
    assert query["response_type"] == ["code"]
    assert {key: form[key] for key in ("client_id", "grant_type", "code")} == {
        "client_id": "client",
        "grant_type": "authorization_code",
        "code": "auth-code",
    }
    assert form["redirect_uri"] == "https://example.invalid/cb"


@pytest.mark.asyncio
async def test_authenticated_requests_add_a_bearer_header_without_mutating_input(
    oauth_with_tokens,
):
    """Requests carry the access token while caller headers stay untouched."""
    # Arrange: Provide caller headers that must not be modified.
    url = f"{API_BASE_URL}{ENDPOINT_INSTALLATIONS}"
    caller_headers = {"Accept": "application/json"}

    with aioresponses() as mock_responses:
        mock_responses.get(url, payload={"data": []})
        async with aiohttp.ClientSession() as session:
            oauth = _oauth_with_websession(oauth_with_tokens, session)

            # Act: Send one authenticated request.
            async with await oauth.request("GET", url, headers=caller_headers):
                pass

        # Assert: The sent request has both headers; the caller's dict is unchanged.
        (request,) = mock_responses.requests[("GET", URL(url))]
    assert request.kwargs["headers"] == {
        "Accept": "application/json",
        "Authorization": "Bearer test_access_token",
    }
    assert caller_headers == {"Accept": "application/json"}
