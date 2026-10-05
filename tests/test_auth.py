"""Tests for OAuth token retrieval, refresh, code exchange, and session ownership.

Credential document reading and writing is covered in test_credentials.py.
"""

import asyncio
import base64
import hashlib
import json
import logging
import time
from collections.abc import Collection
from pathlib import Path
from typing import Self, cast
from unittest.mock import MagicMock
from urllib.parse import parse_qs, urlsplit

import aiohttp
import pytest
from aioresponses import aioresponses
from builders import load_fixture_json
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

INSTALLATIONS_URL = f"{API_BASE_URL}{ENDPOINT_INSTALLATIONS}"


def _write_token_document(
    token_file: Path, *, without: Collection[str] = (), **fields: object
) -> None:
    """Persist a token document with stored tokens, overridden or removed fields."""
    document = {
        "access_token": "stored-access",
        "refresh_token": "stored-refresh",
        **fields,
    }
    for field_name in without:
        del document[field_name]
    token_file.write_text(json.dumps(document), encoding="utf-8")


def _token_request_form(mock_responses: aioresponses) -> dict[str, str]:
    """Return the form data of the single request sent to the token endpoint."""
    (request,) = mock_responses.requests[("POST", URL(ENDPOINT_TOKEN))]
    return request.kwargs["data"]


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


def _blocking_oauth(token_file: Path, session: _BlockingRefreshSession) -> OAuth:
    """Create OAuth that sends token requests through a controllable session."""
    # The double implements only the post/close surface that OAuth uses.
    return OAuth(
        "client",
        "https://example.invalid",
        token_file,
        cast(aiohttp.ClientSession, session),
    )


async def _release_refresh_when_started(session: _BlockingRefreshSession) -> None:
    """Release a delayed refresh after its HTTP request has started."""
    await session.started.wait()
    session.release.set()


def test_oauth_websession_is_read_only_after_constructor_injection(token_file):
    """OAuth should expose but not replace a caller-provided session."""
    # Arrange: Construct OAuth with a caller-owned session reference.
    external_websession = MagicMock(spec=aiohttp.ClientSession)
    oauth = OAuth("client", "https://example.invalid", token_file, external_websession)

    # Act and assert: The public reference is observable but cannot be replaced.
    assert oauth.websession is external_websession
    with pytest.raises(AttributeError, match="has no setter"):
        oauth.websession = MagicMock(spec=aiohttp.ClientSession)  # type: ignore[misc]


def test_oauth_rejects_malformed_token_file_without_modifying_it(token_file):
    """Malformed token files should remain intact and explain the recovery action."""
    # Arrange: Store invalid JSON in the configured token file.
    invalid_content = "{invalid"
    token_file.write_text(invalid_content, encoding="utf-8")

    # Act and assert: Loading the malformed token file should preserve its content.
    with pytest.raises(ViAuthError, match="Repair or remove the file"):
        OAuth("client", "https://example.invalid", token_file)

    # Assert: The invalid file should remain available for manual recovery.
    assert token_file.read_text(encoding="utf-8") == invalid_content


@pytest.mark.parametrize(
    ("seconds_left", "expected_token", "expected_refreshes"),
    [
        pytest.param(30, "refreshed-access", 1, id="inside-margin-refreshes"),
        pytest.param(120, "stored-access", 0, id="outside-margin-reuses"),
    ],
)
async def test_access_token_is_renewed_shortly_before_it_expires(
    mock_responses, token_file, seconds_left, expected_token, expected_refreshes
):
    """Tokens expiring within the 60-second margin are refreshed before use."""
    # Arrange: Store a token that expires in the given number of seconds.
    _write_token_document(token_file, expires_at=time.time() + seconds_left)

    mock_responses.post(ENDPOINT_TOKEN, payload={"access_token": "refreshed-access"})
    async with aiohttp.ClientSession() as session:
        oauth = OAuth("client", "https://example.invalid", token_file, session)

        # Act: Request a token for the next API call.
        token = await oauth.async_get_access_token()

    # Assert: Only the token inside the margin was refreshed.
    token_requests = mock_responses.requests.get(("POST", URL(ENDPOINT_TOKEN)), [])
    assert token == expected_token
    assert len(token_requests) == expected_refreshes


