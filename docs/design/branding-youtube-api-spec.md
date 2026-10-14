# Specification #21

## Problem Statement

Stream Recorder currently has fixed identity and limited accent presets, time-based recording filenames, no YouTube integration, and an undocumented/unversioned API whose key permissions are coarse. The operator needs application-level branding, useful recording titles, resilient YouTube sharing, and a complete documented automation interface while preserving existing recording behavior and saved configuration.

## Solution

Keep the established recorder layout and extend Settings with shared branding, YouTube connection/upload defaults, scoped API key controls, and webhook configuration. Default to a neutral grayscale palette and browser-specific System theme. Provide editable recording titles, manual and optional automatic Unlisted YouTube uploads through a durable queue, and a versioned API covering every GUI operation with interactive and repository documentation.

## User Stories

1. As an Administrator, I want to configure a shared application name, so that the recorder carries my organization's identity.
2. As an Administrator, I want to configure app-wide colors with RGB, hex, and a color picker, so that I can choose precise brand colors.
3. As an Administrator, I want to upload separate light/dark logos and one favicon, so that branding remains readable across themes and browser tabs.
4. As an Administrator, I want to preview branding and receive contrast warnings, so that I can assess changes before saving.
5. As an Administrator, I want to reset branding independently, so that I can restore defaults without losing authentication or integrations.
6. As an user, I want to start with a neutral grayscale appearance and System theme, so that the app follows my device preference.
7. As an user, I want to choose System, Light, or Dark per browser, so that my appearance preference does not change another user's browser.
8. As an Administrator, I want to customize SSO button text, icon, and colors, so that the login action is recognizable and clearly a button.
9. As an Operator, I want to name a recording at start, so that captured events are easy to identify.
10. As an Operator, I want to rename recordings and their downloaded filenames, so that I can correct titles without changing recording IDs or dates.
11. As an automation client, I want to retain stable recording references after rename, so that existing flows keep working.
12. As an Administrator, I want to configure Google OAuth credentials and see the callback URL, so that I can connect my YouTube channel.
13. As an Administrator, I want to connect, inspect, disconnect, and reconnect one shared channel, so that I can manage upload authorization.
14. As an Administrator, I want to see actionable setup and reconnect instructions, so that integration problems are diagnosable.
15. As an Operator, I want to manually upload a finalized recording, so that I can share footage through YouTube.
16. As an Operator, I want to edit title, description, visibility, tags, category, audience, and subscriber notifications, so that each upload has the intended metadata.
17. As an Operator, I want to default uploads to Unlisted and subscriber notifications off, so that sharing follows the agreed defaults.
18. As an Administrator, I want to make an explicit audience choice before enabling uploads, so that uploads have an intentional made-for-kids declaration.
19. As an Operator, I want to observe upload progress, failure details, retries, and cancellation, so that I can manage background transfers.
20. As an Operator, I want to copy the resulting video link, so that I can share the uploaded recording.
21. As an Operator, I want to keep the local recording after upload, so that uploading does not discard my footage.
22. As an Administrator, I want to enable optional automatic uploads for future successful recordings, so that routine publishing can run unattended.
23. As an Administrator, I want to configure automatic-upload templates and defaults, so that metadata does not require repeated entry.
24. As an automation client, I want to override upload metadata per recording, so that calendar-driven flows can label and share each event.
25. As an Operator, I want to manually upload interrupted or recovered finalized footage, so that I can inspect it before publication.
26. As an Operator, I want to have queued upload metadata remain stable after a recording rename, so that queued work keeps its chosen title.
27. As an Operator, I want to resume persistent upload work after a restart or update, so that transfers do not depend on a browser session.
28. As an Operator, I want to upload one video at a time with retries and duplicate prevention, so that transfers remain manageable.
29. As an Operator, I want to explicitly re-upload a recording, so that an intentional new upload is possible.
30. As an Operator, I want to protect queued, uploading, and failed-job recordings from cleanup, so that pending footage is retained.
31. As an Operator, I want to receive a clear capacity refusal when protected media prevents capture, so that storage pressure cannot silently remove pending footage.
32. As an Operator, I want to cancel upload work without deleting an already-created YouTube video, so that cancellation has a predictable boundary.
33. As an Administrator, I want to pause pending jobs on disconnect and bind them to their original channel, so that reconnection cannot silently publish to a different channel.
34. As an Administrator, I want to create scoped, expiring, revocable API keys and inspect last use, so that automation has appropriate access.
35. As an Administrator, I want to require API keys independently of browser login, so that machine access can be controlled separately.
36. As an automation client, I want to perform every GUI operation through a versioned API, so that the recorder is fully automatable.
37. As an automation client, I want to retry recording starts and upload requests with idempotency keys, so that retries do not create duplicate work.
38. As an automation client, I want to receive signed, retried webhooks and poll job state, so that I can react to completion and failures.
39. As an automation client, I want to use existing endpoints during the transition, so that deployed integrations continue working.
40. As an developer, I want to read OpenAPI and interactive documentation with practical examples, so that I can build reliable integrations.
41. As an user, I want to try API requests with my own session permissions or an entered key, so that documentation does not bypass access controls.
42. As an Administrator, I want to keep saved settings, credentials, assets, records, and keys through upgrades, so that updates preserve configuration.
43. As an Administrator, I want to keep branding, connection, and key management restricted to administrators, so that shared configuration is protected.
44. As an Operator, I want to rename and upload without gaining deletion or settings privileges, so that operational access remains appropriately limited.

