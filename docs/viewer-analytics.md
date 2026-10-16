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
- `GET/PUT /api/v1/viewers/config` reads or changes player URL, allowed origin, public tracking base URL, timezone, and enablement. Configuration reads and writes require `viewers:manage` (Administrator). `GET` returns `configuration_id`, `detail_retention_days` (default 90), and `history_retention_days` (default 730). `PUT` may update either retention value; both must be whole days and satisfy `1 <= detail_retention_days <= history_retention_days <= 730`. Player URL, allowed origin, and public tracking URL changes create a new configuration identity and revoke the prior tracking key. Timezone, enablement, and retention changes preserve that identity.
- `POST /api/v1/viewers/key/rotate` revokes the old key and returns a replacement key and snippet once. It requires `viewers:manage`.
- `POST /api/v1/viewers/report` is the only anonymous ingestion operation. It requires the `X-Tracking-Key` returned once during save/rotation, the matching `Origin`, and exactly `{ "browser_id": "…", "tab_id": "…", "playing": true|false }`. IDs are 16–80 ASCII letters, digits, underscore, or hyphen. Requests are capped at 4 KiB, 60 per client IP per minute, and 10,000 concurrent tab sessions. Successful reports return 202; invalid/revoked key returns 401, rejected origin 403, and exceeded limit 429. CORS allows only the configured player origin.

Scoped keys may be limited independently to `viewers:read`, `viewers:export`, or `viewers:manage`. Operators and Administrators can read and export; only Administrators can manage tracking. Invalid or expired bearer keys never fall back to a browser session, and `require_api_key` applies independently to machine calls. Key material is hashed in persistent state and never appears in API reads. `POST /api/v1/viewers/key/rotate` immediately revokes the former key and returns a replacement once; `DELETE /api/v1/viewers/key` revokes the current key without returning material and clears active presence. A later save or rotate issues a new key. Disabling tracking prevents ingestion; changing the player, origin, or public tracking URL starts a new active cohort. Configuration and active presence survive process replacement using the existing persistent data volume; sessions naturally expire after 90 seconds without reports.

## Measurement boundaries

The live count is the number of distinct browser IDs with at least one recently reporting tab. It estimates browser audiences and does not represent unique people. This initial viewer-tracking slice does not reconstruct audience history before instrumentation or count uninstrumented players. Opening or polling the public player URL is not used to infer audience size; only valid reports from the installed media-progress snippet count.

Reports can be blocked by browser privacy controls, content security policy, extensions, DNS/network failures, gateway policy, or a revoked key. Browsers can delay timers in background tabs, and client reports can be forged or shared. The counter is therefore an operational estimate, not an auditable attendance record. The collector health indicator reports whether this recorder has sampled recently; **Last playback report** reports when any accepted player report last arrived. A healthy collector with no playback reports is not by itself an outage, and an old report does not establish current reachability.

There is no backfill before installation and no person-level identity. The script sends random browser and tab tokens only. Local-storage restrictions may make tabs appear as separate browsers. A stream recording is never counted as a browser audience.

### Bounded collection and query cost

The collector writes one sample every 30 seconds whether or not a recording is running: at most 2,880 sample rows per day and about 259,200 rows during the default 90-day detailed window. Older covered samples are compacted to at most 17,520 UTC-hour summaries over the 730-day maximum history window. The database stores only count, coverage, timestamp, and configuration identity for those summaries; browser/tab identifiers are not stored with samples. A report is 4 KiB maximum, accepted at no more than 60 reports per client IP per minute, and active tab presence is capped at 10,000 sessions. History responses are bounded to 1,000 timeline buckets; requests over configured retention are rejected. These fixed bounds keep ingestion, storage growth, and response size finite; they are not a promise of a particular disk footprint or latency on a given host.

For a deployment sanity check, inspect the persistent SQLite file size before and after several days of representative use, query a long permitted history range, and confirm the response has no more than 1,000 points. If an audience shares one public IP, the 60-per-minute limit is shared by that audience; each continuously playing tab reports about twice per minute. A 429 should be treated as dropped telemetry, not as an audience decrease.

### Deployment checklist and diagnosis

1. Set **Player page URL** to the actual page containing the `<video>` element; set **Allowed player origin** to its exact origin (for example `https://player.example`, without a path). Set **Public tracking base URL** to the externally reachable HTTPS recorder URL.
2. Save and install the generated snippet on the player page after its video element exists. Do not use the investigation example as a default and do not put API, gateway service, source, or user credentials in the page.
3. At the gateway, allow anonymous `OPTIONS` preflight and `POST` only for `/api/v1/viewers/report`. Keep live/history/configuration/key management and all other recorder paths under the existing access policy. Test that report succeeds from the player origin and that anonymous live/history/configuration requests remain denied.
4. Confirm collector health separately from last playback report. If the collector is unavailable, inspect recorder process health, storage write errors, and whether the process has restarted. If collector health is healthy but last report is empty/stale, inspect the browser console/network panel for blocked CORS/preflight, 401 (missing or revoked key), 403 (origin mismatch), 413 (oversized payload), or 429 (rate/session limit); then compare the configured base URL and player origin to the actual browser URLs.
5. To rotate a lost or exposed key, use **Settings → External player analytics → Rotate key**, replace the deployed snippet, and verify the previous key now receives 401. To revoke without replacement, disable tracking or use the API DELETE operation. Key changes clear active presence; reports from the old snippet cannot repopulate the current cohort.
6. Choose detail and total retention to fit operational needs. Lowering detail retention compacts old data; lowering total retention removes expired detail/rollups and does not recreate them if retention is later raised. Set the shared reporting timezone explicitly and use it consistently when comparing local days, DST transitions, charts, and CSV exports.

