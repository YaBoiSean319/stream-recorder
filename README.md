# Stream Recorder

Self-hosted live HLS preview and recording, with one MP4 per session, restart recovery, and automatic storage retention. Initial source: `https://ch91.gvsit.org/hls/test.m3u8`.

## Deploy

Requires a Linux Docker host with Docker Compose and enough writable storage for capture plus conversion. The application has no built-in authentication. Route **all** browser and API access through your operator-managed Cloudflare Access policy or another trusted boundary. Configure Entra groups for browser access and a Cloudflare service-token policy for Power Automate. Keep the origin inaccessible to bypass traffic. Compose binds only `127.0.0.1:8080` by default; a host-side tunnel or reverse proxy can reach it. For a separate-container tunnel, attach it to the Compose network and use `http://recorder:8080`, without publishing a public host port.

```sh
git clone https://github.com/YaBoiSean319/stream-recorder.git
cd stream-recorder
docker compose up -d --pull always
```

The initial release image is `ghcr.io/yaboisean319/stream-recorder:0.1.0`; it becomes available after the `v0.1.0` tag's publishing workflow passes. Until then, build locally with `docker compose up -d --build --pull never`. Open `http://localhost:8080`. The named `recorder-data` volume keeps settings, recording history, pins, source footage, and final MP4s across container replacement. Never use `docker compose down -v` unless deliberately erasing all data.

Environment overrides: `RECORDER_IMAGE` selects an image, `RECORDER_PORT` changes the host port, and `RECORDER_BIND` changes the host bind address. Changing the bind address requires an appropriately restricted network boundary. The app itself accepts `PORT` (8080) and `DATA_DIR` (/data).

To use a host storage directory, replace `recorder-data:/data` with `/your/storage/recorder:/data` and create it with ownership `10001:10001` before starting. The image runs as this non-root UID/GID. Back up the entire data directory while the service is stopped, including metadata and intermediate footage.

## Use

The preview uses bundled hls.js 1.6.13 (MIT license), with native HLS on supporting browsers. Playback is independent of recording, requires the browser to reach the source, and may require the source's CORS policy to allow your application origin. Browser autoplay restrictions may require pressing Play. A preview failure does not cancel server capture.

Start immediately, optionally enter a duration in minutes, and stop when finished. The server owns capture: closing the browser does not stop it. Exactly one session is admitted through capture, reconnecting, restart recovery, and MP4 processing. The next recording can start after Ready or terminal Error. Downloads become available only after final output validation. Pin important footage to prevent automatic cleanup. Manual deletion requires UI confirmation and can deliberately remove pinned footage.

Settings apply to future recordings; the active session retains its original source. Duration includes outages and container downtime. Manual sessions resume after restart until stopped; timed sessions retain their original deadline. A session whose deadline expired while offline proceeds to finalization. Explicitly stopped sessions do not resume. Interrupted finalization is recovered at startup.

Outages are recorded as interruptions. Reconnection preserves available footage but cannot reconstruct missing footage. HLS segment boundaries and connection timing mean starts/stops are not frame-exact; use the deterministic tests to characterize your source. MP4 filenames use the original start timestamp in UTC: `YYYY-MM-DD_HH-MM-SS_UTC_shortid.mp4`. The browser displays times in its local timezone. Compatible video/audio are remuxed to retain quality; incompatible media use H.264/AAC conversion.

## Storage

Default media cap: **100 GiB** (1 GiB = 1,073,741,824 bytes). The UI shows application media usage, configured cap, disk capacity, and free space. Usage includes active captures and conversion files. The configured cap is distinct from total disk capacity; other applications can exhaust the disk before the cap is reached.

Cleanup removes the oldest unpinned Ready recordings, ordered by original start time. Pinned and in-use media are protected. Admission requires at least 16 MiB of headroom. Finalization reserves the larger of three times source media size or 16 MiB, to cover conversion growth. If safe headroom cannot be freed, capture stops or admission is refused with a storage error. Recoverable footage is preserved; insufficient space can prevent a final MP4. The minimum accepted cap is 1 MiB, but useful recording requires more than the reserve. Increase the cap/free disk space and inspect any preserved source media when an error occurs.

## API

Successful API routes use JSON except completed downloads. Errors return non-success HTTP status with a descriptive text body: invalid inputs 400, unknown ID 404, and busy/in-use/unavailable operations 409. Check the response before proceeding. Start conflicts never create a second session. Save the ID returned by Start so delayed calendar stops cannot stop a later recording.

