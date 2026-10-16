# External player live viewer count

Stream Recorder can count anonymous browsers playing an external HTML video player. This is an estimate of active browsers, not named people, provider audience totals, or a count of server-side recordings. Fresh installs have an empty player URL and disabled tracking. The example `https://ch91.gvsit.org/player` is intentionally not configured by default. This setting is independent from the direct HLS stream source.

## Configure the player

1. Sign in as an Administrator and open **Settings → External player analytics**.
2. Enter the player page URL, its exact origin (scheme, host, and optional port only), the public HTTPS base URL used to reach this recorder, and an IANA reporting timezone such as `America/Indiana/Indianapolis`.
3. Enable tracking and save. Copy the generated snippet and install it on the external page containing the HTML `<video>` element. The key is returned only in this save response. If you lose the snippet, rotate the key and replace the old snippet; rotation immediately revokes old copies.
4. Confirm **Collector health** and **Last playback report** separately. Health indicates the collector endpoint is running; no last report means no valid player report has arrived yet.

The snippet sends only an opaque browser ID, an opaque per-tab ID, and a playing flag. It reports every 30 seconds only after media time has progressed. Muted and background playback count. Pause, end, waiting/stall, error, and page exit send a best-effort stop for that tab. Lost reports expire after 90 seconds. Tabs sharing browser local storage count once; separate browser profiles and devices count separately. No name, email, Administrator session, stream source credential, or device profile is sent.

Browser storage restrictions, script blockers, browser throttling, network loss, and key sharing can reduce or falsify counts. Origin validation and the dedicated public tracking key reduce casual misuse; they cannot prove a real person watched the player.

## Cloudflare Access / gateway setup

The player browser must reach the tracker directly. In the gateway policy, create a narrowly scoped bypass/service rule for `POST` and `OPTIONS` on `/api/v1/viewers/report` only, so the browser does not need a recorder sign-in or gateway service token. Keep `/api/v1/viewers/live`, `/api/v1/viewers/config`, `/api/v1/viewers/key/rotate`, `/api/v1/settings`, and the rest of the application behind the existing access policy. Do not put gateway service credentials in the player snippet. Ensure the player page's actual origin exactly matches **Allowed player origin**; the application also checks CORS and rejects other origins.

## HTTP API

- `GET /api/v1/viewers/live` returns `state: available` with `count` and `as_of`, or `state: disabled` with a null count. It also returns collector health and the last valid playback report timestamp. Operators and Administrators need `viewers:read`.
- `GET/PUT /api/v1/viewers/config` reads or changes player URL, allowed origin, public tracking base URL, timezone, and enablement. Configuration reads and writes require `viewers:manage` (Administrator).
- `POST /api/v1/viewers/key/rotate` revokes the old key and returns a replacement key and snippet once. It requires `viewers:manage`.
- `POST /api/v1/viewers/report` is the only anonymous ingestion operation. It requires the `X-Tracking-Key` returned once during save/rotation, the matching `Origin`, and exactly `{ "browser_id": "…", "tab_id": "…", "playing": true|false }`. IDs are 16–80 ASCII letters, digits, underscore, or hyphen. Requests are capped at 4 KiB, 60 per client IP per minute, and 10,000 concurrent tab sessions. Successful reports return 202; invalid/revoked key returns 401, rejected origin 403, and exceeded limit 429. CORS allows only the configured player origin.

Scoped keys may be limited to `viewers:read`; Administrator scoped keys need `viewers:manage`. Key material is hashed in persistent state and is never included in API reads. Disabling tracking prevents ingestion; changing the player, origin, or public tracking URL starts a new active cohort. Configuration and active presence survive process replacement using the existing persistent data volume; sessions naturally expire after 90 seconds without reports.

## Measurement boundaries

The live count is the number of distinct browser IDs with at least one recently reporting tab. It does not represent unique people. This initial viewer-tracking slice does not reconstruct audience history before instrumentation or count uninstrumented players.

## Historical timeline and statistics

Stream Recorder samples current distinct active browser IDs every 30 seconds in the background, independent of recordings and open browser pages. Samples, coverage markers, and player configuration identities are stored in the application's existing durable SQLite state. A disabled collector stores uncovered intervals; a write failure or process outage leaves missing intervals. The collector does not backfill them or integrate stale presence. History is retained alongside the rest of application state; this implementation does not yet apply the parent specification's configurable detail/rollup retention policy.

Operators and Administrators can open **Viewer analytics** from the main navigation. The page offers last hour, 24 hours, 7 days, 30 days, and custom ranges. It refreshes every two minutes while open. The chart supports pointer hover, drag-to-zoom and reset, average/peak display, keyboard focus and arrow-key inspection, and responsive mobile layout. The live count and its timestamp remain separate from the selected-range statistics. A configuration selector keeps audiences from different player setups separate; the default is the currently configured player.

### History API

`GET /api/v1/viewers/history` requires `viewers:read` (Operator or Administrator). Parameters:

- `from` and `to`: required Unix timestamps in seconds; `to` is exclusive, `from < to`, future end is limited to one minute, and the range may not exceed 90 days.
- `resolution`: `auto` (default), `30s`, `1m`, `5m`, `15m`, `1h`, `6h`, or `1d`. The server raises the effective resolution when needed to bound the response to at most 1,000 buckets.
- `configuration_id`: optional identity returned in `configurations`; omitted means the current player configuration.

The response includes `timezone`, `configuration_id`, all known `configurations` with their first and last sample times, `resolution_seconds`, `coverage_seconds`, `coverage_percent`, `statistics`, current `live` status, and ordered `points`. Each point has `timestamp`, covered duration, weighted `average`, and `peak`. A bucket with no known samples has `covered_seconds: 0` and null metrics. No unknown duration contributes to the range peak, time-weighted average, or viewer-hours. All-zero covered intervals return zero metrics; a wholly uncovered range returns null peak and average and zero viewer-hours. `statistics.average` is the time-weighted concurrent browser count; `statistics.viewer_hours` is the sum of covered count-times-hours. Partial coverage is shown explicitly and must not be interpreted as a full-period average.

Invalid/missing timestamps, reversed or over-90-day ranges, unknown resolutions, and malformed configuration IDs return `400 invalid_request`. Read requests with no permission return `401 unauthorized` or `403 forbidden` according to authentication state. Configuration endpoints remain Administrator-only (`viewers:manage`). These APIs never return tracking keys.

The sampler persists a point on its regular interval; therefore history resolution is bounded by that sampling cadence. Reporting-timezone changes affect labels, not stored Unix timestamps. This timeline does not yet include daily peaks, heatmaps, recording overlays, CSV export, or configurable retention; those are separate analytics work.
