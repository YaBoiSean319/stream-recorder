# Testing

Run `python -m unittest discover -s tests -v` from the checkout with Python 3.12+, aiohttp, FFmpeg and FFprobe installed. Acceptance tests start a private recorder process and a real-time local HLS source, each on a dynamically selected loopback port. Their temporary persistent data and generated media are isolated from operator storage. No public stream or identity provider is required.

The acceptance seam is the HTTP API and completed MP4 download. FFprobe verifies audio/video tracks and duration; FFmpeg fully decodes downloaded output. Killing and restarting the application against the same data directory exercises persisted recovery through public API state. Fixture HTTP outages represent unavailable footage, not recoverable replay.

Docker is unavailable in the implementation environment. Local application tests do not establish that a Docker image or Compose deployment works. The CI workflow must build and smoke-test the image before publication; operators must verify persistent mounts and their Cloudflare Access policy in deployment.

The browser test is opt-in with `RUN_BROWSER_TESTS=1`. Install Playwright and its Chromium browser first, then provide `NODE_PATH` (or `CODEX_PRIMARY_RUNTIME_NODE_MODULES`) pointing at the installed Node modules. `CHROMIUM_PATH` optionally selects a browser executable. It tests actual live preview playback, browser closure during recording, controls, downloads, pinning and delete/cancel confirmation. Local browser installation was blocked by the network; this test is supplied for CI and is not claimed as locally passed.

Failure coverage uses external process fixtures: a temporary FFmpeg command shim rejects conversion while real capture continues; an optional compiled `LD_PRELOAD` fixture reports zero available filesystem blocks. The latter verifies public API handling of OS capacity, without filling the host disk. It requires a C compiler and is skipped when none exists. These controlled failures do not substitute for physical disk-failure testing.
