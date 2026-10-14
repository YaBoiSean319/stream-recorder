# Branding, recording titles, YouTube uploads, and API parity

Status: design approved on 2026-10-14. Specification #21 and implementation tickets #22–#33 are published; implementation is not claimed complete.

## Confirmed requirements

- Preserve the main interface layout. Shared branding settings apply to the application, login page, browser title, and favicon.
- Provide app name, uploaded logo/favicon, branding preview, and reset to defaults.
- Default theme preference is System, following device light/dark preferences. Use charcoal/black, gray surfaces, and white text in dark mode, with a matching grayscale light mode.
- Accept accent colors through RGB input, hex input, and a color picker. Preserve distinct success, warning, and error indicators.
- Provide independently customizable SSO button text, uploaded icon, and colors wherever SSO login is offered. Render it as a styled button, with default text “Sign in with SSO”.
- Renaming changes a recording's visible title and downloaded filename. Preserve its stable internal identity and original dates. Allow duplicate titles and sanitize downloaded filenames.
- Initially connect one shared YouTube channel in Settings. Manually upload completed recordings, with editable title, description, and visibility.
- Default upload visibility is Unlisted. Display background progress, failures, retry controls, and a copyable YouTube video link. Keep the local recording after uploading.
- Expose every GUI operation through a versioned backend API, including recording operations, sources, settings, and upload jobs. Provide scoped API keys, OpenAPI documentation, and practical repository examples.

## Round two confirmed requirements

- Operators may rename recordings and upload them; Administrators manage shared branding, the YouTube connection, and API keys. Recording deletion remains administrator-only.
- YouTube Settings include Google OAuth credentials, the callback URL, Connect/Disconnect, connected channel name, setup documentation, and a Reconnect required state.
- Automatic upload after completion is optional and disabled by default. Configure title/description templates and visibility, defaulting to Unlisted, with per-recording automation overrides.
- A persistent upload queue survives restarts and updates. Protect queued/uploading recordings from automatic storage cleanup. Upload one at a time, retry temporary failures, offer cancellation, prevent accidental duplicate uploads, and allow explicit re-upload.
- Signed outgoing webhooks report recording completion/failure and upload results, with delivery retries and polling alternatives. Start/upload operations support idempotency keys.
- Branding and default colors apply at application level. Each browser stores its own System/Light/Dark choice and starts on System. Updates preserve settings and browser preferences; branding changes only on explicit edit/reset.

## Round three confirmed requirements

- Branding supports separate light/dark logos, one favicon, and accents/backgrounds/surfaces/text/SSO button colors. Color controls accept RGB, hex, and color-picker input. Warn about poor contrast. Branding-only reset preserves authentication and connections.
- Recordings may be named on start and renamed later. Renaming does not modify existing YouTube videos. Upload metadata is captured when queued, initially using the recording title.
- Upload forms and automatic-upload defaults include tags, category, audience (made for kids), and subscriber notifications. An explicit audience choice is required before enabling uploads. Subscriber notifications default off. Templates support recording title, source name, and start date/time.
- Refuse new captures with an explanatory message if protected queued/uploading recordings prevent enough space being freed. Failed uploads retain protection until retried, cancelled, or explicitly released. Block deliberate deletion during queued/running uploads.
- API keys have selectable permissions, optional expiration, revocation, and last-used information, independently of browser authentication. A separate Require API key setting defaults off for compatibility. Reveal key values only on creation. Read endpoints never expose saved passwords or OAuth tokens.
- Serve interactive API documentation in the application and maintain repository documentation. Introduce /api/v1 with existing routes retained as compatibility aliases. Document examples, permissions, errors, pagination, webhooks, and Power Automate/n8n workflows.
- Initial Google OAuth consent requires a human; authorized recording/upload operations can subsequently run unattended.

## Round four confirmed requirements

- Automatic uploads include only successfully finalized recordings. Interrupted/recovered recordings require manual upload. Enabling automatic uploads affects future recordings only; existing recordings remain manually uploadable.
- Cancellation stops further work without deleting an already-created YouTube video. Preserve known video links. Disconnecting pauses pending jobs. Jobs remain bound to the original channel; connecting a different channel requires explicit requeuing.
- Interactive documentation follows application access controls. Try it uses the current user's permissions or an entered API key. Never embed credentials. Identify write/delete operations clearly and execute only on explicit submission.

## Open design questions

None. Implementation details must satisfy the requirements above and existing project standards; they do not expand product scope.

## Terminology

See the root GLOSSARY.md for canonical domain terms.