After an application image replacement, verify the persistent `/data` volume is still mounted, then check that player configuration, configuration identities, history coverage, and retention settings remain present. A process/container outage creates a visible history gap; it is not backfilled or converted into zero viewers. Container replacement must not remove the SQLite state or recording files.

## Historical timeline and statistics

Stream Recorder samples current distinct active browser IDs every 30 seconds in the background, independent of recordings and open browser pages. Samples, coverage markers, and player configuration identities are stored in the application's existing durable SQLite state. A disabled collector stores uncovered intervals; a write failure or process outage leaves missing intervals. The collector does not backfill them or integrate stale presence. Administrators can configure detailed sample retention (1–730 days) and total history retention (up to 730 days, at or beyond the detail period). Defaults are 90 days of detailed samples and 730 days total. As samples age out of detail retention, the collector compacts known intervals into durable UTC-hour summaries containing weighted sums, covered seconds, and peaks. Unknown intervals remain missing. Retention runs with the background collector and uses the same persistent SQLite state as settings and history.

Operators and Administrators can open **Viewer analytics** from the main navigation. The page offers last hour, 24 hours, 7 days, 30 days, and custom ranges, a player configuration selector, and a resolution selector (automatic, 30 seconds, 1, 5, 15 minutes, 1, 6 hours, or 1 day). Server-side limits may raise the effective resolution, which the page displays. Export uses the same selected filters. The page refreshes every two minutes while open. The chart supports pointer hover, drag-to-zoom and reset, average/peak display, keyboard focus and arrow-key inspection, and responsive mobile layout. The live count and its timestamp remain separate from the selected-range statistics. A configuration selector keeps audiences from different player setups separate; the default is the currently configured player.

### Versioned analytics operations

All routes below are under `/api/v1`. Browser role checks and scoped-key permissions are applied on every request. Read and export operations never expose tracking secrets or browser/tab identifiers.

| Operation | Route | Permission | Result |
| --- | --- | --- | --- |
| Live state and current count | `GET /viewers/live` | `viewers:read` | Available count, as-of timestamp, collector health, and last valid report; disabled tracking returns a null count. |
| Historical analytics | `GET /viewers/history` | `viewers:read` | Timeline, statistics, coverage/gaps, daily peaks, heatmap and actual DST-aware periods, plus overlapping Recording context. |
| CSV export | `GET /viewers/export.csv` | `viewers:export` | Same bounded query filters and aggregation as history, as an attachment. |
| Player configuration history | `GET /viewers/configurations` | `viewers:read` | Current ID and known configuration identities, URLs, creation time, and first/last stored history timestamps. |
| Settings and diagnostics | `GET/PUT /viewers/config`; `GET /viewers/diagnostics` | `viewers:manage` | Administrator configuration, collector health, last sample/report and retention status; no secret readback. |
| Tracking key lifecycle | `POST /viewers/key/rotate`; `DELETE /viewers/key` | `viewers:manage` | Rotate reveals new key/snippet once; DELETE revokes and clears active presence. |

CSV downloads as `viewer-analytics.csv`, is UTF-8 with a header and a `row_type` discriminator (`meta`, `live_status`, `statistic`, `point`, `daily_peak`, `heatmap`, `heatmap_period`, `recording`). The shared columns explicitly label Unix timestamps, reporting timezone, browser-count unit, aggregation, known and expected coverage, and percentage. Empty metrics mean unknown; a point row with zero covered seconds is a gap, while a covered zero is a measured zero. Statistics report peak, time-weighted average concurrency and viewer-hours; daily/heatmap rows retain applicable coverage; Recording rows are context only. `from` and exclusive `to`, selected configuration, requested and effective resolution, precision, retention/expiration flags and excluded partial rollup hours are included as metadata. CSV quoting follows RFC 4180 and spreadsheet formula-leading text is neutralized. Results include at most 1,000 timeline buckets; range is bounded by configured total retention (at most 730 days), so derived rows are bounded too. No tracking key, browser identifier, or tab identifier is exported.

Example: create an Operator API key with `viewers:read` and `viewers:export`, then use the same query filters for JSON and CSV:

