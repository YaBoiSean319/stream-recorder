# GUI to API operation matrix

The API is the supported automation surface for server state. Paths in the matrix are `/api/v1` routes; the legacy `/api` counterparts remain supported where noted in [API reference](api.md). Browser-local color theme has no server operation. Google authorization still requires an Administrator to consent in a browser at Google; API credentials do not bypass that boundary.

## Recorder and shared settings

| GUI operation | API operation | Permission | Effect / boundary |
| --- | --- | --- | --- |
| Health and connection status | `GET /api/v1/health`; `GET /api/v1/status` | public; `status:read` | Read only. |
| Load preview | `GET /api/v1/preview`; `GET /preview/{token}` | `preview:read` | Token is short-lived and opaque; it does not expose the source URL. |
| Choose System, Light, or Dark theme | Browser-local preference | No server permission | Each browser stores its own choice and follows device changes while System is selected. |
| Read or save stream source, credentials, and storage cap | `GET/PUT /api/v1/settings`; focused aliases `GET/PUT /api/v1/source`, `/storage` | `settings:read`, `settings:write` | Administrator only; masked secrets are retained unless explicitly cleared. |
| Start recording or opt out of auto-upload | `POST /api/v1/recordings` | `recordings:start` | Starts one capture; supports `Idempotency-Key` and optional upload metadata snapshot. |
| Change automatic-upload overrides during capture | `PATCH /api/v1/recordings/{id}` | `recordings:start` for `auto_upload`/`upload_metadata` | Active capture only; values are validated and snapshotted before completion. |
| Stop active or selected recording | `POST /api/v1/stop`; `POST /api/v1/recordings/{id}/stop` | `recordings:stop` | Selected ID is retry-safe; legacy active-stop stays available. |
| Extend an active timed recording | `POST /api/v1/recordings/{id}/extend` | `recordings:extend` | Operators and Administrators; positive whole minutes from 1–1,440; deadline additions are durable and caller-scoped idempotent. |
| Review missed start attempts | `GET /api/v1/events` | `recordings:events` | Operators and Administrators; lists external start requests rejected while capture/finalization was busy. |
| List, inspect, download recordings | `GET /api/v1/recordings`; `GET /api/v1/recordings/{id}`; `GET /api/v1/recordings/{id}/download` | `recordings:read`, `recordings:download` | List uses `limit` and `offset`; ready output is MP4. |
| Rename and pin/unpin | `PATCH /api/v1/recordings/{id}` | `recordings:title`, `recordings:pin` | Each field is authorized independently for scoped keys. |
| Delete recording | `DELETE /api/v1/recordings/{id}` | `recordings:delete` | Destructive. Upload protection and active use return conflict; pins do not prevent deliberate deletion. |

## Branding and authentication

| GUI operation | API operation | Permission | Effect / boundary |
| --- | --- | --- | --- |
| Read sign-in presentation | `GET /api/v1/branding`; `GET /api/v1/branding/assets/{slot}` | public; `branding:read` | Presentation only; no credentials. Browser theme remains per browser. |
| Preview, discard, or inspect contrast for unsaved branding | Browser-local draft plus `GET /api/v1/branding` | `branding:read` | Does not save shared state until the operator submits Save. |
| Save shared branding and SSO-button appearance | `PUT /api/v1/branding` | `branding:write` | Administrator write. |
| Choose/preview a logo, favicon, or SSO icon | Browser-local file preview | No server permission until saved | Preview is local; upload occurs only when Save branding is submitted. |
| Upload/clear a logo, favicon, or SSO icon | `PUT/DELETE /api/v1/branding/assets/{slot}` | `branding:write` | Binary image write or destructive clear. |
| Reset all shared branding assets and values | `POST /api/v1/branding/reset` | `branding:write` | Destructive to branding only; does not reset other settings. |
| Sign in/out and inspect access state | `GET /api/v1/auth/status`; `POST /api/v1/auth/login`, `/logout` | public/session | Session writes require CSRF. |
| Configure local access and independent API-key requirement | `GET/POST /api/v1/auth/config` | `auth:manage` | Administrator only; recovery requirements are enforced by the server. |
| Manage scoped API keys | `GET/POST /api/v1/auth/keys`; `DELETE /api/v1/auth/keys/{id}`; `GET /api/v1/auth/permissions` | `auth:manage` | Key creation reveals reusable key material once; deletion revokes immediately. |
| Configure SSO or start/callback OIDC | `GET/POST /api/v1/auth/oidc/config`; `GET /api/v1/auth/oidc/login`, `/callback` | `auth:manage` for config; public browser flow | OIDC login redirects to the configured identity provider; it does not expose secrets. |

## YouTube and uploads

