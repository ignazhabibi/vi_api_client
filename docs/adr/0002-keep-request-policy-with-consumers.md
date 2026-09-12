---
status: accepted
---

# Keep request policy with consumers

Consumers own API request concurrency, retries, backoff, polling cadence, stale
state, and the interpretation of partial availability. The library does not add
a client-wide lock, rate limiter, retry policy, or automatic parallelization of
composite reads.

Each standalone `OAuth` instance coordinates its own in-flight token refreshes
for tasks on one event loop. Overlapping automatic and explicit refresh callers
share that operation, and cancellation of one caller does not cancel it for the
others. Applications should share one `OAuth` instance for a running context;
the library does not coordinate refreshes across threads, event loops,
instances, processes, or credential documents.

On a 429 response, `ViRateLimitError.retry_after` exposes valid server guidance
in seconds when available. Consumers decide whether and how to delay and retry.
