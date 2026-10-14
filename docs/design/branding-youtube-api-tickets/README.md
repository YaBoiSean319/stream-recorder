# Approved implementation tickets

Parent specification: https://github.com/YaBoiSean319/stream-recorder/issues/21

Approved by the user on 2026-10-14. All twelve issues are published with ready-for-agent.

| Issue | Deliverable | Blocked by |
|---|---|---|
| [#22](https://github.com/YaBoiSean319/stream-recorder/issues/22) | Shared grayscale branding and browser-specific themes | None |
| [#23](https://github.com/YaBoiSean319/stream-recorder/issues/23) | Persistent light/dark logos and favicon customization | #22 |
| [#24](https://github.com/YaBoiSean319/stream-recorder/issues/24) | Customizable accessible SSO login button | #22, #23 |
| [#25](https://github.com/YaBoiSean319/stream-recorder/issues/25) | Editable recording titles and download filenames | None |
| [#26](https://github.com/YaBoiSean319/stream-recorder/issues/26) | Versioned recording/settings API with retry-safe starts | None |
| [#27](https://github.com/YaBoiSean319/stream-recorder/issues/27) | Fine-grained API keys and independent API protection | #26 |
| [#28](https://github.com/YaBoiSean319/stream-recorder/issues/28) | Shared YouTube OAuth connection in Settings | #26, #27 |
| [#29](https://github.com/YaBoiSean319/stream-recorder/issues/29) | Reliable manual YouTube uploads and copyable video links | #25, #28 |
| [#30](https://github.com/YaBoiSean319/stream-recorder/issues/30) | Optional automatic uploads with metadata templates | #29 |
| [#31](https://github.com/YaBoiSean319/stream-recorder/issues/31) | Signed persistent webhooks for recording and upload results | #27, #29 |
| [#32](https://github.com/YaBoiSean319/stream-recorder/issues/32) | Complete GUI/API parity and interactive automation documentation | #23, #24, #25, #27, #30, #31 |
| [#33](https://github.com/YaBoiSean319/stream-recorder/issues/33) | Verify combined branding, uploads, automation, and upgrades | #32 |

Start with #22, #25, and #26. Other tickets become eligible only when their blockers are complete. Explicit parent/blocking issue references are used because the connector does not expose native relationships. The parent specification has not been closed or modified by ticket publication.
