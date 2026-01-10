"""Constants for Viessmann API Client."""

API_BASE_URL = "https://api.viessmann-climatesolutions.com"
AUTH_BASE_URL = "https://iam.viessmann-climatesolutions.com/idp/v3"

# Endpoints
ENDPOINT_AUTHORIZE = f"{AUTH_BASE_URL}/authorize"
ENDPOINT_TOKEN = f"{AUTH_BASE_URL}/token"

ENDPOINT_INSTALLATIONS = "/iot/v2/equipment/installations"
ENDPOINT_GATEWAYS = "/iot/v2/equipment/gateways"
ENDPOINT_ANALYTICS_THERMAL = "/iot/v1/analytics-api/dataLake/chronos/v0/thermal_energy"

# Scopes
SCOPE_IOT_USER = "IoT User"
SCOPE_OFFLINE_ACCESS = "offline_access"
DEFAULT_SCOPES = f"{SCOPE_IOT_USER} {SCOPE_OFFLINE_ACCESS}"
