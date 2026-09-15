---
status: accepted
---

# Use a recursive JSON value contract at data boundaries

`vi_api_client` exposes `JsonValue` for JSON-compatible dynamic data and
`FeatureValue` for feature values. Both retain normal Python `list` and `dict`
runtime shapes, so consumers can use newly introduced or device-specific API
feature values without a release for each concrete schema.

External responses and persisted documents cross an explicit validation
boundary. The shared `validate_json_value` helper rejects non-JSON Python
objects with `ViResponseError`; response-specific code must separately validate
known fields it consumes. Unknown additional fields remain allowed for forward
compatibility. This avoids broad `Any` contracts without introducing a runtime
modeling dependency or asserting a concrete type for every API feature.