## Implementation Decisions

### Shared branding and browser appearance

- Branding is shared application configuration, managed by Administrators when authentication is enabled. It covers app name, browser title, login/main views, separate light/dark logos, one favicon, accents, backgrounds, surfaces, text, and independent SSO button appearance.
- Fresh defaults use charcoal/black and gray dark surfaces with white text and a matching grayscale light theme. Success, warning, and error states remain distinguishable.
- Color controls accept validated RGB, hex, and picker input. Provide previews, contrast warnings, and a branding-only reset that preserves authentication, connections, other settings, and recordings.
- System/Light/Dark selection is browser-specific and defaults to System for new browsers. Follow system changes while System is selected. Preserve valid existing per-browser theme choices across updates; prior browser-local accent choices migrate to the new app-wide branding model without writing global branding from an arbitrary browser.
- Persist branding configuration and uploaded assets in durable application storage, not container image layers. Validate asset types, sizes, and decoded content with documented supported formats; reject active/untrusted content. These concrete limits are implementation choices.
- Public pre-login branding exposes only presentation information, never protected settings or credentials.
- SSO uses a keyboard-accessible styled button, not a plain text link. Default label is “Sign in with SSO”; editable text, uploaded icon, and independent colors apply wherever SSO is offered. Preserve OIDC and local recovery behavior.

### Recording titles

- Accept an optional title at start and permit Operators and Administrators to rename it later. The visible title and downloaded filename change; stable recording ID, original capture dates, media identity, and retention ordering do not.
- Duplicate titles are allowed. Sanitize download filenames and response headers. Existing unnamed recordings retain useful start-time-derived names after migration.
- A renamed recording does not update an existing YouTube video. Snapshot resolved upload metadata when enqueuing, initially deriving its title from the recording title.

### YouTube connection and upload defaults

- Initially support one shared channel, administered through Settings with Google OAuth client credentials, exact callback URL, channel identity, Connect/Disconnect, reconnect-required status, and a repository setup guide.
- Initial Google consent requires human interaction. Configuration, connection status, authorization initiation/callback, disconnect, and all subsequent upload operations have backend interfaces; do not claim that API access bypasses Google consent.
- Mask secrets in reads and preserve them on unrelated edits; never expose reusable OAuth tokens in API responses, browser configuration, documentation, or logs.
- Upload only finalized playable media. Operators may manually upload, rename, retry, and cancel within their operational permissions; shared connection/default configuration and deliberate recording deletion remain administrator-only.
- Upload metadata includes title, description, visibility, tags, category, explicit made-for-kids audience, and subscriber notifications. Default visibility is Unlisted; notifications default off. Require an explicit audience choice before uploads can be enabled. Permit editing all applicable fields before manual enqueue.
- Automatic uploads are optional, disabled by default, and apply only to future successfully finalized recordings. Interrupted/recovered recordings require manual upload even if they later finalize successfully. Existing recordings are not backfilled when enabling the feature.
- Automatic defaults support templates using recording title, source name, and original start date/time, plus visibility and remaining upload metadata. Per-recording automation can override these values. If no source name exists, document a neutral fallback rather than introducing a multi-source catalog.
- After upload, show and return the YouTube video ID and copyable URL. Preserve local recordings. If remote processing or response ambiguity matters, distinguish known video creation from a completed usable result.