async def test_explicit_refresh_replaces_the_token_in_use_and_on_disk(
    mock_responses, token_file
):
    """An explicit refresh stores the new token even if the old one is valid."""
    # Arrange: Store a valid token and mock the token endpoint with a new one.
    _write_token_document(token_file, expires_at=time.time() + 3600)
    data = load_fixture_json("auth_token.json")

    mock_responses.post(ENDPOINT_TOKEN, payload=data)

    async with aiohttp.ClientSession() as session:
        oauth = OAuth("client", "https://example.invalid", token_file, session)

        # Act: Refresh explicitly, then request the token in use.
        await oauth.async_refresh_access_token()
        token = await oauth.async_get_access_token()

    # Assert: The refreshed token is used and persisted.
    assert token == "refreshed_access_token"
    saved = json.loads(token_file.read_text(encoding="utf-8"))
    assert saved["access_token"] == "refreshed_access_token"


async def test_refresh_sends_the_stored_refresh_token_and_keeps_it(
    mock_responses, token_file
):
    """A refresh response without a new refresh token keeps the stored one."""
    # Arrange: Store an expired token and answer without a refresh token.
    _write_token_document(token_file, expires_at=0)

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


async def test_overlapping_access_token_refreshes_share_one_request(
    token_file,
) -> None:
    """Overlapping automatic refreshes should share one token request."""
    # Arrange: Expire the token and pause the first refresh at the HTTP boundary.
    _write_token_document(token_file, expires_at=0)
    session = _BlockingRefreshSession()
    oauth = _blocking_oauth(token_file, session)

    # Act: Request a token concurrently from both callers.
    first_token, second_token, _ = await asyncio.gather(
        oauth.async_get_access_token(),
        oauth.async_get_access_token(),
        _release_refresh_when_started(session),
    )

    # Assert: Both callers receive the refresh result from one HTTP request.
    assert (first_token, second_token) == (
        "refreshed_access_token",
        "refreshed_access_token",
    )
    assert session.calls == 1


async def test_overlapping_explicit_and_automatic_refreshes_share_one_request(
    token_file,
) -> None:
    """Explicit and automatic refresh callers should share one refresh."""
    # Arrange: Expire the token and delay the shared refresh.
    _write_token_document(token_file, expires_at=0)
    session = _BlockingRefreshSession()
    oauth = _blocking_oauth(token_file, session)

    # Act: Start an explicit refresh alongside automatic token retrieval.
    _, token, _ = await asyncio.gather(
        oauth.async_refresh_access_token(),
        oauth.async_get_access_token(),
        _release_refresh_when_started(session),
    )

    # Assert: Both calls use the same successful refresh.
    assert token == "refreshed_access_token"
    assert session.calls == 1


async def test_overlapping_explicit_refreshes_share_one_request(token_file) -> None:
    """Overlapping explicit refreshes should share one token request."""
    # Arrange: Store a valid token and delay the first explicit refresh.
    _write_token_document(token_file, expires_at=time.time() + 3600)
    session = _BlockingRefreshSession()
    oauth = _blocking_oauth(token_file, session)

    # Act: Start two explicit refreshes at the same time, then use the token.
    await asyncio.gather(
        oauth.async_refresh_access_token(),
        oauth.async_refresh_access_token(),
        _release_refresh_when_started(session),
    )
    token = await oauth.async_get_access_token()

    # Assert: One token request produced the refreshed token now in use.
    assert token == "refreshed_access_token"
    assert session.calls == 1


async def test_later_explicit_refresh_starts_a_new_request(token_file) -> None:
    """A completed explicit refresh should not suppress a later forced refresh."""
    # Arrange: Allow token responses to complete immediately.
    _write_token_document(token_file, expires_at=time.time() + 3600)
    session = _BlockingRefreshSession()
    session.release.set()
    oauth = _blocking_oauth(token_file, session)

    # Act: Refresh twice without overlap.
    await oauth.async_refresh_access_token()
    await oauth.async_refresh_access_token()

    # Assert: Each completed explicit refresh sends a new request.
    assert session.calls == 2


