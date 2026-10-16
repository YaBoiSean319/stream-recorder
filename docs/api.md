# Stream Recorder API v1

The checked [OpenAPI contract](openapi.json) is also available at `/api/v1/openapi.json`; interactive request documentation is at `/api/v1/docs`. The docs obey the current session's role and CSRF protection, or an entered API key. Entered keys stay in the current page only. Writes run only when Send request is pressed; deletes require a second confirmation. The Try it path must match the selected operation and remain on this recorder. Binary branding uploads accept a selected image file. It does not store reusable credentials or proxy calls to another host.

See [external player viewer tracking](viewer-analytics.md) for tracking setup, gateway boundaries, live counts, and report semantics. See the [GUI-to-API operation matrix](gui-api-parity.md) for every Settings and recorder action, scoped permission, and external-consent boundary. [Authentication and recovery](authentication.md), [YouTube setup and upload lifecycle](youtube.md), and [webhook verification](webhooks.md) contain the detailed operational guides. The matrix and contract cover all server-side GUI operations; browser-local System/Light/Dark theme intentionally has no API that changes another browser's preference. Google authorization requires an interactive user consent and browser-bound callback.

Existing `/api` paths and responses remain compatible. `/api/v1` shares their operations, except that `GET /api/v1/recordings` returns a page and v1 errors are JSON (`{"error":{"code":"not_found","message":"Unknown recording"}}`). The v1 source and storage endpoints are narrower Settings views. A preview URL is an opaque short lived relay reference; never treat it as a reusable upstream URL. Browser theme choice is browser-local and has no server endpoint.

| Operation | Operator key | Administrator key |
| --- | --- | --- |
| Status, preview, recordings list/detail/download | `status:read`, `preview:read`, `recordings:read`, `recordings:download` | Same |
| Start, stop, pin, title | `recordings:start`, `recordings:stop`, `recordings:pin`, `recordings:title` | Same |
| Source, storage, settings read/write | — | `settings:read`, `settings:write` |
| Delete recordings; auth/OIDC/key management | — | `recordings:delete`, `auth:manage` |
| Read live viewer count, bounded history/statistics, and configuration history | `GET /api/v1/viewers/live`; `GET /api/v1/viewers/history`; `GET /api/v1/viewers/configurations` | `viewers:read` | Operators and Administrators; current player by default; gaps and coverage are explicit; history is bounded by configured retention (up to 730 days). |
| Export analytics CSV | `GET /api/v1/viewers/export.csv` | `viewers:export` | Operators and Administrators; preserves history filters and includes metrics, coverage and gap markers without tracking secrets or viewer identifiers. |
| Configure/diagnose player tracking | `GET/PUT /api/v1/viewers/config`; `GET /api/v1/viewers/diagnostics` | `viewers:manage` | Administrator only; settings and diagnostics never return key material. |
| Rotate or revoke player tracking key | `POST /api/v1/viewers/key/rotate`; `DELETE /api/v1/viewers/key` | `viewers:manage` | Rotation returns replacement key/snippet once; DELETE revokes immediately without returning secret material. |

An omitted permission list gives a key all operations allowed by its role, preserving valid older role-only keys. `GET /api/v1/auth/permissions` returns the selectable permission sets for the Settings UI and automation. Supply a nonempty subset in `POST /api/v1/auth/keys` to narrow it. `expires_at` is an optional future Unix timestamp. The raw key is shown only in the creation response; the server keeps a hash. Listing includes `last_used_at`; deletion revokes immediately. Browser sessions retain role checks and CSRF. To enable `require_api_key`, first configure a local recovery administrator, then POST `/api/v1/auth/config` with `{"require_api_key":true}`. This toggle is separate from `enabled` browser authentication: it rejects unauthenticated machine requests without a key while preserving signed-in browser access. If browser authentication is off, the browser prompts for the local administrator when Require API key is on.

The caller-scoped `Idempotency-Key` header accepts 1–200 visible ASCII characters. For a given key identity (or local anonymous client identity when authentication is disabled), an identical request body returns the same recording and 201 with `Idempotency-Replayed: true`, even after restart. A different body returns 409 `idempotency_conflict`; a deleted recording returns 409 and needs a new key. Mappings currently have no automatic expiration. A concurrent start with another key gets 409 `conflict` while a session is active.

Collection pages use `limit` (1–200, default 50) and `offset` (nonnegative, default 0) and return `items`, `total`, `limit`, `offset`, and `next_offset` (null at the end). Changes while paging can shift offsets, so a client should deduplicate by stable recording ID.

## Examples

```sh
BASE=https://recorder.example
KEY='<one-time-key-from-administrator>'
curl -H "Authorization: Bearer $KEY" "$BASE/api/v1/status"
curl -H "Authorization: Bearer $KEY" "$BASE/api/v1/recordings?limit=50&offset=0"
curl -X POST "$BASE/api/v1/recordings" \
  -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: calendar-event-12345' -d '{"duration_seconds":3600}'
curl -X POST "$BASE/api/v1/recordings/RECORDING_ID/stop" \
  -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' -d '{}'
```

