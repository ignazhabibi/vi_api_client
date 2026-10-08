"""Tests for the installation event history read path."""

import pytest
from builders import load_fixture_device

from vi_api_client.const import API_BASE_URL, ENDPOINT_EVENT_HISTORY
from vi_api_client.exceptions import ViResponseError
from vi_api_client.models import EventHistoryPage, InstallationEvent

INSTALLATION_ID = "99999"
EVENTS_URL = f"{API_BASE_URL}{ENDPOINT_EVENT_HISTORY}/{INSTALLATION_ID}/events"


def _page_url(query: str) -> str:
    """Return one event history request URL with its query string."""
    return f"{EVENTS_URL}?{query}"


async def test_get_event_history_returns_first_page_by_days(vi_client, mock_responses):
    """A days window should request one page and preserve provider details."""
    # Arrange: Load the bundled page and mock the verified GET route.
    payload = load_fixture_device("event_history")
    url = _page_url("lastNDays=7&limit=50")

    mock_responses.get(url, payload=payload)

    # Act: Request the first page of a rolling week.
    page = await vi_client.get_event_history(INSTALLATION_ID, days=7, limit=50)

    # Assert: Known fields are typed, unknown fields and bodies survive.
    assert len(page.events) == 3
    first = page.events[0]
    assert first.event_type == "feature-changed"
    assert first.created_at == "2026-09-20T10:15:30.878Z"
    assert first.event_timestamp == "2026-09-20T10:15:30.000Z"
    assert first.gateway_serial == "1234567890123456"
    assert first.body == {
        "featureName": "heating.dhw.temperature.main",
        "commandName": "setTargetTemperature",
        "commandBody": {"temperature": 55},
    }
    assert first.fields["editedBy"] == "3f2b1c0d-1111-4a5b-8c9d-0e1f2a3b4c5d"
    assert first.fields["audiences"] is None
    third = page.events[2]
    assert third.fields["unknownFutureField"] == {"nested": ["kept", "as-is"]}
    assert page.next_cursor == "b3BhcXVlLWN1cnNvci10b2tlbg=="

    request_entries = list(mock_responses.requests.items())
    assert len(request_entries) == 1
    (method, request_url), _requests = request_entries[0]
    assert method == "GET"
    assert str(request_url) == url
    request = _requests[0]
    assert dict(request.query) == {"lastNDays": "7", "limit": "50"}


async def test_get_event_history_follows_cursor_without_window(
    vi_client, mock_responses
):
    """A cursor page should not repeat the lookback window."""
    # Arrange: Register the continuation URL, with the cursor as the provider
    # receives it on the wire, and a final page.
    cursor = "b3BhcXVlLWN1cnNvci10b2tlbg=="
    url = _page_url("cursor=b3BhcXVlLWN1cnNvci10b2tlbg%3D%3D")

    mock_responses.get(url, payload={"data": []})

    # Act: Request the next page through the opaque cursor.
    page = await vi_client.get_event_history(INSTALLATION_ID, cursor=cursor)

    # Assert: The final page is empty without a further cursor, and the
    # request carries the cursor instead of the lookback window.
    assert page.events == ()
    assert page.next_cursor is None
    request = next(iter(mock_responses.requests.values()))[0]
    assert dict(request.query) == {"cursor": cursor}


@pytest.mark.parametrize(
    ("days", "cursor", "limit", "message"),
    [
        pytest.param(
            None,
            None,
            None,
            "exactly one of 'days' or 'cursor'",
            id="neither-days-nor-cursor",
        ),
        pytest.param(
            7,
            "cursor-token",
            None,
            "exactly one of 'days' or 'cursor'",
            id="both-days-and-cursor",
        ),
        pytest.param(0, None, None, "positive lookback window", id="zero-days"),
        pytest.param(-3, None, None, "positive lookback window", id="negative-days"),
        pytest.param(None, "", None, "non-empty string", id="empty-cursor"),
        pytest.param(7, None, 0, "between 1 and 1000", id="limit-below-range"),
        pytest.param(7, None, 1001, "between 1 and 1000", id="limit-above-range"),
    ],
)
@pytest.mark.usefixtures("no_http_requests")
async def test_get_event_history_rejects_invalid_windows(
    vi_client,
    days: int | None,
    cursor: str | None,
    limit: int | None,
    message: str,
):
    """Invalid window arguments should fail before any request is sent."""
    with pytest.raises(ValueError, match=message):
        await vi_client.get_event_history(
            INSTALLATION_ID, days=days, cursor=cursor, limit=limit
        )