### Durable jobs, recovery, and storage

- Use a persistent upload queue with one active upload at a time, independent of recording/browser lifetime. Persist channel binding, metadata, attempt/progress state, error details, and known remote video identity.
- Survive restart/container replacement and resume uploads where supported. Retry temporary failures with bounded backoff; surface actionable permanent failures and reconnect needs.
- Prevent accidental duplicate uploads and support explicit re-upload. If a remote outcome is uncertain, reconcile or report uncertainty rather than blindly starting a second remote video. Do not promise cross-service exactly-once execution.
- Protect local media while queued/uploading; failed jobs retain protection until retried, cancelled, or explicitly released. Protection is independent of user pinning, and multiple jobs for one recording must not release each other's protection.
- Automatic cleanup and deliberate deletion cannot remove queued/uploading media. If protection prevents enough space being reclaimed, refuse new captures with a clear explanation and retain existing safe-stop behavior for a running capture.
- Cancellation stops further work and releases appropriate job protection; it does not delete a video already created on YouTube. Preserve known links and local media. Local recording deletion never silently deletes a remote video.
- Disconnect pauses pending jobs and stops further transfer work. Reconnecting the same channel can resume eligible work. Replacing the channel requires explicit requeueing; never send old queued jobs to the replacement channel automatically.

### API coverage, access, and notifications

- Introduce /api/v1 with preserved existing routes as compatible aliases. Preserve response semantics for existing automation while documenting the new contract.
- Cover every GUI operation: status/health, preview access, recording start/stop/list/detail/download/delete/pin/title, source/storage/settings read/update, branding/assets/preview/reset, authentication/OIDC configuration and flows, scoped key lifecycle, YouTube configuration/connect/status/disconnect/defaults, upload enqueue/list/detail/retry/cancel/requeue/protection release, and webhook management/delivery inspection.
- Browser-specific theme selection can remain client storage; document the distinction from shared server configuration and expose any necessary appearance data without changing another browser's preference.
- Create an explicit operation-permission model that handles both versioned and compatibility routes. API keys have selectable permissions, optional expiry, immediate revocation, and last-used metadata. Show reusable key material once on creation; store nonrecoverable verifiers.
- Support a separate Require API key setting, initially off for compatibility, independently of browser authentication. Distinguish browser-session requests from machine requests so authenticated GUI functionality is not broken; valid session CSRF and normal role checks remain enforced. Require a usable key-management/recovery path before enabling restrictions.
- Preserve existing valid keys and roles by migrating to equivalent permissions. Never allow an invalid/expired/revoked supplied key to fall back to unauthenticated access. When browser authentication is enabled, Administrators manage shared settings, credentials, and keys; Operators retain existing operational functions plus title/upload operations.
- Document validated request/response schemas, pagination, stable error codes/statuses, permission requirements, and retry behavior. Recording start and upload enqueue support persisted idempotency keys: same caller/key/payload replays the admitted result; different payload conflicts. Document retention of idempotency records.
- Signed outgoing webhooks report recording completion/failure and upload results, with persistent delivery retries, event IDs for receiver deduplication, actionable delivery state, and polling alternatives. Never include credentials or sensitive source URLs in event payloads.
- Treat webhook receivers as explicitly configured Administrator destinations; validate URLs, bound delivery resources/timeouts, redact signing material, and prevent unsigned cross-origin forwarding of secrets on redirects.