| GUI operation | API operation | Permission | Effect / boundary |
| --- | --- | --- | --- |
| Read/save Google client config and inspect channel | `GET/PUT /api/v1/youtube/config`; `GET /api/v1/youtube/status` | `youtube:manage` | Administrator only; reads mask secrets and tokens. |
| Connect/reconnect shared channel | `POST /api/v1/youtube/connect`; `GET /api/v1/youtube/callback`; status/refresh/disconnect operations | Signed-in Administrator browser when authentication is enabled; `youtube:manage` for API-key operations other than starting consent | Google consent requires an interactive browser and a browser-bound callback. Disconnect clears local authorization; it does not revoke Google's grant. |
| Read/update automatic-upload defaults | `GET/PUT /api/v1/youtube/automation` | `youtube:manage` | Disabled by default; applies only to eligible future recordings. |
| Read channel identity or upload jobs | `GET /api/v1/uploads/channel`; `GET /api/v1/uploads`; `GET /api/v1/uploads/{job_id}`; `GET /api/v1/recordings/{recording_id}/uploads` | `uploads:read` | Read-only. Upload jobs are durable and channel-bound. |
| Queue manual upload | `POST /api/v1/recordings/{recording_id}/uploads` | `uploads:create` | Creates work; accepts metadata and `Idempotency-Key`. This does not discard local media. |
| Retry, cancel, explicitly requeue, or release protection | `POST /api/v1/uploads/{job_id}/retry`, `/cancel`, `/requeue`, `/release` | `uploads:retry` or `uploads:cancel` | Mutations. Requeue can create another remote video; uncertain outcomes require reconciliation. Cancel never deletes a created YouTube video. |

## Webhooks and interactive API docs

| GUI operation | API operation | Permission | Effect / boundary |
| --- | --- | --- | --- |
| Create/list/edit/delete webhook receiver | `GET/POST /api/v1/webhooks`; `GET/PUT/DELETE /api/v1/webhooks/{destination_id}` | `webhooks:manage` | Administrator only. Delete cancels pending deliveries; saved secrets are not returned. |
| Inspect webhook delivery status | `GET /api/v1/webhooks/deliveries` | `webhooks:manage` | Read-only latest 200 delivery records. |
| Read OpenAPI or use Try it | `GET /api/v1/openapi.json`; `GET /api/v1/docs` | current application access | Authenticated sessions use their normal role and CSRF; API keys use their scopes. Try it submits only after explicit confirmation and restricts requests to the selected `/api/v1` operation. |

## Viewer analytics

| GUI operation | API operation | Permission | Effect / boundary |
| --- | --- | --- | --- |
| View live audience, timeline, statistics, daily peaks, day/hour heatmap, or Recording context | `GET /api/v1/viewers/live`; `GET /api/v1/viewers/history`; `GET /api/v1/viewers/configurations` | `viewers:read` | Operators and Administrators; reports include per-period coverage, selectable local-time heatmap periods, and capture windows; range is bounded by configured retention (maximum 730 days). Optional `timezone` preserves report grouping context. |
| Export the selected viewer analytics range | `GET /api/v1/viewers/export.csv` | `viewers:export` | Operators and Administrators; preserves selected range, configuration, timezone and resolution. CSV includes coverage and gap rows and does not contain tracking secrets or browser/tab identifiers. |
| Configure external player tracking, inspect diagnostics, or rotate/revoke key | `GET/PUT /api/v1/viewers/config`; `GET /api/v1/viewers/diagnostics`; `POST /api/v1/viewers/key/rotate`; `DELETE /api/v1/viewers/key` | `viewers:manage` | Administrator only; configuration reads never reveal key material; rotation returns replacement once and DELETE revokes without readback. |

## Permission model

With authentication enabled, browser roles are checked on every request and state-changing session requests require CSRF. An Operator may perform recording, preview, download, title/pin, and upload operations. Administrators additionally manage shared settings, branding, authentication, YouTube, and webhooks, and may deliberately delete recordings. Scoped keys are checked against the operation and, for a combined recording `PATCH`, each changed field. An invalid supplied key never falls back to a browser session. `require_api_key` applies to machine requests independently of browser login; a signed-in session continues to work.

When built-in authentication is disabled, the deployment's external access boundary still applies. In local mode the app treats requests as an Administrator unless `require_api_key` is enabled. Do not expose the recorder directly to an untrusted network.


## External player viewer tracking

| GUI operation | API operation | Permission | Effect / boundary |
| --- | --- | --- | --- |
| Read live viewer count and timestamp on Recorder | `GET /api/v1/viewers/live` | `viewers:read` | Returns available count or disabled state, collector health, last valid player report, and as-of time. |
| Configure external player, origin, public base URL, reporting timezone, and enablement | `GET/PUT /api/v1/viewers/config` | `viewers:manage` | Administrator only; player URL is separate from the direct HLS stream source. |
| Generate/revoke the external player key and copyable snippet | Save configuration or `POST /api/v1/viewers/key/rotate` | `viewers:manage` | Raw key and snippet appear only in the write response; rotate when the snippet is lost. |
| Report anonymous playback from an instrumented video | `POST /api/v1/viewers/report` | Dedicated tracking key + exact allowed `Origin` | Narrow public ingestion; only browser ID, tab ID, and playback flag are accepted. |
