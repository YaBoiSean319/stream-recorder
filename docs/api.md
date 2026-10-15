# Stream Recorder API v1

The checked [OpenAPI contract](openapi.json) is also available at `/api/v1/openapi.json`; interactive request documentation is at `/api/v1/docs`. The docs obey the current session's role and CSRF protection, or an entered API key. Entered keys stay in the current page only. Writes run only when Send request is pressed.

Existing `/api` paths and responses remain compatible. `/api/v1` shares their operations, except that `GET /api/v1/recordings` returns a page and v1 errors are JSON (`{"error":{"code":"not_found","message":"Unknown recording"}}`). The v1 source and storage endpoints are narrower Settings views. A preview URL is an opaque short lived relay reference; never treat it as a reusable upstream URL. Browser theme choice is browser-local and has no server endpoint.

| Operation | Operator key | Administrator key |
| --- | --- | --- |
| Status, preview, recordings list/detail/download | `status:read`, `preview:read`, `recordings:read`, `recordings:download` | Same |
| Start, stop, pin, title | `recordings:start`, `recordings:stop`, `recordings:pin`, `recordings:title` | Same |
| Source, storage, settings read/write | — | `settings:read`, `settings:write` |
| Delete recordings; auth/OIDC/key management | — | `recordings:delete`, `auth:manage` |

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