@pytest.mark.usefixtures("no_http_requests")
async def test_get_event_history_rejects_empty_installation_ids(vi_client):
    """An event history read requires a usable installation scope."""
    with pytest.raises(ValueError, match="non-empty string"):
        await vi_client.get_event_history("", days=7)


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        pytest.param([], "response must be an object", id="root-not-object"),
        pytest.param({"data": {}}, "data must be a list", id="data-not-list"),
        pytest.param(
            {"data": ["not-an-object"]},
            "entries must be objects",
            id="data-entry-not-object",
        ),
        pytest.param(
            {"data": [{"eventType": "device.error.raised"}]},
            "createdAt must be a non-empty string",
            id="missing-required-fields",
        ),
        pytest.param(
            {
                "data": [
                    {
                        "eventType": "device.error.raised",
                        "createdAt": 5,
                        "eventTimestamp": "2026-09-18T08:02:10.500Z",
                    }
                ]
            },
            "createdAt must be a non-empty string",
            id="createdAt-not-string",
        ),
        pytest.param(
            {
                "data": [
                    {
                        "eventType": "device.error.raised",
                        "createdAt": "2026-09-18T08:02:11.000Z",
                        "eventTimestamp": "2026-09-18T08:02:10.500Z",
                        "gatewaySerial": 5,
                    }
                ]
            },
            "gatewaySerial must be a string",
            id="gatewaySerial-not-string",
        ),
        pytest.param(
            {
                "data": [
                    {
                        "eventType": "device.error.raised",
                        "createdAt": "2026-09-18T08:02:11.000Z",
                        "eventTimestamp": "2026-09-18T08:02:10.500Z",
                        "audiences": "OWNER",
                    }
                ]
            },
            "audiences must be a list of strings",
            id="audiences-not-list",
        ),
        pytest.param(
            {
                "data": [
                    {
                        "eventType": "device.error.raised",
                        "createdAt": "2026-09-18T08:02:11.000Z",
                        "eventTimestamp": "2026-09-18T08:02:10.500Z",
                        "audiences": [5],
                    }
                ]
            },
            "audiences must be a list of strings",
            id="audiences-entry-not-string",
        ),
        pytest.param(
            {"data": [], "cursor": []},
            "cursor must be an object",
            id="cursor-not-object",
        ),
        pytest.param(
            {"data": [], "cursor": {"next": 5}},
            "cursor next must be a string",
            id="cursor-next-not-string",
        ),
    ],
)
async def test_get_event_history_rejects_malformed_responses(
    vi_client, mock_responses, payload: dict | list, message: str
):
    url = _page_url("lastNDays=7")

    mock_responses.get(url, payload=payload)

    with pytest.raises(ViResponseError, match=message):
        await vi_client.get_event_history(INSTALLATION_ID, days=7)


async def test_get_event_history_accepts_empty_cursor_next_as_final_page(
    vi_client, mock_responses
):
    """The provider reports the final page with an empty next cursor."""
    # Arrange: The live API sends cursor.next as "" when no pages follow.
    url = _page_url("lastNDays=7")

    mock_responses.get(url, payload={"data": [], "cursor": {"next": ""}})

    # Act: Request the final page of the window.
    page = await vi_client.get_event_history(INSTALLATION_ID, days=7)

    # Assert: The empty string means no continuation cursor.
    assert page.events == ()
    assert page.next_cursor is None


async def test_get_event_history_rejects_successful_non_json_responses(
    vi_client, mock_responses
):
    url = _page_url("lastNDays=7")

    mock_responses.get(url, body="not JSON", content_type="text/plain")

    with pytest.raises(ViResponseError, match="not valid JSON"):
        await vi_client.get_event_history(INSTALLATION_ID, days=7)


def test_installation_event_rejects_non_json_nested_values():
    """Event data outside the JSON value contract should reject publicly."""
    # Act and assert: A non-JSON nested value cannot be preserved.
    with pytest.raises(ViResponseError, match="non-JSON nested value"):
        InstallationEvent.from_api(
            {
                "eventType": "device.error.raised",
                "createdAt": "2026-09-18T08:02:11.000Z",
                "eventTimestamp": "2026-09-18T08:02:10.500Z",
                "unknownField": object(),
            }
        )


def test_event_history_page_stores_immutable_snapshots():
    """Pages and events should not alias caller-owned collections."""
    # Arrange: Build a page from caller-owned collections.
    body = {"errorCode": "F.9000"}
    raw_event = {
        "eventType": "device.error.raised",
        "createdAt": "2026-09-18T08:02:11.000Z",
        "eventTimestamp": "2026-09-18T08:02:10.500Z",
        "body": body,
    }
    event = InstallationEvent.from_api(raw_event)
    caller_events = [event]
    page = EventHistoryPage(events=caller_events, next_cursor="cursor-token")

    # Act: Mutate the caller-owned collections in place after construction.
    body["errorCode"] = "CHANGED"
    caller_events.append(event)

    # Assert: The page and its event keep their original snapshots.
    assert page.events == (event,)
    assert page.events[0].body == {"errorCode": "F.9000"}
    assert page.events[0].fields["body"] == {"errorCode": "F.9000"}
    assert page.next_cursor == "cursor-token"


def test_installation_event_rejects_non_object_events():
    """An event must be a JSON object."""
    with pytest.raises(ViResponseError, match="Event must be an object"):
        InstallationEvent.from_api(["not", "an", "event"])  # type: ignore[arg-type]
