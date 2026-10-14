# Branding, recording titles, YouTube uploads, and API parity

Status: design interview in progress. Confirmed choices below are requirements; open questions are not defaults or implementation authorization.

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

## Remaining decisions

## Round two confirmed requirements

- Operators may rename recordings and upload them; Administrators manage shared branding, the YouTube connection, and API keys. Recording deletion remains administrator-only.
- YouTube Settings include Google OAuth credentials, the callback URL, Connect/Disconnect, connected channel name, setup documentation, and a Reconnect required state.
- Automatic upload after completion is optional and disabled by default. Configure title/description templates and visibility, defaulting to Unlisted, with per-recording automation overrides.
- A persistent upload queue survives restarts and updates. Protect queued/uploading recordings from automatic storage cleanup. Upload one at a time, retry temporary failures, offer cancellation, prevent accidental duplicate uploads, and allow explicit re-upload.
- Signed outgoing webhooks report recording completion/failure and upload results, with delivery retries and polling alternatives. Start/upload operations support idempotency keys.
- Branding and default colors apply at application level. Each browser stores its own System/Light/Dark choice and starts on System. Updates preserve settings and browser preferences; branding changes only on explicit edit/reset.

## Open design questions

- Permissions for renaming, uploads, branding, credentials, and API key management.
- Google OAuth setup and account/channel connection lifecycle.
- Upload queue, restart recovery, retention protection, cancellation, and duplicate requests.
- API scope boundaries, event notifications, documentation hosting, compatibility, and secret handling.
- Branding asset limits, global versus personal appearance choices, and reset scope.
- Recording title behavior during capture and interaction with YouTube metadata.

## Terminology

See the root GLOSSARY.md for canonical domain terms.