For Power Automate, use an HTTP action with `POST`, URI `https://recorder.example/api/v1/recordings`, Authorization bearer header, JSON body `{"duration_seconds":3600}`, and `Idempotency-Key` equal to the stable calendar event ID. A retry of that action reuses the same key and body. Save the returned `id`; poll `GET /api/v1/recordings/{id}` until `ready` or `error`.

For n8n, use an HTTP Request node with Bearer credential, POST `/api/v1/recordings`, JSON body `{ "duration_seconds": 3600 }`, and `Idempotency-Key` expression derived from the source event identifier. A second HTTP Request node can poll `/api/v1/recordings/{{$json.id}}` and follow the `state`. Keep tokens in credentials, never literal workflow exports.

Stable errors use HTTP 400 `invalid_request`, 401 `unauthorized`, 403 `forbidden`, 404 `not_found`, 405 `method_not_allowed`, 409 `conflict` or `idempotency_conflict`, 429 `rate_limited`, and 507 `insufficient_storage`. The human readable `message` can change; branch on status and `code`.

## Webhooks

Administrator scoped keys use `webhooks:manage` for `GET/POST /api/v1/webhooks`, `GET/PUT/DELETE /api/v1/webhooks/{destination_id}`, and `GET /api/v1/webhooks/deliveries`. See [verification and automation](webhooks.md) for signature, payload, retry, and receiver details. Saved signing secrets are never returned.

## Compatibility and migration

The `/api/v1` surface uses the versioned JSON error envelope and paginated recording collection. Existing `/api` paths keep their established response shapes, including the array returned by `GET /api/recordings`. Where a versioned route was added only for a new feature (branding assets, YouTube, uploads, webhook management, API documentation), it is documented under that route; it does not change old payloads. `/api/v1/source` and `/api/v1/storage` are focused views over the legacy Settings state. Renaming changes only the title and future download filename; IDs and original dates remain stable.

Updates retain saved settings, source credentials, API keys, authentication/OIDC state, branding images, recordings, upload jobs and webhook destinations in the persistent data directory. New settings receive defaults only when absent. Preserve the same `/data` volume during container replacement. See the [README update/rollback guide](../README.md#updates-rollback-and-image-publishing).

## GUI permissions

The complete endpoint-by-endpoint mapping is in [gui-api-parity.md](gui-api-parity.md). At a glance, Operators can read status and recordings, start/stop, download, rename, pin, and manage upload jobs. Administrators additionally manage shared source/storage, branding/assets, authentication/OIDC/keys, YouTube connection/defaults, and signed webhooks; deliberate recording deletion is Administrator-only. Scoped API keys can further restrict each permission. Browser-session mutations require CSRF. Supplying an invalid or expired bearer key always fails instead of falling back to the browser session.

## Upload and notification workflows

A manual upload requires a ready recording, a connected channel, and an explicit `made_for_kids` Boolean. The request below uses Unlisted visibility and disables subscriber notifications. `Idempotency-Key` safely replays an accepted queue request for the same caller and payload; it does not promise exactly-once behavior at YouTube when the remote result is uncertain.

```sh
curl -fsS -X POST "$RECORDER_URL/api/v1/recordings/$RECORDING_ID/uploads" \
  -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: event-2026-10-15-townhall' \
  -d '{"title":"Town Hall","description":"October meeting","privacy_status":"unlisted","made_for_kids":false,"notify_subscribers":false,"tags":["town hall"],"category_id":"22"}'
# Poll the queued job. State can be queued, uploading, paused, failed, uncertain, completed, canceled, or released.
curl -fsS "$RECORDER_URL/api/v1/uploads/JOB_ID" -H "Authorization: Bearer $KEY"
```

For Power Automate, store the bearer value as a secure environment/connection variable. Use an HTTP action for the POST above, set `Idempotency-Key` from a stable event ID, store the returned `id`, then poll `/api/v1/uploads/{id}` until a terminal state. Use a separate action for deliberate retry or requeue; do not automatically requeue `uncertain` until the channel has been checked.

For n8n, use an HTTP Request node with a Bearer credential, JSON metadata, and `Idempotency-Key` derived from the source event identifier. Save the returned upload job ID and use a polling node for `/api/v1/uploads/{{$json.id}}`. Keep credentials in n8n credentials, not exported workflow JSON. Detailed Google client setup, consent, resumable upload and uncertain-outcome behavior is in [youtube.md](youtube.md).

Webhook listeners should verify the raw-body HMAC, timestamp, and stable event ID before processing. See [webhooks.md](webhooks.md) for the signature formula and a receiver workflow outline. A successful API queue response means local upload work is durably accepted; it does not mean YouTube has finished processing the video.