async def test_cancelling_one_refresh_waiter_keeps_the_shared_refresh_running(
    token_file,
) -> None:
    """Cancelling one caller should not cancel a shared refresh."""
    # Arrange: Start a delayed automatic refresh.
    _write_token_document(token_file, expires_at=0)
    session = _BlockingRefreshSession()
    oauth = _blocking_oauth(token_file, session)
    cancelled_caller = asyncio.create_task(oauth.async_get_access_token())
    await session.started.wait()

    async def cancel_and_release() -> None:
        """Cancel one waiter after the remaining waiter joins the refresh."""
        cancelled_caller.cancel()
        session.release.set()

    # Act: Join the refresh from a second caller while cancelling the first.
    remaining_token, _ = await asyncio.gather(
        oauth.async_get_access_token(), cancel_and_release()
    )

    # Assert: The remaining caller receives the result from the one request.
    with pytest.raises(asyncio.CancelledError):
        await cancelled_caller
    assert remaining_token == "refreshed_access_token"
    assert session.calls == 1


async def test_failed_shared_refresh_is_visible_to_waiters_and_can_retry(
    token_file,
) -> None:
    """Shared failures should propagate once and permit a later retry."""
    # Arrange: Make the shared refresh fail after both callers have started.
    _write_token_document(token_file, expires_at=0)
    session = _BlockingRefreshSession(status=400)
    oauth = _blocking_oauth(token_file, session)

    # Act: Request a token from two callers while the shared refresh fails.
    first_caller, second_caller, _ = await asyncio.gather(
        asyncio.create_task(oauth.async_get_access_token()),
        asyncio.create_task(oauth.async_get_access_token()),
        asyncio.create_task(_release_refresh_when_started(session)),
        return_exceptions=True,
    )

    # Assert: Both waiters observe the refresh failure.
    assert isinstance(first_caller, ViAuthError)
    assert isinstance(second_caller, ViAuthError)
    assert session.calls == 1

    # Act: Allow a later caller to refresh successfully.
    session.status = 200
    token = await oauth.async_get_access_token()

    # Assert: The failed operation was not retained indefinitely.
    assert token == "refreshed_access_token"
    assert session.calls == 2


async def test_close_waits_for_refresh_then_closes_an_owned_session(
    token_file, monkeypatch
) -> None:
    """Closing should settle a refresh before closing its owned session."""
    # Arrange: Make OAuth lazily create a delayed, owned transport session.
    _write_token_document(token_file, expires_at=0)
    session = _BlockingRefreshSession()
    monkeypatch.setattr("vi_api_client.auth.aiohttp.ClientSession", lambda: session)
    oauth = OAuth("client", "https://example.invalid", token_file)
    refresh = asyncio.create_task(oauth.async_get_access_token())
    await session.started.wait()

    # Act: Begin closing while the refresh remains in flight, then release it.
    close = asyncio.create_task(oauth.async_close())
    session.release.set()
    token, _ = await asyncio.gather(refresh, close)

    # Assert: The refresh completes and the internally owned session closes.
    assert token == "refreshed_access_token"
    assert session.closed is True
    assert oauth.websession is None


async def test_close_keeps_an_external_session_open_while_refreshing(
    token_file,
) -> None:
    """Closing should not close a caller-provided session around a refresh."""
    # Arrange: Start a delayed refresh through an externally managed session.
    _write_token_document(token_file, expires_at=0)
    session = _BlockingRefreshSession()
    oauth = _blocking_oauth(token_file, session)
    refresh = asyncio.create_task(oauth.async_get_access_token())
    await session.started.wait()

    # Act: Close the provider while the refresh is in flight.
    close = asyncio.create_task(oauth.async_close())
    session.release.set()
    await asyncio.gather(refresh, close)

    # Assert: Caller-owned sessions remain available.
    assert session.closed is False


