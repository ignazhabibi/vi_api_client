"""Constants for Viessmann API Client."""

API_BASE_URL: str = "https://api.viessmann-climatesolutions.com"
AUTH_BASE_URL: str = "https://iam.viessmann-climatesolutions.com/idp/v3"

# Endpoints
ENDPOINT_AUTHORIZE: str = f"{AUTH_BASE_URL}/authorize"
ENDPOINT_FEATURES: str = "/iot/v2/features/installations"
ENDPOINT_GATEWAYS: str = "/iot/v2/equipment/gateways"
ENDPOINT_INSTALLATIONS: str = "/iot/v2/equipment/installations"
ENDPOINT_TOKEN: str = f"{AUTH_BASE_URL}/token"

# Installation event history. The read-only live probe in
# scripts/probe_event_history.py compared both candidate spellings on a real
# installation: this 2023 announcement route returned a valid page with a
# continuation cursor, while the developer-portal OpenAPI export spelling
# ("eventhistory") returned 404 ENDPOINT_NOT_FOUND (issue #118).
ENDPOINT_EVENT_HISTORY: str = "/iot/v2/events-history/installations"
EVENT_HISTORY_MAX_LIMIT: int = 1000

# Scopes
SCOPE_IOT_USER: str = "IoT User"
SCOPE_OFFLINE_ACCESS: str = "offline_access"
DEFAULT_SCOPES: str = f"{SCOPE_IOT_USER} {SCOPE_OFFLINE_ACCESS}"
