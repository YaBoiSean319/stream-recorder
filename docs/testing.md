# Testing

Run `python -m unittest discover -s tests -v` from the checkout with Python 3.12+, aiohttp, FFmpeg and FFprobe installed. Acceptance tests start a private recorder process and a real-time local HLS source, each on a dynamically selected loopback port. Their temporary persistent data and generated media are isolated from operator storage. No public stream or identity provider is required.

The acceptance seam is the HTTP API and completed MP4 download. FFprobe verifies audio/video tracks and duration; FFmpeg fully decodes downloaded output. Killing and restarting the application against the same data directory exercises persisted recovery through public API state. Fixture HTTP outages represent unavailable footage, not recoverable replay.

Docker is unavailable in the implementation environment. Local application tests do not establish that a Docker image or Compose deployment works. The CI workflow must build and smoke-test the image before publication; operators must verify persistent mounts and their Cloudflare Access policy in deployment.

The browser test is opt-in with `RUN_BROWSER_TESTS=1`. Install Playwright and its Chromium browser first, then provide `NODE_PATH` (or `CODEX_PRIMARY_RUNTIME_NODE_MODULES`) pointing at the installed Node modules. `CHROMIUM_PATH` optionally selects a browser executable. It tests actual live preview playback, browser closure during recording, controls, downloads, pinning and delete/cancel confirmation. Local browser installation was blocked by the network; this test is supplied for CI and is not claimed as locally passed.

Settings coverage uses the same public HTTP and browser boundaries: fresh empty-source admission, explicit source clearing and restart, source changes during capture, and pre-upgrade settings fixtures that retain source, storage cap, pinned recording history, and playable media after default filling. Browser coverage navigates to Settings from the empty state, configures Source and Storage, returns to Recorder, and then exercises capture.

Appearance coverage in `tests/appearance.cjs` uses Settings controls and observed browser state for theme/accent changes, reload persistence, isolated browser preferences, device System changes, invalid-preference fallback, keyboard focus, and mobile navigation/overflow. It runs as part of browser smoke. Chromium is not present locally, so these new browser cases require CI validation; no local browser pass is claimed.

Failure coverage uses external process fixtures: a temporary FFmpeg command shim rejects conversion while real capture continues; an optional compiled `LD_PRELOAD` fixture reports zero available filesystem blocks. The latter verifies public API handling of OS capacity, without filling the host disk. It requires a C compiler and is skipped when none exists. These controlled failures do not substitute for physical disk-failure testing.

## Combined upgrade review

`test_combined_access_and_protected_capture_survive_replacement` configures a bearer/header-protected local HLS source, local recovery administrator, enabled authentication, administrator/operator keys, and OIDC group mappings. It kills and replaces the recorder process against the same isolated data directory. The public API must retain masked settings, deny anonymous preview and operator configuration/deletion, permit protected preview/capture/download, accept local recovery login, and immediately reject a revoked key. OIDC protocol validation and signed role assignment use the separate local-provider fixture; the combined restart test deliberately does not contact an external provider.

`test_upgrade_fills_missing_settings_without_replacing_legacy_source` supplies a historical two-field settings fixture, including the formerly bundled saved URL. Constructing historical input is the only database fixture setup; all preservation assertions use the public API and playable media download. The old URL is preserved as historical test input, never a new-install default or deployment example.

CI runs HTTP/media/browser checks before image publication, repeats server acceptance inside the built image, and replaces a container with the same named volume. `browser-review` contains screenshots from isolated fixtures for visual review, uploaded even if browser checks fail. Screenshots show test configuration only, not operator deployment data. A green local server test is not evidence that browser or container checks passed: reviewers must inspect the actual workflow results and artifacts. No merge or deployment is part of verification.

For a focused restart check, run:

```sh
python -m unittest tests.test_acceptance.RecorderAcceptance.test_combined_access_and_protected_capture_survive_replacement -v
```

For browser evidence locally, set `BROWSER_ARTIFACT_DIR` to a scratch directory alongside `RUN_BROWSER_TESTS=1`; screenshots are optional and do not alter the browser assertions. Missing browser binaries are a skipped check, not a pass.

Validation evidence for this slice: the combined process replacement/protected-media/local-recovery/key-revocation acceptance passed locally (one test, 5.358 seconds); JavaScript syntax validation and the appearance preference harness also passed. The browser smoke test now uses a protected local upstream, covers desktop theme/accent combinations, mobile overflow, local login, authenticated mobile preview/capture and Settings, and captures review screenshots. Chromium installation was blocked by an invalid downloaded archive; no local browser execution is claimed. Docker is unavailable locally; actual container replacement, image acceptance and screenshot evidence remain CI checks, not locally verified results.