```sh
# Set your Access-protected origin. Service tokens belong in secret variables.
RECORDER_URL=https://recorder.example.org
# Cloudflare service-token requests also send these headers:
# CF-Access-Client-Id: <secret client id>
# CF-Access-Client-Secret: <secret client secret>

# Start a one-hour capture (omit duration_seconds for manual capture).
curl -fsS "$RECORDER_URL/api/recordings" -H 'Content-Type: application/json' -d '{"duration_seconds":3600}'
# Poll active session and storage. Keep the returned recording ID.
curl -fsS "$RECORDER_URL/api/status"
# Retry-safe stop targets one ID; replace RECORDING_ID with the saved ID.
curl -fsS -X POST "$RECORDER_URL/api/recordings/RECORDING_ID/stop"
# History and one recording's detail.
curl -fsS "$RECORDER_URL/api/recordings"
curl -fsS "$RECORDER_URL/api/recordings/RECORDING_ID"
# Download only when state is ready.
curl -fL "$RECORDER_URL/api/recordings/RECORDING_ID/download" -o recording.mp4
# Pin/unpin: true/false.
curl -fsS -X PATCH "$RECORDER_URL/api/recordings/RECORDING_ID" -H 'Content-Type: application/json' -d '{"pinned":true}'
# Explicit destructive delete, including pinned footage.
curl -fsS -X DELETE "$RECORDER_URL/api/recordings/RECORDING_ID"
# Current settings and a 100 GiB cap.
curl -fsS "$RECORDER_URL/api/settings"
curl -fsS -X PUT "$RECORDER_URL/api/settings" -H 'Content-Type: application/json' -d '{"source_url":"https://ch91.gvsit.org/hls/test.m3u8","storage_cap_bytes":107374182400}'
# Health endpoint.
curl -fsS "$RECORDER_URL/health"
```

Start accepts an optional positive numeric `duration_seconds`, up to one year. It returns a record containing `id`, `state`, `started_at`, `deadline`, `source_url`, `filename`, `pinned`, `progress`, `error`, and `interruptions`. Status returns `{active: record|null, storage: {media_bytes, cap_bytes, total_bytes, free_bytes}}`. History returns an array of records. Lifecycle states are `recording`, `reconnecting`, `recovering`, `processing`, `ready`, and `error`. Poll every few seconds until Ready or Error, rather than treating the stop response as a finished download. Power Automate handles calendar triggers externally; the recorder has no scheduler. Persist each calendar event's returned recording ID and target that ID when stopping. Protect service-token values using your automation platform's secret storage; do not commit tokens.

## Updates, rollback, and image publishing

GitHub Actions tests every PR and push, builds a container, and checks persistent settings after actual container replacement. Successful main pushes publish `edge` and `sha-<commit>` tags. Version tags such as `v0.1.0` publish `0.1.0` and `0.1`; stable Compose deploys pin the exact version. PRs do not publish images and have read-only repository permissions. GHCR publication requires the repository's Actions package permissions. Multi-architecture publishing is not configured; default images target Linux amd64.

Stop or allow current recordings to finish before planned maintenance. Back up persistent data, choose a tested version, then:

```sh
RECORDER_IMAGE=ghcr.io/yaboisean319/stream-recorder:0.1.0 docker compose pull
RECORDER_IMAGE=ghcr.io/yaboisean319/stream-recorder:0.1.0 docker compose up -d --no-build
docker compose logs --tail=100
```

For rollback, select the previous known-good image with the same commands and retain the same volume. Review release notes for metadata compatibility before rollback. Avoid deleting the volume. Graceful shutdown preserves capture intent and footage for recovery, so an intentional recording cancellation must use Stop before shutdown.

## Development and validation

Python 3.12+, FFmpeg/ffprobe, and the dependencies in `requirements.txt` are required. Run `python -m recorder` with an isolated writable `DATA_DIR`; the default HTTP port is 8080. Run acceptance tests with `python -m unittest discover -s tests -v`. Tests use local deterministic media rather than depending on CH91 availability. They cover observable HTTP recording behavior and playable outputs. The workflow provides container build/replacement checks. Cloudflare, Entra, and calendar policy provisioning remains operator-managed.

Before a recording becomes Ready, the service checks the MP4 tracks and duration, then fully decodes its video and audio to verify playback. Progress stays at 99% during this validation. Failed conversion or validation retains captured source segments and blocks downloads.

Lowering the storage cap immediately reclaims the oldest unpinned completed recordings. If pinned, active, processing, or retained error media still exceeds the cap, `/api/status` reports `storage.cap_exceeded: true` and the interface shows the overflow; protected media remains intact.