### Documentation and compatibility

- Maintain OpenAPI and human-readable repository documentation, with permission matrix, upload state model, migration notes, errors, pagination, idempotency, webhook signature verification/retries, Google setup, and practical curl/Power Automate/n8n examples using neutral placeholders.
- Serve interactive docs under application access controls. Try it uses the current session's permissions/CSRF or an entered API key; never embed credentials or execute a mutation automatically. Identify writes/deletes and run only on explicit submission.
- Preserve sources/protected-stream credentials, authentication/OIDC configuration, API keys, storage cap, records/pins/media, and valid browser preferences during upgrades. Fill missing new defaults without resetting saved choices.

## Testing Decisions

- Reuse the existing public HTTP/API and playable-MP4 seam with isolated persistent storage and a controllable local HLS source. Extend current aiohttp acceptance/authentication tests, process-restart tests, Playwright browser checks, and container replacement checks.
- Add a local Google/OAuth/upload fixture and webhook receiver at the external HTTP boundary. Verify outcomes, files, emitted requests/events, access decisions, and observed browser behavior rather than internal class structure or command strings. Normal automated tests must not upload real videos or require production credentials.
- Test global branding across independent browsers, per-browser System changes, RGB/hex/picker equivalence, contrast warnings, safe assets, login/button keyboard interaction, preview/reset scope, desktop/mobile layout, and restart preservation.
- Test naming on start/rename/download; stable IDs/dates; duplicate/sanitized titles; permission denial; legacy unnamed records; and queued metadata snapshots.
- Test valid/failed OAuth and secret masking, channel binding, Unlisted/notifications-off defaults, required audience, manual/automatic eligibility, metadata overrides/templates, copyable links, and retained local media.
- Test queued/active/failed media protection, capacity refusal, cancellation/delete races, independent pinning, explicit re-upload, retries, uncertain outcomes, restart recovery, same/different-channel reconnect, and durable state.
- Test all GUI operations via /api/v1, compatibility routes, fine-grained keys, expiry/revocation/last use, auth-disabled and independently required-key modes, idempotent replay/conflict/concurrency/restart, errors, pagination, and non-disclosure.
- Test webhook signatures, event deduplication identities, failed delivery/backoff/restart recovery, receiver URL handling, permission restrictions, and polling alternatives.
- Verify OpenAPI contracts against routes and representative requests, interactive-doc access/Try it permissions, and realistic Power Automate/n8n examples.
- Run existing media/admission/recovery/protected-source/authentication suites and required CI browser/container checks. Report unavailable or skipped browser/container checks honestly; do not treat them as passed.

## Out of Scope

- Multiple linked YouTube channels at once; per-user YouTube accounts; uploads to other platforms.
- Automatically editing/deleting already-created remote videos when renaming, cancelling, or deleting local recordings.
- Video editing/clipping, thumbnail editing, captions, playlist management, scheduled YouTube publication, or a multi-source catalog.
- Internal calendar scheduling, concurrent captures, rebuilding the established recorder layout, or changing stream extraction behavior.
- Creating Google Cloud projects, completing Google consent/audits, administering Entra/Cloudflare access, or deploying external Power Automate/n8n flows on the user's behalf.
- Merge, deployment, or claims of implementation completion as part of this specification/ticket preparation.

## Further Notes

- User confirmed the complete agreement on 2026-10-14 and requested to-spec plus to-tickets.
- Authoritative design history: https://github.com/YaBoiSean319/stream-recorder/blob/docs/branding-youtube-api-design/docs/design/branding-youtube-api.md
- Existing runtime uses persistent SQLite-backed settings/record payloads, aiohttp HTTP boundaries, separate access/OIDC handling, and browser-local appearance preferences. Extend existing seams rather than mandating a framework rewrite.
- Existing glossary defines Recording, Stream source, Administrator, and Operator; new terms distinguish Recording title, Application branding, YouTube connection, and Upload job.
- Build vertical implementation slices; each owns its persistence/API/UI and observable validation. No separate horizontal backend-only or frontend-only phase is required.