async def test_close_logs_and_cleans_up_a_cancelled_callers_refresh_failure(
    token_file, monkeypatch, caplog
) -> None:
    """Closing should handle a refresh failure left by a cancelled caller."""
    # Arrange: Start a failed refresh with an owned delayed transport session.
    _write_token_document(token_file, expires_at=0)
    session = _BlockingRefreshSession(status=400)
    monkeypatch.setattr("vi_api_client.auth.aiohttp.ClientSession", lambda: session)
    oauth = OAuth("client", "https://example.invalid", token_file)
    cancelled_caller = asyncio.create_task(oauth.async_get_access_token())
    await session.started.wait()
    cancelled_caller.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cancelled_caller

    # Act: Let close observe the failed shared refresh.
    session.release.set()
    await oauth.async_close()
    await oauth.async_close()

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


async def test_close_cleans_up_a_cancelled_callers_transport_failure(
    token_file, monkeypatch, caplog
) -> None:
    """Closing should still close an owned session after a transport failure."""
    # Arrange: Start a refresh that fails while opening the token response.
    _write_token_document(token_file, expires_at=0)
    session = _BlockingRefreshSession(error=aiohttp.ClientConnectionError())
    monkeypatch.setattr("vi_api_client.auth.aiohttp.ClientSession", lambda: session)
    oauth = OAuth("client", "https://example.invalid", token_file)
    cancelled_caller = asyncio.create_task(oauth.async_get_access_token())
    await session.started.wait()
    cancelled_caller.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cancelled_caller

    # Act: Let close observe and clean up the transport failure.
    session.release.set()
    await oauth.async_close()

    # Assert: Closing logs the failure and releases the owned transport session.
    assert session.calls == 1
    assert session.closed is True
    assert any(
        "Token refresh failed while closing authentication" in record.message
        for record in caplog.records
    )


async def test_code_exchange_persists_tokens_and_unknown_fields(
    mock_responses, token_file
):
    """Successful code exchange should store the token response fields."""
    # Arrange: Mock a token response that also carries a field from a newer API.
    token_data = load_fixture_json("auth_token.json")
    token_data["future"] = {"enabled": True}

    mock_responses.post(ENDPOINT_TOKEN, payload=token_data)

    async with aiohttp.ClientSession() as session:
        oauth = OAuth("client", "https://example.invalid", token_file, session)
        oauth.get_authorization_url()

        # Act: Exchange the authorization code for tokens.
        await oauth.async_exchange_code_for_tokens("accepted-code")

    # Assert: The persisted document keeps every response field plus an expiry.
    saved_tokens = json.loads(token_file.read_text(encoding="utf-8"))
    assert saved_tokens["access_token"] == token_data["access_token"]
    assert saved_tokens["refresh_token"] == token_data["refresh_token"]
    assert saved_tokens["expires_in"] == token_data["expires_in"]
    assert saved_tokens["future"] == {"enabled": True}
    assert isinstance(saved_tokens["expires_at"], float)


async def test_code_exchange_without_expires_in_skips_computed_expiry(
    mock_responses, token_file
):
    """Token responses without expires_in should not compute an absolute expiry."""
    # Arrange: Mock a minimal valid token response.
    mock_responses.post(ENDPOINT_TOKEN, status=200, payload={"access_token": "t"})

    async with aiohttp.ClientSession() as session:
        oauth = OAuth("client", "https://example.invalid", token_file, session)
        oauth.get_authorization_url()

        # Act: Exchange the code for the minimal token.
        await oauth.async_exchange_code_for_tokens("accepted-code")

    # Assert: The token persists without a computed absolute expiry.
    saved = json.loads(token_file.read_text(encoding="utf-8"))
    assert saved == {"access_token": "t"}


async def test_code_exchange_sends_the_verifier_matching_the_login_challenge(
    mock_responses,
    token_file,
):
    """The code exchange must send the PKCE verifier behind the login URL."""
    # Arrange: Create the login URL and read its PKCE challenge.
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


async def test_code_exchange_failure_does_not_write_tokens(mock_responses, token_file):
    """Rejected authorization codes should not create token storage."""
    # Arrange: Mock a rejected token exchange.
    mock_responses.post(ENDPOINT_TOKEN, status=400, body="invalid authorization code")

    async with aiohttp.ClientSession() as session:
        oauth = OAuth("client", "https://example.invalid", token_file, session)
        oauth.get_authorization_url()

        # Act and assert: A rejected token exchange should raise a library error.
        with pytest.raises(ViAuthError, match="Failed to fetch token"):
            await oauth.async_exchange_code_for_tokens("rejected-code")

    # Assert: Failed authentication should not create a token file.
    assert not token_file.exists()