```sh
curl -fsS -H "Authorization: Bearer $KEY" \
  "$BASE/api/v1/viewers/history?from=1792166400&to=1792168200&resolution=5m&timezone=America%2FIndiana%2FIndianapolis&configuration_id=$CONFIGURATION_ID"
curl -fsS -H "Authorization: Bearer $KEY" \
  "$BASE/api/v1/viewers/export.csv?from=1792166400&to=1792168200&resolution=5m&timezone=America%2FIndiana%2FIndianapolis&configuration_id=$CONFIGURATION_ID" \
  -o viewer-analytics.csv
curl -fsS -H "Authorization: Bearer $KEY" "$BASE/api/v1/viewers/configurations"
```

### History API

`GET /api/v1/viewers/history` requires `viewers:read` (Operator or Administrator); `/viewers/export.csv` uses the same parameters and requires `viewers:export`:

- `from` and `to`: required Unix timestamps in seconds; `to` is exclusive, `from < to`, future end is limited to one minute, and the range may not exceed configured total retention (maximum 730 days).
- `resolution`: `auto` (default), `30s`, `1m`, `5m`, `15m`, `1h`, `6h`, or `1d`. The server raises the effective resolution when needed to bound the response to at most 1,000 buckets.
- `configuration_id`: optional identity returned in `configurations`; omitted means the current player configuration.

The response includes `timezone`, `configuration_id`, all known `configurations` with their player URL, creation time, and first/last sample times, `resolution_seconds`, `coverage_seconds`, `coverage_percent`, `statistics`, current `live` status, and ordered `points`. It also reports `precision` (`sampled` or `hourly-rollup`), `detail_expired`, `history_expired`, configured retention days, and `partial_rollup_hours_excluded`, the count of intersecting old hourly buckets omitted because the range covers only part of their hour. The UI displays this omission count. Each point has `timestamp`, covered duration, weighted `average`, and `peak`. A bucket with no known samples has `covered_seconds: 0` and null metrics. No unknown duration contributes to the range peak, time-weighted average, or viewer-hours. All-zero covered intervals return zero metrics; a wholly uncovered range returns null peak and average and zero viewer-hours. `statistics.average` is the time-weighted concurrent browser count; `statistics.viewer_hours` is the sum of covered count-times-hours. Partial coverage is shown explicitly and must not be interpreted as a full-period average.

Invalid/missing timestamps, reversed or ranges over configured total retention, unknown resolutions, and malformed configuration IDs return `400 invalid_request`. Read requests with no permission return `401 unauthorized` or `403 forbidden` according to authentication state. Configuration endpoints remain Administrator-only (`viewers:manage`). These APIs never return tracking keys.

The sampler persists a point on its regular interval; recent history resolution is bounded by that cadence. Queries that intersect rollups use at least one-hour resolution. A rollup contributes only when its complete hour is inside the requested range; partial old hours remain unknown rather than estimating sub-hour contents; the response exposes their count as `partial_rollup_hours_excluded`. Reporting-timezone changes affect display and local grouping, not stored Unix timestamps. Requests may cover up to configured total history (maximum 730 days). Daily peaks, heatmaps, and Recording overlays are described above.

## Daily peaks, activity heatmap, and Recording context

The history response also groups known samples by the configured or explicitly selected IANA reporting timezone. `daily_peaks` contains the peak concurrent browser count, its sample time, and known versus expected coverage for each local date. A partly covered date may have missed a higher peak; coverage is shown beside every value. A day with no samples has a null peak and zero coverage. Counts are anonymous browsers, not people.

`heatmap` groups by `day_of_week` (Sunday `0` through Saturday `6`) and local `hour` (`0`–`23`). Each cell reports its peak concurrent browser count, covered and expected seconds, coverage percentage, and `periods`. Periods identify one actual local date/hour with Unix `from`/exclusive `to`, UTC offset, peak and coverage. The UI requires choosing one listed period before opening it in the timeline. Thus a cell aggregated across multiple dates is never treated as one actual date. Fall-back daylight-saving hours have distinct UTC offsets and remain separately selectable; spring-forward hours that do not occur are not invented. All ranges, daily boundaries, and groups use the response `timezone`; timestamps remain absolute Unix seconds.

`recordings` lists capture windows that overlap the query range. New recordings persist `ended_at` at the transition out of HLS capture, before MP4 processing. An active or recovering capture is bounded at the current time / query end. Older completed recordings without a known end time are omitted rather than assigned a fabricated duration. The UI shades capture windows over the timeline and offers a Recording action that opens its start/end range while carrying `configuration_id` and `timezone`. Capture windows are context only: they do not contribute viewers, fill collection gaps, or treat server requests as audience activity. Interrupted sessions remain viewer-history gaps wherever collection did not produce samples.

`GET /api/v1/viewers/history` accepts an optional `timezone` IANA query parameter to preserve a selected report's grouping context after the shared timezone changes. If omitted it uses the current shared reporting timezone. It continues to require `viewers:read`; Operators and Administrators may read it, while only Administrators may change tracking configuration. Results retain the configured total-history bound (maximum 730 days). Queries crossing rollup time use at least one-hour resolution; `partial_rollup_hours_excluded` makes excluded partial-hour buckets visible, and the chart never infers sub-hour viewer detail from summaries. The API returns partial coverage for each day, heatmap cell, and represented period so unavailable time is not mistaken for zero.
