---
status: accepted
---

# Match feature names locally instead of filtering on the server

`get_features(device, feature_names=...)` fetches the device's features without
the API's server-side name filter and selects features locally after parsing. A
requested name matches a feature by its own name or by the name of the API
feature it was parsed from, so both `heating.circuits.0.heating.curve.slope`
and `heating.circuits.0.heating.curve` work.

The server-side filter selects API features, while callers address the flat
features parsed from them. In more than half of the bundled API features, the
parsed features carry a different name than the API feature, so a flat name is
not a valid server-side filter value. A flat name also cannot be mapped back to
its API feature reliably: `heating.circuits.0.name` is both a feature of
`heating.circuits.0` and a separate API feature. Matching in the shared core
keeps the live and fixture-backed clients identical, because the fixture
adapter returns every feature anyway.

The cost is a larger response for targeted reads; the number of API calls does
not change. Unknown names select nothing instead of producing an API 404.