@pytest.mark.parametrize(
    ("token_body", "message"),
    [
        pytest.param("{invalid", "invalid JSON data", id="malformed-json"),
        pytest.param("[]", "must be a JSON object", id="not-an-object"),
        pytest.param(
            '{"refresh_token": "new"}',
            "access_token must be a non-empty string",
            id="access-token-missing",
        ),
        pytest.param(
            '{"access_token": 1}',
            "access_token must be a non-empty string",
            id="access-token-not-text",
        ),
        pytest.param(
            '{"access_token": "t", "refresh_token": 5}',
            "refresh_token must be a string",
            id="refresh-token-not-text",
        ),
        pytest.param(
            '{"access_token": "t", "token_type": 5}',
            "token_type must be a string",
            id="token-type-not-text",
        ),
        pytest.param(
            '{"access_token": "t", "expires_in": NaN}',
            "invalid JSON data",
            id="expires-in-not-json",
        ),
        pytest.param(
            '{"access_token": "t", "expires_in": -1}',
            "non-negative finite number",
            id="expires-in-negative",
        ),
        pytest.param(
            '{"access_token": "t", "expires_in": true}',
            "non-negative finite number",
            id="expires-in-boolean",
        ),
    ],
)
async def test_code_exchange_rejects_invalid_token_response_without_overwriting(
    mock_responses, token_file, token_body: str, message: str
):
    """Invalid successful token responses must leave stored credentials intact."""
    # Arrange: Persist credentials that an invalid 200 response must not replace.
    _write_token_document(token_file)
    original_content = token_file.read_text(encoding="utf-8")

    mock_responses.post(ENDPOINT_TOKEN, status=200, body=token_body)

    async with aiohttp.ClientSession() as session:
        oauth = OAuth("client", "https://example.invalid", token_file, session)
        oauth.get_authorization_url()

        # Act and assert: The exchange rejects the invalid response.
        with pytest.raises(ViAuthError, match=message):
            await oauth.async_exchange_code_for_tokens("accepted-code")

    # Assert: The saved credential document is unchanged.
    assert token_file.read_text(encoding="utf-8") == original_content


async def test_code_exchange_before_creating_the_login_url_is_rejected(token_file):
    """Exchanging a code before generating the authorization URL should reject."""
    oauth = OAuth("client", "https://example.invalid", token_file)

    with pytest.raises(ViAuthError, match="PKCE Verifier missing"):
        await oauth.async_exchange_code_for_tokens("accepted-code")


async def test_access_token_request_before_authentication_is_rejected(token_file):
    """Requesting a token before authentication should explain the requirement."""
    oauth = OAuth("client", "https://example.invalid", token_file)

    with pytest.raises(ViAuthError, match="No tokens loaded"):
        await oauth.async_get_access_token()


async def test_expired_tokens_without_refresh_token_fall_back_with_warning(
    token_file, caplog
):
    """Expired tokens without a refresh token should fall back with a warning."""
    # Arrange: Store an expired access token without a refresh token.
    _write_token_document(
        token_file,
        without=("refresh_token",),
        access_token="expired-token",
        expires_at=0,
    )
    oauth = OAuth("client", "https://example.invalid", token_file)

    # Act: Request the access token.
    with caplog.at_level(logging.WARNING):
        token = await oauth.async_get_access_token()

    # Assert: The possibly expired token returns with a logged warning.
    assert token == "expired-token"
    assert any(
        "No refresh token available" in record.message for record in caplog.records
    )


async def test_stored_tokens_without_access_token_are_rejected(token_file):
    """Token state without a usable access token should reject the request."""
    # Arrange: Store an unexpired token document that lacks the access token.
    _write_token_document(
        token_file, without=("access_token",), expires_at=time.time() + 3600
    )
    oauth = OAuth("client", "https://example.invalid", token_file)

    # Act and assert: The token request explains the invalid state.
    with pytest.raises(ViAuthError, match="No valid access token"):
        await oauth.async_get_access_token()


