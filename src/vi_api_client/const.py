"""Constants for Viessmann API Client."""

API_BASE_URL: str = "https://api.viessmann-climatesolutions.com"
AUTH_BASE_URL: str = "https://iam.viessmann-climatesolutions.com/idp/v3"

# Endpoints
ENDPOINT_ANALYTICS_THERMAL: str = (
    "/iot/v1/analytics-api/dataLake/chronos/v0/thermal_energy"
)
ENDPOINT_AUTHORIZE: str = f"{AUTH_BASE_URL}/authorize"
ENDPOINT_FEATURES: str = "/iot/v2/features/installations"
ENDPOINT_GATEWAYS: str = "/iot/v2/equipment/gateways"
ENDPOINT_INSTALLATIONS: str = "/iot/v2/equipment/installations"
ENDPOINT_TOKEN: str = f"{AUTH_BASE_URL}/token"

# Scopes
SCOPE_IOT_USER: str = "IoT User"
SCOPE_OFFLINE_ACCESS: str = "offline_access"
DEFAULT_SCOPES: str = f"{SCOPE_IOT_USER} {SCOPE_OFFLINE_ACCESS}"