async def test_refresh_without_refresh_token_is_rejected(token_file):
    """Refreshing without a stored refresh token should reject the request."""
    # Arrange: Store a valid access token without a refresh token.
    _write_token_document(
        token_file, without=("refresh_token",), expires_at=time.time() + 3600
    )
    oauth = OAuth("client", "https://example.invalid", token_file)

    # Act and assert: The refresh explains the missing token.
    with pytest.raises(ViAuthError, match="No refresh token available"):
        await oauth.async_refresh_access_token()


async def test_authenticated_requests_add_a_bearer_header_without_mutating_input(
    mock_responses,
    token_file,
):
    """Requests carry the access token while caller headers stay untouched."""
    # Arrange: Store a valid token and provide caller headers to keep unchanged.
    _write_token_document(token_file, expires_at=time.time() + 3600)
    caller_headers = {"Accept": "application/json"}

    mock_responses.get(INSTALLATIONS_URL, payload={"data": []})
    async with aiohttp.ClientSession() as session:
        oauth = OAuth("client", "https://example.invalid", token_file, session)

        # Act: Send one authenticated request.
        async with await oauth.request(
            "GET", INSTALLATIONS_URL, headers=caller_headers
        ):
            pass

    # Assert: The sent request has both headers; the caller's dict is unchanged.
    (request,) = mock_responses.requests[("GET", URL(INSTALLATIONS_URL))]
    assert request.kwargs["headers"] == {
        "Accept": "application/json",
        "Authorization": "Bearer stored-access",
    }
    assert caller_headers == {"Accept": "application/json"}


async def test_oauth_creates_and_closes_internal_websession(mock_responses, token_file):
    """OAuth should manage a session when the caller does not supply one."""
    # Arrange: Store a valid token and mock the installations endpoint.
    _write_token_document(token_file, expires_at=time.time() + 3600)
    oauth = OAuth("client", "https://example.invalid", token_file)

    mock_responses.get(INSTALLATIONS_URL, payload={"data": []})

    # Act: Make a client request within the OAuth resource context.
    async with oauth:
        installations = await ViClient(oauth).get_installations()
        internal_websession = oauth.websession

    # Assert: The request should work and the internally owned session should close.
    assert installations == []
    assert internal_websession is not None
    assert internal_websession.closed is True
    assert oauth.websession is None


async def test_oauth_recreates_an_internal_websession_after_closing(
    mock_responses, token_file
):
    """OAuth should create a new owned session for a request after closing."""
    # Arrange: Store a valid token and mock repeated installation requests.
    _write_token_document(token_file, expires_at=time.time() + 3600)
    oauth = OAuth("client", "https://example.invalid", token_file)

    mock_responses.get(INSTALLATIONS_URL, payload={"data": []}, repeat=True)

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


async def test_oauth_keeps_external_websession_open(token_file):
    """OAuth should not close a session supplied by the caller."""
    # Arrange: Create an external session and an OAuth provider that uses it.
    async with aiohttp.ClientSession() as external_websession:
        oauth = OAuth(
            "client", "https://example.invalid", token_file, external_websession
        )

        # Act: Enter and exit the OAuth resource context.
        async with oauth:
            pass

        # Assert: OAuth should leave the caller-owned session open.
        assert external_websession.closed is False
        assert oauth.websession is external_websession


async def test_close_tolerates_an_already_closed_owned_session(
    mock_responses, token_file
):
    """Closing should stay safe when the owned session was already closed."""
    # Arrange: Create an owned session through one request, then close it directly.
    _write_token_document(token_file, expires_at=time.time() + 3600)
    oauth = OAuth("client", "https://example.invalid", token_file)

    mock_responses.get(INSTALLATIONS_URL, payload={"data": []})
    await ViClient(oauth).get_installations()
    assert oauth.websession is not None
    await oauth.websession.close()

    # Act: Close the provider after its owned session already closed.
    await oauth.async_close()

    # Assert: The close completes and clears the session reference.
    assert oauth.websession is None
